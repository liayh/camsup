"""Licence par pharmacie : code signé, date de fin, blocage de la surveillance, du direct et de la vidéo."""
import time
from datetime import date, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import config, db, licence

KEY = Ed25519PrivateKey.generate()


@pytest.fixture
def vendeur(monkeypatch):
    import base64
    from cryptography.hazmat.primitives import serialization as s
    pub = KEY.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)
    monkeypatch.setattr(licence, "PUBLIC_KEY", base64.b64encode(pub).decode())
    monkeypatch.setattr(config, "LICENCE_REQUIRED", True)
    db.init()
    for k in ("licence", "licence_vu"):
        db.x("DELETE FROM settings WHERE key=?", (k,))
    yield lambda fin, n=7: licence.encode(n, fin, KEY.sign)
    for k in ("licence", "licence_vu"):
        db.x("DELETE FROM settings WHERE key=?", (k,))


def test_code_signe(vendeur):
    code = vendeur(date(2027, 11, 5))
    assert licence.decode(code.lower()) == {"numero": 7, "fin": date(2027, 11, 5)}
    autre = licence.encode(7, date(2099, 1, 1), Ed25519PrivateKey.generate().sign)    # pas la clé du vendeur
    with pytest.raises(ValueError):
        licence.decode(autre)
    with pytest.raises(ValueError):
        licence.decode(code[:-6])
    abime = code[:10] + ("A" if code[10] != "A" else "B") + code[11:]
    with pytest.raises(ValueError):
        licence.decode(abime)


def test_etats_et_heure_reculee(vendeur, monkeypatch):
    assert licence.status()["etat"] == "absente" and not licence.active()
    licence.install(vendeur(date.today() + timedelta(days=200)))
    assert licence.status()["etat"] == "active"
    with pytest.raises(ValueError):                         # pas de retour à un code plus court
        licence.install(vendeur(date.today() + timedelta(days=10)))
    db.x("DELETE FROM settings WHERE key='licence'")
    licence.install(vendeur(date.today() + timedelta(days=10)))
    assert licence.status()["etat"] == "bientot"
    # PC avancé d'un an puis remis à l'heure : l'heure la plus avancée vue fait foi
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 400 * 86400)
    assert licence.status()["etat"] == "expiree"
    monkeypatch.setattr(time, "time", real)
    assert licence.status()["etat"] == "expiree"


def test_api_bloquee_sans_licence(vendeur, monkeypatch):
    from fastapi.testclient import TestClient
    from app import accounts
    from app.main import app
    db.x("DELETE FROM users WHERE username='lic'")
    db.x("INSERT INTO users(username,pw_hash,role,created_ts) VALUES('lic',?,'integrator',?)",
         (accounts.hash_password("motdepasse-licence"), db.now()))
    c = TestClient(app)
    H = {"X-CamSup": "1"}
    assert c.post("/api/login", json={"username": "lic", "password": "motdepasse-licence"}, headers=H).status_code == 200
    assert c.get("/api/overview").json()["licence"]["etat"] == "absente"
    for url, body in (("/api/recordings", {"camera_ids": [1], "start_ts": 0, "end_ts": 60}),
                      ("/api/recordings/days", {"camera_ids": [1], "days": 1})):
        r = c.post(url, json=body, headers=H)
        assert r.status_code == 403 and "Licence" in r.json()["detail"]
    assert c.get("/api/cameras/1/image").status_code == 403
    assert c.post("/api/licence", json={"code": "CS1-faux-code-xxxxx"}, headers=H).status_code == 400
    r = c.post("/api/licence", json={"code": vendeur(date.today() + timedelta(days=365))}, headers=H)
    assert r.status_code == 200 and r.json()["etat"] == "active"
    assert c.get("/api/licence").json()["actif"]
    db.x("DELETE FROM users WHERE username='lic'")


def test_surveillance_arretee(vendeur, monkeypatch):
    from app import alerting, monitor, nvrwatch
    calls = []
    monkeypatch.setattr(nvrwatch, "check", lambda cams: calls.append(1))
    monkeypatch.setattr(monitor._stop, "wait", lambda s: monitor._stop.set())
    monitor._stop.clear()
    monitor._loop()
    assert not calls
    assert db.one("SELECT id FROM alerts WHERE key='licence' AND closed_ts IS NULL")
    alerting.clear_alert("licence")
