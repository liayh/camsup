#!/bin/sh
# Fabrique l'installateur Windows depuis Linux, avec Docker (aucun PC Windows nécessaire) :
#   sh packaging/windows/build-docker.sh [version]      → dist/CamSup-Installation-<version>.exe
# Images publiques utilisées : tobix/pywine:3.12 (Python Windows sous Wine, PyInstaller) et amake/innosetup (Inno Setup 6).
# ffmpeg : build « essentials » de gyan.dev, empreinte SHA-256 vérifiée. Interface : compilée avant (npm run build).
set -e
VERSION=${1:-1.0.0}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$(mktemp -d)
trap 'docker run --rm -v "$WORK":/w alpine:3 rm -rf /w/src >/dev/null 2>&1; rm -rf "$WORK"' EXIT
[ -f "$ROOT/app/static/app/index.html" ] || { echo "Interface non compilée : cd frontend && npm ci && npm run build"; exit 1; }

echo "1/4 copie propre du projet (sans données, réglages ni environnements)"
mkdir -p "$WORK/src"
tar -C "$ROOT" --exclude=./data --exclude=./.env --exclude=./.venv --exclude=./.venv-build --exclude=./frontend/node_modules \
    --exclude=./dist --exclude=./build --exclude='__pycache__' --exclude='*.secret' -cf - . | tar -C "$WORK/src" -xf -

echo "2/4 ffmpeg"
FF="$ROOT/packaging/windows/ffmpeg"
if [ ! -f "$FF/ffmpeg.exe" ]; then
    URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
    curl -sSL -o "$WORK/ff.zip" "$URL"
    [ "$(curl -sSL "$URL.sha256" | cut -c1-64)" = "$(sha256sum "$WORK/ff.zip" | cut -c1-64)" ] || { echo "empreinte ffmpeg incorrecte"; exit 1; }
    mkdir -p "$FF"
    python3 - "$WORK/ff.zip" "$FF" <<'PY'
import shutil, sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
for suffix, out in (("/bin/ffmpeg.exe", "ffmpeg.exe"), ("/LICENSE", "LICENSE-ffmpeg.txt")):
    name = next(n for n in z.namelist() if n.endswith(suffix))
    with z.open(name) as src, open(f"{sys.argv[2]}/{out}", "wb") as dst:
        shutil.copyfileobj(src, dst)
PY
fi
mkdir -p "$WORK/src/packaging/windows/ffmpeg" && cp "$FF"/* "$WORK/src/packaging/windows/ffmpeg/"

echo "3/4 CamSup.exe (PyInstaller sous Wine)"
docker run --rm -v "$WORK/src":/src -w /src tobix/pywine:3.12 sh -c \
  "wine python -m pip install -q -r requirements.txt pyinstaller && wine pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/windows/camsup.spec" \
  2>&1 | grep -E "INFO: Build complete|Error|error:" || true
docker run --rm -v "$WORK/src":/w alpine:3 sh -c "chmod -R a+rwX /w"

echo "4/4 installateur (Inno Setup)"
docker run --rm -v "$WORK/src":/work -w /work amake/innosetup "/DAppVersion=$VERSION" packaging/windows/camsup.iss 2>&1 | grep -E "Successful|Error"
mkdir -p "$ROOT/dist" && cp "$WORK/src/dist/CamSup-Installation-$VERSION.exe" "$ROOT/dist/"
echo "Terminé : dist/CamSup-Installation-$VERSION.exe"
