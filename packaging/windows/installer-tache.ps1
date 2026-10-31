# Lancé par l'installateur (droits administrateur) : droits du dossier de données et tâche planifiée « CamSup ».
#   installer-tache.ps1               → droits + tâche (créée ou remplacée) + démarrage du serveur
#   installer-tache.ps1 -Desinstaller → tâche arrêtée et supprimée (les données restent)
# Le serveur tourne sous le compte système, au démarrage du PC (même sans session ouverte), relancé s'il s'arrête.
# Droits : %PROGRAMDATA%\CamSup lisible par les utilisateurs (camsup.env : port, réglages) ; %PROGRAMDATA%\CamSup\data
# (base, clé de chiffrement, extraits vidéo, journaux) réservé au système et aux administrateurs.
param([switch]$Desinstaller)
$ErrorActionPreference = "Stop"
$Nom = "CamSup"

if ($Desinstaller) {
    Stop-ScheduledTask -TaskName $Nom -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Nom -Confirm:$false -ErrorAction SilentlyContinue
    exit 0
}

$Exe = Join-Path $PSScriptRoot "CamSup.exe"
$Racine = Join-Path $env:ProgramData "CamSup"
$Donnees = Join-Path $Racine "data"
New-Item -ItemType Directory -Force $Donnees | Out-Null

function Icacls { & icacls.exe @args | Out-Null; if ($LASTEXITCODE -ne 0) { throw "icacls $args : code $LASTEXITCODE" } }
$Systeme, $Admins, $Utilisateurs = "*S-1-5-18", "*S-1-5-32-544", "*S-1-5-32-545"
# anciens droits (1.0 : « Utilisateurs » en modification ; propriétaire = l'utilisateur qui a créé les fichiers, qui
# pourrait sinon se redonner l'accès)
$Anciens = @($Utilisateurs, "*S-1-5-11", "*S-1-1-0", "*S-1-3-0")    # Utilisateurs, Utilisateurs authentifiés, Tout le monde, Créateur propriétaire

Icacls $Racine /setowner $Admins /T /C /Q
# data d'abord : protégé (sans héritage) avant d'ouvrir la racine en lecture
Icacls $Donnees /inheritance:r /Q
Icacls $Donnees /grant:r "${Systeme}:(OI)(CI)F" "${Admins}:(OI)(CI)F" /Q
Icacls $Donnees /remove:g @Anciens /T /C /Q
Icacls $Racine /inheritance:r /Q
Icacls $Racine /grant:r "${Systeme}:(OI)(CI)F" "${Admins}:(OI)(CI)F" /Q
Icacls $Racine /remove:g "*S-1-5-11" "*S-1-1-0" "*S-1-3-0" /Q
Icacls $Racine /grant:r "${Utilisateurs}:(OI)(CI)RX" /Q

$Action = New-ScheduledTaskAction -Execute $Exe -Argument "--serveur" -WorkingDirectory $PSScriptRoot
$Declencheur = New-ScheduledTaskTrigger -AtStartup
$Compte = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
# sans limite de durée (72 h par défaut), sur batterie aussi, relancée si elle échoue
$Reglages = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Nom -Action $Action -Trigger $Declencheur -Principal $Compte -Settings $Reglages `
    -Description "Surveillance CamSup (NVR, caméras, disques) et interface http://localhost:8080" -Force | Out-Null
Start-ScheduledTask -TaskName $Nom
