"""Alertes : ouverture/fermeture dédupliquées + notifications (webhook, e-mail), envoyées en arrière-plan."""
import json
import logging
import threading
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
    aid = db.x("INSERT INTO alerts(key,camera_id,kind,message,opened_ts) VALUES(?,?,?,?,?)",
               (key, camera_id, kind, message, db.now()))
    log.warning("ALERTE %s", message)
    notify(f"🔴 {message}", False)                  # webhook
    if kind in config.EMAIL_KINDS:
        _email(aid, message)


_sending: set[int] = set()                         # alertes dont l'e-mail est en cours d'envoi
_send_lock = threading.Lock()


def _email(aid: int, message: str) -> None:
    """E-mail d'une alerte ; noté envoyé (`emailed_ts`) seulement s'il est parti, sinon renvoyé par `resend`."""
    with _send_lock:
        if aid in _sending:
            return
        _sending.add(aid)

    def done(ok: bool) -> None:
        if ok:
            db.x("UPDATE alerts SET emailed_ts=? WHERE id=?", (db.now(), aid))
        with _send_lock:
            _sending.discard(aid)
    if not mail.send_later(f"🔴 {message}", on_done=done):
        done(False)


def resend() -> None:
    """Alertes encore ouvertes dont l'e-mail n'est pas parti (e-mail configuré après coup, serveur d'envoi ou
    internet en panne au moment de l'alerte) : nouvel essai (appelé par la surveillance toutes les 5 min).
    Avant le 06/11/2026, un seul essai : un NVR éteint pendant une panne d'envoi ne prévenait jamais personne."""
    kinds = config.EMAIL_KINDS
    for a in db.q(f"SELECT id, message FROM alerts WHERE closed_ts IS NULL AND emailed_ts IS NULL "
                  f"AND kind IN ({','.join('?' * len(kinds))}) ORDER BY id", kinds):
        _email(a["id"], a["message"])


def popup_state() -> dict:
    """Ce qu'affiche la fenêtre d'alerte du poste (`app.popup`) : alertes ouvertes, battement de cœur, e-mail."""
    kinds = config.POPUP_KINDS
    return {"alerts": db.q(f"SELECT key, kind, message, opened_ts FROM alerts WHERE closed_ts IS NULL AND kind IN "
                           f"({','.join('?' * len(kinds))}) ORDER BY opened_ts", kinds),
            "heartbeat": db.setting("heartbeat"), "instance": db.setting("instance"), "email": mail.configured(),
            # résultat réel du dernier envoi : la fenêtre ne dit « installateur prévenu » que si l'e-mail est parti
            "email_status": json.loads(db.setting("email_status") or "null")}


def clear_alert(key: str) -> None:
    row = db.one("SELECT id, kind, message, emailed_ts FROM alerts WHERE key=? AND closed_ts IS NULL", (key,))
    if not row:
        return
    db.x("UPDATE alerts SET closed_ts=? WHERE key=? AND closed_ts IS NULL", (db.now(), key))
    # « rétabli » par e-mail seulement si l'alerte elle-même est partie (sinon message sans contexte)
    notify(f"🟢 Rétabli : {row['message']}", row["kind"] in config.EMAIL_KINDS and row["emailed_ts"] is not None)
