"""Extrait vidéo à remettre aux forces de l'ordre (vol, agression…), sans passer par l'interface du NVR.

Le pharmacien coupe la période aux ciseaux dans « Voir la vidéo » (heure du PC). CamSup télécharge l'enregistrement
d'origine depuis le NVR Dahua (`loadfile.cgi`, lecture seule), le copie en MP4 sans réencoder l'image et prépare un
ZIP : une vidéo par caméra + `LISEZ-MOI.txt` (pharmacie, période, écart d'horloge du NVR, empreintes SHA-256).
Le navigateur l'enregistre sur le PC ; la copie de travail de CamSup est effacée après `EXPORT_RETENTION_DAYS`.
La période est convertie à l'heure du NVR (son horloge peut être décalée : 5 h 30 de retard vu le 30/10/2026).
Un extrait à la fois, pour ménager le NVR. Mesuré le 30/10/2026 (caméra 5 Mpx H.265) : 10 min = 231 Mo en 22 s ;
période sans enregistrement → le NVR répond 400.
Hikvision : non pris en charge.
"""
import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from requests.auth import HTTPDigestAuth

from . import config, db, devicehttp, nvrwatch, security

log = logging.getLogger("export")
DIR = config.DATA_DIR / "exports"
MB_PER_MINUTE = 40          # place à prévoir par caméra et par minute (mesuré : 23 Mo/min en 5 Mpx H.265)
DISK_RESERVE = 2e9          # octets laissés libres sur le disque du PC (base, journaux) par les extraits et aperçus
DOWNLOAD_SECONDS = 900      # durée maximale du téléchargement d'une vidéo depuis le NVR
# options d'entrée de ffmpeg : fichiers locaux seulement (pas de réseau, pas de playlist qui lirait d'autres fichiers)
FFMPEG_INPUT = ["-protocol_whitelist", "file"]
_pool = ThreadPoolExecutor(max_workers=1)
DAY = "%d/%m/%Y à %H:%M:%S"


class ExportError(Exception):
    """Erreur lisible par le pharmacien."""


def file(eid: int) -> Path:
    return DIR / f"{eid}.zip"


def create(user, site_id: int, cams: list[dict], start_ts: int, end_ts: int, reason: str) -> int:
    """Enregistre la demande et la met en file. Les caméras ont déjà été contrôlées (site, droits, marque)."""
    minutes = (end_ts - start_ts) / 60
    if minutes <= 0:
        raise ExportError("La fin doit être après le début")
    if minutes > config.EXPORT_MAX_MINUTES:
        raise ExportError(f"Période trop longue : {config.EXPORT_MAX_MINUTES} min au plus par extrait")
    if start_ts > db.now():
        raise ExportError("La période demandée n'a pas encore commencé")
    if db.one("SELECT id FROM exports WHERE state IN ('attente','en_cours') LIMIT 1"):
        # un extrait à la fois : sans cette limite, des demandes répétées rempliraient la file (et le disque)
        raise ExportError("Un extrait est déjà en préparation : attendez qu'il soit prêt")
    DIR.mkdir(parents=True, exist_ok=True)
    need = minutes * len(cams) * MB_PER_MINUTE * 2e6          # vidéo d'origine + copie MP4 pendant la conversion
    free = shutil.disk_usage(DIR).free - DISK_RESERVE
    if free < need:
        raise ExportError(f"Pas assez de place sur le disque de l'ordinateur ({free / 1e9:.1f} Go libres, "
                          f"{need / 1e9:.1f} Go nécessaires)")
    eid = db.x("""INSERT INTO exports(site_id,user_id,username,camera_ids,cameras,start_ts,end_ts,reason,state,created_ts)
                  VALUES(?,?,?,?,?,?,?,?,'attente',?)""",
               (site_id, user.id, user.username, json.dumps([c["id"] for c in cams]),
                ", ".join(c["name"] for c in cams), start_ts, end_ts, reason, db.now()))
    _pool.submit(_run, eid)
    return eid


def recover() -> None:
    """Au démarrage : un extrait interrompu par l'arrêt de CamSup ne finira jamais."""
    db.x("UPDATE exports SET state='erreur', error='interrompu : CamSup a été arrêté pendant l''extraction', "
         "done_ts=? WHERE state IN ('attente','en_cours')", (db.now(),))
    for p in DIR.glob("*.tmp"):
        shutil.rmtree(p, ignore_errors=True)


def delete(eid: int) -> None:
    file(eid).unlink(missing_ok=True)
    db.x("DELETE FROM exports WHERE id=?", (eid,))


def purge() -> None:
    for r in db.q("SELECT id FROM exports WHERE created_ts < ? AND state NOT IN ('attente','en_cours')",
                  (db.now() - config.EXPORT_RETENTION_DAYS * 86400,)):
        delete(r["id"])


def max_bytes(start: datetime, end: datetime) -> int:
    """Taille au-delà de laquelle la vidéo reçue n'est pas crédible (×3 sur le débit prévu) : un NVR qui envoie sans
    fin ne doit pas remplir le disque du PC."""
    return int(max(60.0, (end - start).total_seconds()) / 60 * MB_PER_MINUTE * 3e6) + 50_000_000


def check_video(path: Path) -> None:
    """Vidéo binaire attendue (DHAV, MPEG-TS…) : un fichier texte serait une liste de lecture (ffconcat, HLS) qui
    ferait lire à ffmpeg d'autres fichiers du PC (aperçus d'un autre site, autre caméra du même extrait)."""
    with open(path, "rb") as fh:
        head = fh.read(256)
    if not head or all(b in b"\t\n\r" or 32 <= b < 127 for b in head):
        raise ExportError("la vidéo reçue du NVR n'est pas une vidéo")


def _download(cam: dict, pw: str, start: datetime, end: datetime, dest: Path) -> None:
    f = lambda d: quote(d.strftime(nvrwatch.FMT))                         # noqa: E731
    url = (f"http://{cam['host']}:{cam['port_http']}/cgi-bin/loadfile.cgi?action=startLoad&channel={cam['channel']}"
           f"&startTime={f(start)}&endTime={f(end)}&subtype=0")
    with devicehttp.request("GET", url, auth=HTTPDigestAuth(cam["username"], pw), stream=True, timeout=(10, 60)) as r:
        if r.status_code == 400:
            raise ExportError("aucun enregistrement sur cette période")
        if r.status_code == 401:
            raise ExportError("le NVR refuse l'identifiant enregistré dans CamSup")
        r.raise_for_status()
        limit, deadline, size = max_bytes(start, end), time.time() + DOWNLOAD_SECONDS, 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
                size += len(chunk)
                if size > limit:
                    raise ExportError("le NVR envoie plus de vidéo que prévu : téléchargement arrêté")
                if time.time() > deadline:
                    raise ExportError("téléchargement trop long : arrêté")
    if dest.stat().st_size == 0:
        raise ExportError("aucun enregistrement sur cette période")
    check_video(dest)


def _to_mp4(src: Path, dst: Path) -> None:
    """Image copiée telle quelle (aucun réencodage) ; le son éventuel est converti en AAC (le MP4 n'accepte pas le
    G.711 des NVR)."""
    cmd = [config.FFMPEG, "-nostdin", "-y", "-loglevel", "error", *FFMPEG_INPUT, "-i", str(src), "-map", "0:v", "-map", "0:a?",
           "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", str(dst)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        raise ExportError(f"programme de conversion vidéo introuvable ({config.FFMPEG}) : prévenir l'installateur")
    if p.returncode or not dst.is_file() or dst.stat().st_size == 0:
        log.warning("ffmpeg %s : %s", src.name, p.stderr[-500:])
        raise ExportError("la vidéo reçue du NVR n'a pas pu être convertie")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _line(text: str) -> str:
    """Texte sur une ligne, sans caractère de contrôle : un motif ou un nom de caméra ne doit pas pouvoir ajouter de
    fausses lignes (empreintes, vidéos) au LISEZ-MOI."""
    return re.sub(r"[\x00-\x1f\x7f\u2028\u2029]+", " ", text or "").strip()


def _safe(name: str) -> str:
    return re.sub(r"[^\w\- ]", "_", name).strip() or "camera"


def _offset(cam: dict, pw: str) -> int:
    """Avance (+) ou retard (−) de l'horloge du NVR sur celle du PC, en secondes."""
    r = nvrwatch.probe(cam["host"], cam["port_http"], cam["username"], pw, timeout=10)
    return round((datetime.strptime(r["time"], nvrwatch.FMT) - datetime.now()).total_seconds())


def _clock(offset: int) -> str:
    if abs(offset) < 2:
        return "à l'heure (moins de 2 s d'écart)"
    m, s = divmod(abs(offset), 60)
    h, m = divmod(m, 60)
    span = (f"{h} h {m:02d} min " if h else f"{m} min " if m else "") + f"{s} s"
    return f"{'en avance' if offset > 0 else 'en retard'} de {span}"


def _tz(ts: float) -> str:
    """Décalage de l'heure du PC sur le temps universel à cet instant (heure d'été comprise) : « UTC+02:00 »."""
    z = datetime.fromtimestamp(ts).astimezone().strftime("%z")
    return f"UTC{z[:3]}:{z[3:]}"


def _set(eid: int, **kw) -> None:
    db.x(f"UPDATE exports SET {', '.join(f'{k}=?' for k in kw)} WHERE id=?", (*kw.values(), eid))


def _run(eid: int) -> None:
    row = db.one("SELECT * FROM exports WHERE id=?", (eid,))
    if not row:
        return
    work = DIR / f"{eid}.tmp"
    try:
        work.mkdir(parents=True, exist_ok=True)
        cams = [c for c in (db.one("SELECT * FROM cameras WHERE id=?", (i,)) for i in json.loads(row["camera_ids"])) if c]
        site = db.one("SELECT name FROM sites WHERE id=?", (row["site_id"],))
        start, end = datetime.fromtimestamp(row["start_ts"]), datetime.fromtimestamp(row["end_ts"])
        _set(eid, state="en_cours")
        offsets: dict[str, int] = {}
        done, lines = [], []
        for n, cam in enumerate(cams, 1):
            _set(eid, progress=f"caméra {n} sur {len(cams)} : {cam['name']}")
            try:
                pw = security.decrypt(cam["password_enc"])
                if cam["host"] not in offsets:
                    offsets[cam["host"]] = _offset(cam, pw)
                off = timedelta(seconds=offsets[cam["host"]])
                dav, mp4 = work / f"{n}.dav", work / f"{n:02d}_{_safe(cam['name'])}.mp4"
                _download(cam, pw, start + off, end + off, dav)
                original = _sha256(dav)
                _to_mp4(dav, mp4)
                dav.unlink()
                done.append(mp4)
                size = f"{mp4.stat().st_size / 1e6:.1f}".replace(".", ",")
                lines.append(f"- {mp4.name} : {_line(cam['name'])}, canal {cam['channel']} du NVR {cam['host']}, {size} Mo\n"
                             f"  SHA-256 : {_sha256(mp4)}\n"
                             f"  SHA-256 de la vidéo d'origine reçue du NVR (.dav, avant mise en MP4) : {original}")
            except ExportError as e:
                lines.append(f"- {_line(cam['name'])} (canal {cam['channel']}) : {e}")
            except Exception as e:  # noqa: BLE001
                log.warning("extrait %s, %s : %s", eid, cam["name"], security.scrub(str(e))[:300])
                lines.append(f"- {_line(cam['name'])} (canal {cam['channel']}) : {nvrwatch.reason(e)}")
        if not done:
            raise ExportError("aucune vidéo : " + " ; ".join(l.split(" : ", 1)[1] for l in lines))
        clocks = "\n".join(f"- NVR {h} : horloge {_clock(o)} par rapport à l'ordinateur ; période à l'heure du NVR : "
                           f"du {(start + timedelta(seconds=o)).strftime(DAY)} au {(end + timedelta(seconds=o)).strftime(DAY)}"
                           for h, o in offsets.items())
        readme = f"""EXTRAIT DE VIDÉOSURVEILLANCE

Pharmacie : {_line(site['name']) if site else row['site_id']}
Période demandée : du {start.strftime(DAY)} au {end.strftime(DAY)} (heure de l'ordinateur de la pharmacie, {_tz(row['start_ts'])})
Motif / référence : {_line(row['reason']) or 'non renseigné'}
Extrait réalisé le {datetime.now().strftime(DAY)} ({_tz(time.time())}) par « {row['username']} » avec CamSup.

Heure : l'heure incrustée dans l'image est celle du NVR.
{clocks}

Vidéos :
{chr(10).join(lines)}

Les vidéos sont une copie de l'enregistrement d'origine du NVR : l'image n'est pas réencodée (aucune perte de
qualité) ; seul le conteneur change (MP4). Le son, s'il a été enregistré, est converti au format AAC.
Lecture : VLC (gratuit, videolan.org) ; certains lecteurs intégrés à Windows ne lisent pas le format H.265.
Empreinte SHA-256 : permet de vérifier qu'une vidéo n'a pas été modifiée depuis l'extraction.
"""
        (work / "LISEZ-MOI.txt").write_text(readme.replace("\n", "\r\n"), encoding="utf-8")
        tmp_zip = work / "extrait.zip"
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_STORED) as z:       # vidéo déjà compressée
            z.write(work / "LISEZ-MOI.txt", "LISEZ-MOI.txt")
            for p in done:
                z.write(p, p.name)
        tmp_zip.replace(file(eid))
        _set(eid, state="pret", progress="", size=file(eid).stat().st_size, sha256=_sha256(file(eid)),
             error="" if len(done) == len(cams) else "incomplet : voir LISEZ-MOI.txt", done_ts=db.now())
    except Exception as e:  # noqa: BLE001
        msg = str(e) if isinstance(e, ExportError) else "erreur inattendue"
        log.warning("extrait %s : %s", eid, security.scrub(str(e))[:300])
        _set(eid, state="erreur", progress="", error=msg[:300], done_ts=db.now())
    finally:
        shutil.rmtree(work, ignore_errors=True)
