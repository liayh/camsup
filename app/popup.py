"""Fenêtre d'alerte sur le poste (pharmacie) : `python -m app.popup`, lancée par `app.run` si POPUPS=1 (développement),
ou `CamSup.exe --popup` à l'ouverture de chaque session Windows (le serveur tourne alors sous le compte système).

Processus séparé qui interroge le serveur de ce PC toutes les 5 s (`GET /api/fenetre`, réservé à 127.0.0.1) ; il ne
lit pas la base, réservée au compte qui fait tourner le serveur :
- alertes ouvertes de type `config.POPUP_KINDS` → fenêtre au premier plan avec la liste des problèmes ;
- fermée par l'utilisateur → rouverte après `POPUP_REMIND_HOURS` h, ou tout de suite si un nouveau problème apparaît ;
- tout est rétabli → court message « rétabli » (fermé seul après 1 min) ;
- serveur injoignable, ou surveillance muette (battement de cœur), depuis 5 min → fenêtre « surveillance arrêtée ».
Tkinter : fourni avec Python sous Windows, aucune dépendance en plus.
"""
import logging
import os
import sys
import time
from dataclasses import dataclass, field

import requests
import urllib3

from . import config, fenetre

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)     # développement : certificat auto-signé local

log = logging.getLogger("popup")
POLL_MS = 5_000                            # caméra perdue : relevée en ≤ 20 s, affichée en ≤ 25 s
HEARTBEAT_STALE = 300
STOPPED_KEY = "_surveillance"              # pseudo-alerte : le serveur ne tourne plus
CLEAN_STOP = "arret"                       # battement de cœur laissé par un arrêt normal du serveur


class ServerStopped(Exception):
    """Le serveur s'est arrêté normalement, ou un autre démarrage l'a remplacé : la fenêtre s'arrête aussi."""


@dataclass
class Reminder:
    """Décide quand afficher la fenêtre (sans interface : testable)."""
    remind_seconds: float
    dismissed_keys: set = field(default_factory=set)
    dismissed_at: float | None = None

    def should_show(self, keys: set, now: float) -> bool:
        if not keys:
            return False
        if self.dismissed_at is None:
            return True
        return bool(keys - self.dismissed_keys) or now - self.dismissed_at >= self.remind_seconds

    def dismiss(self, keys: set, now: float) -> None:
        self.dismissed_keys, self.dismissed_at = set(keys), now

    def reset(self) -> None:
        self.dismissed_keys, self.dismissed_at = set(), None


def stopped_row(since: float) -> dict:
    # type à part : aucun e-mail ne peut partir d'un serveur arrêté, la fenêtre ne doit pas dire le contraire
    return {"key": STOPPED_KEY, "kind": "surveillance", "opened_ts": int(since),
            "message": "La surveillance CamSup ne fonctionne plus : prévenez votre installateur"}


def fetch(timeout: float = 5) -> dict:
    """État lu sur le serveur de ce PC et vérifié (signature sur un défi neuf) ; `requests.RequestException` s'il ne
    répond pas, `ValueError` si ce n'est pas le vrai serveur."""
    challenge = fenetre.new_challenge()
    url = f"{'https' if config.HTTPS else 'http'}://127.0.0.1:{config.PORT}/api/fenetre"
    with requests.Session() as s:
        s.trust_env = False                # jamais par le proxy du poste (127.0.0.1 doit rester sur ce PC)
        r = s.get(url, params={"defi": challenge}, timeout=timeout, verify=False)   # dev : certificat auto-signé
    r.raise_for_status()
    return fenetre.verify(r.json(), challenge)


def read_state(data: dict, now: float, check_heartbeat: bool = True, instance: str = "") -> list[dict]:
    """Problèmes à afficher : alertes ouvertes, ou l'arrêt de la surveillance elle-même.
    `instance` : démarrage du serveur qui a lancé cette fenêtre ; un autre = fenêtre périmée."""
    hb, owner = data.get("heartbeat") or "", data.get("instance") or ""
    if hb == CLEAN_STOP or (instance and owner and owner != instance):
        raise ServerStopped
    rows = list(data.get("alerts") or [])
    if check_heartbeat and hb and now - float(hb) > HEARTBEAT_STALE:
        rows.insert(0, stopped_row(float(hb)))                # serveur en vie, boucle de surveillance arrêtée
    return rows


def _center(w) -> None:
    w.update_idletasks()
    x = (w.winfo_screenwidth() - w.winfo_reqwidth()) // 2
    y = (w.winfo_screenheight() - w.winfo_reqheight()) // 3
    w.geometry(f"+{x}+{y}")


class App:
    def __init__(self, instance: str = "") -> None:
        import tkinter as tk
        self.instance = instance
        self.tk = tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.win = None
        self.info = None
        self.rem = Reminder(config.POPUP_REMIND_HOURS * 3600)
        self.shown_keys: set = set()
        self.had_alerts = False
        self.started = self.last_ok = time.time()            # 5 min laissées au serveur pour démarrer
        self.email = False
        self.root.after(2000, self.tick)

    def tick(self) -> None:
        now = time.time()
        try:
            data = fetch()
            self.last_ok, self.email = now, bool(data.get("email"))
            # au démarrage, le battement de cœur date de l'arrêt précédent : on laisse 5 min au serveur
            rows = read_state(data, now, now - self.started > HEARTBEAT_STALE, self.instance)
        except ServerStopped:
            log.info("Serveur arrêté ou redémarré : fin de cette fenêtre d'alerte")
            self.root.quit()
            return
        except (requests.RequestException, ValueError) as e:    # serveur injoignable (arrêté, en redémarrage)
            if now - self.last_ok <= HEARTBEAT_STALE:
                log.warning("Serveur CamSup injoignable : %s", type(e).__name__)
                self.root.after(POLL_MS, self.tick)
                return
            rows = [stopped_row(self.last_ok)]
        keys = {r["key"] for r in rows}
        if rows:
            self.had_alerts = True
            if self.win is not None:          # fenêtre déjà ouverte : contenu mis à jour
                new = keys - self.shown_keys
                self.fill(rows)
                if new:
                    self.win.lift()
                    self.win.bell()
            elif self.rem.should_show(keys, now):
                self.show(rows)
        else:
            self.close_alert()
            self.rem.reset()
            if self.had_alerts:
                self.had_alerts = False
                self.notice("Vidéosurveillance rétablie", "Tous les problèmes signalés sont résolus.")
        self.root.after(POLL_MS, self.tick)

    def show(self, rows: list[dict]) -> None:
        tk = self.tk
        self.win = tk.Toplevel(self.root)
        self.win.title("CamSup — Alerte vidéosurveillance")
        self.win.attributes("-topmost", True)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.dismiss)
        tk.Label(self.win, text="⚠  Problème de vidéosurveillance", bg="#b00020", fg="white",
                 font=("Segoe UI", 14, "bold"), padx=16, pady=10, anchor="w").pack(fill="x")
        self.body = tk.Frame(self.win, padx=16, pady=10)
        self.body.pack(fill="both")
        self.foot = tk.Label(self.win, justify="left", wraplength=520, padx=16, fg="#444", font=("Segoe UI", 9))
        self.foot.pack(fill="x")
        tk.Button(self.win, text=f"Fermer (rappel dans {config.POPUP_REMIND_HOURS:g} h)", command=self.dismiss,
                  padx=12, pady=4).pack(pady=12)
        self.fill(rows)
        _center(self.win)
        self.win.lift()
        self.win.focus_force()
        self.win.bell()

    def fill(self, rows: list[dict]) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        for r in rows:
            since = time.strftime("%d/%m %H:%M", time.localtime(r["opened_ts"]))
            self.tk.Label(self.body, text=f"•  {r['message']}   (depuis le {since})", anchor="w", justify="left",
                          wraplength=520, font=("Segoe UI", 11)).pack(fill="x", pady=2)
        emailed = any(r["kind"] in config.EMAIL_KINDS for r in rows) and self.email
        self.foot.config(text=("Votre installateur a été prévenu par e-mail. " if emailed else "")
                         + "Cette fenêtre reviendra tant que le problème n'est pas résolu.")
        self.shown_keys = {r["key"] for r in rows}

    def dismiss(self) -> None:
        self.rem.dismiss(self.shown_keys, time.time())
        self.close_alert()

    def close_alert(self) -> None:
        if self.win is not None:
            self.win.destroy()
            self.win = None

    def notice(self, title: str, text: str) -> None:
        tk = self.tk
        if self.info is not None:
            self.info.destroy()
        self.info = w = tk.Toplevel(self.root)
        w.title(f"CamSup — {title}")
        w.attributes("-topmost", True)
        tk.Label(w, text=f"✔  {title}", bg="#1b7a3a", fg="white", font=("Segoe UI", 13, "bold"),
                 padx=16, pady=8, anchor="w").pack(fill="x")
        tk.Label(w, text=text, padx=16, pady=10, font=("Segoe UI", 11)).pack()
        tk.Button(w, text="OK", command=w.destroy, padx=12).pack(pady=(0, 12))
        _center(w)
        w.after(60_000, lambda: w.winfo_exists() and w.destroy())


_mutex = None


def single_instance() -> bool:
    """Une seule fenêtre d'alerte par session Windows (démarrage de session + lancement après installation)."""
    global _mutex
    if os.name != "nt":
        return True
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _mutex = kernel32.CreateMutexW(None, False, "Local\\CamSup-fenetre-alerte")
    return ctypes.get_last_error() != 183                     # ERROR_ALREADY_EXISTS


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s popup %(levelname)s %(message)s")
    if not single_instance():
        return
    App(sys.argv[1] if len(sys.argv) > 1 else "").root.mainloop()


if __name__ == "__main__":
    main()
