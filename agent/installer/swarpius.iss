; Inno Setup script for the Swarpius Windows installer.
;
; Wraps the PyInstaller one-folder output (agent/dist/Swarpius/) into a
; setup.exe. CI signs the bundled swarpius.exe before this runs, then signs
; the resulting Swarpius-Setup.exe (build-windows job in installer.yml).
;
; Compile from agent/: ISCC.exe installer\swarpius.iss
; Override the version on a release tag: ISCC.exe /DMyAppVersion=1.2.3 ...

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "Swarpius"
#define MyAppPublisher "Paraseva Ltd"
#define MyAppCopyright "Copyright © 2026 Paraseva Ltd"
#define MyAppExeName "swarpius.exe"
#define MyAppURL "https://github.com/paraseva/swarpius"

[Setup]
; Stable across versions so upgrades replace in place (do not change).
AppId={{b2b06358-65e6-41dc-94b5-73545b2190c3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppCopyright={#MyAppCopyright}
; Add/Remove Programs entry shows just the name; the version sits in its own
; column. Without this, Inno defaults to "{#MyAppName} version {#MyAppVersion}".
UninstallDisplayName={#MyAppName}
; Stamp the Setup.exe's own PE version resource (Properties > Details).
; AppVersion above only drives the Add/Remove Programs display version.
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoCopyright={#MyAppCopyright}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Offer per-user (default, no UAC) or all-users; the user chooses at launch.
; User data lives in %LOCALAPPDATA%\Swarpius regardless of this choice.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
; Close a running instance on upgrade so locked files don't block the install.
CloseApplications=yes
RestartApplications=no
OutputDir=..\dist
OutputBaseFilename=Swarpius-Setup
SetupIconFile=swarpius.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "..\dist\Swarpius\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
{ Offer to remove user data (%LOCALAPPDATA%\Swarpius) on uninstall.
  Kept by default so a reinstall preserves config / history / pairing. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\Swarpius');
    if DirExists(DataDir) and (not UninstallSilent) then
    begin
      if MsgBox('Also delete your Swarpius settings, history, and Roon pairing?'
                + #13#10 + 'Choose No to keep them for a future reinstall.',
                mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
