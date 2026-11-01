"""Mode « alertes seulement » : surveillance du NVR, tendance SMART, e-mails ciblés, rappel de la fenêtre d'alerte."""
import pytest

from app import alerting, config, db, nvrwatch, storage
from app.popup import Reminder

NOW = "2026-10-30 10:00:00"


@pytest.fixture()
def env(monkeypatch):
    sent = []
    monkeypatch.setattr(alerting, "notify", lambda text, email=True: sent.append((text, email)))
    db.init()
    for t in ("alerts", "smart_baseline", "status", "cameras", "sites"):
        db.x(f"DELETE FROM {t}")
    site = db.x("INSERT INTO sites(name) VALUES('Pharmacie du Centre')")
    cams = [db.x("INSERT INTO cameras(site_id,name,brand,host,channel,created_ts) VALUES(?,?,'dahua','10.0.0.9',?,0)",
                 (site, f"Caméra {ch}", ch)) for ch in (1, 2)]
    nvrwatch._fails.clear()
    nvrwatch._down.clear()
    nvrwatch._online.clear()
    nvrwatch._last.clear()
    nvrwatch._grace.clear()
    return {"cams": db.q("SELECT * FROM cameras ORDER BY channel"), "sent": sent}


def open_alerts() -> dict:
    return {a["key"]: a for a in db.q("SELECT * FROM alerts WHERE closed_ts IS NULL")}


def result(conn=("Connected", "Connected"), last=("2026-10-30 09:59:58", "2026-10-30 09:59:58")) -> dict:
    return {"time": NOW, "conn": {i + 1: c for i, c in enumerate(conn)}, "last": {i + 1: v for i, v in enumerate(last)}}


def test_tout_va_bien_aucune_alerte(env):
    nvrwatch.evaluate("10.0.0.9", env["cams"], result())
    assert open_alerts() == {}
    assert nvrwatch.online(env["cams"][0]["id"]) is True


def test_nvr_n_enregistre_plus_apres_15_minutes(env):
    c1, c2 = env["cams"]
    # 14 min sans nouvel enregistrement : pas encore d'alerte ; 16 min : alerte (horloge du NVR)
    nvrwatch.evaluate("10.0.0.9", env["cams"], result(last=("2026-10-30 09:46:00", "2026-10-30 09:44:00")))
    a = open_alerts()
    assert set(a) == {f"rec:{c2['id']}"}
    assert a[f"rec:{c2['id']}"]["kind"] == "rec"
    assert "Pharmacie du Centre" in a[f"rec:{c2['id']}"]["message"] and "09:44" in a[f"rec:{c2['id']}"]["message"]
    assert env["sent"][-1][1] is True                                  # e-mail à l'installateur
    nvrwatch.evaluate("10.0.0.9", env["cams"], result())               # l'enregistrement reprend
    assert open_alerts() == {}
    assert env["sent"][-1][0].startswith("🟢") and env["sent"][-1][1] is True


def test_aucun_enregistrement_sur_24_h(env):
    nvrwatch.evaluate("10.0.0.9", env["cams"], result(last=(None, "2026-10-30 09:59:58")))
    msg = open_alerts()[f"rec:{env['cams'][0]['id']}"]["message"]
    assert "plus de 24 h" in msg


def test_camera_deconnectee_apres_deux_releves(env):
    c1 = env["cams"][0]
    nvrwatch.evaluate("10.0.0.9", env["cams"], result(conn=("Unconnect", "Connected")))
    assert f"camera:{c1['id']}" not in open_alerts()                   # un seul relevé : pas d'alerte
    assert nvrwatch.online(c1["id"]) is False
    nvrwatch.evaluate("10.0.0.9", env["cams"], result(conn=("Unconnect", "Connected")))
    a = open_alerts()[f"camera:{c1['id']}"]
    assert a["kind"] == "camera" and "Unconnect" in a["message"]
    assert env["sent"][-1][1] is False                                 # fenêtre seulement, pas d'e-mail
    nvrwatch.evaluate("10.0.0.9", env["cams"], result())
    assert f"camera:{c1['id']}" not in open_alerts()


def test_nvr_injoignable_apres_deux_echecs(env):
    import requests
    err = nvrwatch.reason(requests.ConnectionError("HTTPConnectionPool(host='10.0.0.9', port=80): Max retries"))
    nvrwatch.evaluate("10.0.0.9", env["cams"], None, err)
    assert open_alerts() == {}
    nvrwatch.evaluate("10.0.0.9", env["cams"], None, err)
    a = open_alerts()
    assert set(a) == {"nvr:10.0.0.9"}                                  # pas une alerte par caméra en plus
    assert a["nvr:10.0.0.9"]["kind"] == "nvr" and env["sent"][-1][1] is True
    assert a["nvr:10.0.0.9"]["message"] == ("Pharmacie du Centre : l'enregistreur 10.0.0.9 ne répond plus sur le réseau "
                                            "(éteint, câble ou réseau coupé ?)")
    nvrwatch.evaluate("10.0.0.9", env["cams"], result())
    assert open_alerts() == {}


def test_types_envoyes_par_email():
    assert set(config.EMAIL_KINDS) == {"nvr", "rec", "hdd"}
    assert "camera" in config.POPUP_KINDS and "camera" not in config.EMAIL_KINDS


def _disk(realloc: int, pending: int = 0, serial: str = "WD-123") -> dict:
    d = storage._disk("sda", "Running", True, 4000, 1000, serial=serial)
    d["smart"] = [{"id": 5, "name": "Reallocated", "current": 100, "worst": 100, "threshold": 10, "raw": realloc, "predict": 0},
                  {"id": 197, "name": "Pending", "current": 100, "worst": 100, "threshold": 0, "raw": pending, "predict": 0}]
    return storage._health(d)


def test_smart_compteur_stable_reste_a_surveiller(env):
    d = _disk(3)
    storage._trend("10.0.0.9", d)                          # premier relevé : référence
    d = _disk(3)
    storage._trend("10.0.0.9", d)
    assert d["level"] == "warn"


def test_smart_compteur_en_hausse_disque_a_remplacer(env):
    storage._trend("10.0.0.9", _disk(3))
    d = _disk(5, pending=1)
    storage._trend("10.0.0.9", d)
    assert d["level"] == "bad" and not d["ok"]
    assert any("secteurs réalloués en hausse : 3 → 5" in i for i in d["issues"])
    assert any("secteurs en attente en hausse : 0 → 1" in i for i in d["issues"])


def test_secteurs_affiches_avec_leur_reference(env):
    first = _disk(3)
    storage._trend("10.0.0.9", first)
    assert [(c["id"], c["raw"], c["base"]) for c in first["counters"]] == [(5, 3, 3), (197, 0, 0)]
    d = _disk(5)
    storage._trend("10.0.0.9", d)
    c5 = d["counters"][0]
    assert (c5["label"], c5["raw"], c5["base"]) == ("secteurs réalloués", 5, 3) and c5["since"]
    sans_serie = _disk(2, serial="")
    storage._trend("10.0.0.9", sans_serie)                  # sans n° de série : affiché, sans référence
    assert sans_serie["counters"][0]["base"] is None and sans_serie["level"] == "warn"


def test_smart_disque_remplace_nouvelle_reference(env):
    storage._trend("10.0.0.9", _disk(40))
    d = _disk(0, serial="WD-NEUF")
    storage._trend("10.0.0.9", d)
    assert d["level"] == "ok"


def test_alerte_disque_seulement_si_a_remplacer(env, monkeypatch):
    cams = env["cams"]
    key = ("dahua", "10.0.0.9", 80, "admin", "x")
    monkeypatch.setattr(storage.security, "decrypt", lambda _: "pw")
    monkeypatch.setattr(storage, "_dahua", lambda *a: [_disk(3)])
    storage._poll(key, cams)
    assert "hdd:10.0.0.9" not in open_alerts()             # « à surveiller » : affiché, pas d'alerte
    monkeypatch.setattr(storage, "_dahua", lambda *a: [_disk(9)])
    storage._poll(key, cams)
    assert open_alerts()["hdd:10.0.0.9"]["kind"] == "hdd"


def test_rappel_de_la_fenetre():
    r = Reminder(remind_seconds=4 * 3600)
    assert r.should_show({"rec:1"}, 0)
    r.dismiss({"rec:1"}, 0)
    assert not r.should_show({"rec:1"}, 3600)               # fermée : pas avant 4 h
    assert r.should_show({"rec:1", "nvr:x"}, 3600)          # nouveau problème : tout de suite
    assert r.should_show({"rec:1"}, 4 * 3600)
    assert not r.should_show(set(), 5 * 3600)


def test_fermeture_gardee_si_les_alertes_clignotent():
    """Alerte qui disparaît puis revient peu après : la fenêtre fermée ne se rouvre pas aussitôt."""
    from app import popup
    r = Reminder(remind_seconds=4 * 3600)
    r.dismiss({"camera:1"}, 0)
    r.problems()
    assert not r.all_clear(10)                               # disparue 10 s…
    r.problems()
    assert not r.should_show({"camera:1"}, 15)               # … revenue : fenêtre toujours fermée
    assert not r.all_clear(20)
    assert r.all_clear(20 + popup.RESET_AFTER)               # rétabli pour de bon : fermeture oubliée
    assert r.should_show({"camera:1"}, 20 + popup.RESET_AFTER + 5)


def test_une_seule_fenetre(monkeypatch, tmp_path):
    from app import popup
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(popup, "_mutex", None)
    assert popup.single_instance()
    first = popup._mutex
    assert not popup.single_instance()                      # deuxième fenêtre : refusée
    first.close()


def test_fenetre_serveur_arrete(env):
    from app import popup
    import time
    from app import alerting
    db.set_setting("heartbeat", str(int(time.time()) - 900))
    rows = popup.read_state(alerting.popup_state(), time.time())
    assert rows[0]["key"] == popup.STOPPED_KEY
    assert rows[0]["kind"] not in config.EMAIL_KINDS         # « installateur prévenu » ne doit pas s'afficher
    assert popup.read_state(alerting.popup_state(), time.time(), check_heartbeat=False) == []


def test_fenetre_arret_normal(env):
    from app import popup
    import time
    from app import alerting
    db.set_setting("heartbeat", popup.CLEAN_STOP)
    with pytest.raises(popup.ServerStopped):
        popup.read_state(alerting.popup_state(), time.time())


def test_fichier_env(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("# commentaire\nCAMSUP_ESSAI_A=valeur  # note\nCAMSUP_ESSAI_B='entre guillemets'\nCAMSUP_ESSAI_C=fichier\n",
                 encoding="utf-8")
    monkeypatch.setenv("CAMSUP_ESSAI_C", "environnement")
    monkeypatch.delenv("CAMSUP_ESSAI_A", raising=False)
    monkeypatch.delenv("CAMSUP_ESSAI_B", raising=False)
    config._load_env_file(f)
    import os
    assert os.environ["CAMSUP_ESSAI_A"] == "valeur"
    assert os.environ["CAMSUP_ESSAI_B"] == "entre guillemets"
    assert os.environ["CAMSUP_ESSAI_C"] == "environnement"           # l'environnement l'emporte


def test_migration_ancienne_base(tmp_path, monkeypatch):
    """Base de l'ancienne version (clients, sites.client_id, enregistrements) : rien de perdu, sites modifiables."""
    import sqlite3
    old = tmp_path / "ancienne.db"
    con = sqlite3.connect(old)
    con.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE clients(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE sites(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, notes TEXT DEFAULT '',
                           client_id INTEGER REFERENCES clients(id));
        CREATE TABLE cameras(id INTEGER PRIMARY KEY AUTOINCREMENT,
                             site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE, name TEXT NOT NULL,
                             brand TEXT NOT NULL, host TEXT NOT NULL, port_rtsp INTEGER NOT NULL DEFAULT 554,
                             port_http INTEGER NOT NULL DEFAULT 80, username TEXT NOT NULL DEFAULT '',
                             password_enc TEXT NOT NULL DEFAULT '', channel INTEGER NOT NULL DEFAULT 1,
                             enabled INTEGER NOT NULL DEFAULT 1, created_ts INTEGER NOT NULL);
        CREATE TABLE segments(id INTEGER PRIMARY KEY, camera_id INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE);
        INSERT INTO clients VALUES(1,'Client principal');
        INSERT INTO sites(id,name,client_id) VALUES(7,'Pharmacie',1);
        INSERT INTO cameras(site_id,name,brand,host,created_ts) VALUES(7,'Caméra 1','dahua','10.0.0.9',0);
        INSERT INTO segments VALUES(1,1);
    """)
    con.close()
    real = config.DB_PATH
    monkeypatch.setattr(config, "DB_PATH", old)
    try:
        db.init()
        db.init()                                               # deuxième démarrage : rien à refaire, pas d'erreur
        tables = {r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not tables & {"clients", "segments"}
        assert db.q("SELECT id, name FROM sites") == [{"id": 7, "name": "Pharmacie"}]
        assert db.one("SELECT COUNT(*) n FROM cameras")["n"] == 1
        db.x("UPDATE sites SET name='Pharmacie du Centre' WHERE id=7")
        assert db.one("SELECT COUNT(*) n FROM pragma_foreign_key_check")["n"] == 0
    finally:
        monkeypatch.setattr(config, "DB_PATH", real)
        db.init()


def test_releve_rapide_camera_perdue_en_deux_releves(env):
    """Relevé rapide (sans recherche d'enregistrements) toutes les 10 s : 2 relevés = alerte en ≤ 20 s."""
    c1 = env["cams"][0]
    nvrwatch.evaluate("10.0.0.9", env["cams"], result())                     # relevé complet
    fast = {"time": NOW, "conn": {1: "Unconnect", 2: "Connected"}}           # pas de clé « last »
    nvrwatch.evaluate("10.0.0.9", env["cams"], fast)
    assert f"camera:{c1['id']}" not in open_alerts()
    entry = nvrwatch.evaluate("10.0.0.9", env["cams"], fast)
    assert f"camera:{c1['id']}" in open_alerts()
    assert not any(k.startswith("rec:") for k in open_alerts())             # sans recherche : rien sur l'enregistrement
    assert entry["cameras"][0]["last_record"] == "2026-10-30 09:59:58"      # dernier enregistrement connu conservé


def test_delais_de_detection():
    """Pire cas sous les 30 s demandées."""
    from app import popup
    # caméra débranchée : images arrêtées depuis CAMERA_STALL_SECONDS, vu au relevé suivant, puis lu par la fenêtre
    assert config.CAMERA_STALL_SECONDS + config.CAMERA_CHECK_SECONDS + popup.POLL_MS / 1000 <= 30
    assert config.CHECK_INTERVAL <= config.CAMERA_CHECK_SECONDS / 2           # la boucle tourne assez souvent


def test_fenetre_perimee_apres_redemarrage(env):
    from app import popup
    import time
    db.set_setting("heartbeat", str(int(time.time())))
    from app import alerting
    db.set_setting("instance", "nouveau")
    assert popup.read_state(alerting.popup_state(), time.time(), instance="nouveau") == []
    with pytest.raises(popup.ServerStopped):
        popup.read_state(alerting.popup_state(), time.time(), instance="ancien")


def test_retour_du_nvr_delai_de_grace(env, monkeypatch):
    """NVR revenu après une coupure : ses caméras se reconnectent en ≈ 1 min, pas de fausse alerte entre-temps."""
    import time
    c1, c2 = env["cams"]
    t = [1000.0]
    monkeypatch.setattr(time, "time", lambda: t[0])
    nvrwatch.evaluate("10.0.0.9", env["cams"], None, "ne répond plus")
    nvrwatch.evaluate("10.0.0.9", env["cams"], None, "ne répond plus")
    assert set(open_alerts()) == {"nvr:10.0.0.9"}
    booting = result(conn=("Unconnect", "Unconnect"), last=("2026-10-30 09:30:00", "2026-10-30 09:30:00"))
    for _ in range(3):                                   # 3 relevés pendant le redémarrage
        t[0] += 10
        nvrwatch.evaluate("10.0.0.9", env["cams"], booting)
    assert open_alerts() == {}                            # NVR rétabli, aucune alerte caméra ni enregistrement
    t[0] += 10
    nvrwatch.evaluate("10.0.0.9", env["cams"], result(conn=("Connected", "Unconnect"), last=("2026-10-30 09:59:58", "2026-10-30 09:30:00")))
    t[0] = 1000 + 20 + config.NVR_BOOT_GRACE_SECONDS      # fin du délai : la caméra 2 ne s'est pas reconnectée
    nvrwatch.evaluate("10.0.0.9", env["cams"], result(conn=("Connected", "Unconnect"), last=("2026-10-30 09:59:58", "2026-10-30 09:30:00")))
    assert set(open_alerts()) == {f"camera:{c2['id']}", f"rec:{c2['id']}"}


def test_retour_au_vert_au_premier_releve(env):
    """Caméra revenue : ses deux alertes se ferment au premier relevé qui la voit enregistrer ; un relevé partiel
    ne touche pas les autres caméras."""
    c1, c2 = env["cams"]
    nvrwatch.evaluate("10.0.0.9", env["cams"], result(last=("2026-10-30 09:30:00", "2026-10-30 09:59:58")))
    assert set(open_alerts()) == {f"camera:{c1['id']}", f"rec:{c1['id']}"}
    fast = {"time": NOW, "conn": {1: "Connected", 2: "Connected"}, "last": {1: "2026-10-30 09:59:59"}}
    entry = nvrwatch.evaluate("10.0.0.9", env["cams"], fast)
    assert open_alerts() == {}
    assert [c["last_record"] for c in entry["cameras"]] == ["2026-10-30 09:59:59", "2026-10-30 09:59:58"]


def test_releve_cherche_tous_les_canaux(env, monkeypatch):
    seen = {}
    def fake_probe(host, port, user, pw, channels=None, timeout=20):
        seen.update(channels=channels, timeout=timeout)
        return {"time": NOW, "conn": {1: "Connected", 2: "Connected"}, "last": {ch: "2026-10-30 09:59:59" for ch in channels}}
    monkeypatch.setattr(nvrwatch, "probe", fake_probe)
    monkeypatch.setattr(nvrwatch.security, "decrypt", lambda _: "pw")
    nvrwatch._poll(("dahua", "10.0.0.9", 80, "admin", "x"), env["cams"])
    assert seen == {"channels": [1, 2], "timeout": 10}
    assert open_alerts() == {}


def test_camera_debranchee_vue_par_l_arret_des_images(env):
    """Le NVR dit encore « Connected » (il met ≈ 50 s), mais la caméra 2 n'enregistre plus depuis 16 s : alerte
    dès le premier relevé, la caméra 1 étant à jour."""
    c1, c2 = env["cams"]
    entry = nvrwatch.evaluate("10.0.0.9", env["cams"], result(last=("2026-10-30 10:00:00", "2026-10-30 09:59:44")))
    a = open_alerts()
    assert set(a) == {f"camera:{c2['id']}"}
    assert "n'envoie plus d'images au NVR depuis 16 s" in a[f"camera:{c2['id']}"]["message"]
    assert env["sent"][-1][1] is False                                   # fenêtre seulement
    assert [c["state"] for c in entry["cameras"]] == ["Connected", "NoVideo"]
    assert nvrwatch.online(c2["id"]) is False
    nvrwatch.evaluate("10.0.0.9", env["cams"], result())                 # rebranchée : images à jour
    assert open_alerts() == {}


def test_retard_de_14_s_toleré(env):
    nvrwatch.evaluate("10.0.0.9", env["cams"], result(last=("2026-10-30 10:00:00", "2026-10-30 09:59:46")))
    assert open_alerts() == {}


def test_toutes_les_cameras_en_retard_pas_d_alerte_camera(env):
    """Horloge du NVR qui saute ou enregistrement arrêté partout : ce n'est pas une caméra, pas d'alerte caméra."""
    nvrwatch.evaluate("10.0.0.9", env["cams"], result(last=("2026-10-30 09:58:00", "2026-10-30 09:58:00")))
    assert open_alerts() == {}
