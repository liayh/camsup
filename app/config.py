r"""Configuration CamSup — variables d'environnement, ou fichier de réglages (une ligne `CLÉ=valeur` par réglage).
Une variable déjà définie dans l'environnement l'emporte sur le fichier.

- développement : `.env` à la racine du projet, données dans `./data` ;
- programme Windows (PyInstaller, `FROZEN`) : `%PROGRAMDATA%\CamSup\camsup.env` et `%PROGRAMDATA%\CamSup\data` (communs
  à tous les comptes du poste), interface sur ce poste seulement (127.0.0.1, HTTP : `localhost` est un contexte sûr
  pour le navigateur, sans avertissement de certificat), ffmpeg livré à côté de `CamSup.exe`.
"""
import os
import sys
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():     # « -sig » : fichier écrit avec BOM
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
HOME = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "CamSup" if FROZEN else APP_DIR
ENV_FILE = Path(os.getenv("CAMSUP_ENV", HOME / ("camsup.env" if FROZEN else ".env")))
_load_env_file(ENV_FILE)


def forget_admin_password() -> None:
    """Après la création du compte intégrateur : retire ADMIN_PASSWORD (saisi à l'installation) du fichier de réglages."""
    if ENV_FILE.is_file():
        lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()
        kept = [x for x in lines if not x.strip().startswith("ADMIN_PASSWORD")]
        if kept != lines:
            ENV_FILE.write_text("\n".join(kept) + "\n", encoding="utf-8")


DATA_DIR = Path(os.getenv("DATA_DIR", HOME / "data" if FROZEN else "./data")).resolve()
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "camsup.db"

# Surveillance
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "5"))                   # tour de la boucle de surveillance
OFFLINE_ALERT_SECONDS = int(os.getenv("OFFLINE_ALERT_SECONDS", "60"))    # caméra hors NVR Dahua injoignable
HDD_CHECK_SECONDS = int(os.getenv("HDD_CHECK_SECONDS", "30"))            # état des disques des NVR (SMART)
CAMERA_CHECK_SECONDS = int(os.getenv("CAMERA_CHECK_SECONDS", "10"))     # relevé des NVR Dahua (état + enregistrements)
CAMERA_STALL_SECONDS = int(os.getenv("CAMERA_STALL_SECONDS", "15"))     # plus d'images depuis… → caméra perdue
NVR_FAILS = int(os.getenv("NVR_FAILS", "2"))                             # relevés consécutifs en échec avant d'alerter
NVR_BOOT_GRACE_SECONDS = int(os.getenv("NVR_BOOT_GRACE_SECONDS", "120"))  # NVR revenu : il reconnecte ses caméras
RECORD_STALE_MINUTES = int(os.getenv("RECORD_STALE_MINUTES", "15"))      # sans nouvel enregistrement → alerte

# Fenêtres d'alerte sur le poste (pharmacie) et e-mail (installateur)
POPUPS = os.getenv("POPUPS", "1") not in ("0", "false", "non")
POPUP_REMIND_HOURS = float(os.getenv("POPUP_REMIND_HOURS", "4"))         # fenêtre fermée → rouverte après ce délai
POPUP_KINDS = ("nvr", "camera", "rec", "hdd", "offline")  # types d'alerte affichés au pharmacien
EMAIL_KINDS = ("nvr", "rec", "hdd")                        # types d'alerte envoyés par e-mail (état critique du NVR)

# Extrait vidéo pour les forces de l'ordre (NVR Dahua)
EXPORT_MAX_MINUTES = int(os.getenv("EXPORT_MAX_MINUTES", "15"))          # durée maximale d'un extrait
EXPORT_RETENTION_DAYS = int(os.getenv("EXPORT_RETENTION_DAYS", "1"))     # copie de travail effacée du PC après ce délai
FFMPEG = os.getenv("FFMPEG", str(APP_DIR / "ffmpeg.exe") if (APP_DIR / "ffmpeg.exe").is_file() else "ffmpeg")                                    # conversion en MP4 (chemin de ffmpeg)

# Accès web
ADMIN_USER = os.getenv("ADMIN_USER", "admin")       # compte intégrateur créé au premier démarrage (base vide)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")  # vide => généré et affiché au démarrage
HOST = os.getenv("HOST", "127.0.0.1" if FROZEN else "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
HTTPS = os.getenv("HTTPS", "0" if FROZEN else "1") not in ("0", "false", "non")  # certificat auto-signé si TLS_CERT/TLS_KEY absents
TLS_CERT = os.getenv("TLS_CERT", "")
TLS_KEY = os.getenv("TLS_KEY", "")
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "365"))

# Notifications
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # Slack/Teams/Discord/ntfy... reçoit un JSON {text, content, message}
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "camsup@localhost")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")

for d in (DATA_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)
