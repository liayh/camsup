"""Réponses du serveur à la fenêtre d'alerte (`GET /api/fenetre`), signées.

La fenêtre tourne dans la session de chaque utilisateur et ne peut pas lire les données : elle ne saurait pas
distinguer le vrai serveur d'un programme qui aurait pris le port 8080 avant lui (pendant une mise à jour, une relance)
et répondrait « aucune alerte ». Le serveur signe donc chaque réponse (Ed25519) avec une clé privée rangée dans les
données (système et administrateurs) ; la clé publique est lisible par tous (`config.POPUP_PUBKEY`). La fenêtre envoie
un défi aléatoire à chaque fois (pas de rejeu) ; réponse non signée = serveur absent → « surveillance arrêtée ».
"""
import base64
import json
import re
import secrets

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import config

KEY_FILE = config.DATA_DIR / "fenetre.key"
CHALLENGE_RE = re.compile(r"^[0-9a-f]{32,128}$")
_key: Ed25519PrivateKey | None = None


def _message(data: dict, challenge: str) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"|" + challenge.encode()


def ensure_key() -> Ed25519PrivateKey:
    """Clé du serveur (créée au premier démarrage) ; la clé publique est réécrite à chaque démarrage."""
    global _key
    if _key is None:
        if KEY_FILE.is_file():
            _key = serialization.load_pem_private_key(KEY_FILE.read_bytes(), None)
        else:
            _key = Ed25519PrivateKey.generate()
            KEY_FILE.write_bytes(_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
        pub = _key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        text = base64.b64encode(pub).decode() + "\n"
        if not config.POPUP_PUBKEY.is_file() or config.POPUP_PUBKEY.read_text() != text:
            config.POPUP_PUBKEY.write_text(text)
    return _key


def sign(data: dict, challenge: str) -> dict:
    return {"data": data, "sig": base64.b64encode(ensure_key().sign(_message(data, challenge))).decode()}


def new_challenge() -> str:
    return secrets.token_hex(16)


def verify(reply: dict, challenge: str) -> dict:
    """Données d'une réponse signée par le vrai serveur ; `ValueError` sinon (clé publique absente comprise)."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(config.POPUP_PUBKEY.read_text().strip()))
        data = reply["data"]
        pub.verify(base64.b64decode(reply["sig"]), _message(data, challenge))
    except (OSError, KeyError, TypeError, ValueError, InvalidSignature) as e:
        raise ValueError(f"réponse non signée par le serveur CamSup ({type(e).__name__})") from None
    if not isinstance(data, dict):
        raise ValueError("réponse inattendue")
    return data
