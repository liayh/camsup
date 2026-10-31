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


def open_interface(wait: float = 0) -> bool:
    """Ouvre l'interface dans le navigateur si le serveur répond (attendu jusqu'à `wait` s, juste après l'installation)."""
    end = time.time() + wait
    while not server_running():
        if time.time() >= end:
            return False
        time.sleep(1)
    webbrowser.open(f"{'https' if config.HTTPS else 'http'}://localhost:{config.PORT}/")
    return True


def main(popups: bool | None = None, browser: bool = True) -> None:
    """Serveur. `popups=False` : sans fenêtre d'alerte (programme Windows : service sous le compte système, la fenêtre
    est lancée dans chaque session) ; `browser=False` : déjà lancé → rien (pas de navigateur à ouvrir)."""
    log_to_file("camsup")
    if server_running():
        if browser:
            open_interface()
        return
    popup = PopupProcess() if (config.POPUPS if popups is None else popups) else None
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
    uvicorn.run(app, host=config.HOST, port=config.PORT, workers=1, proxy_headers=False,
                timeout_graceful_shutdown=10, access_log=not config.FROZEN, **kw)   # poste Windows : pas de ligne par requête


if __name__ == "__main__":
    main()
