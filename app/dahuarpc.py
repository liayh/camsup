"""Session RPC2 Dahua : la même API que l'interface web du NVR (connexion par défi MD5).

Utilisée en lecture seule (disques, état des caméras, liste des enregistrements). Le port HTTP des NVR plante
parfois ou redirige vers HTTPS : on suit la redirection, sinon on bascule sur le HTTPS qui répond (`devicehttp`).

`shared()` garde une session ouverte par NVR : se reconnecter à chaque relevé (toutes les 10 s) écrivait ≈ 380
connexions / déconnexions par heure dans le journal du NVR (vu le 30/10/2026) et noyait les vraies entrées.
"""
import contextlib
import hashlib
import threading
import time
from itertools import count
from urllib.parse import urlsplit

import requests

from . import devicehttp


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest().upper()


class Session:
    """`with Session(hôte, port, utilisateur, mot_de_passe) as s: s.call("méthode", params)`."""

    def __init__(self, host: str, port: int, user: str, pw: str, timeout: int = 10):
        self.host, self.port, self.user, self.pw, self.timeout = host, port, user, pw, timeout
        self.http = requests.Session()
        self.http.verify = False                      # certificat auto-signé du NVR (réseau local / VPN)
        self.base = ""
        self.sid = None
        self._seq = count(1)

    def _base(self) -> str:
        base = f"http://{self.host}:{self.port}"
        try:
            r = self.http.get(base + "/", timeout=self.timeout, allow_redirects=False)
        except (requests.ConnectionError, requests.Timeout):          # port HTTP muet : HTTPS directement
            base = devicehttp.https_base(self.host)
            if not base:
                raise
            return base
        if r.is_redirect and r.headers.get("Location", "").startswith("https://"):
            return "https://" + urlsplit(r.headers["Location"]).netloc
        return base

    def call(self, method: str, params=None, obj=None, path: str = "/RPC2") -> dict:
        body = {"method": method, "params": params, "id": next(self._seq), "session": self.sid}
        if obj is not None:
            body["object"] = obj
        return self.http.post(self.base + path, json=body, timeout=self.timeout).json()

    def params(self, method: str, params=None, obj=None) -> dict:
        """`params` de la réponse ; erreur si le NVR répond `result: false`."""
        r = self.call(method, params, obj)
        if r.get("result") is False:
            raise RuntimeError(f"RPC2 {method} refusé")
        return r.get("params") or {}

    def __enter__(self) -> "Session":
        self.base = self._base()
        login = {"userName": self.user, "password": "", "clientType": "Web3.0", "loginType": "Direct"}
        ch = self.call("global.login", login, path="/RPC2_Login")
        p = ch.get("params") or {}
        if "random" not in p:
            raise RuntimeError("RPC2 indisponible")
        self.sid = ch.get("session")
        realm_hash = _md5(f"{self.user}:{p['realm']}:{self.pw}")
        login.update(password=_md5(f"{self.user}:{p['random']}:{realm_hash}"),
                     authorityType=p.get("encryption", "Default"))
        r = self.call("global.login", login, path="/RPC2_Login")
        if not r.get("result"):
            raise RuntimeError("Authentification refusée (RPC2)")
        self.sid = r["session"]
        return self

    def __exit__(self, *_) -> None:
        try:
            self.call("global.logout")
        except Exception:  # noqa: BLE001
            pass
        self.http.close()


KEEPALIVE_AFTER = 20                    # s sans appel : on vérifie que la session est toujours valide
_shared: dict[tuple, tuple[threading.Lock, list]] = {}
_shared_lock = threading.Lock()


@contextlib.contextmanager
def shared(host: str, port: int, user: str, pw: str, timeout: int = 10):
    """Session gardée ouverte pour ce NVR (une à la fois par NVR) ; refaite si elle a expiré ou après une erreur."""
    key = (host, port, user, hashlib.sha256(pw.encode()).hexdigest())
    with _shared_lock:
        lock, slot = _shared.setdefault(key, (threading.Lock(), [None, 0.0]))
    with lock:
        s, last = slot
        if s is not None and time.time() - last > KEEPALIVE_AFTER:
            try:
                ok = s.call("global.keepAlive", {"timeout": 300, "active": True}).get("result")
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                s.__exit__()
                s = slot[0] = None
        if s is None:
            s = Session(host, port, user, pw, timeout).__enter__()
            slot[0] = s
        s.timeout = timeout
        try:
            yield s
        except Exception:
            s.__exit__()                # état inconnu (NVR redémarré, session expirée…) : nouvelle session au prochain appel
            slot[0] = None
            raise
        slot[1] = time.time()


def close_all() -> None:
    """À l'arrêt de CamSup : déconnexion propre de chaque NVR."""
    with _shared_lock:
        slots = [slot for _, slot in _shared.values()]
        _shared.clear()
    for slot in slots:
        if slot[0] is not None:
            slot[0].__exit__()
