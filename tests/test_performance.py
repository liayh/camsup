"""Échanges plus rapides avec le NVR : port HTTP mort retenu, connexions et authentification réutilisées, recherche
d'enregistrements resserrée, écart d'horloge réutilisé, SMART relu moins souvent."""
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from app import devicehttp, export, nvrwatch, storage


@pytest.fixture()
def serveur():
    """Faux NVR HTTP avec authentification Digest ; compte les requêtes et les connexions."""
    stats = {"requetes": 0, "refus": 0, "connexions": set()}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            stats["requetes"] += 1
            stats["connexions"].add(self.client_address)
            if "Digest" not in (self.headers.get("Authorization") or ""):
                stats["refus"] += 1
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Digest realm="NVR", nonce="abc123", qop="auth"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], stats
    srv.shutdown()


def test_connexion_et_authentification_reutilisees(serveur):
    port, stats = serveur
    devicehttp._auths.clear()
    for _ in range(5):
        r = devicehttp.request("GET", f"http://127.0.0.1:{port}/cgi-bin/x", auth=devicehttp.digest("u", "p"), timeout=5)
        assert r.text == "ok"
    assert stats["refus"] == 1                  # un seul défi 401, au lieu d'un par requête
    assert stats["requetes"] == 6               # au lieu de 10
    # une connexion par requête, refermée : rien ne reste ouvert sur le NVR (un vieux NVR en accepte peu)
    assert len(stats["connexions"]) == 5


def test_port_http_mort_retenu(monkeypatch):
    """Port HTTP en panne : essayé une fois, ensuite directement en HTTPS."""
    essais = []

    def send(method, url, **kw):
        essais.append(url)
        if url.startswith("http://"):
            raise requests.ConnectTimeout("port 80 muet")
        return "réponse"

    monkeypatch.setattr(devicehttp, "_send", send)
    monkeypatch.setattr(devicehttp, "https_variant", lambda url: url.replace("http://10.9.9.1:80", "https://10.9.9.1:8080"))
    devicehttp._http_dead.pop("10.9.9.1", None)
    for _ in range(3):
        assert devicehttp.request("GET", "http://10.9.9.1:80/x") == "réponse"
    assert [u.split(":")[0] for u in essais] == ["http", "https", "https", "https"]


def test_recherche_resserree_sur_la_derniere_fin_connue():
    """Fin déjà connue : recherche depuis peu avant elle, pas sur les 24 h."""
    now = datetime(2026, 11, 3, 12, 0, 0)
    fenetres = []

    class S:
        pass

    def files(s, ch, start, end):
        fenetres.append(end - start)
        return [((now - timedelta(minutes=2)).strftime(nvrwatch.FMT), now.strftime(nvrwatch.FMT))]

    orig = nvrwatch._files
    nvrwatch._files = files
    try:
        assert nvrwatch._last_record(S(), 1, now) == now.strftime(nvrwatch.FMT)
        assert nvrwatch._last_record(S(), 1, now, now - timedelta(seconds=10)) == now.strftime(nvrwatch.FMT)
    finally:
        nvrwatch._files = orig
    assert fenetres[0] == timedelta(hours=24) and fenetres[1] < timedelta(minutes=15)


def test_recherche_complete_si_rien_de_recent():
    now = datetime(2026, 11, 3, 12, 0, 0)
    old = (now - timedelta(hours=3)).strftime(nvrwatch.FMT)
    fenetres = []

    def files(s, ch, start, end):
        fenetres.append(end - start)
        return [] if end - start < timedelta(hours=1) else [(old, old)]

    orig = nvrwatch._files
    nvrwatch._files = files
    try:
        assert nvrwatch._last_record(object(), 1, now, now - timedelta(minutes=1)) == old
    finally:
        nvrwatch._files = orig
    assert fenetres == [timedelta(minutes=11), timedelta(hours=24)]


def test_ecart_horloge_reutilise(monkeypatch):
    nvrwatch._clock["10.7.7.7"] = (3600.4, time.time())
    monkeypatch.setattr(nvrwatch, "probe", lambda *a, **k: pytest.fail("le NVR ne doit pas être réinterrogé"))
    assert export._offset({"host": "10.7.7.7", "port_http": 80, "username": "u"}, "p") == 3600
    nvrwatch._clock["10.7.7.7"] = (3600, time.time() - 120)          # trop ancien : redemandé
    assert nvrwatch.clock_offset("10.7.7.7") is None


def test_smart_relu_toutes_les_10_minutes():
    appels = []

    class S:
        def call(self, method, params=None, obj=None):
            appels.append(method)
            return {"result": 7} if method.endswith("instance") else {"params": {"values": [{"ID": 5, "Raw": "0"}]}}

    storage._smart_cache.clear()
    d = {"name": "HDD 1", "serial": "X"}
    assert storage._smart(S(), "10.0.0.1", d) == storage._smart(S(), "10.0.0.1", d)
    assert appels.count("devStorage.getSmartValue") == 1
