; AFO's installer (step 10.4c, Q16): the usual Windows wizard - language, welcome, folder, shortcuts, progress,
; «Launch» - for the current user, with no administrator rights. The program's folder (the embeddable Python, the
; libraries, AFO's code) is made by `python -m installer.build`, which then runs this script with the defines below.

#ifndef AppVersion
  #error Build with: python -m installer.build
#endif
#ifndef Source
  #error Build with: python -m installer.build
#endif
#ifndef OutputDir
  #define OutputDir "..\build\dist"
#endif
#ifndef MaxDir
  ; the longest install folder: 259 characters less the deepest file's path (installer.build measures it)
  #define MaxDir 80
#endif

[Setup]
; one id for every version: an update installs over the old one, «Installed apps» shows one AFO
AppId={{6C1F4E2B-8A0D-4F3B-9E57-AF0A2C5D7E11}
AppName=AFO
AppVersion={#AppVersion}
AppVerName=AFO {#AppVersion}
AppPublisher=AFO
VersionInfoVersion={#AppVersion}
; SignPath Foundation's rule for the files it signs: the product's name and version in each (20.6)
VersionInfoProductName=AFO
VersionInfoProductVersion={#AppVersion}
; for the current user: %LOCALAPPDATA%\Programs\AFO, no UAC prompt
PrivilegesRequired=lowest
DefaultDirName={autopf}\AFO
DisableProgramGroupPage=yes
DisableWelcomePage=no
ShowLanguageDialog=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile={#Source}\afo.ico
UninstallDisplayIcon={app}\afo.ico
UninstallDisplayName=AFO
OutputDir={#OutputDir}
OutputBaseFilename=AFO-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
; the window and the hub hold the program's files: they are asked to close (the hub is stopped in [Code] first)
CloseApplications=force
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
russian.AtLogin=Запускать AFO при входе в Windows — сразу в трей (модели и проекты готовы до того, как откроется окно)
english.AtLogin=Start AFO when I sign in to Windows - in the tray (models and projects ready before the window opens)
russian.Starting=Запуск:
english.Starting=Starting:
; the program's language until one is chosen in its Settings (common/i18n.py reads it)
russian.LanguageCode=ru
english.LanguageCode=en
russian.DeleteData=Удалить и данные AFO — разговоры, настройки, журналы — и скачанные модели?%n%nЕсли их оставить, они пригодятся, когда AFO будет установлен снова.
english.DeleteData=Also delete AFO's data - chats, settings, logs - and the downloaded models?%n%nKept, they are there when AFO is installed again.
russian.DirTooLong=Путь к папке слишком длинный: Windows не запишет в неё самые глубокие файлы программы. Вернитесь и выберите папку с путём не длиннее %1 знаков — например, предложенную.
english.DirTooLong=The folder's path is too long: Windows would not write the program's deepest files in it. Go back and choose a folder whose path is at most %1 characters - the one offered, say.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "atlogin"; Description: "{cm:AtLogin}"; GroupDescription: "{cm:Starting}"

[InstallDelete]
; an update replaces the code and the libraries whole (nothing left of the old version); the models stay
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\hub"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\common"

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; the window's own app id (app/__main__.py APP_ID): a pinned shortcut and the window are one taskbar button
Name: "{autoprograms}\AFO"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m app"; WorkingDir: "{app}"; IconFilename: "{app}\afo.ico"; AppUserModelID: "AFO.App"
Name: "{autodesktop}\AFO"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m app"; WorkingDir: "{app}"; IconFilename: "{app}\afo.ico"; AppUserModelID: "AFO.App"; Tasks: desktopicon

[Registry]
; the value «Settings → Start» turns on and off (hub/autostart.py RUN_VALUE, login_command): AFO in the tray (43.4)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "AFO"; ValueData: """{app}\python\pythonw.exe"" -m app --tray"; Flags: uninsdeletevalue; Tasks: atlogin
; the hub alone at login, before 43.4: AFO in the tray takes its place
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "AFO hub"; Flags: deletevalue; Tasks: atlogin
; the language the wizard was run in: AFO's too, until one is chosen in Settings (common/i18n.py)
Root: HKCU; Subkey: "Software\AFO"; ValueType: string; ValueName: "Language"; ValueData: "{cm:LanguageCode}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m app"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,AFO}"; Flags: postinstall nowait skipifsilent
; an update AFO itself started (hub/release.py, /UPDATE=1) is silent: the window opens again on the new version
Filename: "{app}\python\pythonw.exe"; Parameters: "-m app"; WorkingDir: "{app}"; Flags: nowait; Check: IsUpdate

[UninstallDelete]
; what the program's Python wrote beside its files (compiled modules, the libraries' caches)
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\hub"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\common"

[Code]
// The hub is asked to stop, then the program's other processes (the window) end: their files can then be replaced
// or removed. Nothing to do on a first install.
procedure LetGo();
var
  Python: String;
  Code: Integer;
begin
  Python := ExpandConstant('{app}\python\python.exe');
  if FileExists(Python) then
    Exec(Python, '-m hub stop --program', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
end;

// AFO's own update (hub/release.py) runs the installer with /VERYSILENT /UPDATE=1
function IsUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:update|0}') = '1';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  // before anything is written: a folder too deep would stop the install half way with «path not found»
  if Length(ExpandConstant('{app}')) > {#MaxDir} then
  begin
    Result := FmtMessage(CustomMessage('DirTooLong'), [IntToStr({#MaxDir})]);
    exit;
  end;
  LetGo();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  LetGo();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data, Models, Login: String;
begin
  // this installation's AFO at login (and the hub alone, before 43.4), set here or in Settings, goes with it;
  // another copy's (a checkout's) stays
  if (CurUninstallStep = usUninstall) and
     RegQueryStringValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'AFO', Login) and
     (Pos(Lowercase(ExpandConstant('{app}\')), Lowercase(Login)) > 0) then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'AFO');
  if (CurUninstallStep = usUninstall) and
     RegQueryStringValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'AFO hub', Login) and
     (Pos(Lowercase(ExpandConstant('{app}\')), Lowercase(Login)) > 0) then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'AFO hub');
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{localappdata}\AFO');
    Models := ExpandConstant('{app}\model');
    if (DirExists(Data) or DirExists(Models)) and
       (SuppressibleMsgBox(CustomMessage('DeleteData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES) then
    begin
      DelTree(Data, True, True, True);
      DelTree(Models, True, True, True);
    end;
    RemoveDir(ExpandConstant('{app}'));
  end;
end;
