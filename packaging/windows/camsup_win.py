"""Point d'entrée de CamSup.exe (PyInstaller).

`CamSup.exe` : serveur de surveillance + interface web (http://localhost:8080) + fenêtre d'alerte.
`CamSup.exe --popup <jeton>` : la fenêtre d'alerte elle-même, relancée par le serveur (processus à part).
Lancé une deuxième fois (raccourci du menu Démarrer), il ouvre seulement l'interface dans le navigateur.
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--popup":
        from app import popup, run
        run.log_to_file("fenetre-alerte")
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        popup.main()
    else:
        from app import run
        run.main()
