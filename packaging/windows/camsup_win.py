"""Point d'entrée de CamSup.exe (PyInstaller).

- `CamSup.exe --serveur` : surveillance + interface web (http://127.0.0.1:8080), lancé au démarrage du PC sous le
  compte système par la tâche planifiée « CamSup » (`installer-tache.ps1`) ; relance le serveur s'il s'arrête.
  Les données (%PROGRAMDATA%\\CamSup\\data) sont réservées au système et aux administrateurs.
- `CamSup.exe --popup` : fenêtre d'alerte, lancée à l'ouverture de chaque session Windows ; elle interroge le
  serveur local (`/api/fenetre`) et n'a pas accès aux données.
- `CamSup.exe --mot-de-passe-oublie` (menu Démarrer) : réinitialiser le mot de passe d'un compte ; demande les
  droits d'administrateur de Windows (les données de CamSup leur sont réservées).
- `CamSup.exe` (menu Démarrer, bureau) : ouvre l'interface dans le navigateur.
"""
import multiprocessing
import subprocess
import sys
import time


def supervise() -> None:
    """Serveur dans un processus enfant, relancé s'il s'arrête (attente croissante, sans boucler)."""
    from app import config, run
    run.log_to_file("superviseur")
    delay = 5
    while True:
        started = time.time()
        code = subprocess.call([sys.executable, "--serveur-processus"], cwd=config.APP_DIR)
        delay = 5 if time.time() - started > 600 else min(delay * 2, 300)
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} serveur arrêté (code {code}), relance dans {delay} s", flush=True)
        time.sleep(delay)


def open_ui() -> None:
    from app import run
    if run.open_interface(wait=30):
        return
    import tkinter as tk
    from tkinter import messagebox
    tk.Tk().withdraw()
    messagebox.showerror("CamSup", "CamSup ne répond pas sur ce poste.\n\nRedémarrez l'ordinateur ; si le problème "
                         "continue, prévenez votre installateur.")


def forgot_password() -> None:
    """Fenêtre « mot de passe oublié », relancée avec les droits d'administrateur si besoin."""
    import ctypes
    import tkinter as tk
    from tkinter import messagebox, ttk
    if not ctypes.windll.shell32.IsUserAnAdmin():
        # demande de droits (UAC) : c'est elle qui autorise la réinitialisation
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "--mot-de-passe-oublie", None, 1)
        return
    from app import recovery
    root = tk.Tk()
    root.title("CamSup — mot de passe oublié")
    root.resizable(False, False)
    try:
        rows = recovery.users()
    except Exception as e:  # noqa: BLE001
        messagebox.showerror("CamSup", f"Base de CamSup illisible : {e}")
        return
    if not rows:
        messagebox.showinfo("CamSup", "Aucun compte : ouvrez CamSup pour créer le vôtre.")
        return
    f = ttk.Frame(root, padding=16)
    f.pack()
    ttk.Label(f, text="Compte").grid(row=0, column=0, sticky="w", pady=4)
    who = ttk.Combobox(f, values=[r["username"] for r in rows], state="readonly", width=30)
    who.current(0)
    who.grid(row=0, column=1, pady=4)
    ttk.Label(f, text="Nouveau mot de passe (10 caractères min.)").grid(row=1, column=0, sticky="w", pady=4)
    p1 = ttk.Entry(f, show="•", width=32)
    p1.grid(row=1, column=1, pady=4)
    ttk.Label(f, text="Confirmation").grid(row=2, column=0, sticky="w", pady=4)
    p2 = ttk.Entry(f, show="•", width=32)
    p2.grid(row=2, column=1, pady=4)

    def ok() -> None:
        if p1.get() != p2.get():
            messagebox.showerror("CamSup", "Les deux mots de passe sont différents")
            return
        try:
            recovery.reset(who.get(), p1.get())
        except ValueError as e:
            messagebox.showerror("CamSup", str(e))
            return
        messagebox.showinfo("CamSup", f"Mot de passe de « {who.get()} » changé : connectez-vous avec le nouveau.")
        root.destroy()

    ttk.Button(f, text="Changer le mot de passe", command=ok).grid(row=3, column=1, sticky="e", pady=(12, 0))
    root.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--serveur":
        supervise()
    elif arg == "--serveur-processus":
        from app import run
        run.main(popups=False, browser=False)
    elif arg == "--mot-de-passe-oublie":
        forgot_password()
    elif arg == "--popup":
        from app import popup, run
        run.log_to_file("fenetre-alerte", run.user_log_dir())
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        popup.main()
    else:
        open_ui()
