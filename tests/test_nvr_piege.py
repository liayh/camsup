"""Réponses piégées d'un NVR (pentest du 05/11/2026)."""
from datetime import datetime

from app import live, nvrwatch, storage


class FakeSession:
    def __init__(self, ends):
        self.ends = ends


def test_fin_dans_le_futur_ignoree(monkeypatch):
    now = datetime(2026, 11, 5, 12, 0, 0)
    monkeypatch.setattr(nvrwatch, "_files", lambda s, ch, a, b: [("2026-11-05 11:00:00", e) for e in s.ends])
    s = FakeSession(["2026-11-05 11:59:00", "2099-01-01 00:00:00", "pas une date", None])
    assert nvrwatch._last_record(s, 1, now) == "2026-11-05 11:59:00"
    assert nvrwatch._last_record(FakeSession(["2099-01-01 00:00:00"]), 1, now) is None


def test_smart_valeurs_aberrantes():
    assert storage.smart_raw("0xFFFFFFFFFFFFFFFF") == 0
    assert storage.smart_raw(10 ** 30) == 0
    assert storage.smart_raw("0x00000000007B") == 123
    d = storage._health({"name": "sda", "smart": [{"id": 5, "name": "x", "current": storage._int("x"),
                                                   "threshold": storage._int(None), "raw": 0, "predict": 0}]})
    assert d["level"] == "ok"


def test_diagnostic_types_inattendus():
    rpc = {"disques": {"params": {"device": [{"Name": "/dev/sda", "Partitions": "abc"}]}}, "smart": "abc",
           "modele": {"params": "x"}}
    out = storage.diagnostic_summary(rpc)
    assert out["disques"][0]["smart"] == []
    rpc["smart"] = {"/dev/sda": {"params": {"values": [{"ID": 5, "Current": "x", "Threshold": 10, "Raw": "abc"}]}}}
    out = storage.diagnostic_summary(rpc)
    assert out["disques"][0]["smart"][0]["etat"] in ("bon", "surveiller", "defaillant")


def test_direct_mot_de_passe_retire_des_erreurs():
    s = live.Stream.__new__(live.Stream)
    s._secrets = ["mdp secret", "mdp%20secret"]
    msg = s._clean("rtsp://admin:mdp%20secret@1.2.3.4 refused; tried mdp secret")
    assert "secret" not in msg
