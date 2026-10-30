"""Chiffrement des mots de passe des enregistreurs et masquage des identifiants. Les comptes sont dans `accounts.py`."""
import os
import re
import secrets

from cryptography.fernet import Fernet

from . import config

_key_file = config.DATA_DIR / "secret.key"


def _load_key() -> bytes:
    env = os.getenv("SECRET_KEY")
    if env:
        return env.encode()
    if _key_file.exists():
        return _key_file.read_bytes().strip()
    key = Fernet.generate_key()
    _key_file.write_bytes(key)
    os.chmod(_key_file, 0o600)
    return key


_fernet = Fernet(_load_key())


def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode() if plain else ""


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode() if token else ""


def scrub(text: str) -> str:
    """Masque les identifiants de toute URL (`schéma://utilisateur:mot_de_passe@…`) affichée ou journalisée."""
    return re.sub(r"(\w+://)[^/@\s]*@", r"\1***@", text or "")


ADMIN_PASSWORD = config.ADMIN_PASSWORD or secrets.token_urlsafe(12)
GENERATED_PASSWORD = not config.ADMIN_PASSWORD
