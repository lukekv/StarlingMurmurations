; Inno Setup script for the StarlingMurmurations installer.
; Compile from the repo root after the PyInstaller build:
;   iscc /DAppVersion=1.0.0 installer\installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppName=StarlingMurmurations
AppVersion={#AppVersion}
AppPublisher=lukekv
WizardStyle=modern
DefaultDirName={autopf}\StarlingMurmurations
DefaultGroupName=StarlingMurmurations
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=StarlingMurmurations-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; Flags: unchecked

[Files]
Source: "..\dist\StarlingMurmurations\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\StarlingMurmurations"; Filename: "{app}\StarlingMurmurations.exe"
Name: "{autodesktop}\StarlingMurmurations"; Filename: "{app}\StarlingMurmurations.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StarlingMurmurations.exe"; Description: "Launch StarlingMurmurations"; Flags: nowait postinstall skipifsilent
