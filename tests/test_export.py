"""Extrait vidéo pour les forces de l'ordre : conversion de la période à l'heure du NVR, ZIP + fiche, droits."""
import hashlib
import subprocess
import time
import zipfile
from datetime import datetime
from urllib.parse import quote

import pytest

from app import config, db, export
from test_accounts import H, client, env  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(subprocess.run(["which", config.FFMPEG], capture_output=True).returncode,
                                reason="ffmpeg absent")


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    """2 s de mire en H.264 (MPEG-TS), à la place de la vidéo d'origine renvoyée par le NVR."""
    p = tmp_path_factory.mktemp("nvr") / "nvr.ts"
    subprocess.run([config.FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
                    "-c:v", "libx264", "-f", "mpegts", str(p)], check=True)
    return p


class Now:
    """Exécute l'extraction tout de suite (au lieu de la file d'attente)."""
    @staticmethod
    def submit(fn, *a):
        fn(*a)


@pytest.fixture()
def nvr(monkeypatch, video):
    calls = []

    def download(cam, pw, start, end, dest):
        calls.append((cam["name"], start, end))
        if cam["name"] == "sans enregistrement":
            raise export.ExportError("aucun enregistrement sur cette période")
        dest.write_bytes(video.read_bytes())

    monkeypatch.setattr(export, "_pool", Now)
    monkeypatch.setattr(export, "_download", download)
    monkeypatch.setattr(export, "_offset", lambda cam, pw: 3600)       # horloge du NVR en avance d'une heure
    return calls


def _ask(c, site, cams, minutes=10):
    start = int(time.time()) - 3600
    return c.post("/api/exports", json={"site_id": site, "camera_ids": cams, "start_ts": start,
                                         "end_ts": start + minutes * 60, "reason": "plainte 123"}, headers=H)


def test_extrait_complet(env, nvr):
    c = client("pharma", "motdepasse-pharma")
    r = _ask(c, env["a"], [env["cam_a"]])
    assert r.status_code == 200, r.text
    job = c.get(f"/api/exports/{r.json()['id']}").json()
    assert job["state"] == "pret" and not job["error"] and job["cameras"] == "cam A"
    # période convertie à l'heure du NVR (+1 h)
    _, start, end = nvr[0]
    assert start == datetime.fromtimestamp(job["start_ts"] + 3600) and (end - start).total_seconds() == 600
    f = c.get(f"/api/exports/{job['id']}/fichier")
    assert f.status_code == 200 and hashlib.sha256(f.content).hexdigest() == job["sha256"]
    path = export.DIR / "test.zip"
    path.write_bytes(f.content)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        readme = z.read("LISEZ-MOI.txt").decode()
        mp4 = z.read("01_cam A.mp4")
    assert names == ["LISEZ-MOI.txt", "01_cam A.mp4"]
    assert "Pharmacie A" in readme and "plainte 123" in readme and "en avance de 1 h 00 min 0 s" in readme
    assert hashlib.sha256(mp4).hexdigest() in readme and mp4[4:8] == b"ftyp"
    assert "- 01_cam A.mp4 : cam A, canal 1 du NVR 10.0.0.1, " in readme
    assert "motdepasse" not in readme
    actions = [a["action"] for a in db.q("SELECT action FROM audit")]
    assert "video.extraction" in actions and "video.telechargement" in actions
    assert not list(export.DIR.glob("*.tmp"))                           # fichiers de travail effacés


def test_camera_sans_enregistrement(env, nvr):
    other = db.x("INSERT INTO cameras(site_id,name,brand,host,channel,created_ts) VALUES(?, 'sans enregistrement', "
                 "'dahua','10.0.0.1',2,0)", (env["a"],))
    c = client("admin", config.ADMIN_PASSWORD)
    job = c.get(f"/api/exports/{_ask(c, env['a'], [env['cam_a'], other]).json()['id']}").json()
    assert job["state"] == "pret" and job["error"].startswith("incomplet")
    job = c.get(f"/api/exports/{_ask(c, env['a'], [other]).json()['id']}").json()
    assert job["state"] == "erreur" and "aucun enregistrement" in job["error"]


def test_controles_et_cloisonnement(env, nvr):
    c = client("pharma", "motdepasse-pharma")
    assert _ask(c, env["b"], [env["cam_b"]]).status_code == 404                     # pharmacie d'un autre
    assert _ask(c, env["a"], [env["cam_b"]]).status_code == 404
    assert _ask(c, env["a"], [env["cam_a"]], minutes=config.EXPORT_MAX_MINUTES + 1).status_code == 400
    future = int(time.time()) + 600
    assert c.post("/api/exports", json={"site_id": env["a"], "camera_ids": [env["cam_a"]], "start_ts": future,
                                        "end_ts": future + 60}, headers=H).status_code == 400
    db.x("UPDATE cameras SET brand='hikvision' WHERE id=?", (env["cam_a"],))
    assert _ask(c, env["a"], [env["cam_a"]]).status_code == 400
    assert not nvr
    # extrait d'une autre pharmacie : invisible
    admin = client("admin", config.ADMIN_PASSWORD)
    eid = _ask(admin, env["b"], [env["cam_b"]]).json()["id"]
    assert admin.get(f"/api/exports/{eid}").json()["state"] == "pret"
    assert c.get(f"/api/exports/{eid}").status_code == 404
    assert c.get(f"/api/exports/{eid}/fichier").status_code == 404


def test_reprise_et_purge(env, nvr):
    c = client("admin", config.ADMIN_PASSWORD)
    eid = _ask(c, env["a"], [env["cam_a"]]).json()["id"]
    db.x("UPDATE exports SET state='en_cours' WHERE id=?", (eid,))
    assert c.get(f"/api/exports/{eid}/fichier").status_code == 409        # pas prêt
    export.recover()                                                    # CamSup redémarré pendant l'extraction
    assert db.one("SELECT state FROM exports WHERE id=?", (eid,))["state"] == "erreur"
    db.x("UPDATE exports SET created_ts=? WHERE id=?", (int(time.time()) - 3600, eid))
    export.purge()                                                      # moins d'un jour : gardé
    assert db.one("SELECT id FROM exports WHERE id=?", (eid,))
    db.x("UPDATE exports SET created_ts=? WHERE id=?", (int(time.time()) - config.EXPORT_RETENTION_DAYS * 86400 - 1, eid))
    export.purge()
    assert not db.one("SELECT id FROM exports WHERE id=?", (eid,)) and not export.file(eid).exists()


def test_apercu(env, nvr, monkeypatch):
    from app import preview
    monkeypatch.setattr(preview, "_pool", Now)
    preview.purge(everything=True)
    c = client("pharma", "motdepasse-pharma")
    start = int(time.time()) - 3600
    r = c.post("/api/previews", json={"camera_id": env["cam_a"], "start_ts": start}, headers=H)
    job = r.json()
    assert r.status_code == 200 and job["start_ts"] == start - start % 60 and job["end_ts"] - job["start_ts"] == 300
    assert c.get(f"/api/previews/{job['id']}").json()["state"] == "pret"
    # fenêtre convertie à l'heure du NVR (+1 h), vidéo H.264 lisible par le navigateur, lecture par morceaux
    assert nvr[0][1] == datetime.fromtimestamp(job["start_ts"] + 3600)
    v = c.get(f"/api/previews/{job['id']}/video", headers={"Range": "bytes=0-99"})
    assert v.status_code == 206 and len(v.content) == 100 and v.content[4:8] == b"ftyp"
    codec = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                            str(preview.path(job["id"]))], capture_output=True, text=True).stdout.strip()
    assert codec == "h264"
    # même fenêtre redemandée : réutilisée
    assert c.post("/api/previews", json={"camera_id": env["cam_a"], "start_ts": start}, headers=H).json()["id"] == job["id"]
    assert len(nvr) == 1
    # autre pharmacie, futur, Hikvision
    assert c.post("/api/previews", json={"camera_id": env["cam_b"], "start_ts": start}, headers=H).status_code == 404
    other = client("admin", config.ADMIN_PASSWORD)
    jb = other.post("/api/previews", json={"camera_id": env["cam_b"], "start_ts": start}, headers=H).json()
    assert c.get(f"/api/previews/{jb['id']}").status_code == 404
    assert c.get(f"/api/previews/{jb['id']}/video").status_code == 404
    assert c.post("/api/previews", json={"camera_id": env["cam_a"], "start_ts": int(time.time()) + 600},
                  headers=H).status_code == 400
    assert "video.apercu" in [a["action"] for a in db.q("SELECT action FROM audit")]
    preview.purge(everything=True)
    assert not preview.path(job["id"]).exists() and c.get(f"/api/previews/{job['id']}").status_code == 404


def test_apercu_copie_sans_conversion(env, nvr, monkeypatch):
    """Navigateur qui lit la vidéo d'origine : simple copie, même codec que le NVR (ici H.264 de la mire)."""
    from app import preview
    monkeypatch.setattr(preview, "_pool", Now)
    c = client("pharma", "motdepasse-pharma")
    start = int(time.time()) - 3600
    job = c.post("/api/previews", json={"camera_id": env["cam_a"], "start_ts": start, "mode": "copie"}, headers=H).json()
    assert job["state"] == "pret" and job["mode"] == "copie" and job["id"].endswith("-copie")
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width", "-of", "csv=p=0",
                          str(preview.path(job["id"]))], capture_output=True, text=True).stdout.strip()
    assert out == "h264,320"                                            # pas réduite à 960 px : copiée telle quelle
    assert c.post("/api/previews", json={"camera_id": env["cam_a"], "start_ts": start, "mode": "autre"},
                  headers=H).status_code == 422
    assert c.get("/api/overview").json()["export_max_minutes"] == config.EXPORT_MAX_MINUTES == 15
