"""Caméras en direct (demandé le 05/11/2026) : flux secondaire du NVR (petite résolution prévue pour le direct) lu en
RTSP par ffmpeg et renvoyé au navigateur en MJPEG (une suite d'images JPEG : s'affiche dans une simple balise <img>,
dans tous les navigateurs, sans greffon).

- Un seul ffmpeg par caméra, partagé par tous ceux qui la regardent ; arrêté `IDLE_SECONDS` après le dernier
  spectateur (le NVR ne garde pas de connexion inutile) ; `MAX_STREAMS` caméras à la fois au plus.
- Adresse RTSP Dahua : rtsp://hôte:554/cam/realmonitor?channel=N&subtype=1 (flux secondaire). Le mot de passe passe
  à ffmpeg dans l'URL : jamais journalisé (`security.scrub`).
- Non vérifié sur le NVR réel au moment de l'écriture (NVR injoignable le 05/11/2026) : port RTSP 554 supposé ouvert.
"""
import asyncio
import logging
import subprocess
import threading
import time
from urllib.parse import quote

from . import config, security

log = logging.getLogger("live")
RTSP_PORT = 554
FPS = 5                         # images par seconde envoyées : suffisant pour voir, léger pour le PC et le NVR
WIDTH = 640                     # largeur des images (le flux secondaire fait souvent 704 ou 640 px)
IDLE_SECONDS = 20
MAX_STREAMS = 16
SOI, EOI = b"\xff\xd8", b"\xff\xd9"


def rtsp_url(cam: dict, pw: str) -> str:
    user = quote(cam.get("username") or "", safe="")
    return (f"rtsp://{user}:{quote(pw, safe='')}@{cam['host']}:{RTSP_PORT}/cam/realmonitor"
            f"?channel={int(cam.get('channel') or 1)}&subtype=1")


def source_args(url: str) -> list[str]:
    """Entrée de ffmpeg : flux RTSP du NVR (TCP, délai de 10 s) ; sinon une mire générée (essais sans NVR)."""
    if url.startswith("rtsp://"):
        return ["-rtsp_transport", "tcp", "-timeout", "10000000", "-i", url]
    return ["-re", "-f", "lavfi", "-i", url]


def split_jpegs(buf: bytearray) -> list[bytes]:
    """Retire de `buf` les images JPEG complètes (SOI … EOI) et les renvoie ; garde le reste pour la suite."""
    out = []
    while True:
        a = buf.find(SOI)
        if a < 0:
            buf.clear()
            return out
        b = buf.find(EOI, a + 2)
        if b < 0:
            del buf[:a]
            return out
        out.append(bytes(buf[a:b + 2]))
        del buf[:b + 2]


class Stream:
    """Un ffmpeg qui lit une caméra ; garde la dernière image."""

    def __init__(self, cam: dict, pw: str):
        self.cam_id = cam["id"]
        self.frame: bytes | None = None
        self.seq = 0
        self.error = ""
        self.viewers = 0
        self.last_seen = time.time()
        self.lock = threading.Lock()
        cmd = [config.FFMPEG, "-nostdin", "-loglevel", "error", *source_args(rtsp_url(cam, pw)), "-an",
               "-vf", f"fps={FPS},scale={WIDTH}:-2", "-q:v", "7", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            self.proc = None
            self.error = f"programme vidéo introuvable ({config.FFMPEG})"
            return
        threading.Thread(target=self._read, daemon=True, name=f"live-{self.cam_id}").start()

    def _read(self) -> None:
        buf = bytearray()
        while True:
            chunk = self.proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 8_000_000:                  # flux incohérent : on repart de zéro
                buf.clear()
            for jpg in split_jpegs(buf):
                with self.lock:
                    self.frame, self.seq = jpg, self.seq + 1
        err = (self.proc.stderr.read() or b"").decode(errors="replace")
        self.error = "flux interrompu" + (f" : {security.scrub(err.strip().splitlines()[-1])[:200]}" if err.strip() else "")
        log.warning("Direct caméra %s : %s", self.cam_id, self.error)

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


_streams: dict[int, Stream] = {}
_lock = threading.Lock()


def acquire(cam: dict, pw: str) -> Stream:
    """Flux de la caméra (démarré si besoin) ; `RuntimeError` si trop de caméras en direct à la fois."""
    with _lock:
        s = _streams.get(cam["id"])
        if s is None or not s.alive:
            if s is not None:
                s.stop()
            if sum(1 for x in _streams.values() if x.alive) >= MAX_STREAMS:
                raise RuntimeError(f"{MAX_STREAMS} caméras en direct au plus à la fois")
            s = _streams[cam["id"]] = Stream(cam, pw)
        s.viewers += 1
        s.last_seen = time.time()
        return s


def latest(cam: dict, pw: str) -> Stream:
    """Flux de la caméra pour le mur (dernière image demandée plusieurs fois par seconde) : démarré si besoin, gardé
    tant que des images sont demandées (`IDLE_SECONDS`), sans compter de spectateur permanent."""
    s = acquire(cam, pw)
    release(s)
    return s


def release(s: Stream) -> None:
    with _lock:
        s.viewers = max(0, s.viewers - 1)
        s.last_seen = time.time()


def reap() -> None:
    """Arrête les flux sans spectateur depuis `IDLE_SECONDS` (appelé par la surveillance)."""
    with _lock:
        for cid, s in list(_streams.items()):
            if (s.viewers == 0 and time.time() - s.last_seen > IDLE_SECONDS) or not s.alive and s.viewers == 0:
                s.stop()
                _streams.pop(cid, None)


def stop_all() -> None:
    with _lock:
        for s in _streams.values():
            s.stop()
        _streams.clear()


async def mjpeg(s: Stream, max_seconds: float = 3600):
    """Images au format multipart/x-mixed-replace (MJPEG) tant que le navigateur regarde (1 h au plus)."""
    seq, end = -1, time.time() + max_seconds
    try:
        while time.time() < end:
            with s.lock:
                frame, n = s.frame, s.seq
            if frame is not None and n != seq:
                seq = n
                yield (b"--image\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(frame)).encode()
                       + b"\r\n\r\n" + frame + b"\r\n")
            elif not s.alive and (frame is None or n == seq):
                return
            await asyncio.sleep(1 / (FPS * 2))
    finally:
        release(s)
