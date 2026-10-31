; Installateur Windows de CamSup (Inno Setup 6) : compilé par build.ps1, ou « iscc packaging\windows\camsup.iss ».
; Contenu : dist\CamSup (PyInstaller) + ffmpeg.exe. Données : %PROGRAMDATA%\CamSup (communes aux comptes du poste).
; CamSup démarre à l'ouverture de session de chaque utilisateur (pas en service : la fenêtre d'alerte doit s'afficher).
; Installation sans question (déploiement) : CamSup-Installation-x.y.z.exe /VERYSILENT /MOTDEPASSE=MotDePasseAdmin

#define AppVersion "1.0.0"

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
; le serveur et la fenêtre d'alerte tournent sous le compte de l'utilisateur connecté : écriture autorisée
Name: "{commonappdata}\CamSup"; Permissions: users-modify
Name: "{commonappdata}\CamSup\data"; Permissions: users-modify

[Files]
Source: "..\..\dist\CamSup\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "ffmpeg\ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion
; ffmpeg (gyan.dev, build « essentials ») : licence GPL, livrée avec
Source: "ffmpeg\LICENSE-ffmpeg.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; réglages e-mail de l'intégrateur (email.env, hors dépôt git) : importés et chiffrés au premier démarrage, puis effacés
Source: "email.env"; DestDir: "{commonappdata}\CamSup"; DestName: "camsup-email.env"; Flags: skipifsourcedoesntexist

[Icons]
; démarrage automatique à l'ouverture de session (tous les utilisateurs du poste)
Name: "{commonstartup}\CamSup"; Filename: "{app}\CamSup.exe"; WorkingDir: "{app}"
; menu Démarrer et bureau : CamSup déjà lancé → ouvre l'interface dans le navigateur
Name: "{group}\CamSup"; Filename: "{app}\CamSup.exe"; WorkingDir: "{app}"
Name: "{commondesktop}\CamSup"; Filename: "{app}\CamSup.exe"; WorkingDir: "{app}"; Tasks: bureau
Name: "{group}\Désinstaller CamSup"; Filename: "{uninstallexe}"

[Tasks]
Name: "bureau"; Description: "Icône sur le bureau"

[Run]
Filename: "{app}\CamSup.exe"; Description: "Démarrer CamSup maintenant"; Flags: nowait postinstall

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM CamSup.exe"; Flags: runhidden; RunOnceId: "ArretCamSup"

[Code]
var
  PasswordPage: TInputQueryWizardPage;

function EnvFile: String;
begin
  Result := ExpandConstant('{commonappdata}\CamSup\camsup.env');
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

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
begin
  { lu par CamSup au premier démarrage, puis retiré du fichier une fois le compte créé (config.forget_admin_password) }
  if (CurStep = ssPostInstall) and FirstInstall and (PasswordPage.Values[0] <> '') then begin
    SetArrayLength(Lines, 1);
    Lines[0] := 'ADMIN_PASSWORD=' + PasswordPage.Values[0];
    SaveStringsToUTF8File(EnvFile, Lines, True);
  end;
end;
