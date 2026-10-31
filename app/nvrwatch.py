"""Surveillance des NVR Dahua (API RPC2, lecture seule).

Un relevé toutes les `CAMERA_CHECK_SECONDS` s (≈ 0,8 s pour 7 canaux) : heure du NVR, état des caméras et fin du
dernier enregistrement de chaque canal. Exigence : une caméra débranchée s'affiche en moins de 30 s. Pour chaque NVR :
- injoignable `NVR_FAILS` fois de suite                      → alerte `nvr:<hôte>` (fenêtre + e-mail) ;
- caméra perdue                                              → alerte `camera:<id>` (fenêtre) :
  plus rien d'enregistré depuis `CAMERA_STALL_SECONDS` s alors qu'une autre caméra du NVR est à jour (signal rapide :
  le fichier en cours avance avec l'horloge du NVR, retard mesuré 0 s), ou état ≠ « Connected » `NVR_FAILS` fois
  (signal lent : le NVR met ≈ 50 s à déclarer une caméra débranchée, mesuré le 30/10/2026) ;
- aucun enregistrement depuis `RECORD_STALE_MINUTES` min     → alerte `rec:<id>` (fenêtre + e-mail).
Toutes les caméras en retard ensemble (horloge du NVR qui saute, enregistrement arrêté) : pas d'alerte caméra.

L'âge du dernier enregistrement est calculé avec l'horloge du NVR lui-même (`global.getCurrentTime`) :
une horloge de NVR décalée ne crée ni fausse alerte ni alerte manquée.
Suppose un enregistrement continu ; un NVR réglé pour n'enregistrer que sur détection aurait des trous normaux.
Hikvision : pas encore pris en charge ici (seul l'état des disques l'est, dans `storage`).
"""
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

from . import alerting, cameras, config, dahuarpc, db, security

log = logging.getLogger("nvrwatch")
FMT = "%Y-%m-%d %H:%M:%S"
_lock = threading.Lock()
_running = threading.Lock()                 # un seul relevé à la fois
_fails: dict[str, int] = {}                 # hôte → échecs consécutifs
_down: dict[int, int] = {}                  # caméra → relevés consécutifs « non connectée »
_state: dict[str, dict] = {}                # hôte → dernier relevé (affichage)
_online: dict[int, bool] = {}               # caméra → vue connectée par le NVR
_last: dict[int, str | None] = {}           # caméra → fin du dernier enregistrement (heure du NVR)
_grace: dict[str, float] = {}               # hôte → fin du délai de grâce après le retour du NVR


FIND_PAGES = 100                            # pages de 100 fichiers par canal et par relevé, au plus
FIND_SECONDS = 30                           # durée maximale de la recherche pour un canal
_poll_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="nvr")


def _last_record(s: dahuarpc.Session, channel: int, nvr_now: datetime) -> str | None:
    """Fin du dernier enregistrement du canal (1-based) sur 24 h, à l'heure du NVR, ou None."""
    obj = s.call("mediaFileFind.factory.create").get("result")
    if not obj:
        raise RuntimeError("recherche d'enregistrements indisponible")
    try:
        cond = {"condition": {"Channel": channel - 1, "StartTime": (nvr_now - timedelta(hours=24)).strftime(FMT),
                              "EndTime": nvr_now.strftime(FMT), "Types": ["dav"]}}
        last = None
        if s.call("mediaFileFind.findFile", cond, obj).get("result"):
            # le NVR ignore l'ordre demandé : on lit tout et on garde le max ; borné (NVR bogué ou piégé qui renvoie
            # toujours une page pleine)
            deadline = time.time() + FIND_SECONDS
            for _ in range(FIND_PAGES):
                if time.time() > deadline:
                    raise RuntimeError("recherche d'enregistrements trop longue")
                infos = (s.call("mediaFileFind.findNextFile", {"count": 100}, obj).get("params") or {}).get("infos") or []
                for i in infos:
                    if i.get("EndTime") and (last is None or i["EndTime"] > last):
                        last = i["EndTime"]
                if len(infos) < 100:
                    break
        return last
    finally:
        s.call("mediaFileFind.close", None, obj)
        s.call("mediaFileFind.destroy", None, obj)


def probe(host: str, port: int, user: str, pw: str, channels: list[int] | None = None, timeout: int = 20) -> dict:
    """Relevé brut d'un NVR : heure et état de connexion des caméras ; avec `channels` (même vide), aussi la fin du
    dernier enregistrement de ces canaux (recherche plus lourde)."""
    with dahuarpc.shared(host, port, user, pw, timeout) as s:
        nvr_now = datetime.strptime(s.params("global.getCurrentTime")["time"], FMT)
        states = s.params("LogicDeviceManager.getCameraState", {"uniqueChannels": [-1]}).get("states") or []
        out = {"time": nvr_now.strftime(FMT), "conn": {int(x["channel"]) + 1: x.get("connectionState", "") for x in states}}
        if channels is not None:
            out["last"] = {ch: _last_record(s, ch, nvr_now) for ch in channels}
        return out


_GENERIC = re.compile(r"^(ipc|camera|caméra|channel|canal|ch|cam)\s*\d*$", re.I)


def discover(host: str, port: int, user: str, pw: str) -> dict:
    """Nouveau NVR : modèle, n° de série et canaux occupés (nom affiché par le NVR, état de connexion).
    Les canaux « Empty » (rien de branché) sont écartés ; un nom générique (« IPC », « Channel8 ») devient « Caméra N »."""
    with dahuarpc.Session(host, port, user, pw, timeout=10) as s:
        model = (s.call("magicBox.getDeviceType").get("params") or {}).get("type", "")
        serial = (s.call("magicBox.getSerialNo").get("params") or {}).get("sn", "")
        titles = (s.call("configManager.getConfig", {"name": "ChannelTitle"}).get("params") or {}).get("table") or []
        states = s.params("LogicDeviceManager.getCameraState", {"uniqueChannels": [-1]}).get("states") or []
    channels = []
    for x in sorted(states, key=lambda x: int(x["channel"])):
        ch, state = int(x["channel"]) + 1, x.get("connectionState", "")
        if state == "Empty":
            continue
        title = (titles[ch - 1].get("Name") or "").strip() if ch <= len(titles) else ""
        channels.append({"channel": ch, "state": state, "title": title,
                         "name": f"Caméra {ch}" if not title or _GENERIC.match(title) else title})
    return {"model": model, "serial": serial, "channels": channels}


def _site_name(site_id: int) -> str:
    row = db.one("SELECT name FROM sites WHERE id=?", (site_id,))
    return row["name"] if row else f"site {site_id}"


def _lag(nvr_now: datetime, last: str | None) -> float:
    """Secondes depuis la fin du dernier enregistrement (horloge du NVR) ; infini si aucun sur 24 h."""
    return float("inf") if last is None else (nvr_now - datetime.strptime(last, FMT)).total_seconds()


def evaluate(host: str, cams: list[dict], result: dict | None, error: str = "") -> dict:
    """Ouvre / ferme les alertes d'après un relevé (`result`) ou un échec (`error`). Renvoie l'état affiché.
    Un relevé sans `last` (tests) ne juge que l'état de connexion ; le dernier enregistrement connu est gardé."""
    site = _site_name(cams[0]["site_id"])
    entry = {"host": host, "site_id": cams[0]["site_id"], "site": site, "checked": db.now(), "error": error,
             "nvr_time": "", "cameras": []}
    if result is None:
        _fails[host] = _fails.get(host, 0) + 1
        if _fails[host] >= config.NVR_FAILS:
            alerting.raise_alert(f"nvr:{host}", cams[0]["id"], "nvr", f"{site} : l'enregistreur {host} {error}")
            for c in cams:                   # état inconnu : affichées hors ligne, sans alerte par caméra
                _online[c["id"]] = False
        return entry
    if _fails.pop(host, 0) >= config.NVR_FAILS:
        # NVR revenu (souvent après une coupure de courant) : il met ≈ 1 min à reconnecter ses caméras et à
        # reprendre l'enregistrement ; sans ce délai, chaque caméra ouvrirait une fausse alerte
        _grace[host] = time.time() + config.NVR_BOOT_GRACE_SECONDS
    alerting.clear_alert(f"nvr:{host}")
    quiet = time.time() < _grace.get(host, 0)          # pendant le délai : on ferme, on n'ouvre pas
    nvr_now = datetime.strptime(result["time"], FMT)
    entry["nvr_time"] = result["time"]
    last_by_ch = result.get("last", {})
    lags = {int(c.get("channel") or 1): _lag(nvr_now, last_by_ch[int(c.get("channel") or 1)])
            for c in cams if int(c.get("channel") or 1) in last_by_ch}
    for c in cams:
        cid, ch = c["id"], int(c.get("channel") or 1)
        state = result["conn"].get(ch, "absent")
        lag = lags.get(ch)
        # images arrêtées : seulement si une autre caméra du NVR enregistre normalement (sinon c'est le NVR)
        stalled = lag is not None and lag >= config.CAMERA_STALL_SECONDS and any(
            v < config.CAMERA_STALL_SECONDS for k, v in lags.items() if k != ch)
        _down[cid] = 0 if state == "Connected" else _down.get(cid, 0) + 1
        lost = stalled or _down[cid] >= config.NVR_FAILS
        _online[cid] = state == "Connected" and not stalled
        if not lost and state == "Connected":
            alerting.clear_alert(f"camera:{cid}")
        elif lost and not quiet:
            why = (f"n'envoie plus d'images au NVR depuis {int(lag)} s" if stalled and lag != float("inf")
                   else f"déconnectée du NVR (état « {state} »)")
            alerting.raise_alert(f"camera:{cid}", cid, "camera", f"{site} : {c['name']} {why}")
        if ch in last_by_ch:
            last = _last[cid] = last_by_ch[ch]
            if lag > config.RECORD_STALE_MINUTES * 60:
                if not quiet:
                    since = f"depuis {last[11:16]} (heure du NVR)" if last else "depuis plus de 24 h"
                    alerting.raise_alert(f"rec:{cid}", cid, "rec", f"{site} : {c['name']} n'est plus enregistrée {since}")
            else:
                alerting.clear_alert(f"rec:{cid}")
        entry["cameras"].append({"id": cid, "name": c["name"], "channel": ch,
                                 "state": "NoVideo" if stalled and state == "Connected" else state,
                                 "last_record": _last.get(cid)})
    return entry


def _poll(key: tuple, cams: list[dict]) -> None:
    _, host, port, user, _ = key
    try:
        pw = security.decrypt(cams[0]["password_enc"])
        result = probe(host, port, user, pw, sorted({int(c.get("channel") or 1) for c in cams}), timeout=10)
        entry = evaluate(host, cams, result)
    except Exception as e:  # noqa: BLE001
        log.warning("NVR %s : %s", host, security.scrub(str(e))[:300])
        entry = evaluate(host, cams, None, reason(e))
    with _lock:
        _state[host] = entry


def reason(e: Exception) -> str:
    """Cause lisible par le pharmacien (le détail technique va dans le journal)."""
    if isinstance(e, requests.Timeout):
        return "ne répond plus (délai dépassé)"
    if isinstance(e, requests.ConnectionError):
        return "ne répond plus sur le réseau (éteint, câble ou réseau coupé ?)"
    if "Authentification refusée" in str(e):
        return "refuse l'identifiant ou le mot de passe enregistré dans CamSup"
    return "réponse inattendue"


def _groups(cams: list[dict]) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = {}
    for c in cams:
        if c["brand"] == "dahua" and c["host"]:
            groups.setdefault(cameras.device_key(c), []).append(c)
    hosts = {k[1] for k in groups}
    with _lock:
        for h in list(_state):
            if h not in hosts:
                _state.pop(h)
    return groups


def check(cams: list[dict]) -> None:
    """Relève chaque NVR Dahua déclaré. Ne fait rien si le relevé précédent n'est pas fini."""
    if not _running.acquire(blocking=False):
        return
    try:
        # en parallèle : un NVR lent ne retarde pas les autres
        list(_poll_pool.map(lambda kv: _poll(*kv), _groups(cams).items()))
    finally:
        _running.release()


def online(cid: int) -> bool | None:
    """Caméra vue connectée par son NVR (None : pas surveillée par un NVR ou pas encore relevée)."""
    with _lock:
        return _online.get(cid)


def snapshot() -> list[dict]:
    with _lock:
        return [dict(v) for v in _state.values()]
