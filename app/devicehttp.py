"""Accès HTTP(S) aux enregistreurs avec repli automatique.

Les URL sont construites en `http://hôte:port_http`. Si ce port ne répond plus (service HTTP du NVR arrêté,
HTTP désactivé au profit du HTTPS…), on essaie le HTTPS sur les ports courants (443, 8080, 8443) et on retient
celui qui répond, pendant `CACHE_SECONDS`. Certificats auto-signés acceptés (verify=False) : réseau local / VPN.
"""
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
HTTPS_PORTS = (443, 8080, 8443)
CACHE_SECONDS = 600
_cache: dict[str, tuple[str, float]] = {}       # hôte → (« https://hôte:port », horodatage)
_lock = threading.Lock()


def https_base(host: str, timeout: float = 4) -> str | None:
    """Premier port HTTPS qui répond sur l'hôte (mis en cache), ou None."""
    with _lock:
        hit = _cache.get(host)
        if hit and time.time() - hit[1] < CACHE_SECONDS:
            return hit[0]
    for port in HTTPS_PORTS:
        base = f"https://{host}:{port}"
        try:
            requests.get(base + "/", timeout=timeout, verify=False, allow_redirects=False)
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


def request(method: str, url: str, **kw) -> requests.Response:
    """Comme `requests.request`, avec repli HTTPS si la connexion HTTP échoue (refusée, injoignable)."""
    kw.setdefault("verify", False)
    try:
        return requests.request(method, url, **kw)
    except (requests.ConnectionError, requests.Timeout):
        alt = https_variant(url)
        if not alt:
            raise
        return requests.request(method, alt, **kw)
