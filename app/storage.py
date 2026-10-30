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

from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from . import alerting, cameras, dahuarpc, db, devicehttp, security

log = logging.getLogger("storage")
_pool = ThreadPoolExecutor(max_workers=8)
_state: dict[tuple, dict] = {}
_lock = threading.Lock()


def device_get(url: str, user: str, pw: str, raw: bool = False, timeout: int = 10):
    # verify=False : les enregistreurs ont un certificat auto-signé (Dahua redirige le CGI de stockage vers HTTPS)
    for auth in (HTTPDigestAuth(user, pw), HTTPBasicAuth(user, pw)):
        r = devicehttp.request("GET", url, auth=auth, timeout=timeout)    # repli HTTPS si le HTTP ne répond plus
        if r.status_code != 401:
            r.raise_for_status()
            return r.content if raw else r.text
    raise RuntimeError("Authentification refusée (401)")


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
    root = ET.fromstring(device_get(f"http://{host}:{port}/ISAPI/ContentMgmt/Storage", user, pw))
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
                                    "/SMARTTest/status", user, pw))
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


def _dahua_rpc(host: str, port: int, user: str, pw: str) -> list[dict]:
    """Même API que l'interface web du NVR (lecture seule)."""
    with dahuarpc.shared(host, port, user, pw) as s:
        disks = []
        for dev in s.params("StorageDeviceManager.getDeviceInfos").get("device", []):
            parts = dev.get("Partitions") or []
            total = int(sum(x.get("Total", 0) for x in parts)) or int(dev.get("Capacity", 0))
            free = int(sum(x.get("Remain", 0) for x in parts))
            state = dev.get("State", "")
            d = _disk(dev.get("Name", "?"), state, state in ("Running", "Success") and
                      all(x.get("Status", "LvAvailable") == "LvAvailable" for x in parts),
                      total, max(0, total - free), model=dev.get("Module", ""), serial=dev.get("SerialNo", ""))
            obj = s.call("devStorage.factory.instance", {"name": d["name"]}).get("result")
            if obj:
                vals = (s.call("devStorage.getSmartValue", None, obj).get("params") or {}).get("values", [])
                s.call("devStorage.destroy", None, obj)
                for v in vals:
                    raw = int(re.match(r"\d*", str(v.get("Raw", "0")))[0] or 0)
                    d["smart"].append({"id": v.get("ID"), "name": v.get("Name", ""), "current": v.get("Current", 0),
                                       "worst": v.get("Worst", 0), "threshold": v.get("Threshold", 0),
                                       "raw": raw, "predict": v.get("Predict", 0)})
                    if v.get("ID") == 194:
                        d["temp"] = raw & 0xFF          # certains disques codent min/max dans les octets hauts
                    elif v.get("ID") == 9:
                        d["hours"] = raw
            disks.append(_health(d))
        return disks


def _dahua(host: str, port: int, user: str, pw: str) -> list[dict]:
    try:
        return _dahua_rpc(host, port, user, pw)
    except Exception as e:  # noqa: BLE001
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
            for i, d in sorted(info.items())]


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
    except Exception as e:  # noqa: BLE001
        entry["error"] = security.scrub(str(e))[:200]
        log.warning("État disques %s:%s : %s", host, port, entry["error"])
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


def check(cams: list[dict]) -> None:
    """Interroge chaque équipement (en parallèle) et oublie ceux qui ne sont plus configurés."""
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
