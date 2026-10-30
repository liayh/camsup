"""Comptes, sessions, CSRF, cloisonnement entre pharmacies et journal d'audit."""
import pytest
from fastapi.testclient import TestClient

from app import accounts, config, db, nvrwatch, security, storage
from app.main import app

H = {"X-CamSup": "1"}


@pytest.fixture()
def env():
    """Base neuve : 2 pharmacies, 1 caméra chacune, 1 intégrateur, 1 pharmacien rattaché au site A."""
    db.init()
    for t in ("audit", "sessions", "user_sites", "users", "alerts", "cameras", "sites"):
        db.x(f"DELETE FROM {t}")
    accounts._fails.clear()
    accounts.bootstrap(security.ADMIN_PASSWORD)
    a = db.x("INSERT INTO sites(name) VALUES('Pharmacie A')")
    b = db.x("INSERT INTO sites(name) VALUES('Pharmacie B')")
    cams = {}
    for sid, name in ((a, "cam A"), (b, "cam B")):
        cams[sid] = db.x("INSERT INTO cameras(site_id,name,brand,host,created_ts) VALUES(?,?,'dahua','10.0.0.1',0)",
                         (sid, name))
    uid = db.x("INSERT INTO users(username,pw_hash,role,created_ts) VALUES('pharma',?,'pharmacist',0)",
               (accounts.hash_password("motdepasse-pharma"),))
    db.x("INSERT INTO user_sites(user_id,site_id) VALUES(?,?)", (uid, a))
    return {"a": a, "b": b, "cam_a": cams[a], "cam_b": cams[b]}


def client(user=None, pw=None) -> TestClient:
    c = TestClient(app)
    if user:
        r = c.post("/api/login", json={"username": user, "password": pw}, headers=H)
        assert r.status_code == 200, r.text
    return c


def test_hash_mot_de_passe():
    h = accounts.hash_password("un-secret-long")
    assert h.startswith("scrypt$") and "un-secret-long" not in h
    assert accounts.verify_password("un-secret-long", h)
    assert not accounts.verify_password("autre", h)
    assert not accounts.verify_password("x", "format-invalide")


def test_connexion_et_session(env):
    c = TestClient(app)
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/login", json={"username": "admin", "password": "faux"}, headers=H).status_code == 401
    r = c.post("/api/login", json={"username": "admin", "password": config.ADMIN_PASSWORD}, headers=H)
    assert r.status_code == 200 and r.json()["role"] == "integrator"
    cookie = r.cookies.get(accounts.COOKIE)
    assert cookie and db.one("SELECT COUNT(*) n FROM sessions WHERE token_hash=?", (cookie,))["n"] == 0  # jeton haché
    assert c.get("/api/me").json()["username"] == "admin"
    assert c.post("/api/logout", headers=H).status_code == 200
    assert c.get("/api/me").status_code == 401


def test_verrouillage_apres_5_echecs(env):
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/login", json={"username": "pharma", "password": "x"}, headers=H).status_code == 401
    r = c.post("/api/login", json={"username": "pharma", "password": "motdepasse-pharma"}, headers=H)
    assert r.status_code == 429                      # même le bon mot de passe est refusé pendant 15 min
    assert db.one("SELECT COUNT(*) n FROM audit WHERE action='connexion.echec'")["n"] == 5


def test_csrf_entete_obligatoire(env):
    c = client("admin", config.ADMIN_PASSWORD)
    assert c.post("/api/sites", json={"name": "X"}).status_code == 403
    assert c.post("/api/sites", json={"name": "X"}, headers=H).status_code == 200


def test_cloisonnement_pharmacien(env, monkeypatch):
    monkeypatch.setattr(nvrwatch, "_state", {h: {"host": h, "site_id": sid} for h, sid in (("nvr-a", env["a"]), ("nvr-b", env["b"]))})
    monkeypatch.setattr(storage, "_state", {(h,): {"host": h, "site_id": sid, "disks": []}
                                            for h, sid in (("nvr-a", env["a"]), ("nvr-b", env["b"]))})
    c = client("pharma", "motdepasse-pharma")
    ov = c.get("/api/overview").json()
    assert [s["name"] for s in ov["sites"]] == ["Pharmacie A"]
    assert [x["name"] for x in ov["cameras"]] == ["cam A"]
    assert [n["host"] for n in ov["nvr"]] == ["nvr-a"]
    assert [r["host"] for r in ov["recorders"]] == ["nvr-a"]
    assert all("password_enc" not in x for x in ov["cameras"])
    # caméra d'une autre pharmacie : rien ne s'y rapporte, et les routes de modification sont réservées
    assert c.post(f"/api/cameras/{env['cam_b']}/test", headers=H).status_code == 403


def test_routes_integrateur_refusees_au_pharmacien(env):
    c = client("pharma", "motdepasse-pharma")
    for method, url in (("get", "/api/users"), ("post", "/api/sites"), ("post", "/api/cameras"),
                        ("post", "/api/notify/test")):
        r = getattr(c, method)(url, headers=H, **({"json": {"name": "x"}} if method == "post" else {}))
        assert r.status_code == 403, url


def test_alertes_cloisonnees(env):
    db.x("INSERT INTO alerts(key,camera_id,kind,message,opened_ts) VALUES('k1',?,'offline','A hors ligne',1)", (env["cam_a"],))
    db.x("INSERT INTO alerts(key,camera_id,kind,message,opened_ts) VALUES('k2',?,'offline','B hors ligne',1)", (env["cam_b"],))
    db.x("INSERT INTO alerts(key,camera_id,kind,message,opened_ts) VALUES('disk',NULL,'disk','Disque',1)")
    assert [a["message"] for a in client("pharma", "motdepasse-pharma").get("/api/alerts").json()] == ["A hors ligne"]
    assert len(client("admin", config.ADMIN_PASSWORD).get("/api/alerts").json()) == 3


def test_journal_cloisonne(env):
    admin = client("admin", config.ADMIN_PASSWORD)
    r = admin.post("/api/cameras", json={"site_id": env["b"], "name": "cam B2", "brand": "dahua", "host": "10.0.0.2",
                                         "username": "u", "password": "secret-nvr"}, headers=H)
    assert r.status_code == 200
    assert db.one("SELECT password_enc FROM cameras WHERE id=?", (r.json()["id"],))["password_enc"] != "secret-nvr"
    db.x("INSERT INTO audit(ts,username,site_id,action) VALUES(1,'x',?,'site.modifie')", (env["a"],))
    # le pharmacien ne voit que le journal de ses sites, l'intégrateur voit tout
    rows = client("pharma", "motdepasse-pharma").get("/api/audit").json()
    assert rows and all(r["site_id"] == env["a"] for r in rows)
    assert any(r["site_id"] == env["b"] for r in admin.get("/api/audit").json())


def test_test_camera_sans_identifiants_dans_l_erreur(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("échec http://admin:secret-nvr@10.0.0.1/RPC2")
    monkeypatch.setattr(nvrwatch, "probe", boom)
    r = client("admin", config.ADMIN_PASSWORD).post(f"/api/cameras/{env['cam_a']}/test", headers=H).json()
    assert r["ok"] is False and "secret-nvr" not in r["error"] and "***@" in r["error"]


def test_notification_sans_email_configure(env):
    assert client("admin", config.ADMIN_PASSWORD).post("/api/notify/test", headers=H).status_code == 409


def test_gestion_des_comptes(env):
    c = client("admin", config.ADMIN_PASSWORD)
    assert c.post("/api/users", json={"username": "p2", "role": "pharmacist", "password": "court"}, headers=H).status_code == 400
    r = c.post("/api/users", json={"username": "p2", "role": "pharmacist", "password": "assez-long-123",
                                   "site_ids": [env["b"]]}, headers=H)
    assert r.status_code == 200
    p2 = client("p2", "assez-long-123")
    assert [s["name"] for s in p2.get("/api/overview").json()["sites"]] == ["Pharmacie B"]
    # désactivation : la session ouverte est coupée
    c.put(f"/api/users/{r.json()['id']}", json={"username": "p2", "role": "pharmacist", "active": False,
                                                 "site_ids": [env["b"]]}, headers=H)
    assert p2.get("/api/me").status_code == 401
    me = c.get("/api/me").json()
    assert c.delete(f"/api/users/{me['id']}", headers=H).status_code == 400   # pas d'auto-suppression
    assert db.one("SELECT COUNT(*) n FROM audit WHERE action LIKE 'compte.%'")["n"] >= 2
