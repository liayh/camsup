"""Durcissement : verrouillage par adresse, sessions après changement de mot de passe, comptes, adresses des NVR,
files d'extraits et d'aperçus."""
import pytest

from app import accounts, db, export, preview
from test_accounts import H, client, env  # noqa: F401  (fixture)


def test_verrouillage_par_adresse_tous_comptes(env):
    """Essai de nombreux identifiants depuis une même adresse : bloqué après IP_LOCK_FAILS échecs."""
    c = client()
    for i in range(accounts.IP_LOCK_FAILS):
        r = c.post("/api/login", json={"username": f"inconnu{i}", "password": "faux"}, headers=H)
        assert r.status_code == 401
    r = c.post("/api/login", json={"username": "pharma", "password": "motdepasse-pharma"}, headers=H)
    assert r.status_code == 429


def test_compte_inconnu_et_mauvais_mot_de_passe_meme_reponse(env):
    c = client()
    a = c.post("/api/login", json={"username": "personne", "password": "faux"}, headers=H)
    b = c.post("/api/login", json={"username": "pharma", "password": "faux"}, headers=H)
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_changement_mot_de_passe_ferme_les_autres_sessions(env):
    autre = client("pharma", "motdepasse-pharma")
    c = client("pharma", "motdepasse-pharma")
    r = c.post("/api/me/password", json={"current": "motdepasse-pharma", "new": "nouveau-secret-long"}, headers=H)
    assert r.status_code == 200
    assert c.get("/api/me").status_code == 200          # la session courante reste ouverte
    assert autre.get("/api/me").status_code == 401      # l'autre navigateur est déconnecté


def test_renommage_compte_en_doublon_refuse(env):
    from app import config, security
    c = client(config.ADMIN_USER, security.ADMIN_PASSWORD)
    uid = db.one("SELECT id FROM users WHERE username='pharma'")["id"]
    r = c.put(f"/api/users/{uid}", json={"username": config.ADMIN_USER, "role": "pharmacist", "site_ids": [env["a"]]},
              headers=H)
    assert r.status_code == 409
    r = c.put(f"/api/users/{uid}", json={"username": "pharma2", "role": "pharmacist", "password": "court",
                                         "site_ids": [env["a"]]}, headers=H)
    assert r.status_code == 400
    assert db.one("SELECT username FROM users WHERE id=?", (uid,))["username"] == "pharma"   # rien n'a changé


@pytest.mark.parametrize("host", ["10.0.0.1/evil", "user@10.0.0.1", "10.0.0.1?x=1", "10.0.0.1#a", "a b"])
def test_adresse_nvr_invalide(env, host):
    from app import config, security
    c = client(config.ADMIN_USER, security.ADMIN_PASSWORD)
    r = c.post("/api/nvr/discover", json={"host": host, "username": "admin", "password": "x"}, headers=H)
    assert r.status_code == 422
    r = c.post("/api/cameras", json={"site_id": env["a"], "name": "x", "brand": "dahua", "host": host}, headers=H)
    assert r.status_code == 422


def test_un_seul_extrait_a_la_fois(env, monkeypatch):
    monkeypatch.setattr(export._pool, "submit", lambda *a, **k: None)     # reste « en attente »
    user = accounts._load(db.one("SELECT id FROM users WHERE username='pharma'")["id"])
    cam = db.one("SELECT * FROM cameras WHERE id=?", (env["cam_a"],))
    now = db.now()
    db.x("DELETE FROM exports")
    export.create(user, env["a"], [cam], now - 600, now - 300, "")
    with pytest.raises(export.ExportError):
        export.create(user, env["a"], [cam], now - 600, now - 300, "")
    db.x("DELETE FROM exports")


def test_apercus_limites(env, monkeypatch):
    monkeypatch.setattr(preview._pool, "submit", lambda *a, **k: None)
    monkeypatch.setattr(preview, "_jobs", {})
    cam = db.one("SELECT * FROM cameras WHERE id=?", (env["cam_a"],))
    now = db.now() - 86400
    for i in range(preview.MAX_PENDING):
        preview.request(cam, now + i * 60)
    with pytest.raises(export.ExportError):
        preview.request(cam, now + 3600)
