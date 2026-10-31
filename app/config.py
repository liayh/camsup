r"""Configuration CamSup — variables d'environnement, ou fichier de réglages (une ligne `CLÉ=valeur` par réglage).
Une variable déjà définie dans l'environnement l'emporte sur le fichier.

- développement : `.env` à la racine du projet, données dans `./data` ;
- programme Windows (PyInstaller, `FROZEN`) : `%PROGRAMDATA%\CamSup\camsup.env` (lisible par tous) et
  `%PROGRAMDATA%\CamSup\data` (système et administrateurs seulement : le serveur tourne sous le compte système), interface sur ce poste seulement (127.0.0.1, HTTP : `localhost` est un contexte sûr
  pour le navigateur, sans avertissement de certificat), ffmpeg livré à côté de `CamSup.exe`.
"""
import os
import sys
from pathlib import Path


# Programme Windows : `camsup.env` est lisible (et, avant la 1.1, modifiable) par tous les comptes du poste ; le serveur
# tourne sous le compte système. Seuls ces réglages sans danger y sont lus : ni chemins (données, ffmpeg), ni adresse
# d'écoute, ni proxy, ni secrets (le mot de passe d'e-mail se saisit dans Paramètres, chiffré dans la base).
SAFE_KEYS = {"CHECK_INTERVAL", "OFFLINE_ALERT_SECONDS", "HDD_CHECK_SECONDS", "CAMERA_CHECK_SECONDS",
             "CAMERA_STALL_SECONDS", "NVR_FAILS", "NVR_BOOT_GRACE_SECONDS", "RECORD_STALE_MINUTES", "POPUPS",
             "POPUP_REMIND_HOURS", "EXPORT_MAX_MINUTES", "EXPORT_RETENTION_DAYS", "ADMIN_USER", "PORT",
             "SESSION_HOURS", "AUDIT_RETENTION_DAYS", "WEBHOOK_URL", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_FROM",
             "ALERT_EMAIL_TO", "ALLOWED_HOSTS"}


def _load_env_file(path: Path, allowed: set[str] | None = None) -> None:
    """`CLÉ=valeur` par ligne → variables d'environnement (sans écraser l'existant) ; `allowed` : seulement ces clés."""
    try:
        text = path.read_text(encoding="utf-8-sig")                     # « -sig » : fichier écrit avec BOM
    except OSError:                    # absent, ou réservé aux administrateurs (fenêtre d'alerte d'un utilisateur)
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if allowed is not None and key not in allowed:
            continue
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


VERSION = "1.1.0"
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
HOME = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "CamSup" if FROZEN else APP_DIR
ENV_FILE = Path(os.getenv("CAMSUP_ENV", HOME / ("camsup.env" if FROZEN else ".env")))
_load_env_file(ENV_FILE, SAFE_KEYS if FROZEN else None)


def is_link(p: Path) -> bool:
    """Lien symbolique ou jonction (point d'analyse NTFS)."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return p.is_symlink() or bool(getattr(st, "st_file_attributes", 0) & 0x400)   # FILE_ATTRIBUTE_REPARSE_POINT


if FROZEN:
    # un dossier de données remplacé par une jonction ferait lire, écrire ou effacer ailleurs par le compte système
    for _d in (HOME, HOME / "data", *(HOME / "data" / n for n in ("logs", "previews", "exports", "tls"))):
        if is_link(_d):
            raise SystemExit(f"CamSup : {_d} est un lien (jonction) : refusé, prévenir l'installateur")


def forget_admin_password() -> None:
    """Après la création du compte intégrateur : efface le mot de passe saisi à l'installation (`INSTALL_FILE`) et
    retire ADMIN_PASSWORD du fichier de réglages (installations d'avant la 1.1)."""
    INSTALL_FILE.unlink(missing_ok=True)
    if ENV_FILE.is_file():
        lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()
        kept = [x for x in lines if not x.strip().startswith("ADMIN_PASSWORD")]
        if kept != lines:
            ENV_FILE.write_text("\n".join(kept) + "\n", encoding="utf-8")


DATA_DIR = (HOME / "data") if FROZEN else Path(os.getenv("DATA_DIR", "./data")).resolve()
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "camsup.db"
# clé publique du serveur, lue par la fenêtre d'alerte (`fenetre`) : lisible par tous, modifiable par le serveur seul
POPUP_PUBKEY = (HOME / "fenetre.pub") if FROZEN else (DATA_DIR / "fenetre.pub")
# programme Windows : mot de passe admin saisi à l'installation, dans data/ (réservé au système et aux administrateurs)
INSTALL_FILE = DATA_DIR / "installation.env"
if FROZEN:
    _load_env_file(INSTALL_FILE, {"ADMIN_PASSWORD"})

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
# conversion en MP4 (chemin de ffmpeg). Programme Windows : toujours celui installé à côté de CamSup.exe (Program Files,
# modifiable par l'administrateur seulement) ; le fichier de réglages, modifiable par tout utilisateur du poste, ne peut
# pas désigner un autre programme (il serait lancé dans la session des autres utilisateurs).
_BUNDLED_FFMPEG = APP_DIR / "ffmpeg.exe"
FFMPEG = str(_BUNDLED_FFMPEG) if FROZEN else os.getenv("FFMPEG", str(_BUNDLED_FFMPEG) if _BUNDLED_FFMPEG.is_file() else "ffmpeg")

# Accès web
ADMIN_USER = os.getenv("ADMIN_USER", "admin")       # compte intégrateur créé au premier démarrage (base vide)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")  # vide => généré et affiché au démarrage
HOST = "127.0.0.1" if FROZEN else os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
HTTPS = os.getenv("HTTPS", "0" if FROZEN else "1") not in ("0", "false", "non")  # certificat auto-signé si TLS_CERT/TLS_KEY absents
TLS_CERT = os.getenv("TLS_CERT", "")
TLS_KEY = os.getenv("TLS_KEY", "")
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
# noms par lesquels on peut joindre l'interface (en-tête Host) ; « * » = tous. Programme Windows : ce PC seulement.
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost,::1" if FROZEN else "*").split(",")
                 if h.strip()]
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
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:                    # fenêtre d'alerte d'un utilisateur : dossier réservé, elle n'en a pas besoin
        pass
