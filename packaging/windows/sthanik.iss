; Inno Setup script. Compile:  iscc /DAppVersion=2.0.0 packaging\windows\sthanik.iss   (expects dist\Sthanik from PyInstaller; writes release\)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6D1B7C54-3E0A-4C57-9A2E-5B1F0D7A9C31}
AppName=Sthanik
AppVersion={#AppVersion}
AppPublisher=Sthanik
DefaultDirName={autopf}\Sthanik
DefaultGroupName=Sthanik
UninstallDisplayIcon={app}\Sthanik.exe
SetupIconFile=..\icons\sthanik.ico
OutputDir=..\..\release
OutputBaseFilename=Sthanik-{#AppVersion}-windows-x64-setup
Compression=lzma2/normal
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; installs for the current user by default (no administrator prompt); the dialog offers all users
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern

[Files]
Source: "..\..\dist\Sthanik\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Sthanik"; Filename: "{app}\Sthanik.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Sthanik"; Filename: "{app}\Sthanik.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; Flags: unchecked

[Run]
Filename: "{app}\Sthanik.exe"; Description: "Start Sthanik now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; chats, documents and models live in %LOCALAPPDATA%\Sthanik and are deliberately kept
Type: filesandordirs; Name: "{app}"
