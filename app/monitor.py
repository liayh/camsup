"""Boucle de surveillance : NVR (caméras vues, enregistrement), disques, état des caméras, purge (journaux, extraits)."""
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import alerting, config, db, export, nvrwatch, preview, storage

log = logging.getLogger("monitor")
_stop = threading.Event()
_thread: threading.Thread | None = None
_first_fail: dict[int, float] = {}
_pool = ThreadPoolExecutor(max_workers=8)


def _tcp_ok(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def _update_cameras(cams: list[dict]) -> None:
    """État affiché de chaque caméra. Derrière un NVR Dahua, c'est lui qui dit si la caméra est connectée
    (alerte `camera:<id>` dans `nvrwatch`) ; sinon on teste seulement que l'équipement répond (alerte `offline`)."""
    now = time.time()
    nvr = {c["id"]: nvrwatch.online(c["id"]) for c in cams}
    tcp = dict(zip([c["id"] for c in cams if nvr[c["id"]] is None],
                   _pool.map(lambda c: _tcp_ok(c["host"], c["port_http"]), [c for c in cams if nvr[c["id"]] is None])))
    for cam in cams:
        cid = cam["id"]
        online = nvr[cid] if nvr[cid] is not None else tcp[cid]
        err = ""
        if online or nvr[cid] is not None:
            _first_fail.pop(cid, None)
            alerting.clear_alert(f"offline:{cid}")
            if not online:
                err = "Caméra non vue par le NVR"
        else:
            err = "Équipement injoignable"
            if now - _first_fail.setdefault(cid, now) >= config.OFFLINE_ALERT_SECONDS:
                alerting.raise_alert(f"offline:{cid}", cid, "offline", f"{cam['name']} : équipement injoignable")
        db.x("""INSERT INTO status(camera_id,online,last_check,last_ok,error) VALUES(?,?,?,?,?)
                ON CONFLICT(camera_id) DO UPDATE SET online=excluded.online,last_check=excluded.last_check,
                  last_ok=CASE WHEN excluded.online=1 THEN excluded.last_check ELSE status.last_ok END,
                  error=excluded.error""",
             (cid, int(online), int(now), int(now) if online else None, err))


def purge() -> None:
    now = db.now()
    db.x("DELETE FROM alerts WHERE closed_ts IS NOT NULL AND closed_ts < ?", (now - 365 * 86400,))
    db.x("DELETE FROM audit WHERE ts < ?", (now - config.AUDIT_RETENTION_DAYS * 86400,))
    db.x("DELETE FROM sessions WHERE expires_ts < ?", (now,))
    export.purge()
    preview.purge()


def _loop() -> None:
    last_nvr = last_hdd = last_purge = 0.0
    while not _stop.is_set():
        try:
            cams = db.q("SELECT * FROM cameras WHERE enabled=1")
            now = time.time()
            db.set_setting("heartbeat", str(int(now)))   # surveillé par la fenêtre d'alerte (serveur figé/arrêté)
            if now - last_nvr >= config.CAMERA_CHECK_SECONDS:
                _pool.submit(nvrwatch.check, cams)     # en arrière-plan : un NVR injoignable met jusqu'à 30 s à répondre
                last_nvr = now
            if now - last_hdd > config.HDD_CHECK_SECONDS:
                _pool.submit(storage.check, cams)
                last_hdd = now
            _update_cameras(cams)
            if now - last_purge > 3600:
                purge()
                last_purge = now
        except Exception:  # noqa: BLE001
            log.exception("Erreur dans la boucle de surveillance")
        _stop.wait(config.CHECK_INTERVAL)


def start() -> None:
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="monitor", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
