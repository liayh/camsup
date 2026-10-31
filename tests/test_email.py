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
        if isinstance(FakeSMTP.fail_login, Exception):
            raise FakeSMTP.fail_login
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
    # Gmail : connexion coupée au lieu d'un refus (vu le 31/10/2026 avec le mot de passe habituel du compte)
    smtp.fail_login = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
    r = admin.post("/api/notify/test", headers=H)
    assert r.status_code == 502 and "16 lettres" in r.json()["detail"] and "injoignable" not in r.json()["detail"]
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


def test_serveur_trouve_d_apres_l_adresse(env, smtp, monkeypatch):
    """Le pharmacien ne connaît ni serveur ni port : l'adresse et le mot de passe suffisent."""
    assert mail.detect("pharmacie@orange.fr") == {"host": "smtp.orange.fr", "port": 465, "security": "ssl"}
    assert mail.detect("Alertes@GMAIL.com")["host"] == "smtp.gmail.com"
    tried = []
    monkeypatch.setattr(mail, "_answers", lambda h, p, s: tried.append((h, p)) or (h, p) == ("mail.pharma-test.fr", 587))
    assert mail.detect("contact@pharma-test.fr") == {"host": "mail.pharma-test.fr", "port": 587, "security": "starttls"}
    assert tried == [("smtp.pharma-test.fr", 465), ("smtp.pharma-test.fr", 587), ("mail.pharma-test.fr", 465),
                     ("mail.pharma-test.fr", 587)]
    monkeypatch.setattr(mail, "_answers", lambda *a: False)
    with pytest.raises(ValueError, match="introuvable"):
        mail.detect("x@inconnu.example")
    admin = client("admin", config.ADMIN_PASSWORD)
    r = admin.put("/api/settings/email", json={"user": "alertes@gmail.com", "password": "abcd efgh ijkl mnop",
                                                "to": "installateur@exemple.fr"}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["host"], d["port"], d["security"], d["app_password"]) == ("smtp.gmail.com", 587, "starttls", "Google")
    r = admin.put("/api/settings/email", json={"user": "x@inconnu.example", "to": "installateur@exemple.fr"}, headers=H)
    assert r.status_code == 400 and "Réglages avancés" in r.json()["detail"]


def test_reglages_livres_avec_l_installateur(env, smtp, monkeypatch, tmp_path):
    preset = tmp_path / "camsup-email.env"
    monkeypatch.setattr(mail, "PRESET", preset)
    preset.write_text("﻿# réglages de l'intégrateur\nSMTP_USER=alertes@free.fr\nSMTP_PASSWORD=secret-envoi\n"
                      "ALERT_EMAIL_TO=installateur@exemple.fr\n", encoding="utf-8")
    assert mail.import_preset() is True
    assert not preset.exists()                                   # mot de passe en clair effacé
    s = mail.current()
    assert (s["host"], s["port"], s["security"], s["to"]) == ("smtp.free.fr", 465, "ssl", ["installateur@exemple.fr"])
    assert "secret-envoi" not in db.setting("email")
    mail.send("t", "t")
    assert smtp.logins == [("alertes@free.fr", "secret-envoi")]
    # réinstallation : les réglages faits depuis ne sont pas écrasés, le fichier est tout de même effacé
    preset.write_text("SMTP_USER=autre@orange.fr\nSMTP_PASSWORD=x\nALERT_EMAIL_TO=a@exemple.fr\n", encoding="utf-8")
    assert mail.import_preset() is False and not preset.exists()
    assert mail.current()["host"] == "smtp.free.fr"
