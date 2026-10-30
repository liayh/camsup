"""Caméras déclarées sur un enregistreur (NVR) : regroupement par équipement et forme publique (sans secret)."""
import hashlib

from . import security


def device_key(cam: dict) -> tuple:
    """Identifie un équipement physique (NVR = plusieurs canaux) : marque, hôte, port HTTP, identifiants.

    Le mot de passe est comparé en clair (haché) : le chiffrement Fernet donne un jeton différent par caméra.
    """
    pw = hashlib.sha256(security.decrypt(cam.get("password_enc") or "").encode()).hexdigest()
    return (cam["brand"], cam["host"], cam["port_http"], cam["username"], pw)


def public(cam: dict) -> dict:
    """Caméra sans secret, pour l'API."""
    return {k: v for k, v in cam.items() if k != "password_enc"}
