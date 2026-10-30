"""Comptes, sessions, cloisonnement par site et journal d'audit.

- Rôles : `integrator` (vous : tous les sites, configuration) et `pharmacist` (uniquement ses sites attribués).
- Connexion par formulaire → cookie de session HttpOnly/SameSite=Strict (jeton aléatoire, seul son SHA-256 est en base).
  HTTP Basic reste accepté pour les scripts (même vérification, même verrouillage).
- Toute requête qui modifie quelque chose doit porter l'en-tête `X-CamSup: 1` (protection CSRF : un site tiers
  ne peut pas l'ajouter sans pré-vérification CORS, que CamSup n'autorise pas).
- Ressource d'un site non attribué → 404 (on ne révèle pas son existence).
"""
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import config, db

log = logging.getLogger("accounts")
COOKIE = "camsup_session"
_basic = HTTPBasic(auto_error=False)
_fails: dict[str, list[float]] = {}
_flock = threading.Lock()
LOCK_FAILS, LOCK_SECONDS = 5, 900


# ---------------------------------------------------------------- mots de passe
def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(h) // 2)
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError):
        return False


def check_password_strength(pw: str) -> None:
    if len(pw) < 10:
        raise HTTPException(400, "Mot de passe trop court (10 caractères minimum)")


# ---------------------------------------------------------------- utilisateur courant
@dataclass
class User:
    id: int
    username: str
    role: str
    site_ids: frozenset[int] | None          # None = tous les sites (intégrateur)

    @property
    def is_integrator(self) -> bool:
        return self.role == "integrator"

    def can_site(self, site_id: int | None) -> bool:
        if self.site_ids is None:
            return True
        return site_id is not None and site_id in self.site_ids

    def public(self) -> dict:
        return {"id": self.id, "username": self.username, "role": self.role,
                "site_ids": None if self.site_ids is None else sorted(self.site_ids)}


def _load(uid: int) -> User | None:
    u = db.one("SELECT id,username,role FROM users WHERE id=? AND active=1", (uid,))
    if not u:
        return None
    if u["role"] == "integrator":
        sites = None
    else:
        sites = frozenset(r["site_id"] for r in db.q("SELECT site_id FROM user_sites WHERE user_id=?", (uid,)))
    return User(u["id"], u["username"], u["role"], sites)


def bootstrap(password: str) -> bool:
    """Base sans compte : crée l'intégrateur ADMIN_USER. Renvoie True si un compte a été créé."""
    if db.one("SELECT id FROM users LIMIT 1"):
        return False
    db.x("INSERT INTO users(username,pw_hash,role,created_ts) VALUES(?,?,?,?)",
         (config.ADMIN_USER, hash_password(password), "integrator", db.now()))
    audit(None, "compte.cree", obj=config.ADMIN_USER, detail="compte intégrateur initial")
    return True


def _locked(key: str) -> bool:
    with _flock:
        recent = [t for t in _fails.get(key, []) if time.time() - t < LOCK_SECONDS]
        _fails[key] = recent
        return len(recent) >= LOCK_FAILS


def _fail(key: str) -> None:
    with _flock:
        _fails.setdefault(key, []).append(time.time())


def authenticate(username: str, password: str, ip: str) -> User | None:
    key = f"{username.lower()}|{ip}"
    if _locked(key):
        audit(None, "connexion.bloquee", obj=username, ip=ip)
        raise HTTPException(429, "Trop d'essais : réessayez dans 15 minutes")
    row = db.one("SELECT id,pw_hash FROM users WHERE username=? AND active=1", (username,))
    if not row or not verify_password(password, row["pw_hash"]):
        _fail(key)
        audit(None, "connexion.echec", obj=username, ip=ip)
        return None
    with _flock:
        _fails.pop(key, None)
    return _load(row["id"])


def open_session(user: User, ip: str) -> str:
    token = secrets.token_urlsafe(32)
    now = db.now()
    db.x("DELETE FROM sessions WHERE expires_ts < ?", (now,))
    db.x("INSERT INTO sessions(token_hash,user_id,created_ts,expires_ts,ip) VALUES(?,?,?,?,?)",
         (hashlib.sha256(token.encode()).hexdigest(), user.id, now, now + config.SESSION_HOURS * 3600, ip))
    db.x("UPDATE users SET last_login_ts=? WHERE id=?", (now, user.id))
    audit(user, "connexion", ip=ip)
    return token


def close_session(token: str) -> None:
    db.x("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def session_user(token: str | None) -> User | None:
    """Utilisateur d'un jeton de session valide (cookie), ou None."""
    if not token:
        return None
    s = db.one("SELECT user_id FROM sessions WHERE token_hash=? AND expires_ts>?",
               (hashlib.sha256(token.encode()).hexdigest(), db.now()))
    return _load(s["user_id"]) if s else None


def current_user(request: Request, creds: HTTPBasicCredentials | None = Depends(_basic)) -> User:
    user = session_user(request.cookies.get(COOKIE))
    if not user and creds:
        user = authenticate(creds.username, creds.password, client_ip(request))
    if not user:
        raise HTTPException(401, "Authentification requise")   # pas de WWW-Authenticate : la page affiche son formulaire
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-CamSup") != "1":
        raise HTTPException(403, "En-tête X-CamSup manquant")
    request.state.user = user
    return user


def integrator(user: User = Depends(current_user)) -> User:
    if not user.is_integrator:
        raise HTTPException(403, "Réservé à l'intégrateur")
    return user


def require_site(user: User, site_id: int | None) -> None:
    if not user.can_site(site_id):
        raise HTTPException(404, "Introuvable")


def site_filter(user: User, column: str = "site_id") -> tuple[str, tuple]:
    """Fragment SQL limitant aux sites de l'utilisateur : (" AND col IN (…)", args)."""
    if user.site_ids is None:
        return "", ()
    ids = tuple(sorted(user.site_ids)) or (-1,)
    return f" AND {column} IN ({','.join('?' * len(ids))})", ids


# ---------------------------------------------------------------- journal d'audit
def audit(user: User | None, action: str, site_id: int | None = None, obj: str = "", detail: str = "",
          ip: str = "") -> None:
    """Trace qui a fait quoi."""
    db.x("INSERT INTO audit(ts,user_id,username,site_id,action,object,detail,ip) VALUES(?,?,?,?,?,?,?,?)",
         (db.now(), user.id if user else None, user.username if user else "", site_id, action, obj[:200],
          detail[:500], ip))


def audit_req(request: Request, action: str, site_id: int | None = None, obj: str = "", detail: str = "") -> None:
    audit(getattr(request.state, "user", None), action, site_id, obj, detail, client_ip(request))
