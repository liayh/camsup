"""Licence par pharmacie (demandée le 05/11/2026) : un code saisi à l'installation rend CamSup actif jusqu'à une date
(1 an en général) ; sans nouveau code après cette date, CamSup s'arrête : surveillance (informations plus mises à
jour), direct et vidéo (lecture, extraits) bloqués. Les comptes, Paramètres et la saisie d'un nouveau code restent
possibles.

Code = numéro de licence + date de fin, signés Ed25519 par le vendeur (clé privée chez lui seulement,
`outils/creer_licence.py`) : un client ne peut ni fabriquer un code ni repousser la date. Le même code marche sur
plusieurs PC (pas d'identifiant de poste dans le code).
Heure reculée pour prolonger : l'heure la plus avancée déjà vue est gardée (`settings.licence_vu`) et fait foi.
"""
import base64
import struct
import time
from datetime import date, datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import config, db

PUBLIC_KEY = "1EEp7wh+Lomlcrl+LusX/S/t35edbXiSIf2OA7DwPVg="      # clé publique du vendeur
PREFIX = "CS1"
EPOCH = date(2020, 1, 1)
WARN_DAYS = 30                       # bandeau « expire dans N jours » avant la date de fin
_FMT = ">BIH"                        # version, numéro, jours depuis EPOCH (date de fin incluse)


def _b32(data: bytes) -> str:
    return base64.b32encode(data).decode().rstrip("=")


def encode(numero: int, fin: date, sign) -> str:
    """Code lisible (`CS1-XXXXXX-…`) ; `sign(bytes) -> bytes` : signature Ed25519 du vendeur."""
    payload = struct.pack(_FMT, 1, numero, (fin - EPOCH).days)
    raw = _b32(payload + sign(payload))
    return "-".join([PREFIX, *(raw[i:i + 6] for i in range(0, len(raw), 6))])


def decode(code: str) -> dict:
    """{numero, fin} d'un code authentique ; `ValueError` lisible sinon."""
    text = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if not text.startswith(PREFIX):
        raise ValueError("Code de licence invalide (il commence par CS1-)")
    text = text[len(PREFIX):]
    try:
        data = base64.b32decode(text + "=" * (-len(text) % 8))
    except ValueError:
        raise ValueError("Code de licence invalide (caractère inattendu)") from None
    size = struct.calcsize(_FMT)
    if len(data) != size + 64:
        raise ValueError("Code de licence incomplet : vérifiez qu'il a été copié en entier")
    payload, sig = data[:size], data[size:]
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY)).verify(sig, payload)
    except InvalidSignature:
        raise ValueError("Code de licence refusé (faux ou mal recopié)") from None
    version, numero, days = struct.unpack(_FMT, payload)
    if version != 1:
        raise ValueError("Code de licence d'une autre version de CamSup")
    return {"numero": numero, "fin": EPOCH + timedelta(days=days)}


def required() -> bool:
    """Licence exigée : programme Windows (les essais en développement n'en ont pas besoin)."""
    return config.LICENCE_REQUIRED


def now() -> float:
    """Heure du PC, jamais avant la plus avancée déjà vue (heure reculée pour prolonger la licence : sans effet)."""
    t = time.time()
    seen = float(db.setting("licence_vu") or 0)
    if t > seen + 60:
        db.set_setting("licence_vu", str(int(t)))
    return max(t, seen)


def status() -> dict:
    """État affiché : `etat` = active / bientot (≤ WARN_DAYS jours) / expiree / absente / sans (pas exigée)."""
    if not required():
        return {"etat": "sans", "actif": True}
    code = db.setting("licence") or ""
    if not code:
        return {"etat": "absente", "actif": False}
    try:
        lic = decode(code)
    except ValueError:
        return {"etat": "absente", "actif": False}
    end = datetime.combine(lic["fin"] + timedelta(days=1), datetime.min.time()).timestamp()   # fin du dernier jour
    left = end - now()
    days = int(left // 86400) if left > 0 else 0
    etat = "expiree" if left <= 0 else "bientot" if days < WARN_DAYS else "active"
    return {"etat": etat, "actif": left > 0, "fin": lic["fin"].isoformat(), "jours": days, "numero": lic["numero"]}


def active() -> bool:
    return status()["actif"]


def install(code: str) -> dict:
    """Enregistre un code authentique. Un code qui finit avant celui en place est refusé (pas de retour en arrière)."""
    lic = decode(code)
    current = db.setting("licence") or ""
    if current:
        try:
            if decode(current)["fin"] > lic["fin"]:
                raise ValueError(f"Ce code finit le {lic['fin']:%d/%m/%Y}, avant la licence en place : rien n'est changé")
        except ValueError as e:
            if "rien n'est changé" in str(e):
                raise
    db.set_setting("licence", code.strip().upper())
    return status()


def message() -> str:
    """Texte de l'alerte et des refus (direct, vidéo)."""
    what = "expirée" if db.setting("licence") else "non activée"
    return (f"Licence CamSup {what} : surveillance et vidéo arrêtées. Entrez le code dans Paramètres › Licence "
            "(demandez-le à votre installateur)")
