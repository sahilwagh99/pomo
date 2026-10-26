; Pomo installer (Inno Setup 6).
; Built by build.bat, which passes /DAppVersion=<version from pomodoro/__init__.py>.
; Expects the PyInstaller one-folder build in ..\dist\Pomo\ and the wizard images that
; tools\prepare_build.py renders into ..\build\wizard\.
;
; Pomo was called FocusOrb before 1.2. It keeps the same AppId, so an existing FocusOrb
; install is recognised, removed first (files and shortcuts), and replaced by Pomo.
;
; Pomo updates itself (see pomodoro/updater.py) by running this installer with
;   /VERYSILENT /UPDATE=1 /DIR=<its folder> /MERGETASKS=!autostart,!desktopicon /RELAUNCH=<Pomo.exe>
; then quitting. Setup waits for it to close, and starts it again at the end, whatever happened.

#ifndef AppVersion
  #define AppVersion "1.4.0"
#endif
#ifndef AppName
  #define AppName "Pomo"
#endif
#ifndef LegacyName
  #define LegacyName "FocusOrb"
#endif
#ifndef AppIdValue
  ; Never change this: it is how Windows recognises upgrades of the same app.
  #define AppIdValue "{{8F3C2B1E-6D4A-4E7B-9A51-2C7D0E5F9B13}"
#endif
#define AppExe "Pomo.exe"
#define LegacyExe LegacyName + ".exe"

[Setup]
AppId={#AppIdValue}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
#ifdef AppURL
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
#endif
DefaultDirName={autopf}\{#AppName}
; don't reuse FocusOrb's old folder: Pomo gets its own
UsePreviousAppDir=no
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install by default (no admin prompt); the user can choose "all users" instead.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
; Stable name (no version) so README links to releases/latest/download/Pomo-Setup.exe keep working.
OutputBaseFilename={#AppName}-Setup
SetupIconFile=..\assets\pomo.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
WizardImageFile=..\build\wizard\large-1x.bmp,..\build\wizard\large-2x.bmp
WizardSmallImageFile=..\build\wizard\small-1x.bmp,..\build\wizard\small-2x.bmp
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Run {#AppName} on startup"; GroupDescription: "Startup:"

[Files]
Source: "..\dist\Pomo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Same value the in-app "Run on startup" setting writes, so the two stay in sync.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"""; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  RunningMutex = 'PomoAppRunning';  // held by a running Pomo (main.py)

function IsSelfUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;

procedure WaitForPomoToClose();
var
  Waited: Integer;
begin
  // Pomo quits by itself right after starting its own update: give it time to save and go
  Waited := 0;
  while CheckForMutexes(RunningMutex) and (Waited < 15000) do
  begin
    Sleep(250);
    Waited := Waited + 250;
  end;
end;

procedure StopRunningApp();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#LegacyExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function UninstallKeyName(): String;
begin
  Result := ExpandConstant('Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1');
end;

function FindPreviousInstall(var Location, Uninstaller: String): Boolean;
begin
  Result := (RegQueryStringValue(HKCU, UninstallKeyName(), 'InstallLocation', Location)
             and RegQueryStringValue(HKCU, UninstallKeyName(), 'UninstallString', Uninstaller))
         or (RegQueryStringValue(HKLM, UninstallKeyName(), 'InstallLocation', Location)
             and RegQueryStringValue(HKLM, UninstallKeyName(), 'UninstallString', Uninstaller));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Location, Uninstaller: String;
  ResultCode, Waited: Integer;
begin
  // Upgrading over a running copy would fail on locked files.
  if IsSelfUpdate() then
    WaitForPomoToClose();
  StopRunningApp();
  if FindPreviousInstall(Location, Uninstaller) then
  begin
    Location := RemoveBackslashUnlessRoot(Location);
    if CompareText(Location, RemoveBackslashUnlessRoot(ExpandConstant('{app}'))) <> 0 then
    begin
      // An older copy lives in another folder (FocusOrb before 1.2): remove it first so its files
      // and shortcuts don't linger. Its data folder is kept and moved over by Pomo on first run.
      Exec(RemoveQuotes(Uninstaller), '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE,
           ewWaitUntilTerminated, ResultCode);
      Waited := 0;
      while FileExists(AddBackslash(Location) + 'unins000.exe') and (Waited < 20000) do
      begin
        Sleep(250);
        Waited := Waited + 250;
      end;
    end;
  end;
  Result := '';
end;

procedure DeinitializeSetup();
var
  Relaunch: String;
  ResultCode: Integer;
begin
  // A self-update starts Pomo again at the end, updated or not, so it never just disappears
  Relaunch := ExpandConstant('{param:RELAUNCH|}');
  if (Relaunch <> '') and FileExists(Relaunch) then
    Exec(Relaunch, '', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopRunningApp();
    // The app may have registered itself for start-up even if the installer task was not chosen.
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, '{#AppName}');
    RegDeleteValue(HKEY_CURRENT_USER, RunKey, '{#LegacyName}');
  end;
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
  begin
    if MsgBox('Also delete your {#AppName} history and settings?' + #13#10 + #13#10 +
              'Choose No to keep them for a future install.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      DelTree(ExpandConstant('{userappdata}\{#AppName}'), True, True, True);
      DelTree(ExpandConstant('{userappdata}\{#LegacyName}'), True, True, True);
    end;
  end;
end;
