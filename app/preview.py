"""Aperçu des images enregistrées, pour trouver le moment à extraire (menu « Extraire une vidéo »).

Ce NVR n'enregistre pas le flux secondaire (`loadfile.cgi` avec `subtype=1` → 400, vérifié le 30/10/2026) : on
télécharge l'enregistrement d'origine d'une fenêtre de `MINUTES` min. Deux modes, choisis par le navigateur :
- `copie` : la vidéo telle quelle dans un MP4 (0,04 s par minute), si le navigateur dit lire le H.265 (Chrome / Edge
  avec décodage par la carte graphique ; non vérifié sous Windows) ;
- `conversion` : H.264 960 px 15 i/s, lisible partout, mais le décodage du H.265 5 Mpx coûte ≈ 30 s de calcul par
  minute de vidéo (mesuré sur un PC 16 cœurs : 4 à 6 s réelles) ; priorité basse pour ne pas gêner le poste.
Deux aperçus à la fois (lecture de plusieurs caméras) ; gardés `KEEP_SECONDS` après la dernière demande, puis effacés.
L'heure incrustée dans l'image est celle du NVR ; la fenêtre demandée (heure du PC) est convertie comme pour l'extrait.
"""
import logging
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from . import cameras, config, export, nvrwatch, security

log = logging.getLogger("preview")
DIR = config.DATA_DIR / "previews"
MINUTES = 5
KEEP_SECONDS = 3600
MODES = ("copie", "conversion")
MAX_PENDING = 8                             # aperçus en attente ou en cours, tous utilisateurs : au-delà, refusé
MAX_TOTAL_BYTES = 3e9                       # aperçus prêts gardés sur le disque : au-delà, les plus anciens sont effacés
_pool = ThreadPoolExecutor(max_workers=2)
_lock = threading.Lock()
_jobs: dict[str, dict] = {}                 # « caméra-début » → état de l'aperçu


def path(key: str) -> Path:
    return DIR / f"{key}.mp4"


def public(job: dict) -> dict:
    return {k: job[k] for k in ("id", "camera_id", "start_ts", "end_ts", "mode", "state", "error")}


def request(cam: dict, start_ts: int, mode: str = "conversion") -> dict:
    """Aperçu de `MINUTES` min à partir de `start_ts` (arrondi à la minute) : réutilisé s'il existe déjà."""
    if mode not in MODES:
        raise export.ExportError("Mode d'aperçu inconnu")
    start_ts -= start_ts % 60
    if start_ts > time.time():
        raise export.ExportError("Ce moment n'est pas encore enregistré")
    key = f"{cam['id']}-{start_ts}-{mode}"
    with _lock:
        job = _jobs.get(key)
        if job and job["state"] != "erreur":
            job["used"] = time.time()
            return public(job)
        if sum(j["state"] in ("attente", "en_cours") for j in _jobs.values()) >= MAX_PENDING:
            raise export.ExportError("Trop de vidéos en préparation : réessayez dans un instant")
        _trim()
        DIR.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(DIR).free - export.DISK_RESERVE < MINUTES * export.MB_PER_MINUTE * 2e6:
            raise export.ExportError("Pas assez de place sur le disque de l'ordinateur pour afficher la vidéo")
        job = _jobs[key] = {"id": key, "camera_id": cam["id"], "site_id": cam["site_id"], "start_ts": start_ts,
                            "end_ts": start_ts + MINUTES * 60, "mode": mode, "state": "attente", "error": "", "used": time.time()}
    _pool.submit(_run, job, cam)
    return public(job)


def _trim() -> None:
    """Aperçus prêts au-delà de `MAX_TOTAL_BYTES` : les moins récemment demandés sont effacés (verrou tenu)."""
    ready = sorted((j for j in _jobs.values() if j["state"] == "pret"), key=lambda j: j["used"])
    sizes = {j["id"]: (path(j["id"]).stat().st_size if path(j["id"]).is_file() else 0) for j in ready}
    total = sum(sizes.values())
    for j in ready:
        if total <= MAX_TOTAL_BYTES:
            break
        _jobs.pop(j["id"], None)
        path(j["id"]).unlink(missing_ok=True)
        total -= sizes[j["id"]]


def recorded(cams: list[dict], start_ts: int, end_ts: int) -> dict[int, list[list[int]]]:
    """Périodes enregistrées de chaque caméra entre `start_ts` et `end_ts` (heure du PC), pour la barre de temps :
    la tranche demandée peut tomber dans un trou (caméra débranchée, NVR arrêté) ; un NVR à la fois."""
    out: dict[int, list[list[int]]] = {}
    by_nvr: dict[tuple, list[dict]] = {}
    for c in cams:
        by_nvr.setdefault(cameras.device_key(c), []).append(c)
    for group in by_nvr.values():
        c0 = group[0]
        pw = security.decrypt(c0["password_enc"])
        shift = timedelta(seconds=export._offset(c0, pw))
        found = nvrwatch.recordings(c0["host"], c0["port_http"], c0["username"], pw, [c["channel"] for c in group],
                                    datetime.fromtimestamp(start_ts) + shift, datetime.fromtimestamp(end_ts) + shift)
        for c in group:
            out[c["id"]] = [[int((a - shift).timestamp()), int((b - shift).timestamp())] for a, b in found[c["channel"]]]
    return out


def get(key: str) -> dict | None:
    with _lock:
        job = _jobs.get(key)
        if job:
            job["used"] = time.time()
        return job


def _low_priority() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.BELOW_NORMAL_PRIORITY_CLASS}
    return {}


def _run(job: dict, cam: dict) -> None:
    job["state"] = "en_cours"
    # dossier propre à l'aperçu : ffmpeg ne voit pas les aperçus des autres caméras (ni des autres sites)
    work = DIR / f"{job['id']}.tmp"
    work.mkdir(parents=True, exist_ok=True)
    dav, tmp = work / "nvr.dav", work / "apercu.mp4"
    try:
        pw = security.decrypt(cam["password_enc"])
        start = datetime.fromtimestamp(job["start_ts"]) + timedelta(seconds=export._offset(cam, pw))
        export._download(cam, pw, start, start + timedelta(minutes=MINUTES), dav)
        head = [config.FFMPEG, "-nostdin", "-y", "-loglevel", "error", *export.FFMPEG_INPUT, "-i", str(dav), "-an"]
        tail = ["-movflags", "+faststart", str(tmp)]
        if job["mode"] == "copie":
            # « hvc1 » : étiquette H.265 attendue par les navigateurs ; refusée si la caméra filme en H.264
            p = subprocess.run([*head, "-c:v", "copy", "-tag:v", "hvc1", *tail], capture_output=True, text=True, timeout=600)
            if p.returncode:
                p = subprocess.run([*head, "-c:v", "copy", *tail], capture_output=True, text=True, timeout=600)
        else:
            cmd = [*head, "-vf", "scale=960:-2,fps=15", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                   "-pix_fmt", "yuv420p", *tail]
            if os.name != "nt" and shutil.which("nice"):
                cmd = ["nice", "-n", "10", *cmd]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, **_low_priority())
        if p.returncode or not tmp.is_file() or tmp.stat().st_size == 0:
            log.warning("aperçu %s : %s", job["id"], p.stderr[-500:])
            raise export.ExportError("la vidéo reçue du NVR n'a pas pu être convertie")
        tmp.replace(path(job["id"]))
        job["state"] = "pret"
    except export.ExportError as e:
        job.update(state="erreur", error=str(e))
    except FileNotFoundError:
        job.update(state="erreur", error=f"programme de conversion vidéo introuvable ({config.FFMPEG})")
    except Exception as e:  # noqa: BLE001
        log.warning("aperçu %s : %s", job["id"], security.scrub(str(e))[:300])
        job.update(state="erreur", error=nvrwatch.reason(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def purge(everything: bool = False) -> None:
    """Efface les aperçus plus demandés depuis `KEEP_SECONDS` (tous au démarrage)."""
    limit = time.time() - KEEP_SECONDS
    with _lock:
        for key, job in list(_jobs.items()):
            if everything or (job["used"] < limit and job["state"] in ("pret", "erreur")):
                _jobs.pop(key)
                path(key).unlink(missing_ok=True)
    if everything and DIR.is_dir():
        for p in DIR.iterdir():
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
