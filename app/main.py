"""API FastAPI + interface web de CamSup (alertes de vidéosurveillance).

Accès : `accounts.current_user` sur toutes les routes /api. Les ressources d'un site non attribué répondent 404
(cloisonnement entre pharmacies). Les modifications sont tracées dans le journal d'audit.
"""
import logging
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from . import accounts, alerting, cameras, config, dahuarpc, db, export, mail, monitor, nvrwatch, preview, security, storage
from .accounts import User

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("camsup")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    export.recover()
    preview.purge(everything=True)
    if accounts.bootstrap(security.ADMIN_PASSWORD):
        if security.GENERATED_PASSWORD:
            # jamais dans le journal (fichier lisible sur le poste) : seulement sur la console qui a lancé CamSup
            log.warning("ADMIN_PASSWORD non défini : mot de passe généré pour '%s', affiché sur la console",
                        config.ADMIN_USER)
            if not config.FROZEN and sys.__stderr__:
                print(f"Mot de passe de '{config.ADMIN_USER}' : {security.ADMIN_PASSWORD}", file=sys.__stderr__)
        elif config.FROZEN:
            config.forget_admin_password()     # saisi à l'installation : inutile (et à ne pas laisser) une fois le compte créé
    monitor.start()
    yield
    monitor.stop()
    dahuarpc.close_all()
    db.set_setting("heartbeat", "arret")       # arrêt normal : la fenêtre d'alerte se ferme sans alerter


app = FastAPI(title="CamSup", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.url.scheme == "https":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    return resp


# adresse d'enregistreur : nom ou IP seulement (ni « @ », ni « / », ni « ? » qui détourneraient l'URL construite)
HOST_RE = r"^\s*[A-Za-z0-9.\-\[\]:]+\s*$"

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(accounts.current_user)])
admin = APIRouter(prefix="/api", dependencies=[Depends(accounts.integrator)])
Me = Depends(accounts.current_user)


# ------------------------------------------------------------------ modèles
class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    notes: str = ""


class CameraIn(BaseModel):
    site_id: int
    name: str = Field(min_length=1, max_length=120)
    brand: Literal["hikvision", "dahua"]
    host: str = Field(min_length=1, max_length=200, pattern=HOST_RE)     # adresse de l'enregistreur
    port_http: int = Field(80, ge=1, le=65535)
    username: str = ""
    password: Optional[str] = None          # None = inchangé en modification
    channel: int = Field(1, ge=1, le=256)
    enabled: bool = True


def _cam(user: User, cid: int) -> dict:
    """Caméra visible par l'utilisateur, sinon 404."""
    cam = db.one("SELECT * FROM cameras WHERE id=?", (cid,))
    if not cam or not user.can_site(cam["site_id"]):
        raise HTTPException(404, "Caméra introuvable")
    return cam


# ------------------------------------------------------------------ connexion
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


@public.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    if request.headers.get("X-CamSup") != "1":
        raise HTTPException(403, "En-tête X-CamSup manquant")
    ip = accounts.client_ip(request)
    user = accounts.authenticate(body.username, body.password, ip)
    if not user:
        raise HTTPException(401, "Identifiant ou mot de passe incorrect")
    token = accounts.open_session(user, ip)
    response.set_cookie(accounts.COOKIE, token, max_age=config.SESSION_HOURS * 3600, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https")
    return user.public()


@public.get("/session")
def session(request: Request):
    """Utilisateur connecté, ou null : vérification au chargement de l'interface sans provoquer d'erreur 401."""
    user = accounts.session_user(request.cookies.get(accounts.COOKIE))
    return user.public() if user else None


@router.post("/logout")
def logout(request: Request, response: Response):
    if request.cookies.get(accounts.COOKIE):
        accounts.close_session(request.cookies[accounts.COOKIE])
    accounts.audit_req(request, "deconnexion")
    response.delete_cookie(accounts.COOKIE)
    return {"ok": True}


@router.get("/me")
def me(user: User = Me):
    return user.public()


class PasswordIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


@router.post("/me/password")
def change_password(body: PasswordIn, request: Request, user: User = Me):
    row = db.one("SELECT pw_hash FROM users WHERE id=?", (user.id,))
    if not accounts.verify_password(body.current, row["pw_hash"]):
        raise HTTPException(400, "Mot de passe actuel incorrect")
    accounts.check_password_strength(body.new)
    db.x("UPDATE users SET pw_hash=? WHERE id=?", (accounts.hash_password(body.new), user.id))
    # les autres sessions (autre navigateur, ancien mot de passe connu d'un tiers) sont fermées ; celle-ci reste
    token = request.cookies.get(accounts.COOKIE) or ""
    db.x("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user.id, accounts.token_hash(token)))
    accounts.audit_req(request, "mot_de_passe.change", obj=user.username)
    return {"ok": True}


# ------------------------------------------------------------------ comptes (intégrateur)
class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9_.@-]+$")
    role: Literal["integrator", "pharmacist"]
    password: Optional[str] = Field(None, max_length=200)   # None = inchangé en modification
    site_ids: list[int] = []                # rôle pharmacist : ses sites
    active: bool = True


def _user_sites(uid: int, role: str, site_ids: list[int]) -> None:
    db.x("DELETE FROM user_sites WHERE user_id=?", (uid,))
    if role == "pharmacist":
        for sid in set(site_ids):
            if db.one("SELECT id FROM sites WHERE id=?", (sid,)):
                db.x("INSERT INTO user_sites(user_id,site_id) VALUES(?,?)", (uid, sid))


@admin.get("/users")
def list_users():
    users = db.q("SELECT id,username,role,active,created_ts,last_login_ts FROM users ORDER BY username")
    for u in users:
        u["site_ids"] = [r["site_id"] for r in db.q("SELECT site_id FROM user_sites WHERE user_id=?", (u["id"],))]
    return users


@admin.post("/users")
def create_user(u: UserIn, request: Request):
    if not u.password:
        raise HTTPException(400, "Mot de passe requis")
    accounts.check_password_strength(u.password)
    if db.one("SELECT id FROM users WHERE username=?", (u.username,)):
        raise HTTPException(409, "Ce nom d'utilisateur existe déjà")
    uid = db.x("INSERT INTO users(username,pw_hash,role,active,created_ts) VALUES(?,?,?,?,?)",
               (u.username, accounts.hash_password(u.password), u.role, int(u.active), db.now()))
    _user_sites(uid, u.role, u.site_ids)
    accounts.audit_req(request, "compte.cree", obj=u.username, detail=f"rôle {u.role}, sites {sorted(u.site_ids)}")
    return {"id": uid}


@admin.put("/users/{uid}")
def update_user(uid: int, u: UserIn, request: Request):
    me_ = request.state.user
    if not db.one("SELECT id FROM users WHERE id=?", (uid,)):
        raise HTTPException(404, "Compte introuvable")
    if uid == me_.id and (u.role != "integrator" or not u.active):
        raise HTTPException(400, "Vous ne pouvez pas retirer vos propres droits")
    if db.one("SELECT id FROM users WHERE username=? AND id<>?", (u.username, uid)):
        raise HTTPException(409, "Ce nom d'utilisateur existe déjà")
    if u.password:
        accounts.check_password_strength(u.password)
    db.x("UPDATE users SET username=?, role=?, active=? WHERE id=?", (u.username, u.role, int(u.active), uid))
    if u.password:
        db.x("UPDATE users SET pw_hash=? WHERE id=?", (accounts.hash_password(u.password), uid))
    if not u.active or u.password:
        db.x("DELETE FROM sessions WHERE user_id=?", (uid,))       # déconnecte le compte
    _user_sites(uid, u.role, u.site_ids)
    accounts.audit_req(request, "compte.modifie", obj=u.username,
                       detail=f"rôle {u.role}, actif {u.active}, sites {sorted(u.site_ids)}"
                              + (", mot de passe réinitialisé" if u.password else ""))
    return {"ok": True}


@admin.delete("/users/{uid}")
def delete_user(uid: int, request: Request):
    if uid == request.state.user.id:
        raise HTTPException(400, "Vous ne pouvez pas supprimer votre propre compte")
    u = db.one("SELECT username FROM users WHERE id=?", (uid,))
    if not u:
        raise HTTPException(404, "Compte introuvable")
    db.x("DELETE FROM users WHERE id=?", (uid,))
    accounts.audit_req(request, "compte.supprime", obj=u["username"])
    return {"ok": True}


# ------------------------------------------------------------------ journal d'audit
@router.get("/audit")
def list_audit(user: User = Me, site_id: Optional[int] = None, action: str = "", from_ts: int = 0,
               to_ts: int = 4102444800, limit: int = 300):
    """Intégrateur : tout le journal. Pharmacien : uniquement les lignes de ses sites."""
    sql = "SELECT a.*, s.name AS site FROM audit a LEFT JOIN sites s ON s.id=a.site_id WHERE a.ts>=? AND a.ts<=?"
    args: list = [from_ts, to_ts]
    frag, fargs = accounts.site_filter(user, "a.site_id")
    sql += frag
    args += fargs
    if site_id:
        sql += " AND a.site_id=?"
        args.append(site_id)
    if action:
        sql += " AND a.action LIKE ?"
        args.append(action + "%")
    return db.q(sql + " ORDER BY a.id DESC LIMIT ?", (*args, min(limit, 2000)))


# ------------------------------------------------------------------ état
@router.get("/overview")
def overview(user: User = Me):
    frag, fargs = accounts.site_filter(user, "c.site_id")
    cams = db.q(f"""SELECT c.*, s.online, s.last_check, s.error FROM cameras c LEFT JOIN status s ON s.camera_id=c.id
                    WHERE 1=1{frag} ORDER BY c.site_id, c.channel, c.name""", fargs)
    sfrag, sargs = accounts.site_filter(user, "id")
    return {
        "me": user.public(),
        "sites": db.q(f"SELECT * FROM sites WHERE 1=1{sfrag} ORDER BY name", sargs),
        "cameras": [cameras.public(c) for c in cams],
        "alerts": _alerts(user, False, 200),
        "recorders": [r for r in storage.snapshot() if user.can_site(r["site_id"])],
        "nvr": [n for n in nvrwatch.snapshot() if user.can_site(n["site_id"])],
        "email": mail.configured(),
        "export_max_minutes": config.EXPORT_MAX_MINUTES,
        "now": db.now(),
    }


# ------------------------------------------------------------------ sites
@router.get("/sites")
def list_sites(user: User = Me):
    frag, args = accounts.site_filter(user, "s.id")
    return db.q(f"""SELECT s.*, (SELECT COUNT(*) FROM cameras c WHERE c.site_id=s.id) AS cameras FROM sites s
                    WHERE 1=1{frag} ORDER BY s.name""", args)


@admin.post("/sites")
def create_site(s: SiteIn, request: Request):
    sid = db.x("INSERT INTO sites(name,notes) VALUES(?,?)", (s.name, s.notes))
    accounts.audit_req(request, "site.cree", sid, s.name)
    return {"id": sid}


@admin.put("/sites/{sid}")
def update_site(sid: int, s: SiteIn, request: Request):
    db.x("UPDATE sites SET name=?, notes=? WHERE id=?", (s.name, s.notes, sid))
    accounts.audit_req(request, "site.modifie", sid, s.name)
    return {"ok": True}


@admin.delete("/sites/{sid}")
def delete_site(sid: int, request: Request):
    if db.one("SELECT id FROM cameras WHERE site_id=?", (sid,)):
        raise HTTPException(409, "Supprimer d'abord les caméras du site")
    db.x("DELETE FROM sites WHERE id=?", (sid,))
    accounts.audit_req(request, "site.supprime", sid)
    return {"ok": True}


# ------------------------------------------------------------------ caméras (intégrateur)
def _cam_values(c: CameraIn) -> list:
    return [c.site_id, c.name, c.brand, c.host.strip(), c.port_http, c.username, c.channel, int(c.enabled)]


@admin.post("/cameras")
def create_camera(c: CameraIn, request: Request):
    if not db.one("SELECT id FROM sites WHERE id=?", (c.site_id,)):
        raise HTTPException(400, "Site inconnu")
    cid = db.x("""INSERT INTO cameras(site_id,name,brand,host,port_http,username,channel,enabled,password_enc,created_ts)
                  VALUES(?,?,?,?,?,?,?,?,?,?)""", (*_cam_values(c), security.encrypt(c.password or ""), db.now()))
    accounts.audit_req(request, "camera.creee", c.site_id, f"#{cid} {c.name}")
    return {"id": cid}


@admin.put("/cameras/{cid}")
def update_camera(cid: int, c: CameraIn, request: Request):
    old = _cam(request.state.user, cid)
    if not db.one("SELECT id FROM sites WHERE id=?", (c.site_id,)):
        raise HTTPException(400, "Site inconnu")
    db.x("""UPDATE cameras SET site_id=?,name=?,brand=?,host=?,port_http=?,username=?,channel=?,enabled=?
            WHERE id=?""", (*_cam_values(c), cid))
    if c.password is not None:
        db.x("UPDATE cameras SET password_enc=? WHERE id=?", (security.encrypt(c.password), cid))
    accounts.audit_req(request, "camera.modifiee", c.site_id, f"#{cid} {c.name}",
                       f"active {bool(old['enabled'])}→{c.enabled}" if bool(old["enabled"]) != c.enabled else "")
    return {"ok": True}


@admin.delete("/cameras/{cid}")
def delete_camera(cid: int, request: Request):
    cam = _cam(request.state.user, cid)
    db.x("DELETE FROM cameras WHERE id=?", (cid,))
    for k in (f"camera:{cid}", f"rec:{cid}", f"offline:{cid}"):   # ses alertes ouvertes n'ont plus d'objet
        alerting.clear_alert(k)
    accounts.audit_req(request, "camera.supprimee", cam["site_id"], f"#{cid} {cam['name']}")
    return {"ok": True, "name": cam["name"]}


class NvrIn(BaseModel):
    host: str = Field(min_length=1, max_length=200, pattern=HOST_RE)
    port_http: int = Field(80, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(max_length=200)


class NvrChannel(BaseModel):
    channel: int = Field(ge=1, le=256)
    name: str = Field(min_length=1, max_length=120)


class NvrAdd(NvrIn):
    site_id: int
    channels: list[NvrChannel] = Field(min_length=1, max_length=256)
    password: Optional[str] = Field(None, max_length=200)   # None : identifiants déjà enregistrés pour ce NVR


class NvrHost(BaseModel):
    host: str = Field(min_length=1, max_length=200)


def _nvr_cams(user: User, host: str) -> list[dict]:
    """Caméras Dahua déjà enregistrées pour ce NVR (dans le périmètre de l'utilisateur), sinon 404."""
    cams = [c for c in db.q("SELECT * FROM cameras WHERE host=? AND brand='dahua' ORDER BY channel", (host.strip(),))
            if user.can_site(c["site_id"])]
    if not cams:
        raise HTTPException(404, "Enregistreur inconnu")
    return cams


def _discover(host: str, port: int, username: str, password: str) -> dict:
    try:
        found = nvrwatch.discover(host, port, username, password)
    except Exception as e:  # noqa: BLE001
        log.warning("Découverte NVR %s : %s", host, security.scrub(str(e))[:300])
        raise HTTPException(400, f"L'enregistreur {host} {nvrwatch.reason(e)}")
    known = {r["channel"] for r in db.q("SELECT channel FROM cameras WHERE host=? AND brand='dahua'", (host,))}
    for c in found["channels"]:
        c["known"] = c["channel"] in known
    return found


@admin.post("/nvr/discover")
def discover_nvr(body: NvrIn):
    """Nouvel enregistreur (Dahua) : connexion avec ses identifiants et liste de ses caméras, rien n'est enregistré."""
    return _discover(body.host.strip(), body.port_http, body.username, body.password)


@admin.post("/nvr/rescan")
def rescan_nvr(body: NvrHost, request: Request):
    """NVR déjà enregistré : liste ses caméras avec les identifiants gardés (nouvelles caméras branchées depuis)."""
    c = _nvr_cams(request.state.user, body.host)[0]
    found = _discover(c["host"], c["port_http"], c["username"], security.decrypt(c["password_enc"]))
    return {**found, "host": c["host"], "port_http": c["port_http"], "username": c["username"], "site_id": c["site_id"]}


class NvrCredentials(NvrHost):
    port_http: int = Field(80, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=100)
    password: Optional[str] = Field(None, max_length=200)   # None = inchangé


@admin.put("/nvr/credentials")
def nvr_credentials(body: NvrCredentials, request: Request):
    """Identifiants du NVR changés une fois pour toutes ses caméras ; vérifiés sur le NVR avant d'être enregistrés."""
    cams = _nvr_cams(request.state.user, body.host)
    pw = body.password if body.password is not None else security.decrypt(cams[0]["password_enc"])
    _discover(cams[0]["host"], body.port_http, body.username, pw)          # refusé : rien n'est modifié
    enc = security.encrypt(pw)
    for c in cams:
        db.x("UPDATE cameras SET port_http=?, username=?, password_enc=? WHERE id=?", (body.port_http, body.username, enc, c["id"]))
    accounts.audit_req(request, "nvr.identifiants", cams[0]["site_id"], cams[0]["host"],
                       f"{len(cams)} caméra(s), utilisateur {body.username}" + (", mot de passe changé" if body.password else ""))
    return {"updated": len(cams)}


@admin.post("/nvr")
def add_nvr(body: NvrAdd, request: Request):
    """Ajoute les caméras choisies d'un enregistreur Dahua (un canal = une caméra) ; les canaux déjà présents sont ignorés."""
    if not db.one("SELECT id FROM sites WHERE id=?", (body.site_id,)):
        raise HTTPException(400, "Site inconnu")
    host = body.host.strip()
    if body.password is None:                                  # NVR déjà enregistré : mêmes identifiants
        enc = _nvr_cams(request.state.user, host)[0]["password_enc"]
    else:
        enc = security.encrypt(body.password)
    known = {r["channel"] for r in db.q("SELECT channel FROM cameras WHERE host=? AND brand='dahua'", (host,))}
    added = []
    for c in body.channels:
        if c.channel in known:
            continue
        added.append(db.x("""INSERT INTO cameras(site_id,name,brand,host,port_http,username,channel,enabled,password_enc,created_ts)
                             VALUES(?,?,'dahua',?,?,?,?,1,?,?)""", (body.site_id, c.name.strip(), host, body.port_http,
                                                                    body.username, c.channel, enc, db.now())))
        known.add(c.channel)
    accounts.audit_req(request, "nvr.ajoute", body.site_id, host, f"{len(added)} caméra(s)")
    return {"added": len(added)}


@admin.post("/cameras/{cid}/test")
def test_camera(cid: int, request: Request):
    """Interroge l'enregistreur tout de suite : Dahua → caméra vue et dernier enregistrement ; Hikvision → disques."""
    cam = _cam(request.state.user, cid)
    try:
        pw = security.decrypt(cam["password_enc"])
        if cam["brand"] == "dahua":
            r = nvrwatch.probe(cam["host"], cam["port_http"], cam["username"], pw, [cam["channel"]])
            return {"ok": True, "nvr_time": r["time"], "state": r["conn"].get(cam["channel"], "absent"),
                    "last_record": r["last"].get(cam["channel"])}
        disks = storage._hik(cam["host"], cam["port_http"], cam["username"], pw)
        return {"ok": True, "disks": len(disks)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": security.scrub(str(e))[:300]}


# ------------------------------------------------------------------ extraits vidéo (forces de l'ordre)
class ExportIn(BaseModel):
    site_id: int
    camera_ids: list[int] = Field(min_length=1, max_length=64)
    start_ts: int
    end_ts: int
    reason: str = Field("", max_length=200)


def _export(user: User, eid: int) -> dict:
    row = db.one("SELECT * FROM exports WHERE id=?", (eid,))
    if not row or not user.can_site(row["site_id"]):
        raise HTTPException(404, "Extrait introuvable")
    return row


@router.get("/exports/{eid}")
def get_export(eid: int, user: User = Me):
    row = _export(user, eid)
    return {k: row[k] for k in ("id", "site_id", "username", "cameras", "start_ts", "end_ts", "reason", "state",
                                "progress", "error", "size", "sha256", "created_ts", "done_ts")}


@router.post("/exports")
def create_export(body: ExportIn, request: Request, user: User = Me):
    accounts.require_site(user, body.site_id)
    cams = [_cam(user, cid) for cid in dict.fromkeys(body.camera_ids)]
    if any(c["site_id"] != body.site_id for c in cams):
        raise HTTPException(400, "Caméras d'un autre site")
    if any(c["brand"] != "dahua" for c in cams):
        raise HTTPException(400, "Extraction vidéo possible seulement depuis un NVR Dahua")
    try:
        eid = export.create(user, body.site_id, cams, body.start_ts, body.end_ts, body.reason.strip())
    except export.ExportError as e:
        raise HTTPException(400, str(e))
    accounts.audit_req(request, "video.extraction", body.site_id, f"#{eid}",
                       f"{', '.join(c['name'] for c in cams)} ; {body.start_ts}→{body.end_ts} ; {body.reason[:100]}")
    return {"id": eid}


@router.get("/exports/{eid}/fichier")
def download_export(eid: int, request: Request, user: User = Me):
    row = _export(user, eid)
    if row["state"] != "pret" or not export.file(eid).is_file():
        raise HTTPException(409, "Extrait pas prêt")
    site = db.one("SELECT name FROM sites WHERE id=?", (row["site_id"],))
    start = datetime.fromtimestamp(row["start_ts"])
    name = re.sub(r"[^\w\-]", "_", f"Video_{site['name'] if site else row['site_id']}_{start:%Y-%m-%d_%Hh%M}")
    accounts.audit_req(request, "video.telechargement", row["site_id"], f"#{eid}")
    return FileResponse(export.file(eid), media_type="application/zip", filename=f"{name}.zip")


class PreviewIn(BaseModel):
    camera_id: int
    start_ts: int
    mode: Literal["copie", "conversion"] = "conversion"


def _preview(user: User, key: str) -> dict:
    job = preview.get(key)
    if not job or not user.can_site(job["site_id"]):
        raise HTTPException(404, "Aperçu introuvable")
    return job


@router.post("/previews")
def create_preview(body: PreviewIn, request: Request, user: User = Me):
    """Voir les images enregistrées avant d'extraire (fenêtre de quelques minutes, convertie pour le navigateur)."""
    cam = _cam(user, body.camera_id)
    if cam["brand"] != "dahua":
        raise HTTPException(400, "Images visibles seulement depuis un NVR Dahua")
    try:
        job = preview.request(cam, body.start_ts, body.mode)
    except export.ExportError as e:
        raise HTTPException(400, str(e))
    accounts.audit_req(request, "video.apercu", cam["site_id"], cam["name"], str(job["start_ts"]))
    return job


@router.get("/previews/{key}")
def get_preview(key: str, user: User = Me):
    return preview.public(_preview(user, key))


@router.get("/previews/{key}/video")
def preview_video(key: str, user: User = Me):
    job = _preview(user, key)
    if job["state"] != "pret" or not preview.path(key).is_file():
        raise HTTPException(409, "Aperçu pas prêt")
    return FileResponse(preview.path(key), media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})


# ------------------------------------------------------------------ alertes
def _alerts(user: User, include_closed: bool, limit: int) -> list[dict]:
    """Alertes visibles : celles des caméras de ses sites ; les alertes sans caméra pour l'intégrateur."""
    where = "WHERE 1=1" + ("" if include_closed else " AND a.closed_ts IS NULL")
    args: tuple = ()
    if not user.is_integrator:
        frag, args = accounts.site_filter(user, "c.site_id")
        where += " AND a.camera_id IS NOT NULL" + frag
    return db.q(f"SELECT a.* FROM alerts a LEFT JOIN cameras c ON c.id=a.camera_id {where} "
                "ORDER BY a.opened_ts DESC LIMIT ?", (*args, min(limit, 500)))


@router.get("/alerts")
def list_alerts(user: User = Me, include_closed: bool = False, limit: int = 100):
    return _alerts(user, include_closed, limit)


# ------------------------------------------------------------------ e-mail à l'installateur (intégrateur)
class EmailIn(BaseModel):
    host: str = Field(max_length=200)
    port: int = Field(587, ge=1, le=65535)
    security: Literal["starttls", "ssl", "aucune"] = "starttls"
    user: str = Field("", max_length=200)
    password: Optional[str] = Field(None, max_length=500)   # None = inchangé, "" = effacé
    sender: str = Field("", max_length=200)
    to: str = Field("", max_length=1000)                    # une ou plusieurs adresses (virgule, point-virgule)


@admin.get("/settings/email")
def get_email():
    return mail.public(mail.current())


@admin.put("/settings/email")
def put_email(body: EmailIn, request: Request):
    try:
        to = mail.recipients(body.to)
        if body.sender:
            mail.recipients(body.sender)
    except ValueError as e:
        raise HTTPException(400, str(e))
    mail.save(body.host, body.port, body.security, body.user, body.password, body.sender, to)
    accounts.audit_req(request, "email.reglages", detail=f"{body.host}:{body.port} {body.security} → {', '.join(to)}"
                       + (", mot de passe changé" if body.password is not None else ""))
    return mail.public(mail.current())


@admin.post("/notify/test")
def notify_test(request: Request):
    """Envoi immédiat, pour vérifier les réglages : l'erreur exacte est renvoyée."""
    if not mail.configured():
        raise HTTPException(409, "E-mail non configuré : serveur d'envoi et destinataire")
    try:
        mail.send("[CamSup] Test d'envoi", "Ceci est un e-mail de test de CamSup.\n\nSi vous le recevez, les alertes du "
                  "NVR (enregistreur injoignable, enregistrement arrêté, disque à remplacer) vous parviendront.\n")
    except Exception as e:  # noqa: BLE001
        mail._record(False, security.scrub(str(e)))
        raise HTTPException(502, f"Échec de l'envoi : {security.scrub(str(e))}")
    mail._record(True)
    accounts.audit_req(request, "email.test")
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True}


# ------------------------------------------------------------------ interface (React, `cd frontend && npm run build`)
SPA = STATIC / "app"
SPA_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
           "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
_SPA_ROUTES = {"video", "sites", "cameras", "enregistreurs", "utilisateurs", "parametres"}


def _spa() -> Response:
    if not (SPA / "index.html").is_file():
        return HTMLResponse("Interface non compilée : <code>cd frontend &amp;&amp; npm run build</code>", 503)
    return HTMLResponse((SPA / "index.html").read_text(encoding="utf-8"),
                        headers={"Content-Security-Policy": SPA_CSP, "Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def index():
    """Page publique : elle ne contient aucune donnée, tout passe par l'API authentifiée."""
    return _spa()


@app.get("/assets/{name}")
def spa_asset(name: str):
    f = (SPA / "assets" / name).resolve()
    if f.parent != (SPA / "assets").resolve() or not f.is_file():
        raise HTTPException(404)
    # noms de fichiers versionnés par Vite (empreinte) : cache long sans risque
    return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"})


app.include_router(public)
app.include_router(router)
app.include_router(admin)


@app.get("/extraction")
def old_extraction_page():
    """Ancienne adresse de « Voir la vidéo » (renommée le 30/10/2026) : un onglet resté ouvert y revient."""
    return RedirectResponse("/video", 308)


@app.get("/{page}", response_class=HTMLResponse)
def spa_page(page: str):
    """Rechargement direct d'une page de l'application : même document, le routeur affiche la page."""
    if page not in _SPA_ROUTES:
        raise HTTPException(404)
    return _spa()
