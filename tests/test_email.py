"""E-mail à l'installateur : réglages saisis dans Paramètres, mot de passe jamais renvoyé, envoi et erreurs lisibles."""
import smtplib

import pytest

from app import alerting, config, db, mail
from test_accounts import H, client, env  # noqa: F401  (fixture)

GMAIL = {"host": "smtp.gmail.com", "port": 587, "security": "starttls", "user": "pharma@gmail.com",
         "password": "mot-de-passe-application", "sender": "", "to": "installateur@exemple.fr; associe@exemple.fr"}


class FakeSMTP:
    """Serveur d'envoi factice : garde ce qui lui est demandé."""
    sent: list = []
    logins: list = []
    fail_login = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def starttls(self, context=None):
        FakeSMTP.tls = True

    def login(self, user, pw):
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"refus")
        FakeSMTP.logins.append((user, pw))

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture()
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.fail_login = [], [], False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    db.init()
    db.x("DELETE FROM settings WHERE key IN ('email','email_status')")
    yield FakeSMTP
    db.x("DELETE FROM settings WHERE key IN ('email','email_status')")


def test_reglages_et_mot_de_passe_jamais_renvoye(env, smtp):
    admin = client("admin", config.ADMIN_PASSWORD)
    assert admin.get("/api/settings/email").json()["configured"] is False
    r = admin.put("/api/settings/email", json=GMAIL, headers=H)
    assert r.status_code == 200
    d = admin.get("/api/settings/email").json()
    assert d["configured"] and d["has_password"] and d["to"] == ["installateur@exemple.fr", "associe@exemple.fr"]
    assert "mot-de-passe-application" not in r.text + str(d) + db.setting("email")      # chiffré en base
    # mot de passe inchangé si absent
    admin.put("/api/settings/email", json={**GMAIL, "password": None, "port": 465, "security": "ssl"}, headers=H)
    assert admin.post("/api/notify/test", headers=H).status_code == 200
    assert smtp.logins == [("pharma@gmail.com", "mot-de-passe-application")]
    msg = smtp.sent[0]
    assert msg["To"] == "installateur@exemple.fr, associe@exemple.fr" and "pharma@gmail.com" in msg["From"]
    assert admin.get("/api/settings/email").json()["last"]["ok"] is True
    assert admin.get("/api/overview").json()["email"] is True
    assert "email.reglages" in [a["action"] for a in db.q("SELECT action FROM audit")]


def test_adresse_invalide_et_droits(env, smtp):
    admin = client("admin", config.ADMIN_PASSWORD)
    r = admin.put("/api/settings/email", json={**GMAIL, "to": "installateur@exemple.fr, pas-une-adresse"}, headers=H)
    assert r.status_code == 400 and "pas-une-adresse" in r.json()["detail"]
    pharma = client("pharma", "motdepasse-pharma")
    assert pharma.get("/api/settings/email").status_code == 403
    assert pharma.put("/api/settings/email", json=GMAIL, headers=H).status_code == 403


def test_erreur_d_envoi_lisible(env, smtp):
    admin = client("admin", config.ADMIN_PASSWORD)
    admin.put("/api/settings/email", json=GMAIL, headers=H)
    smtp.fail_login = True
    r = admin.post("/api/notify/test", headers=H)
    assert r.status_code == 502 and "mot de passe d'application" in r.json()["detail"]
    last = admin.get("/api/settings/email").json()["last"]
    assert last["ok"] is False and "mot-de-passe-application" not in last["error"]


def test_alerte_critique_envoyee_en_arriere_plan(env, smtp, monkeypatch):
    mail.save("smtp.exemple.fr", 587, "starttls", "", None, "", ["installateur@exemple.fr"])
    monkeypatch.setattr(mail._pool, "submit", lambda fn: fn())
    alerting.raise_alert("rec:1", None, "rec", "Caméra 1 n'est plus enregistrée")
    alerting.raise_alert("camera:1", None, "camera", "Caméra 1 déconnectée")                # fenêtre seulement
    alerting.clear_alert("rec:1")
    alerting.clear_alert("camera:1")
    assert [m["Subject"] for m in smtp.sent] == ["[CamSup] 🔴 Caméra 1 n'est plus enregistrée",
                                                  "[CamSup] 🟢 Rétabli : Caméra 1 n'est plus enregistrée"]
    assert smtp.logins == []                                                              # relais sans compte
