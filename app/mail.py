"""E-mails à l'installateur (états critiques du NVR, cf. `config.EMAIL_KINDS`).

Réglages saisis dans Paramètres : table `settings`, clé `email` (JSON, mot de passe chiffré par `security`). Sans
réglage enregistré, repli sur les variables `SMTP_*` / `ALERT_EMAIL_TO` du fichier `.env`.
L'envoi des alertes se fait en arrière-plan (un serveur d'e-mail lent ne doit pas bloquer la surveillance) ; le
résultat du dernier envoi est gardé (clé `email_status`) et affiché dans Paramètres.
Personne n'a à connaître le serveur d'envoi : il est déduit de l'adresse du compte (`detect`), et l'installateur Windows
peut apporter les réglages de l'intégrateur (`PRESET`, importé au premier démarrage puis effacé).
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
PRESET = config.HOME / "camsup-email.env"   # déposé par l'installateur (réglages e-mail de l'intégrateur)

# Serveurs d'envoi publiés par les fournisseurs courants (réglages documentés ; envoi réel non essayé ici)
_OUTLOOK = ("smtp-mail.outlook.com", 587, "starttls")
KNOWN = {
    "gmail.com": ("smtp.gmail.com", 587, "starttls"), "googlemail.com": ("smtp.gmail.com", 587, "starttls"),
    "outlook.com": _OUTLOOK, "outlook.fr": _OUTLOOK, "hotmail.com": _OUTLOOK, "hotmail.fr": _OUTLOOK,
    "live.com": _OUTLOOK, "live.fr": _OUTLOOK, "msn.com": _OUTLOOK,
    "orange.fr": ("smtp.orange.fr", 465, "ssl"), "wanadoo.fr": ("smtp.orange.fr", 465, "ssl"),
    "free.fr": ("smtp.free.fr", 465, "ssl"), "sfr.fr": ("smtp.sfr.fr", 465, "ssl"), "neuf.fr": ("smtp.sfr.fr", 465, "ssl"),
    "laposte.net": ("smtp.laposte.net", 465, "ssl"),
    "yahoo.com": ("smtp.mail.yahoo.com", 465, "ssl"), "yahoo.fr": ("smtp.mail.yahoo.com", 465, "ssl"),
    "icloud.com": ("smtp.mail.me.com", 587, "starttls"), "me.com": ("smtp.mail.me.com", 587, "starttls"),
}
# comptes qui exigent un « mot de passe d'application » (validation en 2 étapes)
APP_PASSWORD = {"smtp.gmail.com": "Google", "smtp.mail.yahoo.com": "Yahoo", "smtp.mail.me.com": "Apple"}


def _answers(host: str, port: int, sec: str) -> bool:
    """Un serveur d'envoi répond-il à cette adresse, avec ce chiffrement ?"""
    try:
        ctx = ssl.create_default_context()
        if sec == "ssl":
            with smtplib.SMTP_SSL(host, port, timeout=6, context=ctx) as smtp:
                return smtp.noop()[0] == 250
        with smtplib.SMTP(host, port, timeout=6) as smtp:
            smtp.ehlo()
            return smtp.has_extn("starttls")
    except (OSError, smtplib.SMTPException):
        return False


def detect(address: str) -> dict:
    """Serveur d'envoi d'une adresse : fournisseur connu, sinon essai de smtp.<domaine> et mail.<domaine>."""
    domain = address.rpartition("@")[2].strip().lower()
    if not domain or "." not in domain:
        raise ValueError("Adresse e-mail d'envoi incomplète")
    if domain in KNOWN:
        host, port, sec = KNOWN[domain]
        return {"host": host, "port": port, "security": sec}
    for host in (f"smtp.{domain}", f"mail.{domain}"):
        for port, sec in ((465, "ssl"), (587, "starttls")):
            if _answers(host, port, sec):
                return {"host": host, "port": port, "security": sec}
    raise ValueError(f"Serveur d'envoi de « {domain} » introuvable : le saisir dans « Réglages avancés » "
                     "(il figure dans l'aide du fournisseur de l'adresse)")


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


def import_preset() -> bool:
    """Réglages apportés par l'installateur : enregistrés (mot de passe chiffré) s'il n'y en a pas déjà, puis le
    fichier est effacé (mot de passe en clair). Clés : SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO, et au besoin
    SMTP_HOST / SMTP_PORT / SMTP_SECURITY / SMTP_FROM."""
    if not PRESET.is_file():
        return False
    try:
        v = {}
        for line in PRESET.read_text(encoding="utf-8-sig").splitlines():
            k, sep, val = line.partition("=")
            if sep and not k.strip().startswith("#"):
                v[k.strip()] = val.strip()
        if db.setting("email"):
            return False                     # déjà réglé (réinstallation) : on garde l'existant
        to = recipients(v.get("ALERT_EMAIL_TO", ""))
        user = v.get("SMTP_USER", "")
        srv = ({"host": v["SMTP_HOST"], "port": int(v.get("SMTP_PORT") or 587),
                "security": v.get("SMTP_SECURITY") or ("ssl" if v.get("SMTP_PORT") == "465" else "starttls")}
               if v.get("SMTP_HOST") else detect(user))
        save(srv["host"], srv["port"], srv["security"], user, v.get("SMTP_PASSWORD", ""), v.get("SMTP_FROM", ""), to)
        log.info("Réglages e-mail de l'installation enregistrés (%s → %s)", srv["host"], ", ".join(to))
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Réglages e-mail de l'installation inutilisables : %s", security.scrub(str(e)))
        return False
    finally:
        PRESET.unlink(missing_ok=True)


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
            "app_password": APP_PASSWORD.get(s.get("host", ""), ""),
            "last": json.loads(status) if status else None}


def save(host: str, port: int, sec: str, user: str, password: str | None, sender: str, to: list[str]) -> None:
    """`password` None = inchangé, "" = effacé."""
    old = json.loads(db.setting("email") or "{}")
    enc = old.get("password_enc", "") if password is None else security.encrypt(password)
    db.set_setting("email", json.dumps({"host": host.strip(), "port": port, "security": sec, "user": user.strip(),
                                        "password_enc": enc, "sender": sender.strip(), "to": to}))


class _Refused(Exception):
    """Identifiants refusés à la connexion au serveur d'envoi."""


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
                try:
                    smtp.login(s["user"], security.decrypt(s.get("password_enc", "")))
                except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected):
                    # Gmail coupe la connexion (au lieu de répondre 535) quand le mot de passe n'est pas accepté
                    raise _Refused()
            smtp.send_message(msg)
    except _Refused:
        who = APP_PASSWORD.get(s["host"])
        raise RuntimeError("le serveur refuse l'adresse ou le mot de passe" + (
            f" ({who} : il faut un « mot de passe d'application » de 16 lettres, pas le mot de passe habituel)" if who
            else " (certains fournisseurs exigent un « mot de passe d'application »)"))
    except smtplib.SMTPServerDisconnected as e:
        raise RuntimeError(f"le serveur d'envoi a coupé la connexion ({e}) : vérifier le port et la sécurité")
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
