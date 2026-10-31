"""Point d'entrée de CamSup.exe (PyInstaller).

- `CamSup.exe --serveur` : surveillance + interface web (http://localhost:8080), lancé au démarrage du PC sous le
  compte système par la tâche planifiée « CamSup » (`installer-tache.ps1`) ; relance le serveur s'il s'arrête.
  Les données (%PROGRAMDATA%\\CamSup\\data) sont réservées au système et aux administrateurs.
- `CamSup.exe --popup` : fenêtre d'alerte, lancée à l'ouverture de chaque session Windows ; elle interroge le
  serveur local (`/api/fenetre`) et n'a pas accès aux données.
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


if __name__ == "__main__":
    multiprocessing.freeze_support()
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--serveur":
        supervise()
    elif arg == "--serveur-processus":
        from app import run
        run.main(popups=False, browser=False)
    elif arg == "--popup":
        from app import popup, run
        run.log_to_file("fenetre-alerte", run.user_log_dir())
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        popup.main()
    else:
        open_ui()
