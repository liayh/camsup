"""État des disques durs des enregistreurs (NVR/DVR), interrogé toutes les `HDD_CHECK_SECONDS` s (30 s ;
un relevé Dahua avec SMART prend 0,17 s, mesuré le 30/10/2026).

- Hikvision : GET /ISAPI/ContentMgmt/Storage (XML : hddList/hdd → status, capacity, freeSpace en Mo)
- Dahua     : API RPC2 de l'interface web (modèle, n° de série, SMART : température, heures, secteurs…),
              repli sur GET /cgi-bin/storageDevice.cgi?action=getDeviceAllInfo (état seul) si RPC2 échoue
- Hikvision : SMART via /ISAPI/ContentMgmt/Storage/hdd/<id>/SMARTTest/status (si l'appareil le fournit)

Un équipement = `cameras.device_key()` (marque, hôte, port HTTP, identifiants). L'état est gardé en mémoire
(ré-interrogé au démarrage) : pas de table dédiée.
"""
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from requests.auth import HTTPBasicAuth

from . import alerting, cameras, dahuarpc, db, devicehttp, security

log = logging.getLogger("storage")
_pool = ThreadPoolExecutor(max_workers=8)
_state: dict[tuple, dict] = {}
_lock = threading.Lock()


def device_get(url: str, user: str, pw: str, raw: bool = False, timeout: int = 10, basic: bool = False):
    """GET authentifié (Digest). `basic=True` (Hikvision anciens) : repli sur l'authentification Basic, qui envoie le
    mot de passe tel quel, seulement si la réponse vient en HTTPS ; jamais en HTTP clair."""
    # verify=False : les enregistreurs ont un certificat auto-signé (Dahua redirige le CGI de stockage vers HTTPS)
    r = devicehttp.request("GET", url, auth=devicehttp.digest(user, pw), timeout=timeout)   # repli HTTPS si besoin
    if r.status_code == 401 and basic and r.url.startswith("https://"):
        r = devicehttp.request("GET", r.url, auth=HTTPBasicAuth(user, pw), timeout=timeout)
    if r.status_code == 401:
        raise RuntimeError("Authentification refusée (401)")
    r.raise_for_status()
    return r.content if raw else r.text


# Attributs SMART surveillés : valeur brute > 0 = secteurs abîmés ou erreurs de liaison (câble SATA)
_SMART_COUNTERS = {5: "secteurs réalloués", 197: "secteurs en attente", 198: "secteurs illisibles",
                   199: "erreurs CRC (câble SATA)"}
TEMP_WARN = 50


def _health(d: dict) -> dict:
    """Complète un disque avec `level` (ok / warn / bad), `issues` et `ok` (False si à remplacer)."""
    issues, level = [], "ok"
    if not d.get("state_ok", True):
        issues.append(f"état {d['state']}")
        level = "bad"
    for a in d.get("smart", []):
        if a.get("threshold") and a["current"] <= a["threshold"]:
            issues.append(f"SMART {a['name']} sous le seuil")
            level = "bad"
        elif a.get("predict"):
            issues.append(f"SMART {a['name']} : défaillance prévue")
            level = "bad"
        elif a["id"] in _SMART_COUNTERS and a["raw"] > 0:
            issues.append(f"{a['raw']} {_SMART_COUNTERS[a['id']]}")
            level = "bad" if a["id"] != 199 and a["raw"] >= 50 else max(level, "warn", key=["ok", "warn", "bad"].index)
    if d.get("temp") is not None and d["temp"] >= TEMP_WARN:
        issues.append(f"température {d['temp']} °C")
        level = max(level, "warn", key=["ok", "warn", "bad"].index)
    d.pop("state_ok", None)
    return {**d, "level": level, "issues": issues, "ok": level != "bad"}


def _disk(name: str, state: str, state_ok: bool, total: int, used: int, **extra) -> dict:
    return {"name": name, "state": state, "state_ok": state_ok, "total": total, "used": used, "model": "",
            "serial": "", "temp": None, "hours": None, "smart": [], **extra}


def _hik(host: str, port: int, user: str, pw: str) -> list[dict]:
    root = ET.fromstring(device_get(f"http://{host}:{port}/ISAPI/ContentMgmt/Storage", user, pw, basic=True))
    for el in root.iter():                      # retire les espaces de noms XML
        el.tag = el.tag.split("}")[-1]
    disks = []
    for h in root.iter("hdd"):
        status = (h.findtext("status") or "").strip()
        total = int(float(h.findtext("capacity") or 0)) * 1024 * 1024
        free = int(float(h.findtext("freeSpace") or 0)) * 1024 * 1024
        d = _disk(h.findtext("hddName") or f"HDD {h.findtext('id')}", status, status.lower() == "ok",
                  total, max(0, total - free), model=(h.findtext("model") or "").strip())
        try:                                    # SMART : pas fourni par tous les modèles
            sm = ET.fromstring(device_get(f"http://{host}:{port}/ISAPI/ContentMgmt/Storage/hdd/{h.findtext('id')}"
                                    "/SMARTTest/status", user, pw, basic=True))
            for el in sm.iter():
                el.tag = el.tag.split("}")[-1]
            if sm.findtext("temprature") or sm.findtext("temperature"):
                d["temp"] = int(sm.findtext("temprature") or sm.findtext("temperature"))
            if sm.findtext("powerOnDay"):
                d["hours"] = int(sm.findtext("powerOnDay")) * 24
            if (sm.findtext("allEvaluaingStatus") or "ok").lower() not in ("ok", "pass"):
                d["state_ok"], d["state"] = False, f"SMART {sm.findtext('allEvaluaingStatus')}"
        except Exception:  # noqa: BLE001
            pass
        disks.append(_health(d))
    return disks


def smart_raw(v) -> int:
    """Valeur brute SMART telle que renvoyée par les NVR : nombre, « 123 », « 123 (0 5 0) », « 0x00000000007B »."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return max(0, int(v))
    t = str(v or "").strip()
    if t.lower().startswith("0x"):
        try:
            return int(t[2:].split()[0] or "0", 16)
        except ValueError:
            return 0
    m = re.match(r"\d+", t)
    return int(m[0]) if m else 0


def power_on_hours(raw: int) -> int | None:
    """Heures de marche (SMART 9) : les 32 bits de poids faible (certains disques mettent autre chose au-dessus) ;
    None si invraisemblable (plus de 20 ans)."""
    h = raw & 0xFFFFFFFF
    return h if h <= 20 * 8760 else None


def to_bytes(v) -> int:
    """Taille d'un disque ou d'une partition en octets. Certains NVR anciens la donnent en Mo : une valeur
    inférieure à 100 Mo pour un disque de vidéosurveillance est donc lue en Mo."""
    try:
        n = int(float(v or 0))
    except (TypeError, ValueError):
        return 0
    return n * 1024 * 1024 if 0 < n < 100_000_000 else max(n, 0)


SMART_SECONDS = 600                     # SMART (secteurs, température…) relu toutes les 10 min ; l'état du disque, à chaque relevé
_smart_cache: dict[tuple, tuple[float, list]] = {}


def _smart(s: "dahuarpc.Session", host: str, d: dict) -> list | None:
    """Valeurs SMART d'un disque, relues au plus toutes les `SMART_SECONDS` : elles évoluent lentement, et chaque
    lecture coûte 3 appels par disque au NVR toutes les 30 s."""
    key = (host, d["name"], d.get("serial", ""))
    hit = _smart_cache.get(key)
    if hit and time.time() - hit[0] < SMART_SECONDS:
        return hit[1]
    try:
        obj = s.call("devStorage.factory.instance", {"name": d["name"]}).get("result")
        if not obj:
            return hit[1] if hit else None
        try:
            vals = (s.call("devStorage.getSmartValue", None, obj).get("params") or {}).get("values", [])
        finally:
            s.call("devStorage.destroy", None, obj)
    except Exception as e:  # noqa: BLE001
        # SMART momentanément illisible : on garde le dernier relevé plutôt que de faire « disparaître » les secteurs
        log.info("SMART %s %s illisible (%s)", host, d["name"], security.scrub(str(e))[:120])
        return hit[1] if hit else None
    if not isinstance(vals, list):
        vals = []
    _smart_cache[key] = (time.time(), vals)
    return vals


def phantom_ok(dev: dict, total: int) -> bool:
    """Vrai disque ? Les emplacements vides que certains NVR listent (sans taille, sans n° de série, sans partition)
    ne sont pas affichés ; un disque en panne (taille 0 mais n° de série ou état d'erreur) l'est."""
    if total > 0 or str(dev.get("SerialNo") or "").strip():
        return True
    return str(dev.get("State", "")).lower() not in ("", "unknown", "notexist", "nodisk", "empty", "none", "offline")


def _dahua_rpc(host: str, port: int, user: str, pw: str) -> list[dict]:
    """Même API que l'interface web du NVR (lecture seule)."""
    with dahuarpc.shared(host, port, user, pw) as s:
        disks = []
        for dev in s.params("StorageDeviceManager.getDeviceInfos").get("device", []) or []:
            if not isinstance(dev, dict):
                continue
            parts = [x for x in (dev.get("Partitions") or []) if isinstance(x, dict)]
            total = sum(to_bytes(x.get("Total")) for x in parts) or to_bytes(dev.get("Capacity"))
            free = sum(to_bytes(x.get("Remain")) for x in parts)
            state = dev.get("State", "")
            if not phantom_ok(dev, total):
                continue
            d = _disk(dev.get("Name", "?"), state, state in ("Running", "Success") and
                      all(x.get("Status", "LvAvailable") == "LvAvailable" for x in parts),
                      total, max(0, total - free), model=dev.get("Module", ""), serial=dev.get("SerialNo", ""))
            vals = _smart(s, host, d)
            if vals is not None:
                for v in vals:
                    if not isinstance(v, dict):
                        continue
                    raw = smart_raw(v.get("Raw", 0))
                    d["smart"].append({"id": v.get("ID"), "name": v.get("Name", ""), "current": v.get("Current", 0),
                                       "worst": v.get("Worst", 0), "threshold": v.get("Threshold", 0),
                                       "raw": raw, "predict": v.get("Predict", 0)})
                    if v.get("ID") == 194:
                        d["temp"] = raw & 0xFF          # certains disques codent min/max dans les octets hauts
                    elif v.get("ID") == 9:
                        d["hours"] = power_on_hours(raw)
            disks.append(_health(d))
        return disks


# Noms en clair des compteurs SMART courants ; `watch` : compteur qui doit rester à 0 (sinon le disque s'abîme)
SMART_NAMES = {
    1: ("Erreurs de lecture (taux)", False), 3: ("Temps de démarrage", False), 4: ("Démarrages / arrêts", False),
    5: ("Secteurs réalloués", True), 7: ("Erreurs de positionnement (taux)", False), 9: ("Heures de marche", False),
    10: ("Essais de démarrage", True), 12: ("Mises sous tension", False), 183: ("Ralentissements SATA", False),
    184: ("Erreurs de bout en bout", True), 187: ("Erreurs non corrigées", True), 188: ("Délais de commande dépassés", False),
    189: ("Écritures en hauteur anormale", False), 190: ("Température (flux d'air)", False), 191: ("Chocs détectés", False),
    192: ("Arrêts brusques (coupures)", False), 193: ("Cycles de tête", False), 194: ("Température", False),
    195: ("Corrections ECC", False), 196: ("Réallocations", True), 197: ("Secteurs en attente", True),
    198: ("Secteurs illisibles", True), 199: ("Erreurs de câble (CRC)", False), 200: ("Erreurs d'écriture", True),
}


def diagnostic_summary(rpc: dict) -> dict:
    """Résumé lisible des réponses RPC2 brutes (`diagnostic`) : NVR, puis chaque disque avec taille, partitions et
    compteurs SMART nommés et jugés (bon / à surveiller / défaillant)."""
    def val(x):
        return (x or {}).get("params") if isinstance(x, dict) else None
    model = val(rpc.get("modele")) or {}
    version = val(rpc.get("version")) or {}
    out = {"modele": model.get("type", ""), "version": version.get("version", ""), "disques": []}
    for dev in (val(rpc.get("disques")) or {}).get("device", []) or []:
        if not isinstance(dev, dict):
            continue
        parts = [x for x in (dev.get("Partitions") or []) if isinstance(x, dict)]
        total = sum(to_bytes(x.get("Total")) for x in parts) or to_bytes(dev.get("Capacity"))
        free = sum(to_bytes(x.get("Remain")) for x in parts)
        smart = []
        for v in ((val((rpc.get("smart") or {}).get(dev.get("Name"))) or {}).get("values") or []):
            if not isinstance(v, dict):
                continue
            ident, raw = v.get("ID"), smart_raw(v.get("Raw", 0))
            name, watch = SMART_NAMES.get(ident, (v.get("Name") or f"Attribut {ident}", False))
            cur, thr = v.get("Current", 0), v.get("Threshold", 0)
            if thr and cur and cur <= thr:
                state = "defaillant"
            elif v.get("Predict"):
                state = "defaillant"
            elif watch and raw > 0:
                state = "surveiller"
            else:
                state = "bon"
            shown = (power_on_hours(raw) if ident == 9 else raw & 0xFF if ident in (190, 194) else raw)
            smart.append({"id": ident, "nom": name, "valeur": shown, "brut": v.get("Raw"), "actuel": cur,
                          "pire": v.get("Worst", 0), "seuil": thr, "etat": state})
        out["disques"].append({
            "nom": dev.get("Name", "?"), "modele": dev.get("Module", ""), "serie": dev.get("SerialNo", ""),
            "etat": dev.get("State", ""), "taille": total, "libre": free, "reel": phantom_ok(dev, total),
            "partitions": [{"nom": x.get("Name", ""), "taille": to_bytes(x.get("Total")), "libre": to_bytes(x.get("Remain")),
                            "etat": x.get("Status", "")} for x in parts],
            "smart": smart})
    return out


def diagnostic(host: str, port: int, user: str, pw: str) -> dict:
    """Ce que le NVR renvoie sur ses disques, tel quel (aucun mot de passe), pour corriger un modèle mal lu :
    modèle et version du NVR, réponse RPC2 (disques + SMART), réponse CGI, et ce que CamSup en déduit."""
    out: dict = {"host": host, "date": time.strftime("%Y-%m-%d %H:%M:%S")}
    def attempt(name, fn):
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001
            out[name] = {"erreur": security.scrub(str(e))[:300]}
    def rpc():
        with dahuarpc.shared(host, port, user, pw) as s:
            res = {"modele": s.call("magicBox.getDeviceType"), "version": s.call("magicBox.getSoftwareVersion"),
                   "disques": s.call("StorageDeviceManager.getDeviceInfos"), "smart": {}}
            for dev in (res["disques"].get("params") or {}).get("device", []) or []:
                name = dev.get("Name") if isinstance(dev, dict) else None
                if not name:
                    continue
                obj = s.call("devStorage.factory.instance", {"name": name}).get("result")
                if obj:
                    try:
                        res["smart"][name] = s.call("devStorage.getSmartValue", None, obj)
                    finally:
                        s.call("devStorage.destroy", None, obj)
            return res
    attempt("rpc2", rpc)
    attempt("cgi", lambda: device_get(f"http://{host}:{port}/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo",
                                       user, pw)[:200_000])
    attempt("lu_par_camsup", lambda: _dahua_rpc(host, port, user, pw))
    out["resume"] = diagnostic_summary(out["rpc2"]) if isinstance(out.get("rpc2"), dict) and "erreur" not in out["rpc2"] else None
    return out


_rpc_ok: set[str] = set()                # NVR dont l'API RPC2 a déjà répondu : jamais de repli sur le CGI


def _dahua(host: str, port: int, user: str, pw: str) -> list[dict]:
    """Disques par l'API RPC2 (avec SMART) ; le CGI (sans SMART, autres noms, tailles par partition) seulement pour
    un NVR qui n'a jamais répondu en RPC2. Avant, un échec passager du RPC2 faisait basculer sur le CGI : disques
    affichés différemment d'un relevé à l'autre (état instable, taille fausse, disque « en trop »)."""
    try:
        disks = _dahua_rpc(host, port, user, pw)
        _rpc_ok.add(host)
        return disks
    except Exception as e:  # noqa: BLE001
        if host in _rpc_ok:
            raise
        log.info("RPC2 %s indisponible (%s), repli sur le CGI (sans SMART)", host, security.scrub(str(e)))
    text = device_get(f"http://{host}:{port}/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo", user, pw)
    info: dict[int, dict] = {}
    for line in text.splitlines():
        m = re.match(r"list\.info\[(\d+)\]\.(.+?)=(.*)", line.strip())
        if not m:
            continue
        d = info.setdefault(int(m[1]), {"name": "", "state": "", "error": False, "total": 0, "used": 0})
        k, v = m[2], m[3]
        if k == "Name":
            d["name"] = v
        elif k == "State":
            d["state"] = v
        elif k.endswith(".IsError"):
            d["error"] |= v.lower() == "true"
        elif k.endswith(".TotalBytes"):
            d["total"] += int(float(v))      # une ligne par partition (un disque de 4 To = 4 × 1 To)
        elif k.endswith(".UsedBytes"):
            d["used"] += int(float(v))
    return [_health(_disk(d["name"] or f"HDD {i + 1}", d["state"], d["state"] == "Success" and not d["error"],
                          d["total"], d["used"]))
            for i, d in sorted(info.items()) if d["total"] > 0 or d["state"] not in ("", "Unknown", "NoDisk", "Empty")]


def _trend(host: str, d: dict) -> None:
    """Compteurs SMART de dégradation comparés à leur valeur au premier relevé de ce disque (n° de série).

    Un compteur qui augmente = le disque s'abîme encore : à remplacer, même s'il reste sous les seuils.
    Un disque remplacé a un autre n° de série, donc une nouvelle référence. Sans n° de série : pas de suivi.
    Remplit aussi `counters` (affichage) : secteurs réalloués, en attente, illisibles, erreurs CRC, avec la référence.
    """
    d["counters"] = []
    for a in d.get("smart", []):
        if a["id"] not in _SMART_COUNTERS:
            continue
        base = db.one("SELECT raw, ts FROM smart_baseline WHERE host=? AND serial=? AND attr=?",
                      (host, d["serial"], a["id"])) if d.get("serial") else None
        d["counters"].append({"id": a["id"], "label": _SMART_COUNTERS[a["id"]], "raw": a["raw"],
                              "base": base["raw"] if base else None, "since": base["ts"] if base else None})
        if not d.get("serial") or a["id"] == 199:                     # CRC : câble, pas le disque
            continue
        if not base:
            db.x("INSERT INTO smart_baseline(host,serial,attr,raw,ts) VALUES(?,?,?,?,?)",
                 (host, d["serial"], a["id"], a["raw"], db.now()))
            d["counters"][-1].update(base=a["raw"], since=db.now())
        elif a["raw"] > base["raw"]:
            since = time.strftime("%d/%m/%Y", time.localtime(base["ts"]))
            d["issues"].append(f"{_SMART_COUNTERS[a['id']]} en hausse : {base['raw']} → {a['raw']} depuis le {since}")
            d["level"], d["ok"] = "bad", False


def _poll(key: tuple, cams: list[dict]) -> None:
    brand, host, port, user, _ = key
    entry = {"brand": brand, "host": host, "site_id": cams[0]["site_id"], "last_check": db.now(),
             "disks": [], "error": ""}
    try:
        pw = security.decrypt(cams[0]["password_enc"])
        entry["disks"] = (_hik if brand == "hikvision" else _dahua)(host, port, user, pw)
        if not entry["disks"]:
            entry["error"] = "Aucun disque détecté"
        _fails.pop(key, None)
    except Exception as e:  # noqa: BLE001
        entry["error"] = security.scrub(str(e))[:200]
        log.warning("État disques %s:%s : %s", host, port, entry["error"])
        _fails[key] = _fails.get(key, 0) + 1
        with _lock:
            prev = _state.get(key)
        if prev and prev["disks"] and _fails[key] < STALE_FAILS:
            # échec passager (NVR occupé, réseau) : on garde le dernier relevé au lieu d'afficher « disques illisibles »
            return
    for d in entry["disks"]:
        _trend(host, d)
    with _lock:
        _state[key] = entry
    # alertes : un disque à remplacer (« à surveiller » reste affiché dans la page, sans alerte),
    # ou aucun disque dans un enregistreur qui répond
    if not entry["error"] or entry["error"] == "Aucun disque détecté":
        bad = [d for d in entry["disks"] if d["level"] == "bad"]
        if entry["error"]:
            alerting.raise_alert(f"hdd:{host}", cams[0]["id"], "hdd", f"Enregistreur {host} : aucun disque détecté")
        elif bad:
            alerting.raise_alert(f"hdd:{host}", cams[0]["id"], "hdd", f"Enregistreur {host} : disque "
                                 + " ; ".join(f"{d['name']} {', '.join(d['issues'])}" for d in bad))
        else:
            alerting.clear_alert(f"hdd:{host}")


_running = threading.Lock()
_fails: dict[tuple, int] = {}
STALE_FAILS = 4                          # relevés en échec d'affilée (30 s chacun) avant d'afficher l'erreur


def check(cams: list[dict]) -> None:
    """Interroge chaque équipement (en parallèle) et oublie ceux qui ne sont plus configurés. Ne fait rien si le
    relevé précédent n'est pas fini (un NVR lent ne doit pas empiler les relevés)."""
    if not _running.acquire(blocking=False):
        return
    try:
        _check(cams)
    finally:
        _running.release()


def _check(cams: list[dict]) -> None:
    wanted: dict[tuple, list[dict]] = {}
    for c in cams:
        if not c["host"]:
            continue
        wanted.setdefault(cameras.device_key(c), []).append(c)
    with _lock:
        for key in list(_state):
            if key not in wanted:
                _state.pop(key)
    list(_pool.map(lambda kv: _poll(*kv), wanted.items()))


def snapshot() -> list[dict]:
    with _lock:
        return [dict(v) for v in _state.values()]
