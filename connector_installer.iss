#define AppName "Xianyu Local Connector"
#ifndef AppVersion
#define AppVersion "0.2.0"
#endif
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
MinVersion=10.0.10240
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
CloseApplications=force
CloseApplicationsFilter=XianyuConnector.exe
RestartApplications=yes
UninstallDisplayIcon={app}\XianyuConnector.exe
SetupLogging=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Tasks]
Name: "desktopicon"; Description: "????????"; GroupDescription: "?????"; Flags: checkedonce
Name: "startup"; Description: "?? Windows ????????"; GroupDescription: "?????"; Flags: unchecked

[Files]
Source: "{#BuildRoot}\dist\XianyuConnector\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\XianyuConnector.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\XianyuConnector.exe"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\XianyuConnector.exe"; Parameters: "--startup"; Tasks: startup

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
    if DirExists(DataDir) and (not UninstallSilent) and
       (MsgBox('???????????????????? Cookie/Token ???????' + #13#10 + #13#10 +
         '???????????????????????????????',
         mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO) then
      DelTree(DataDir, True, True, True);
  end;
end;
