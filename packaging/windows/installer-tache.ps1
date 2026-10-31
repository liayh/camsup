# Lancé par l'installateur (droits administrateur) : droits du dossier de données et tâche planifiée « CamSup ».
#   installer-tache.ps1 -Preparer [-Neuf] → avant la copie des fichiers : dossier de données sûr (voir ci-dessous)
#   installer-tache.ps1                  → après la copie : droits revérifiés, tâche créée ou remplacée, serveur démarré
#   installer-tache.ps1 -Desinstaller    → tâche arrêtée et supprimée (les données restent)
# Le serveur tourne sous le compte système (S-1-5-18), au démarrage du PC (même sans session ouverte).
# Droits : %PROGRAMDATA%\CamSup lisible par les utilisateurs (camsup.env, fenetre.pub) ; data\ (base, clé de
# chiffrement, extraits vidéo, journaux, mot de passe d'installation) réservé au système et aux administrateurs.
# Un compte ordinaire peut créer des dossiers dans ProgramData : il aurait pu préparer CamSup\ avant l'installation
# (droits à son nom, base avec son compte, jonctions vers des dossiers système). D'où :
#   -Neuf (aucune installation connue) : un CamSup\ déjà présent est mis de côté (CamSup.suspect-<date>), pas utilisé ;
#   toujours : liens et jonctions supprimés, propriétaire = Administrateurs, toute autre autorisation retirée.
param([switch]$Preparer, [switch]$Neuf, [switch]$Desinstaller)
$ErrorActionPreference = "Stop"
$Nom = "CamSup"
$Systeme, $Admins, $Utilisateurs = "S-1-5-18", "S-1-5-32-544", "S-1-5-32-545"
$Racine = Join-Path $env:ProgramData "CamSup"
$Donnees = Join-Path $Racine "data"

if ($Desinstaller) {
    Stop-ScheduledTask -TaskName $Nom -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Nom -Confirm:$false -ErrorAction SilentlyContinue
    exit 0
}

function Icacls {
    & icacls.exe @args | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls $args : code $LASTEXITCODE" }
}

function Est-Lien($Chemin) {
    (Test-Path -LiteralPath $Chemin) -and (([IO.File]::GetAttributes($Chemin) -band [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Retirer-Lien($Chemin) {
    # le lien seul, jamais sa cible
    if (([IO.File]::GetAttributes($Chemin) -band [IO.FileAttributes]::Directory) -ne 0) { [IO.Directory]::Delete($Chemin, $false) }
    else { [IO.File]::Delete($Chemin) }
    Write-Output "lien supprimé : $Chemin"
}

function Retirer-Liens($Dossier) {
    # parcours à la main : sans suivre aucun lien
    foreach ($e in [IO.Directory]::GetFileSystemEntries($Dossier)) {
        $a = [IO.File]::GetAttributes($e)
        if (($a -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Retirer-Lien $e }
        elseif (($a -band [IO.FileAttributes]::Directory) -ne 0) { Retirer-Liens $e }
    }
}

function Proteger($Chemin, [string[]]$Garder, [string[]]$Accorder) {
    # dossier : seulement les autorisations voulues, explicites, sans héritage ; toute autre (nominative, refus) retirée
    Icacls $Chemin /inheritance:r /grant:r @Accorder /L /Q
    foreach ($r in (Get-Acl -LiteralPath $Chemin).Access) {
        if ($r.IsInherited) { continue }
        try { $sid = $r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
        catch { $sid = $r.IdentityReference.Value }
        if ($Garder -notcontains $sid) { Icacls $Chemin /remove "*$sid" /L /Q }
    }
}

function Reinitialiser-Contenu($Dossier, [string[]]$Sauf) {
    # fichiers et sous-dossiers : uniquement les droits hérités du dossier (autorisations à leur nom effacées)
    foreach ($e in [IO.Directory]::GetFileSystemEntries($Dossier)) {
        if ($Sauf -contains (Split-Path $e -Leaf)) { continue }
        Icacls $e /reset /T /C /L /Q
    }
}

function Securiser {
    if (Est-Lien $Racine) { Retirer-Lien $Racine }
    New-Item -ItemType Directory -Force $Racine | Out-Null
    if (Est-Lien $Donnees) { Retirer-Lien $Donnees }
    New-Item -ItemType Directory -Force $Donnees | Out-Null
    Retirer-Liens $Racine
    Icacls $Racine /setowner "*$Admins" /T /C /L /Q
    # data d'abord : fermé avant que la racine soit ouverte en lecture
    Proteger $Donnees @($Systeme, $Admins) @("*${Systeme}:(OI)(CI)F", "*${Admins}:(OI)(CI)F")
    Reinitialiser-Contenu $Donnees @()
    Proteger $Racine @($Systeme, $Admins, $Utilisateurs) @("*${Systeme}:(OI)(CI)F", "*${Admins}:(OI)(CI)F", "*${Utilisateurs}:(OI)(CI)RX")
    Reinitialiser-Contenu $Racine @("data")
}

if ($Preparer) {
    if ($Neuf -and (Test-Path -LiteralPath $Racine)) {
        # dossier présent sans installation connue : préparé par quelqu'un d'autre, on ne s'en sert pas
        if (Est-Lien $Racine) { Retirer-Lien $Racine }
        else { Rename-Item -LiteralPath $Racine ("CamSup.suspect-" + (Get-Date -Format "yyyyMMdd-HHmmss")) }
    }
    Securiser
    exit 0
}

# Après la copie des fichiers : le programme lancé sous le compte système doit être dans Program Files (modifiable par
# les administrateurs seulement), sinon un compte ordinaire pourrait le remplacer.
$Programme = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
$PF = [IO.Path]::GetFullPath($env:ProgramFiles).TrimEnd('\') + '\'
if (-not $Programme.StartsWith($PF, [StringComparison]::OrdinalIgnoreCase)) {
    throw "CamSup doit être installé dans $PF (installé dans $Programme) : refusé"
}
Securiser

$Exe = Join-Path $PSScriptRoot "CamSup.exe"
$Action = New-ScheduledTaskAction -Execute $Exe -Argument "--serveur" -WorkingDirectory $PSScriptRoot
$Declencheur = New-ScheduledTaskTrigger -AtStartup
$Compte = New-ScheduledTaskPrincipal -UserId $Systeme -LogonType ServiceAccount -RunLevel Highest
# sans limite de durée (72 h par défaut), sur batterie aussi, relancée si elle échoue
$Reglages = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Nom -Action $Action -Trigger $Declencheur -Principal $Compte -Settings $Reglages `
    -Description "Surveillance CamSup (NVR, caméras, disques) et interface http://127.0.0.1:8080" -Force | Out-Null
Start-ScheduledTask -TaskName $Nom
