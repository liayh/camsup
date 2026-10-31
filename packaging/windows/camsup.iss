; Installateur Windows de CamSup (Inno Setup 6) : compilé par build.ps1, ou « iscc packaging\windows\camsup.iss ».
; Contenu : dist\CamSup (PyInstaller) + ffmpeg.exe. Données : %PROGRAMDATA%\CamSup (communes aux comptes du poste).
; Serveur : tâche planifiée « CamSup » sous le compte système, au démarrage du PC (installer-tache.ps1) ; données
; (%PROGRAMDATA%\CamSup\data) réservées au système et aux administrateurs. Fenêtre d'alerte : lancée à l'ouverture de
; session de chaque utilisateur (CamSup.exe --popup), elle interroge le serveur local.
; Installation sans question (déploiement) : CamSup-Installation-x.y.z.exe /VERYSILENT /MOTDEPASSE=MotDePasseAdmin

#define AppVersion "1.1.0"

[Setup]
AppId={{6F1C2B7E-3D4A-4E8B-9C21-CA5A0B1E0D11}
AppName=CamSup
AppVersion={#AppVersion}
AppPublisher=CamSup
DefaultDirName={autopf}\CamSup
DefaultGroupName=CamSup
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=CamSup-Installation-{#AppVersion}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
UninstallDisplayName=CamSup

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Dirs]
; droits posés par installer-tache.ps1 (data : système et administrateurs seulement)
Name: "{commonappdata}\CamSup"
Name: "{commonappdata}\CamSup\data"

[Files]
Source: "..\..\dist\CamSup\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "ffmpeg\ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "installer-tache.ps1"; DestDir: "{app}"; Flags: ignoreversion
; ffmpeg (gyan.dev, build « essentials ») : licence GPL, livrée avec
Source: "ffmpeg\LICENSE-ffmpeg.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
; fenêtre d'alerte à l'ouverture de session (tous les utilisateurs du poste)
Name: "{commonstartup}\CamSup"; Filename: "{app}\CamSup.exe"; Parameters: "--popup"; WorkingDir: "{app}"
; menu Démarrer et bureau : ouvre l'interface dans le navigateur
Name: "{group}\CamSup"; Filename: "{app}\CamSup.exe"; WorkingDir: "{app}"
Name: "{commondesktop}\CamSup"; Filename: "{app}\CamSup.exe"; WorkingDir: "{app}"; Tasks: bureau
Name: "{group}\Désinstaller CamSup"; Filename: "{uninstallexe}"

[Tasks]
Name: "bureau"; Description: "Icône sur le bureau"

[Run]
; le serveur est déjà démarré (tâche planifiée) : fenêtre d'alerte de cette session et interface
Filename: "{app}\CamSup.exe"; Parameters: "--popup"; Description: "Fenêtre d'alerte sur ce poste"; Flags: nowait postinstall
Filename: "{app}\CamSup.exe"; Description: "Ouvrir CamSup"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer-tache.ps1"" -Desinstaller"; Flags: runhidden; RunOnceId: "TacheCamSup"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM CamSup.exe"; Flags: runhidden; RunOnceId: "ArretCamSup"

[Code]
var
  PasswordPage: TInputQueryWizardPage;

function InstallFile: String;
begin
  Result := ExpandConstant('{commonappdata}\CamSup\data\installation.env');
end;

function FirstInstall: Boolean;
begin
  { mise à jour : le compte intégrateur existe déjà (base présente) }
  Result := not FileExists(ExpandConstant('{commonappdata}\CamSup\data\camsup.db'));
end;

procedure InitializeWizard;
begin
  PasswordPage := CreateInputQueryPage(wpSelectDir,
    'Compte de l''installateur', 'Mot de passe du compte « admin » de CamSup',
    'Ce compte (rôle intégrateur) sert à configurer CamSup : enregistreurs, caméras, e-mail, comptes des pharmaciens. ' +
    '10 caractères au moins. Il n''est pas conservé en clair après le premier démarrage.');
  PasswordPage.Add('Mot de passe :', True);
  PasswordPage.Add('Confirmation :', True);
  PasswordPage.Values[0] := ExpandConstant('{param:MOTDEPASSE|}');
  PasswordPage.Values[1] := PasswordPage.Values[0];
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = PasswordPage.ID) and not FirstInstall;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = PasswordPage.ID then begin
    if Length(PasswordPage.Values[0]) < 10 then begin
      MsgBox('Le mot de passe doit faire 10 caractères au moins.', mbError, MB_OK);
      Result := False;
    end else if (Pos('#', PasswordPage.Values[0]) > 0) or (Pos('"', PasswordPage.Values[0]) > 0) or
                (Pos('''', PasswordPage.Values[0]) > 0) then begin
      MsgBox('Le mot de passe ne doit contenir ni # ni guillemets.', mbError, MB_OK);
      Result := False;
    end else if PasswordPage.Values[0] <> PasswordPage.Values[1] then begin
      MsgBox('Les deux mots de passe sont différents.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { mise à jour : serveur (tâche planifiée) et fenêtres d'alerte arrêtés avant de remplacer les fichiers }
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN CamSup', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM CamSup.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
  Code: Integer;
begin
  if CurStep <> ssPostInstall then
    exit;
  { lu par CamSup au premier démarrage puis effacé (config.forget_admin_password) ; dans data\, que les droits posés
    juste après réservent au système et aux administrateurs }
  if FirstInstall and (PasswordPage.Values[0] <> '') then begin
    SetArrayLength(Lines, 1);
    Lines[0] := 'ADMIN_PASSWORD=' + PasswordPage.Values[0];
    SaveStringsToUTF8File(InstallFile, Lines, False);
  end;
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
              '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\installer-tache.ps1') + '"',
              '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
    SuppressibleMsgBox('Le démarrage automatique de CamSup n''a pas pu être configuré (code ' + IntToStr(Code) +
                       '). Prévenez votre installateur.', mbError, MB_OK, IDOK);
end;
