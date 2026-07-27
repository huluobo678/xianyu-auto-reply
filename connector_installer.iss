#define AppName "Xianyu Local Connector"
#define AppVersion "0.1.0"
#define AppPublisher "Xianyu Auto Reply"
#define BuildRoot "D:\AI\Agent_Data\Generated_Files\xianyu-local-connector"

[Setup]
AppId={{A8B9992E-5A1B-4E9D-8F60-0A2B1EF1E68A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\XianyuConnector
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#BuildRoot}
OutputBaseFilename=XianyuConnectorSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
CloseApplications=force
RestartApplications=no
UninstallDisplayIcon={app}\XianyuConnector.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce
Name: "startup"; Description: "Start after Windows sign-in"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#BuildRoot}\XianyuConnector.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\XianyuConnector.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\XianyuConnector.exe"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\XianyuConnector.exe"; Tasks: startup

[Run]
Filename: "{app}\XianyuConnector.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep = usUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\XianyuConnector');
    if DirExists(DataDir) and
       (MsgBox('Delete local device credentials as well?', mbConfirmation, MB_YESNO) = IDYES) then
      DelTree(DataDir, True, True, True);
  end;
end;
