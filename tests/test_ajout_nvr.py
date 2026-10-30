"""Ajout d'un enregistreur : découverte des caméras (noms, canaux vides écartés) et ajout groupé sans doublon."""
from app import dahuarpc, db, nvrwatch, security
from test_accounts import H, client, env  # noqa: F401  (fixture)
from app import config


class FakeNvr:
    """NVR Dahua factice : 4 canaux, dont un vide et des noms génériques."""
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def call(self, method, params=None, obj=None, path="/RPC2"):
        return {"magicBox.getDeviceType": {"params": {"type": "DHI-NVR5216"}},
                "magicBox.getSerialNo": {"params": {"sn": "ABC123"}},
                "configManager.getConfig": {"params": {"table": [{"Name": "IPC"}, {"Name": "Caisse"}, {"Name": "Channel3"},
                                                                 {"Name": "Réserve"}]}}}[method]

    def params(self, method, params=None):
        return {"states": [{"channel": 0, "connectionState": "Connected"}, {"channel": 1, "connectionState": "Unconnect"},
                           {"channel": 2, "connectionState": "Empty"}, {"channel": 3, "connectionState": "Connected"}]}


NVR = {"host": "192.168.5.10", "port_http": 80, "username": "lecture", "password": "secret-du-nvr"}


def test_decouverte_et_ajout(env, monkeypatch):
    monkeypatch.setattr(dahuarpc, "Session", FakeNvr)
    admin = client("admin", config.ADMIN_PASSWORD)
    r = admin.post("/api/nvr/discover", json=NVR, headers=H)
    assert r.status_code == 200, r.text
    found = r.json()
    assert (found["model"], found["serial"]) == ("DHI-NVR5216", "ABC123")
    assert [(c["channel"], c["name"], c["known"]) for c in found["channels"]] == [
        (1, "Caméra 1", False), (2, "Caisse", False), (4, "Réserve", False)]          # canal 3 vide : écarté
    add = {**NVR, "site_id": env["a"], "channels": [{"channel": 1, "name": "Entrée"}, {"channel": 2, "name": "Caisse"}]}
    assert admin.post("/api/nvr", json=add, headers=H).json() == {"added": 2}
    cams = db.q("SELECT name, channel, username, password_enc FROM cameras WHERE host=? ORDER BY channel", (NVR["host"],))
    assert [(c["name"], c["channel"], c["username"]) for c in cams] == [("Entrée", 1, "lecture"), ("Caisse", 2, "lecture")]
    assert security.decrypt(cams[0]["password_enc"]) == "secret-du-nvr"
    # relancé : les canaux déjà présents sont signalés et jamais ajoutés deux fois
    assert [c["known"] for c in admin.post("/api/nvr/discover", json=NVR, headers=H).json()["channels"]] == [True, True, False]
    assert admin.post("/api/nvr", json=add, headers=H).json() == {"added": 0}
    assert "nvr.ajoute" in [a["action"] for a in db.q("SELECT action FROM audit")]
    assert "secret-du-nvr" not in str(db.q("SELECT * FROM audit"))


def test_nvr_injoignable_et_droits(env, monkeypatch):
    def refuse(*a, **k):
        raise RuntimeError("Authentification refusée (RPC2)")
    monkeypatch.setattr(nvrwatch, "discover", refuse)
    admin = client("admin", config.ADMIN_PASSWORD)
    r = admin.post("/api/nvr/discover", json=NVR, headers=H)
    assert r.status_code == 400 and "refuse l'identifiant" in r.json()["detail"]
    pharma = client("pharma", "motdepasse-pharma")
    assert pharma.post("/api/nvr/discover", json=NVR, headers=H).status_code == 403
    assert pharma.post("/api/nvr", json={**NVR, "site_id": env["a"], "channels": [{"channel": 1, "name": "x"}]},
                       headers=H).status_code == 403


class Fake50(FakeNvr):
    """NVR de 64 canaux dont 50 caméras branchées."""
    def call(self, method, params=None, obj=None, path="/RPC2"):
        if method == "configManager.getConfig":
            return {"params": {"table": [{"Name": f"Channel{i + 1}"} for i in range(64)]}}
        return super().call(method, params, obj, path)

    def params(self, method, params=None):
        return {"states": [{"channel": i, "connectionState": "Connected" if i < 50 else "Empty"} for i in range(64)]}


def test_site_de_50_cameras_et_identifiants_communs(env, monkeypatch):
    monkeypatch.setattr(dahuarpc, "Session", Fake50)
    admin = client("admin", config.ADMIN_PASSWORD)
    found = admin.post("/api/nvr/discover", json=NVR, headers=H).json()
    assert len(found["channels"]) == 50 and found["channels"][49]["name"] == "Caméra 50"
    first = [{"channel": c["channel"], "name": c["name"]} for c in found["channels"][:30]]
    assert admin.post("/api/nvr", json={**NVR, "site_id": env["a"], "channels": first}, headers=H).json() == {"added": 30}
    # plus tard : nouvelles caméras, sans ressaisir le mot de passe
    again = admin.post("/api/nvr/rescan", json={"host": NVR["host"]}, headers=H).json()
    assert sum(not c["known"] for c in again["channels"]) == 20 and again["username"] == "lecture"
    assert "password" not in again and "secret-du-nvr" not in str(again)
    rest = [{"channel": c["channel"], "name": c["name"]} for c in again["channels"] if not c["known"]]
    body = {"host": NVR["host"], "port_http": 80, "username": "lecture", "password": None, "site_id": env["a"], "channels": rest}
    assert admin.post("/api/nvr", json=body, headers=H).json() == {"added": 20}
    cams = db.q("SELECT username, password_enc FROM cameras WHERE host=?", (NVR["host"],))
    assert len(cams) == 50 and {security.decrypt(c["password_enc"]) for c in cams} == {"secret-du-nvr"}
    # nouveau mot de passe du NVR : une seule saisie pour les 50 caméras
    r = admin.put("/api/nvr/credentials", json={"host": NVR["host"], "port_http": 80, "username": "camsup",
                                                "password": "nouveau-secret"}, headers=H)
    assert r.json() == {"updated": 50}
    cams = db.q("SELECT username, password_enc FROM cameras WHERE host=?", (NVR["host"],))
    assert {(c["username"], security.decrypt(c["password_enc"])) for c in cams} == {("camsup", "nouveau-secret")}


def test_identifiants_refuses_rien_ne_change(env, monkeypatch):
    monkeypatch.setattr(dahuarpc, "Session", FakeNvr)
    admin = client("admin", config.ADMIN_PASSWORD)
    admin.post("/api/nvr", json={**NVR, "site_id": env["a"], "channels": [{"channel": 1, "name": "Entrée"}]}, headers=H)
    def refuse(*a, **k):
        raise RuntimeError("Authentification refusée (RPC2)")
    monkeypatch.setattr(nvrwatch, "discover", refuse)
    r = admin.put("/api/nvr/credentials", json={"host": NVR["host"], "port_http": 80, "username": "x", "password": "faux"}, headers=H)
    assert r.status_code == 400
    c = db.one("SELECT username, password_enc FROM cameras WHERE host=?", (NVR["host"],))
    assert c["username"] == "lecture" and security.decrypt(c["password_enc"]) == "secret-du-nvr"
    assert admin.post("/api/nvr/rescan", json={"host": "10.9.9.9"}, headers=H).status_code == 404
    pharma = client("pharma", "motdepasse-pharma")
    assert pharma.put("/api/nvr/credentials", json={"host": NVR["host"], "username": "x"}, headers=H).status_code == 403
