"""E-mails à l'installateur (états critiques du NVR, cf. `config.EMAIL_KINDS`).

Réglages saisis dans Paramètres : table `settings`, clé `email` (JSON, mot de passe chiffré par `security`). Sans
réglage enregistré, repli sur les variables `SMTP_*` / `ALERT_EMAIL_TO` du fichier `.env`.
L'envoi des alertes se fait en arrière-plan (un serveur d'e-mail lent ne doit pas bloquer la surveillance) ; le
résultat du dernier envoi est gardé (clé `email_status`) et affiché dans Paramètres.
"""
import json
import logging
import re
import smtplib
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from email.utils import formataddr

from . import config, db, security

log = logging.getLogger("mail")
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
_pool = ThreadPoolExecutor(max_workers=1)


def recipients(text: str) -> list[str]:
    """« a@x.fr; b@y.fr » → liste ; erreur lisible si une adresse est invalide."""
    out = [a for a in re.split(r"[\s,;]+", text or "") if a]
    bad = [a for a in out if not EMAIL_RE.match(a)]
    if bad:
        raise ValueError(f"Adresse e-mail invalide : {', '.join(bad)}")
    return out


def load(raw: str | None) -> dict:
    """Réglages effectifs à partir de la valeur enregistrée (JSON) ou, à défaut, du fichier .env."""
    if raw:
        s = json.loads(raw)
        return {**s, "source": "interface"}
    return {"host": config.SMTP_HOST, "port": config.SMTP_PORT, "security": "starttls", "user": config.SMTP_USER,
            "password_enc": security.encrypt(config.SMTP_PASSWORD), "sender": config.SMTP_FROM,
            "to": [a for a in re.split(r"[\s,;]+", config.ALERT_EMAIL_TO) if a], "source": "fichier .env"}


def current() -> dict:
    return load(db.setting("email") or None)


def configured(s: dict | None = None) -> bool:
    s = s or current()
    return bool(s.get("host") and s.get("to"))


def public(s: dict) -> dict:
    """Pour l'interface : jamais le mot de passe."""
    status = db.setting("email_status")
    return {"host": s.get("host", ""), "port": s.get("port", 587), "security": s.get("security", "starttls"),
            "user": s.get("user", ""), "has_password": bool(s.get("password_enc")), "sender": s.get("sender", ""),
            "to": s.get("to", []), "source": s.get("source", ""), "configured": configured(s),
            "last": json.loads(status) if status else None}


def save(host: str, port: int, sec: str, user: str, password: str | None, sender: str, to: list[str]) -> None:
    """`password` None = inchangé, "" = effacé."""
    old = json.loads(db.setting("email") or "{}")
    enc = old.get("password_enc", "") if password is None else security.encrypt(password)
    db.set_setting("email", json.dumps({"host": host.strip(), "port": port, "security": sec, "user": user.strip(),
                                        "password_enc": enc, "sender": sender.strip(), "to": to}))


def send(subject: str, body: str, s: dict | None = None) -> None:
    """Envoi immédiat ; lève une exception (message lisible) en cas d'échec."""
    s = s or current()
    if not configured(s):
        raise RuntimeError("e-mail non configuré (serveur d'envoi et destinataire)")
    msg = EmailMessage()
    msg["Subject"] = subject[:150]
    msg["From"] = formataddr(("CamSup", s.get("sender") or s.get("user") or "camsup@localhost"))
    msg["To"] = ", ".join(s["to"])
    msg.set_content(body)
    port, sec = int(s.get("port") or 587), s.get("security", "starttls")
    ctx = ssl.create_default_context()
    try:
        smtp = (smtplib.SMTP_SSL(s["host"], port, timeout=20, context=ctx) if sec == "ssl"
                else smtplib.SMTP(s["host"], port, timeout=20))
        with smtp:
            if sec == "starttls":
                smtp.starttls(context=ctx)
            if s.get("user"):
                smtp.login(s["user"], security.decrypt(s.get("password_enc", "")))
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError("le serveur refuse l'identifiant ou le mot de passe (Gmail, Outlook : utiliser un "
                           "« mot de passe d'application »)")
    except (TimeoutError, OSError) as e:
        if isinstance(e, ssl.SSLError):
            raise RuntimeError(f"connexion chiffrée refusée ({e.reason or e}) : vérifier le port et la sécurité")
        raise RuntimeError(f"serveur d'envoi injoignable ({s['host']}:{port}) : {e}")
    except smtplib.SMTPException as e:
        raise RuntimeError(f"le serveur d'envoi a refusé le message : {e}")


def _record(ok: bool, error: str = "") -> None:
    db.set_setting("email_status", json.dumps({"ts": db.now(), "ok": ok, "error": error[:300]}))


def send_later(text: str) -> None:
    """Alerte par e-mail, en arrière-plan ; le résultat est gardé pour Paramètres."""
    def job():
        try:
            send(f"[CamSup] {text}", f"{text}\n\nRelevé du {time.strftime('%d/%m/%Y à %H:%M')} (heure du poste CamSup).\n")
            _record(True)
        except Exception as e:  # noqa: BLE001
            msg = security.scrub(str(e))
            log.warning("E-mail en échec : %s", msg)
            _record(False, msg)
    if configured():
        _pool.submit(job)
