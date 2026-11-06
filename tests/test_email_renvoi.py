"""E-mail d'alerte jamais perdu (06/11/2026 : NVR éteint toute la nuit, aucun e-mail) : une alerte ouverte dont
l'e-mail n'est pas parti (e-mail pas encore configuré, envoi en échec) est renvoyée."""
import time

from app import alerting, db, mail


def _wait(cond, s=5):
    end = time.time() + s
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()


def test_alerte_renvoyee_apres_configuration_et_apres_echec(monkeypatch):
    db.init()
    db.x("DELETE FROM alerts")
    db.x("DELETE FROM settings WHERE key IN ('email','email_status')")
    sent, fail = [], [True]
    monkeypatch.setattr(mail, "configured", lambda s=None: bool(db.setting("email")))

    def fake_send(subject, body, s=None):
        if fail[0]:
            raise RuntimeError("serveur d'envoi injoignable")
        sent.append(subject)
    monkeypatch.setattr(mail, "send", fake_send)

    alerting.raise_alert("nvr:192.0.2.10", None, "nvr", "Pharma : l'enregistreur ne répond plus")
    assert not sent                                          # pas configuré : rien ne part…
    alerting.resend()
    assert not sent
    db.set_setting("email", '{"host": "smtp.test", "to": []}')   # … configuré : nouvel essai, mais envoi en échec
    alerting.resend()
    assert _wait(lambda: db.setting("email_status") and '"ok": false' in db.setting("email_status"))
    assert db.one("SELECT emailed_ts FROM alerts")["emailed_ts"] is None
    fail[0] = False                                          # internet revenu : parti au prochain essai
    _wait(lambda: not alerting._sending)
    alerting.resend()
    assert _wait(lambda: db.one("SELECT emailed_ts FROM alerts")["emailed_ts"] is not None)
    assert len(sent) == 1 and "ne répond plus" in sent[0]
    alerting.resend()                                        # déjà parti : pas de doublon
    time.sleep(0.3)
    assert len(sent) == 1
    alerting.clear_alert("nvr:192.0.2.10")
    assert _wait(lambda: len(sent) == 2) and "Rétabli" in sent[1]
    db.x("DELETE FROM settings WHERE key IN ('email','email_status')")
    db.x("DELETE FROM alerts")
