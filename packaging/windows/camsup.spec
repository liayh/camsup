# Recette PyInstaller de CamSup.exe (dossier dist/CamSup) : `pyinstaller packaging/windows/camsup.spec`
# depuis la racine du projet, après `cd frontend && npm ci && npm run build` (interface dans app/static/app).
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent.parent
if not (ROOT / "app/static/app/index.html").is_file():
    raise SystemExit("Interface non compilée : cd frontend && npm ci && npm run build")

a = Analysis(
    [str(ROOT / "packaging/windows/camsup_win.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "app/static/app"), "app/static/app")],
    hiddenimports=collect_submodules("uvicorn") + collect_submodules("app"),
    excludes=["pytest", "httpx"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="CamSup", console=False,
          icon=str(ROOT / "packaging/windows/camsup.ico") if (ROOT / "packaging/windows/camsup.ico").is_file() else None)
coll = COLLECT(exe, a.binaries, a.datas, name="CamSup")
