#define MyAppName "Hermes OPC"
#define MyAppVersion "0.10.0"
#define MyAppPublisher "Hermes OPC"
#define MyAppExeName "HermesOPC.exe"

[Setup]
AppId={{8F68E55B-586E-4BCF-B1D8-2F5D84903643}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\HermesOPC
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist\installer
OutputBaseFilename=HermesOPC-0.10.0-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription=A股研究与辅助决策工作台，不支持实盘交易
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式:"; Flags: unchecked

[Files]
Source: "..\..\dist\portable\HermesOPC\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Hermes OPC"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\安全与限制说明"; Filename: "{app}\_internal\docs\delivery\security-and-limitations.zh-CN.md"
Name: "{autodesktop}\Hermes OPC"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "首次启动 Hermes OPC"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteUserDataCheck: TNewCheckBox;

function InitializeUninstall(): Boolean;
begin
  Result := True;
end;

procedure InitializeUninstallProgressForm();
begin
  DeleteUserDataCheck := TNewCheckBox.Create(UninstallProgressForm);
  DeleteUserDataCheck.Parent := UninstallProgressForm;
  DeleteUserDataCheck.Left := ScaleX(16);
  DeleteUserDataCheck.Top := UninstallProgressForm.StatusLabel.Top + ScaleY(42);
  DeleteUserDataCheck.Width := UninstallProgressForm.ClientWidth - ScaleX(32);
  DeleteUserDataCheck.Caption := '同时删除全部用户数据库、配置、任务和会话（危险）';
  DeleteUserDataCheck.Checked := False;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usUninstall) and DeleteUserDataCheck.Checked then
  begin
    if MsgBox(
      '再次确认：永久删除本机 Hermes OPC 的全部用户数据？此操作不可恢复。',
      mbConfirmation,
      MB_YESNO
    ) = IDYES then
      DelTree(ExpandConstant('{localappdata}\HermesOPC'), True, True, True);
  end;
end;
