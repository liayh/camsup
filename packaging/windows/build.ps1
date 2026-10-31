# Fabrication de l'installateur Windows de CamSup, sur un PC Windows 10/11 64 bits :
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 [-Version 1.1.0]
# Prérequis : Python 3.12 (python.org, « Add to PATH ») et Inno Setup 6 (jrsoftware.org).
# Node.js 20+ seulement si l'interface n'est pas déjà compilée (app\static\app absent) ou après l'avoir modifiée.
# Résultat : dist\CamSup-Installation-<version>.exe (et dist\CamSup\, le programme seul).
param([string]$Version = "1.1.0")
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

function Need($cmd, $hint) { if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd introuvable : $hint" } }
Need python "installer Python 3.12 depuis python.org (cocher « Add python.exe to PATH »)"

Write-Host "1/5 Interface web"
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location frontend
    npm ci
    npm run build
    Pop-Location
} elseif (Test-Path app\static\app\index.html) {
    Write-Host "   Node.js absent : interface déjà compilée (app\static\app) utilisée telle quelle"
} else {
    throw "Interface non compilée et Node.js absent : installer Node.js 20+ (nodejs.org)"
}

Write-Host "2/5 Environnement Python de fabrication (.venv-build)"
if (-not (Test-Path .venv-build)) { python -m venv .venv-build }
.\.venv-build\Scripts\python -m pip install --upgrade pip
.\.venv-build\Scripts\pip install -r requirements.txt pyinstaller

Write-Host "3/5 ffmpeg (conversion des extraits vidéo)"
$Ffmpeg = "packaging\windows\ffmpeg\ffmpeg.exe"
if (-not (Test-Path $Ffmpeg)) {
    # build « essentials » de gyan.dev, lien donné par ffmpeg.org pour Windows
    $Zip = Join-Path $env:TEMP "ffmpeg-essentials.zip"
    Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $Zip
    $Tmp = Join-Path $env:TEMP "ffmpeg-essentials"
    if (Test-Path $Tmp) { Remove-Item $Tmp -Recurse -Force }
    Expand-Archive $Zip $Tmp
    New-Item -ItemType Directory -Force (Split-Path $Ffmpeg) | Out-Null
    Copy-Item (Get-ChildItem $Tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1).FullName $Ffmpeg
    Copy-Item (Get-ChildItem $Tmp -Recurse -Filter LICENSE | Select-Object -First 1).FullName (Join-Path (Split-Path $Ffmpeg) "LICENSE-ffmpeg.txt")
}

Write-Host "4/5 Programme (PyInstaller) -> dist\CamSup"
.\.venv-build\Scripts\pyinstaller --noconfirm --clean --distpath dist --workpath build packaging\windows\camsup.spec

Write-Host "5/5 Installateur (Inno Setup)"
$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) { $Iscc = "$env:ProgramFiles\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $Iscc)) { throw "Inno Setup 6 introuvable (jrsoftware.org/isinfo.php) : le programme seul est dans dist\CamSup" }
& $Iscc "/DAppVersion=$Version" packaging\windows\camsup.iss
Write-Host "Terminé : dist\CamSup-Installation-$Version.exe"
