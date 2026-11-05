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


def test_fenetre_alerte_depuis_ce_pc_seulement(env, monkeypatch):
    """La fenêtre d'alerte lit l'état sur le serveur local ; depuis le réseau, la route n'existe pas."""
    from app import alerting, config, main
    db.x("INSERT INTO alerts(key,camera_id,kind,message,opened_ts) VALUES('rec:1',?,'rec','Enregistrement arrêté',1)",
         (env["cam_a"],))
    db.x("INSERT INTO alerts(key,camera_id,kind,message,opened_ts) VALUES('x',NULL,'autre','pas affichée',1)")
    from app import fenetre
    c = client()
    defi = fenetre.new_challenge()
    assert c.get("/api/fenetre", params={"defi": defi}).status_code == 404   # client de test = adresse distante
    monkeypatch.setattr(main, "LOOPBACK", {"testclient"})
    assert c.get("/api/fenetre").status_code == 400                        # sans défi
    reply = c.get("/api/fenetre", params={"defi": defi}).json()
    data = fenetre.verify(reply, defi)
    assert [a["key"] for a in data["alerts"]] == ["rec:1"] and set(data) == {"alerts", "heartbeat", "instance", "email", "email_status"}
    assert data == alerting.popup_state()
    with pytest.raises(ValueError):
        fenetre.verify(reply, fenetre.new_challenge())                     # rejeu d'une ancienne réponse
    forged = {"data": {**data, "alerts": []}, "sig": reply["sig"]}
    with pytest.raises(ValueError):
        fenetre.verify(forged, defi)                                       # « aucune alerte » d'un imposteur


def test_fenetre_serveur_injoignable(monkeypatch):
    import requests
    from app import popup
    monkeypatch.setattr(popup.config, "PORT", 1)               # rien n'écoute
    with pytest.raises(requests.RequestException):
        popup.fetch(timeout=1)


def test_mot_de_passe_installation_efface(tmp_path, monkeypatch):
    from app import config
    f = tmp_path / "installation.env"
    f.write_text("ADMIN_PASSWORD=secret-installation\n")
    env_file = tmp_path / "camsup.env"
    env_file.write_text("PORT=8080\nADMIN_PASSWORD=ancien\n")
    monkeypatch.setattr(config, "INSTALL_FILE", f)
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    config.forget_admin_password()
    assert not f.exists() and env_file.read_text() == "PORT=8080\n"


def test_fichier_reglages_illisible(tmp_path):
    from app import config
    config._load_env_file(tmp_path / "absent.env")             # pas d'erreur
    d = tmp_path / "dossier.env"
    d.mkdir()
    config._load_env_file(d)                                    # illisible (ici un dossier) : ignoré


def test_mot_de_passe_nvr_pas_envoye_a_une_autre_adresse(env):
    """Changer l'adresse d'une caméra sans ressaisir le mot de passe : refusé (le mot de passe partirait ailleurs)."""
    from app import config, security
    c = client(config.ADMIN_USER, security.ADMIN_PASSWORD)
    db.x("UPDATE cameras SET password_enc=? WHERE id=?", (security.encrypt("SecretNVR!42"), env["cam_a"]))
    body = {"site_id": env["a"], "name": "cam A", "brand": "hikvision", "host": "10.66.6.6", "port_http": 80}
    assert c.put(f"/api/cameras/{env['cam_a']}", json=body, headers=H).status_code == 400
    ok = {"site_id": env["a"], "name": "cam A renommée", "brand": "dahua", "host": "10.0.0.1", "port_http": 80}
    assert c.put(f"/api/cameras/{env['cam_a']}", json=ok, headers=H).status_code == 200      # simple renommage


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "169.254.1.1", "0.0.0.0", "[::1]"])
def test_adresse_de_ce_pc_refusee(env, host):
    from app import config, security
    c = client(config.ADMIN_USER, security.ADMIN_PASSWORD)
    r = c.post("/api/nvr/discover", json={"host": host, "username": "admin", "password": "x"}, headers=H)
    assert r.status_code == 422


def test_un_nvr_par_pharmacie(env):
    from app import config, security
    c = client(config.ADMIN_USER, security.ADMIN_PASSWORD)
    r = c.post("/api/cameras", json={"site_id": env["b"], "name": "x", "brand": "dahua", "host": "10.0.0.1"}, headers=H)
    assert r.status_code == 409


def test_limites_negatives_refusees(env):
    from app import config, security
    c = client(config.ADMIN_USER, security.ADMIN_PASSWORD)
    assert c.get("/api/alerts", params={"limit": -1}).status_code == 422
    assert c.get("/api/audit", params={"limit": -1}).status_code == 422
    assert c.get("/api/me").headers["Cache-Control"] == "no-store"


def test_verrou_local_court_et_audit_borne(env, monkeypatch):
    """Depuis ce PC : pas de verrou par adresse (tous les comptes du poste partagent 127.0.0.1) et une seule ligne
    d'audit par verrouillage."""
    monkeypatch.setattr(accounts, "client_ip", lambda request: "127.0.0.1")
    c = client()
    for i in range(30):
        c.post("/api/login", json={"username": f"inconnu{i}", "password": "faux"}, headers=H)
    r = c.post("/api/login", json={"username": "pharma", "password": "motdepasse-pharma"}, headers=H)
    assert r.status_code == 200
    db.x("DELETE FROM audit")
    for _ in range(20):
        c.post("/api/login", json={"username": "pharma", "password": "faux"}, headers=H)
    assert db.one("SELECT COUNT(*) n FROM audit WHERE action='connexion.bloquee'")["n"] == 1


def test_essais_mot_de_passe_actuel_limites(env):
    c = client("pharma", "motdepasse-pharma")
    codes = [c.post("/api/me/password", json={"current": "faux", "new": "nouveau-secret-long"}, headers=H).status_code
             for _ in range(7)]
    assert codes[:5] == [400] * 5 and codes[-1] == 429


def test_video_texte_refusee(tmp_path):
    """Une « vidéo » texte (liste de lecture ffconcat/HLS) ferait lire d'autres fichiers à ffmpeg : refusée."""
    f = tmp_path / "x.dav"
    f.write_text("ffconcat version 1.0\nfile 42-1700000000-copie.mp4\n")
    with pytest.raises(export.ExportError):
        export.check_video(f)
    f.write_bytes(b"DHAV\xfd\x00\x00" + bytes(range(256)))
    export.check_video(f)


def test_telechargement_borne(monkeypatch, tmp_path):
    """Un NVR qui envoie sans fin : arrêté au-delà de la taille crédible."""
    from datetime import datetime, timedelta
    from app import devicehttp

    class Flux:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def iter_content(self, n):
            while True:
                yield b"\x47" * n

    monkeypatch.setattr(devicehttp, "request", lambda *a, **k: Flux())
    cam = {"host": "10.0.0.1", "port_http": 80, "channel": 1, "username": "u"}
    start = datetime(2026, 1, 1)
    with pytest.raises(export.ExportError):
        export._download(cam, "pw", start, start + timedelta(minutes=1), tmp_path / "x.dav")
    assert (tmp_path / "x.dav").stat().st_size <= export.max_bytes(start, start + timedelta(minutes=1)) + (1 << 20)


def test_redirection_vers_autre_hote_refusee(monkeypatch):
    import requests
    from app import devicehttp

    class R:
        is_redirect, headers, status_code = True, {"Location": "http://attaquant.example/x"}, 302
        def close(self): pass

    class S:
        def request(self, *a, **k): return R()

    monkeypatch.setattr(devicehttp, "_session", lambda: S())
    monkeypatch.setattr(devicehttp, "https_variant", lambda url: None)
    with pytest.raises(requests.ConnectionError):
        devicehttp.request("GET", "http://10.0.0.1/cgi-bin/x")


def test_rpc2_reste_sur_le_meme_hote(monkeypatch):
    from app import dahuarpc

    class R:
        is_redirect, headers = True, {"Location": "https://attaquant.example:4443/"}

    s = dahuarpc.Session("10.0.0.1", 80, "u", "p")
    monkeypatch.setattr(s.http, "get", lambda *a, **k: R())
    assert s._base() == "https://10.0.0.1:4443"


def test_fenetre_ne_pretend_pas_avoir_prevenu():
    """« Installateur prévenu par e-mail » seulement si un envoi a réellement réussi après l'alerte."""
    from app import popup
    rows = [{"key": "nvr:x", "kind": "nvr", "message": "NVR injoignable", "opened_ts": 1000}]
    assert "Aucun e-mail" in popup.email_sentence(rows, False, None)
    assert "en cours" in popup.email_sentence(rows, True, None)
    assert "a été prévenu" in popup.email_sentence(rows, True, {"ok": True, "ts": 1005})
    assert "a été prévenu" not in popup.email_sentence(rows, True, {"ok": True, "ts": 900})   # envoi d'avant l'alerte
    assert "n'a pas pu partir" in popup.email_sentence(rows, True, {"ok": False, "ts": 1005})
    cam = [{"key": "camera:1", "kind": "camera", "message": "Caméra débranchée", "opened_ts": 1000}]
    assert "a été prévenu" in popup.email_sentence(cam, True, {"ok": True, "ts": 1005})     # caméra : e-mail aussi


def test_premier_compte_cree_par_l_utilisateur(monkeypatch):
    """Base sans compte : l'interface propose de créer le sien, une seule fois, depuis ce PC seulement."""
    from fastapi.testclient import TestClient
    from app import main
    from app.main import app
    db.init()
    for t in ("audit", "sessions", "user_sites", "users"):
        db.x(f"DELETE FROM {t}")
    c = TestClient(app)
    assert c.get("/api/setup").json() == {"needed": True}
    body = {"username": "pharmacien.martin", "password": "un-mot-de-passe-long"}
    assert c.post("/api/setup", json=body, headers=H).status_code == 403          # client de test : pas ce PC
    monkeypatch.setattr(main, "LOOPBACK", {"testclient"})
    assert c.post("/api/setup", json={**body, "password": "court"}, headers=H).status_code == 400
    r = c.post("/api/setup", json=body, headers=H)
    assert r.status_code == 200 and r.json()["role"] == "integrator"
    assert c.get("/api/me").json()["username"] == "pharmacien.martin"           # connecté tout de suite
    assert c.get("/api/setup").json() == {"needed": False}
    assert c.post("/api/setup", json={**body, "username": "intrus"}, headers=H).status_code == 409
    assert db.one("SELECT COUNT(*) n FROM users")["n"] == 1


def test_premier_compte_windows_par_outil_administrateur(monkeypatch):
    """Programme Windows : tout programme du poste joint 127.0.0.1 → pas de création depuis le navigateur ; le premier
    compte se crée avec les droits d'administrateur du PC (`recovery.create`)."""
    from fastapi.testclient import TestClient
    from app import config, main, recovery
    from app.main import app
    db.init()
    for t in ("audit", "sessions", "user_sites", "users"):
        db.x(f"DELETE FROM {t}")
    monkeypatch.setattr(config, "SETUP_WEB", False)
    monkeypatch.setattr(main, "LOOPBACK", {"testclient"})
    c = TestClient(app)
    assert c.get("/api/setup").json() == {"needed": True, "outil": True}
    body = {"username": "voleur", "password": "un-mot-de-passe-long"}
    assert c.post("/api/setup", json=body, headers=H).status_code == 403
    with pytest.raises(ValueError):
        recovery.create("a b", "un-mot-de-passe-long")
    recovery.create("installateur", "un-mot-de-passe-long")
    assert db.one("SELECT role FROM users WHERE username='installateur'")["role"] == "integrator"
    with pytest.raises(ValueError):
        recovery.create("second", "un-mot-de-passe-long")


def test_mot_de_passe_oublie_reinitialise_sur_le_pc(env):
    from app import recovery
    c = client("pharma", "motdepasse-pharma")
    with pytest.raises(ValueError):
        recovery.reset("pharma", "court")
    with pytest.raises(ValueError):
        recovery.reset("inconnu", "nouveau-mot-de-passe")
    recovery.reset("pharma", "nouveau-mot-de-passe")
    assert c.get("/api/me").status_code == 401                   # anciennes sessions fermées
    client("pharma", "nouveau-mot-de-passe")                     # nouveau mot de passe accepté
    assert db.one("SELECT COUNT(*) n FROM audit WHERE action='mot_de_passe.reinitialise'")["n"] == 1


def test_lecture_robuste_des_disques():
    from app import storage
    assert storage.smart_raw("0x00000000007B") == 123
    assert storage.smart_raw("8 (0 4 0)") == 8 and storage.smart_raw(5) == 5 and storage.smart_raw(None) == 0
    assert storage.power_on_hours(0x0000_1234_0000_15E0) == 0x15E0               # octets du haut ignorés
    assert storage.power_on_hours(999_999) is None                              # invraisemblable
    assert storage.to_bytes(3815447) == 3815447 * 1024 * 1024                    # donné en Mo par un vieux NVR
    assert storage.to_bytes(4e12) == 4_000_000_000_000
    assert not storage.phantom_ok({"State": "Unknown", "SerialNo": ""}, 0)       # emplacement vide : masqué
    assert storage.phantom_ok({"State": "Error", "SerialNo": ""}, 0)             # disque en panne : affiché
    assert storage.phantom_ok({"State": "Running", "SerialNo": "WD-123"}, 0)


def test_echec_passager_garde_le_dernier_releve(monkeypatch):
    """Un relevé raté (NVR occupé) ne fait pas « disparaître » les disques ; après STALE_FAILS échecs, l'erreur."""
    from app import storage
    key = ("dahua", "10.4.4.4", 80, "u", "x")
    cam = {"id": 1, "site_id": 1, "password_enc": ""}
    storage._state.pop(key, None); storage._fails.pop(key, None)
    good = [{"name": "HDD 1", "level": "ok", "ok": True, "issues": [], "smart": [], "serial": ""}]
    monkeypatch.setattr(storage, "_dahua", lambda *a: [dict(d) for d in good])
    storage._poll(key, [cam])
    def boom(*a):
        raise RuntimeError("NVR occupé")
    monkeypatch.setattr(storage, "_dahua", boom)
    for _ in range(storage.STALE_FAILS - 1):
        storage._poll(key, [cam])
        assert storage._state[key]["disks"] and not storage._state[key]["error"]
    storage._poll(key, [cam])
    assert storage._state[key]["error"]


def test_diagnostic_des_disques_lisible():
    """Données réelles d'un client (03/11/2026) : un disque de 4 To découpé en 4 partitions par le NVR."""
    from app import storage
    part = lambda n: {"Status": "LvAvailable", "Total": 995715186688, "Remain": 0, "Name": f"/dev/sda{n}"}
    rpc = {"modele": {"params": {"type": "DHI-NVR"}}, "version": {"params": {"version": "3.2"}},
           "disques": {"params": {"device": [{"Name": "/dev/sda", "State": "Success", "SerialNo": "X",
                                              "Partitions": [part(i) for i in range(4)]}]}},
           "smart": {"/dev/sda": {"params": {"values": [{"ID": 5, "Raw": "0", "Current": 200, "Threshold": 140},
                                                         {"ID": 197, "Raw": "2", "Current": 200, "Threshold": 0},
                                                         {"ID": 9, "Raw": "66475", "Current": 9, "Threshold": 0}]}}}}
    d = storage.diagnostic_summary(rpc)["disques"][0]
    assert d["taille"] == 4 * 995715186688 and len(d["partitions"]) == 4 and d["libre"] == 0
    etats = {x["id"]: x["etat"] for x in d["smart"]}
    assert etats == {5: "bon", 197: "surveiller", 9: "bon"}
    assert [x["nom"] for x in d["smart"]] == ["Secteurs réalloués", "Secteurs en attente", "Heures de marche"]


def test_direct_decoupe_des_images():
    from app import live
    a, b = b"\xff\xd8AAA\xff\xd9", b"\xff\xd8BB\xff\xd9"
    buf = bytearray(b"bruit" + a + b + b"\xff\xd8CC")
    assert live.split_jpegs(buf) == [a, b] and bytes(buf) == b"\xff\xd8CC"
    assert "subtype=1" in live.rtsp_url({"username": "ad min", "host": "10.0.0.1", "channel": 3}, "p@ss:/")
    assert "p%40ss%3A%2F" in live.rtsp_url({"username": "u", "host": "10.0.0.1", "channel": 3}, "p@ss:/")


def test_direct_de_bout_en_bout(env, monkeypatch):
    """Une mire à la place du NVR : le navigateur reçoit des images JPEG ; caméra d'un autre site : 404."""
    from app import live
    monkeypatch.setattr(live, "rtsp_url", lambda cam, pw: "testsrc=size=320x240:rate=10")
    c = client("pharma", "motdepasse-pharma")
    assert c.get(f"/api/cameras/{env['cam_b']}/live").status_code == 404
    live.stop_all()
    s = live.acquire(db.one("SELECT * FROM cameras WHERE id=?", (env["cam_a"],)), "x")
    import time
    for _ in range(50):
        if s.frame:
            break
        time.sleep(0.1)
    assert s.frame and s.frame.startswith(b"\xff\xd8")
    live.release(s); live.stop_all()


def test_mur_image_par_image(env, monkeypatch):
    from app import live
    monkeypatch.setattr(live, "rtsp_url", lambda cam, pw: "testsrc=size=320x240:rate=10")
    live.stop_all()
    c = client("pharma", "motdepasse-pharma")
    import time
    for _ in range(60):
        r = c.get(f"/api/cameras/{env['cam_a']}/image")
        if r.status_code == 200:
            break
        assert r.status_code == 202
        time.sleep(0.1)
    assert r.headers["content-type"] == "image/jpeg" and r.content.startswith(b"\xff\xd8")
    assert c.get(f"/api/cameras/{env['cam_b']}/image").status_code == 404
    live.stop_all()


def test_direct_par_websocket(env, monkeypatch):
    """Mur en direct par une seule connexion : images de ses caméras seulement ; sans compte ou d'un autre site : refusé."""
    from starlette.websockets import WebSocketDisconnect
    from app import live
    monkeypatch.setattr(live, "rtsp_url", lambda cam, pw: "testsrc=size=320x240:rate=10")
    live.stop_all()
    with pytest.raises(WebSocketDisconnect):
        with client().websocket_connect("/api/live/ws?ids=1") as ws:
            ws.receive_bytes()
    c = client("pharma", "motdepasse-pharma")
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/api/live/ws", headers={"origin": "https://pirate.example"}) as ws:
            ws.receive_bytes()
    with pytest.raises(WebSocketDisconnect):                 # sans Origin : refusé aussi
        with c.websocket_connect(f"/api/live/ws?ids={env['cam_a']}") as ws:
            ws.receive_bytes()
    with c.websocket_connect(f"/api/live/ws?ids={env['cam_a']},{env['cam_b']}",
                             headers={"origin": "http://testserver"}) as ws:
        got = ws.receive_bytes()
        assert int.from_bytes(got[:4], "big") == env["cam_a"] and got[4:6] == b"\xff\xd8"
        for _ in range(5):                                   # jamais la caméra de l'autre pharmacie
            assert int.from_bytes(ws.receive_bytes()[:4], "big") == env["cam_a"]
    live.stop_all()


def test_alertes_retablies_effacees_apres_une_semaine(env):
    from app import config, monitor
    now = db.now()
    db.x("DELETE FROM alerts")
    for key, closed in (("vieille", now - 8 * 86400), ("recente", now - 2 * 86400), ("en_cours", None)):
        db.x("INSERT INTO alerts(key,camera_id,kind,message,opened_ts,closed_ts) VALUES(?,?,'camera','x',?,?)",
             (key, env["cam_a"], now - 30 * 86400, closed))
    assert config.ALERT_RETENTION_DAYS == 7
    monitor.purge()
    assert {r["key"] for r in db.q("SELECT key FROM alerts")} == {"recente", "en_cours"}


def test_role_integrateur_reserve_a_l_installateur(env):
    """Aucun compte créé ensuite ne peut être intégrateur ; un pharmacien ne peut pas le devenir."""
    from app import config, security
    c = client(config.ADMIN_USER, security.ADMIN_PASSWORD)
    r = c.post("/api/users", json={"username": "employe", "role": "integrator", "password": "un-mot-de-passe-long"}, headers=H)
    assert r.status_code == 403
    uid = db.one("SELECT id FROM users WHERE username='pharma'")["id"]
    r = c.put(f"/api/users/{uid}", json={"username": "pharma", "role": "integrator", "site_ids": []}, headers=H)
    assert r.status_code == 403
    assert db.one("SELECT role FROM users WHERE id=?", (uid,))["role"] == "pharmacist"
    me = db.one("SELECT id FROM users WHERE username=?", (config.ADMIN_USER,))["id"]       # l'installateur garde le sien
    assert c.put(f"/api/users/{me}", json={"username": config.ADMIN_USER, "role": "integrator"}, headers=H).status_code == 200
    p = client("pharma", "motdepasse-pharma")                                              # un pharmacien : 403 partout
    assert p.post("/api/users", json={"username": "x2", "role": "pharmacist", "password": "un-mot-de-passe-long"}, headers=H).status_code == 403
