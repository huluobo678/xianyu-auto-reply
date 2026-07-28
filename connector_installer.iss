#define AppName "闲鱼本地连接器"
#ifndef AppVersion
#define AppVersion "0.3.0"
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
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式"; Flags: checkedonce
Name: "startup"; Description: "登录 Windows 后自动启动连接器"; GroupDescription: "可选设置"; Flags: unchecked

[Files]
Source: "{#BuildRoot}\dist\XianyuConnector\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\XianyuConnector.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\XianyuConnector.exe"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\XianyuConnector.exe"; Parameters: "--startup"; Tasks: startup

[Run]
Filename: "{app}\XianyuConnector.exe"; Description: "安装完成后启动闲鱼本地连接器"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep = usUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\XianyuConnector');
    if DirExists(DataDir) and (not UninstallSilent) and
       (MsgBox('是否保留本机连接器数据（包括设备绑定、闲鱼 Cookie/Token 和发送去重记录）？' + #13#10 + #13#10 +
         '选择“是”可在重新安装或覆盖升级后继续使用；选择“否”将永久删除本机数据。',
         mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO) then
      DelTree(DataDir, True, True, True);
  end;
end;
