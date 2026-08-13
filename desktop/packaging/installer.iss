; Inno Setup script — builds CaesarPOS-Setup.exe
;
; Build order:
;   1. cd desktop && pyinstaller packaging/caesar_pos.spec
;   2. iscc packaging/installer.iss
;
; The installer does NOT bundle a licence key or a server URL. Both are entered
; on the activation screen at first run, so one installer serves every branch
; and nothing sensitive ships inside it.

#define AppName        "Caesar POS"
#define AppNameAr      "نظام القيصر"
#define AppVersion     "0.1.0"
#define AppPublisher   "Caesar Cafe"
#define AppExeName     "CaesarPOS.exe"

[Setup]
AppId={{8C4A1E92-3D7B-4F5A-9E21-6B0D8F2A7C13}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\CaesarPOS
DefaultGroupName={#AppName}
OutputBaseFilename=CaesarPOS-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-machine install: a cafe terminal is shared by every member of staff.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "تشغيل النظام تلقائياً عند بدء Windows"; GroupDescription: "خيارات إضافية"

[Files]
Source: "..\dist\CaesarPOS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppNameAr}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppNameAr}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppNameAr}"; Filename: "{app}\{#AppExeName}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppNameAr}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Logs and the local cache are ours to remove. The device credential lives in
; the Windows Credential Manager and is left alone on purpose: an uninstall for
; a reinstall must not silently consume one of the licence's device seats.
Type: filesandordirs; Name: "{localappdata}\CaesarPOS\*.log"
