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
; Program Files imposé : le serveur tourne sous le compte système, son dossier ne doit être modifiable que par les
; administrateurs (installer-tache.ps1 refuse aussi un autre dossier, /DIR= compris)
DisableDirPage=yes
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

; %PROGRAMDATA%\CamSup et data\ : créés et protégés par installer-tache.ps1 -Preparer, avant la copie des fichiers

[Registry]
; marque d'une installation faite par un administrateur ; gardée à la désinstallation (les données restent) :
; un dossier CamSup sans cette marque (ni installation enregistrée) a été préparé par quelqu'un d'autre
Root: HKLM; Subkey: "Software\CamSup"; ValueType: string; ValueName: "Donnees"; ValueData: "{commonappdata}\CamSup"

[Files]
Source: "..\..\dist\CamSup\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "ffmpeg\ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "installer-tache.ps1"; DestDir: "{app}"; Flags: ignoreversion
; ffmpeg (gyan.dev, build « essentials ») : licence GPL, livrée avec
Source: "ffmpeg\LICENSE-ffmpeg.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; réglages e-mail de l'intégrateur (email.env, hors dépôt git) : importés et chiffrés au premier démarrage, puis effacés
; dans data\ (protégé par installer-tache.ps1 -Preparer avant la copie) : la racine est lisible par tous les comptes
Source: "email.env"; DestDir: "{commonappdata}\CamSup\data"; DestName: "camsup-email.env"; Flags: skipifsourcedoesntexist

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
  NeedPassword, Known, TaskFailed: Boolean;

function InstallFile: String;
begin
  Result := ExpandConstant('{commonappdata}\CamSup\data\installation.env');
end;

function PowerShell: String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

function KnownInstall: Boolean;
begin
  { installation faite par un administrateur (clés réservées aux administrateurs) ; un fichier ne suffit pas : un
    compte ordinaire peut créer %PROGRAMDATA%\CamSup et y déposer une base avec son propre compte }
  Result := RegKeyExists(HKLM64, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F1C2B7E-3D4A-4E8B-9C21-CA5A0B1E0D11}_is1')
            or RegKeyExists(HKLM64, 'Software\CamSup');
end;

function InitializeSetup: Boolean;
begin
  Known := KnownInstall;
  { pas d'installation connue : dossier existant mis de côté, donc compte à créer ; sinon, compte déjà créé si la base existe }
  NeedPassword := (not Known) or not FileExists(ExpandConstant('{commonappdata}\CamSup\data\camsup.db'));
  Result := True;
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
  Result := (PageID = PasswordPage.ID) and not NeedPassword;
end;

function PasswordProblem(const Pw: String): String;
begin
  Result := '';
  if Length(Pw) < 10 then
    Result := 'Le mot de passe doit faire 10 caractères au moins.'
  else if (Pos('#', Pw) > 0) or (Pos('"', Pw) > 0) or (Pos('''', Pw) > 0) then
    Result := 'Le mot de passe ne doit contenir ni # ni guillemets.';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Problem: String;
begin
  Result := True;
  if CurPageID = PasswordPage.ID then begin
    Problem := PasswordProblem(PasswordPage.Values[0]);
    if Problem <> '' then begin
      MsgBox(Problem, mbError, MB_OK);
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
  Params: String;
begin
  Result := '';
  { aussi en installation silencieuse (/MOTDEPASSE=), où la page n'est pas validée }
  if NeedPassword then begin
    Result := PasswordProblem(PasswordPage.Values[0]);
    if Result <> '' then
      exit;
  end;
  { mise à jour : serveur (tâche planifiée) et fenêtres d'alerte arrêtés avant de remplacer les fichiers }
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN CamSup', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM CamSup.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  { dossier de données sûr AVANT d'y écrire quoi que ce soit (mot de passe d'installation compris) }
  ExtractTemporaryFile('installer-tache.ps1');
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\installer-tache.ps1') + '" -Preparer';
  if not Known then
    Params := Params + ' -Neuf';
  if not Exec(PowerShell, Params, '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
    Result := 'Le dossier de données de CamSup n''a pas pu être protégé (code ' + IntToStr(Code) + ').';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
  Code: Integer;
begin
  if CurStep <> ssPostInstall then
    exit;
  { lu par CamSup au premier démarrage puis effacé (config.forget_admin_password) ; data\ est déjà protégé }
  if NeedPassword and (PasswordPage.Values[0] <> '') then begin
    SetArrayLength(Lines, 1);
    Lines[0] := 'ADMIN_PASSWORD=' + PasswordPage.Values[0];
    SaveStringsToUTF8File(InstallFile, Lines, False);
  end;
  if not Exec(PowerShell, '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\installer-tache.ps1') + '"',
              '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then begin
    TaskFailed := True;
    SuppressibleMsgBox('Le démarrage automatique de CamSup n''a pas pu être configuré (code ' + IntToStr(Code) +
                       '). Prévenez votre installateur.', mbError, MB_OK, IDOK);
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  { déploiement silencieux : l'échec doit se voir (sinon le poste semble installé, sans surveillance) }
  if TaskFailed then
    Result := 10
  else
    Result := 0;
end;
