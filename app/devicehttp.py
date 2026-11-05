"""Accès HTTP(S) aux enregistreurs avec repli automatique.

Les URL sont construites en `http://hôte:port_http`. Si ce port ne répond plus (service HTTP du NVR arrêté,
HTTP désactivé au profit du HTTPS…), on essaie le HTTPS sur les ports courants (443, 8080, 8443) et on retient
celui qui répond, pendant `CACHE_SECONDS`. Certificats auto-signés acceptés (verify=False) : réseau local / VPN.
Redirections : suivies seulement vers le même hôte (le NVR renvoie son CGI vers HTTPS) ; jamais vers un autre, qui
recevrait la réponse d'authentification (ou servirait à joindre une autre machine depuis le poste).
"""
import hashlib
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
import urllib3
from requests.auth import HTTPDigestAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
HTTPS_PORTS = (443, 8080, 8443)
CACHE_SECONDS = 600
_cache: dict[str, tuple[str, float]] = {}       # hôte → (« https://hôte:port », horodatage)
_http_dead: dict[str, float] = {}               # hôte → instant où son port HTTP n'a pas répondu
_lock = threading.Lock()
_auths: dict[tuple, HTTPDigestAuth] = {}


def digest(user: str, pw: str) -> HTTPDigestAuth:
    """Authentification Digest gardée par compte : après le premier défi du NVR, les requêtes suivantes partent
    directement avec la réponse (sinon chaque requête est d'abord refusée en 401 puis renvoyée : deux allers-retours)."""
    key = (user, hashlib.sha256(pw.encode()).hexdigest())
    with _lock:
        auth = _auths.get(key)
        if auth is None:
            auth = _auths[key] = HTTPDigestAuth(user, pw)
        return auth


class _OneShot:
    """Une connexion par requête, refermée ensuite (05/11/2026). Les connexions gardées ouvertes (une par fil
    d'exécution) pouvaient saturer un vieux NVR, qui n'en accepte que quelques-unes : il cessait de répondre."""
    @staticmethod
    def request(method: str, url: str, **kw) -> requests.Response:
        with requests.Session() as s:
            s.trust_env = False                 # NVR du réseau local : jamais par un proxy
            r = s.request(method, url, **kw)
        return r


def _session() -> _OneShot:
    return _OneShot()


def https_base(host: str, timeout: float = 4) -> str | None:
    """Premier port HTTPS qui répond sur l'hôte (mis en cache), ou None."""
    with _lock:
        hit = _cache.get(host)
        if hit and time.time() - hit[1] < CACHE_SECONDS:
            return hit[0]
    for port in HTTPS_PORTS:
        base = f"https://{host}:{port}"
        try:
            with requests.Session() as s:
                s.trust_env = False
                s.get(base + "/", timeout=timeout, verify=False, allow_redirects=False).close()
        except requests.RequestException:
            continue
        with _lock:
            _cache[host] = (base, time.time())
        return base
    return None


def https_variant(url: str) -> str | None:
    """`http://hôte:80/chemin` → `https://hôte:<port qui répond>/chemin`, ou None si aucun HTTPS."""
    u = urlsplit(url)
    if u.scheme != "http":
        return None
    base = https_base(u.hostname)
    if not base:
        return None
    b = urlsplit(base)
    return urlunsplit((b.scheme, b.netloc, u.path, u.query, u.fragment))


MAX_REDIRECTS = 3


def _send(method: str, url: str, **kw) -> requests.Response:
    """Requête dont les redirections ne sont suivies que vers le même hôte."""
    host = urlsplit(url).hostname
    for _ in range(MAX_REDIRECTS + 1):
        r = _session().request(method, url, allow_redirects=False, **kw)
        if not r.is_redirect:
            return r
        nxt = urljoin(url, r.headers.get("Location", ""))
        if urlsplit(nxt).hostname != host or urlsplit(nxt).scheme not in ("http", "https"):
            r.close()
            raise requests.ConnectionError(f"redirection vers un autre équipement refusée ({urlsplit(nxt).hostname})")
        r.close()
        url = nxt
    raise requests.ConnectionError("trop de redirections")


def http_dead(host: str) -> bool:
    """Le port HTTP de cet hôte a-t-il échoué il y a moins de `CACHE_SECONDS` ?"""
    with _lock:
        t = _http_dead.get(host)
    return t is not None and time.time() - t < CACHE_SECONDS


def request(method: str, url: str, **kw) -> requests.Response:
    """Comme `requests.request`, avec repli HTTPS si la connexion HTTP échoue (refusée, injoignable). Un port HTTP
    qui vient d'échouer n'est pas réessayé pendant `CACHE_SECONDS` : directement en HTTPS (sinon chaque requête
    attendrait d'abord l'échec du HTTP, jusqu'au délai de connexion)."""
    kw.setdefault("verify", False)
    kw.pop("allow_redirects", None)
    host = urlsplit(url).hostname
    if urlsplit(url).scheme == "http" and http_dead(host):
        alt = https_variant(url)
        if alt:
            return _send(method, alt, **kw)
    try:
        return _send(method, url, **kw)
    except (requests.ConnectionError, requests.Timeout) as e:
        if "redirection" in str(e):
            raise
        alt = https_variant(url)
        if not alt:
            raise
        with _lock:
            _http_dead[host] = time.time()
        return _send(method, alt, **kw)
