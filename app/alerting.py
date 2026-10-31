"""Alertes : ouverture/fermeture dédupliquées + notifications (webhook, e-mail), envoyées en arrière-plan."""
import logging
from concurrent.futures import ThreadPoolExecutor

import requests

from . import config, db, mail

log = logging.getLogger("alerting")
_pool = ThreadPoolExecutor(max_workers=1)


def _webhook(text: str) -> None:
    try:
        requests.post(config.WEBHOOK_URL, json={"text": text, "content": text, "message": text}, timeout=10)
    except Exception as e:  # noqa: BLE001
        log.warning("Webhook en échec : %s", type(e).__name__)   # pas le message : il contient l'URL (jeton secret)


def notify(text: str, email: bool = True) -> None:
    """Webhook (toutes les alertes) et e-mail (`email=True` : états critiques du NVR, cf. `config.EMAIL_KINDS`)."""
    if config.WEBHOOK_URL:
        _pool.submit(_webhook, text)
    if email:
        mail.send_later(text)


def raise_alert(key: str, camera_id: int | None, kind: str, message: str) -> None:
    if db.one("SELECT id FROM alerts WHERE key=? AND closed_ts IS NULL", (key,)):
        return
    db.x("INSERT INTO alerts(key,camera_id,kind,message,opened_ts) VALUES(?,?,?,?,?)",
               (key, camera_id, kind, message, db.now()))
    log.warning("ALERTE %s", message)
    notify(f"🔴 {message}", kind in config.EMAIL_KINDS)


def popup_state() -> dict:
    """Ce qu'affiche la fenêtre d'alerte du poste (`app.popup`) : alertes ouvertes, battement de cœur, e-mail."""
    kinds = config.POPUP_KINDS
    return {"alerts": db.q(f"SELECT key, kind, message, opened_ts FROM alerts WHERE closed_ts IS NULL AND kind IN "
                           f"({','.join('?' * len(kinds))}) ORDER BY opened_ts", kinds),
            "heartbeat": db.setting("heartbeat"), "instance": db.setting("instance"), "email": mail.configured()}


def clear_alert(key: str) -> None:
    row = db.one("SELECT id, kind, message FROM alerts WHERE key=? AND closed_ts IS NULL", (key,))
    if not row:
        return
    db.x("UPDATE alerts SET closed_ts=? WHERE key=? AND closed_ts IS NULL", (db.now(), key))
    notify(f"🟢 Rétabli : {row['message']}", row["kind"] in config.EMAIL_KINDS)
