"""Lancement de CamSup : `python -m app.run` (ou `CamSup.exe`, cf. `packaging/windows`).

HTTPS par défaut. Sans TLS_CERT/TLS_KEY, un certificat auto-signé est généré dans DATA_DIR/tls/ au premier
démarrage (le navigateur affichera un avertissement à accepter une fois ; pour un vrai certificat, fournir
TLS_CERT/TLS_KEY ou placer CamSup derrière un reverse proxy et mettre HTTPS=0).
Un seul worker : la boucle de surveillance vit dans le process. La fenêtre d'alerte (`app.popup`) est un processus à part.
"""
import datetime
import ipaddress
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from . import config

log = logging.getLogger("camsup")


def _local_ips() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))                       # aucune donnée envoyée : sert à trouver l'IP locale
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ips)


def ensure_self_signed() -> tuple[Path, Path]:
    d = config.DATA_DIR / "tls"
    cert, key = d / "cert.pem", d / "key.pem"
    if cert.is_file() and key.is_file():
        return cert, key
    d.mkdir(parents=True, exist_ok=True)
    k = ec.generate_private_key(ec.SECP256R1())
    host = socket.gethostname()
    names = [x509.DNSName("localhost"), x509.DNSName(host)] + [x509.IPAddress(ipaddress.ip_address(i))
                                                                for i in _local_ips()]
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"CamSup {host}")])
    now = datetime.datetime.now(datetime.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
         .not_valid_after(now + datetime.timedelta(days=825))
         .add_extension(x509.SubjectAlternativeName(names), critical=False)
         .sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    key.chmod(0o600)
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return cert, key


class PopupProcess:
    """Fenêtre d'alerte (`app.popup`) dans un processus à part, relancée si elle s'arrête."""

    def __init__(self) -> None:
        self.instance = secrets.token_hex(8)
        self.proc: subprocess.Popen | None = None
        self.stop = threading.Event()

    def _run(self) -> None:
        delay = 5
        while not self.stop.is_set():
            started = time.time()
            cmd = [sys.executable, "--popup", self.instance] if config.FROZEN else [sys.executable, "-m", "app.popup", self.instance]
            self.proc = subprocess.Popen(cmd, cwd=config.APP_DIR)
            self.proc.wait()
            if self.stop.is_set():
                return
            # arrêt rapide = pas d'écran (service, session fermée…) : attente croissante, sans boucler
            delay = 5 if time.time() - started > 600 else min(delay * 2, 600)
            log.warning("Fenêtre d'alerte arrêtée (code %s), relance dans %d s", self.proc.returncode, delay)
            self.stop.wait(delay)

    def start(self) -> None:
        threading.Thread(target=self._run, name="popup", daemon=True).start()

    def close(self) -> None:
        self.stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()      # arrêt normal : la fenêtre ne doit pas annoncer « surveillance arrêtée »


def log_to_file(name: str, folder: Path | None = None) -> None:
    """Programme Windows sans console : sorties vers `<folder>/<name>.log` (`data/logs` par défaut ; gardé sous
    10 Mo, une archive)."""
    if sys.stderr is not None:
        return
    folder = folder or config.LOG_DIR
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{name}.log"
    if f.is_file() and f.stat().st_size > 10_000_000:
        f.replace(f.with_suffix(".log.1"))
    sys.stdout = sys.stderr = open(f, "a", encoding="utf-8", buffering=1)


def user_log_dir() -> Path:
    """Journaux de la fenêtre d'alerte (session de l'utilisateur, qui n'a pas accès au dossier de données)."""
    return Path(os.getenv("LOCALAPPDATA") or Path.home()) / "CamSup" / "logs"


def server_running() -> bool:
    """CamSup répond-il sur ce poste (port occupé) ?"""
    host = "127.0.0.1" if config.HOST in ("0.0.0.0", "") else config.HOST
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((host, config.PORT)) == 0


def genuine() -> bool:
    """Le programme qui répond sur le port est-il le vrai serveur CamSup (réponse signée sur un défi neuf, comme pour
    la fenêtre d'alerte) ? Programme Windows seulement : pendant une relance, un autre programme du poste pourrait
    écouter sur ce port et présenter une fausse page de connexion."""
    if not config.FROZEN:
        return True
    import requests
    from . import fenetre
    challenge = fenetre.new_challenge()
    try:
        with requests.Session() as s:
            s.trust_env = False
            r = s.get(f"{'https' if config.HTTPS else 'http'}://127.0.0.1:{config.PORT}/api/fenetre",
                      params={"defi": challenge}, timeout=5, verify=False)
        fenetre.verify(r.json(), challenge)
        return True
    except (requests.RequestException, ValueError) as e:
        log.warning("Serveur sur le port %s non reconnu : %s", config.PORT, e)
        return False


def open_interface(wait: float = 0) -> bool:
    """Ouvre l'interface dans le navigateur si le serveur répond (attendu jusqu'à `wait` s, juste après l'installation)."""
    end = time.time() + wait
    while not (server_running() and genuine()):
        if time.time() >= end:
            return False
        time.sleep(1)
    # 127.0.0.1 plutôt que « localhost » : le navigateur pourrait essayer d'abord ::1, où un autre programme du poste
    # pourrait écouter et recevoir le cookie de session
    url = f"{'https' if config.HTTPS else 'http'}://127.0.0.1:{config.PORT}/"
    if not app_window(url):
        webbrowser.open(url)
    return True


def app_browser() -> str | None:
    """Navigateur capable du mode « application » (fenêtre seule, sans barre d'adresse ni onglets) : Edge, présent sur
    tout Windows 10/11, sinon Chrome / Chromium."""
    import shutil
    if os.name == "nt":
        # Program Files seulement (modifiable par les administrateurs) : pas %LOCALAPPDATA%, où un programme de la
        # session pourrait poser un faux navigateur
        for base in (os.getenv("ProgramFiles(x86)"), os.getenv("ProgramFiles")):
            for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                if base and Path(base, rel).is_file():
                    return str(Path(base, rel))
        return None
    for name in ("microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def app_window(url: str) -> bool:
    """CamSup dans sa propre fenêtre, comme un logiciel (mode application du navigateur, profil à part pour qu'elle
    ait sa place dans la barre des tâches) ; False si aucun navigateur adapté (repli : navigateur par défaut)."""
    exe = app_browser()
    if not exe:
        return False
    profile = user_log_dir().parent / "fenetre"
    try:
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1440,900",
                          "--no-first-run", "--no-default-browser-check", "--disable-features=Translate",
                          "--disable-extensions"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=os.name != "nt")
    except OSError as e:
        log.warning("Fenêtre CamSup : %s", e)
        return False
    return True


def main(popups: bool | None = None, browser: bool = True) -> None:
    """Serveur. `popups=False` : sans fenêtre d'alerte (programme Windows : service sous le compte système, la fenêtre
    est lancée dans chaque session) ; `browser=False` : déjà lancé → rien (pas de navigateur à ouvrir)."""
    log_to_file("camsup")
    if server_running():
        if browser:
            open_interface()
            return
        # service : le port est pris par un autre programme ; on ne s'arrête pas sans bruit (code ≠ 0, relancé)
        log.error("Port %s déjà occupé par un autre programme : CamSup ne peut pas démarrer", config.PORT)
        sys.exit(3)
    popup = PopupProcess() if (config.POPUPS if popups is None else popups) else None
    if browser and not config.FROZEN:
        # lancement à la main (développement, Linux) : la fenêtre CamSup s'ouvre dès que le serveur répond
        threading.Thread(target=open_interface, kwargs={"wait": 60}, daemon=True).start()
    if popup:
        from . import db
        db.init()                             # efface l'« arrêt » laissé par l'arrêt précédent avant que la fenêtre le lise
        db.set_setting("heartbeat", str(int(time.time())))
        db.set_setting("instance", popup.instance)    # une fenêtre d'un démarrage précédent se fermera d'elle-même
        popup.start()
    try:
        _serve()
    finally:
        if popup:
            popup.close()


def _serve() -> None:
    kw = {}
    if config.HTTPS:
        cert, key = (Path(config.TLS_CERT), Path(config.TLS_KEY)) if config.TLS_CERT else ensure_self_signed()
        kw = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    # timeout_graceful_shutdown : sans lui, un flux vidéo ouvert (WebSocket) bloque l'arrêt indéfiniment
    from .main import app                      # objet direct : fonctionne aussi dans le programme empaqueté
    opts = dict(host=config.HOST, port=config.PORT, workers=1, proxy_headers=False, timeout_graceful_shutdown=10,
                access_log=not config.FROZEN, **kw)                       # poste Windows : pas de ligne par requête
    if config.HOST not in ("127.0.0.1", "localhost"):
        uvicorn.run(app, **opts)
        return
    socks = local_sockets(config.PORT)
    uvicorn.Server(uvicorn.Config(app, **opts)).run(sockets=socks)


def local_sockets(port: int) -> list[socket.socket]:
    """Écoute sur 127.0.0.1 et ::1, en exclusif sous Windows (SO_EXCLUSIVEADDRUSE) : aucun autre programme du poste
    ne peut écouter sur le même port en même temps (ni sur ::1, que le navigateur essaie parfois pour « localhost »)."""
    socks = []
    for family, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            s = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue                                   # IPv6 absent
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        try:
            s.bind((addr, port))
        except OSError as e:
            s.close()
            if family == socket.AF_INET:
                log.error("Port %s déjà occupé (%s) : CamSup ne peut pas démarrer", port, e)
                sys.exit(3)
            log.error("Port %s sur ::1 occupé par un autre programme (%s) : ouvrir http://127.0.0.1:%s", port, e, port)
            continue
        s.listen(2048)
        s.set_inheritable(True)
        socks.append(s)
    return socks


if __name__ == "__main__":
    main()
