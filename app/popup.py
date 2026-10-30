"""Fenêtre d'alerte sur le poste (pharmacie) : `python -m app.popup`, lancée par `app.run` si POPUPS=1.

Processus séparé qui lit la base en lecture seule toutes les 5 s :
- alertes ouvertes de type `config.POPUP_KINDS` → fenêtre au premier plan avec la liste des problèmes ;
- fermée par l'utilisateur → rouverte après `POPUP_REMIND_HOURS` h, ou tout de suite si un nouveau problème apparaît ;
- tout est rétabli → court message « rétabli » (fermé seul après 1 min) ;
- serveur muet depuis 5 min (battement de cœur `settings.heartbeat`) → fenêtre « surveillance arrêtée ».
Tkinter : fourni avec Python sous Windows, aucune dépendance en plus.
"""
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass, field

from . import config, mail

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


def read_state(now: float, check_heartbeat: bool = True, instance: str = "") -> list[dict]:
    """Problèmes à afficher : alertes ouvertes, ou l'arrêt de la surveillance elle-même.
    `instance` : démarrage du serveur qui a lancé cette fenêtre ; un autre en base = fenêtre périmée."""
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        kinds = config.POPUP_KINDS + ("offline",)
        rows = [dict(r) for r in con.execute(
            f"SELECT key, kind, message, opened_ts FROM alerts WHERE closed_ts IS NULL AND kind IN "
            f"({','.join('?' * len(kinds))}) ORDER BY opened_ts", kinds)]
        hb = con.execute("SELECT value FROM settings WHERE key='heartbeat'").fetchone()
        owner = con.execute("SELECT value FROM settings WHERE key='instance'").fetchone()
    finally:
        con.close()
    if (hb and hb[0] == CLEAN_STOP) or (instance and owner and owner[0] != instance):
        raise ServerStopped
    if check_heartbeat and hb and now - float(hb[0]) > HEARTBEAT_STALE:
        # type à part : aucun e-mail ne peut partir d'un serveur arrêté, la fenêtre ne doit pas dire le contraire
        rows.insert(0, {"key": STOPPED_KEY, "kind": "surveillance", "opened_ts": int(float(hb[0])),
                        "message": "La surveillance CamSup ne fonctionne plus : prévenez votre installateur"})
    return rows


def email_on() -> bool:
    """L'installateur est-il prévenu par e-mail ? (réglages de Paramètres, sinon fichier .env)"""
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT value FROM settings WHERE key='email'").fetchone()
    finally:
        con.close()
    return mail.configured(mail.load(row[0] if row else None))


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
        self.started = time.time()
        self.root.after(2000, self.tick)

    def tick(self) -> None:
        now = time.time()
        try:
            # au démarrage, le battement de cœur en base date de l'arrêt précédent : on laisse 5 min au serveur
            rows = read_state(now, now - self.started > HEARTBEAT_STALE, self.instance)
        except ServerStopped:
            log.info("Serveur arrêté ou redémarré : fin de cette fenêtre d'alerte")
            self.root.quit()
            return
        except sqlite3.Error as e:            # base momentanément verrouillée : on réessaie au tour suivant
            log.warning("Lecture de la base impossible : %s", e)
            self.root.after(POLL_MS, self.tick)
            return
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
        emailed = any(r["kind"] in config.EMAIL_KINDS for r in rows) and email_on()
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s popup %(levelname)s %(message)s")
    App(sys.argv[1] if len(sys.argv) > 1 else "").root.mainloop()


if __name__ == "__main__":
    main()
