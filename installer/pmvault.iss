; PMVault Windows 安装包（Inno Setup 6 脚本 · v2.0 双端统一 Flet 版）
;
; 关键红线（绝不可改）：
; 1. AppId 必须保持为 {{8F3C2A54-9B71-4D2E-A6C8-5E1F7B0D3A92}，与用户已装 1.0.4 完全一致，
;    才能实现覆盖升级（覆盖后新程序自动发现原 data\vault.pmvault 并引导单向升级为 v2）。
; 2. 禁止配置 [UninstallDelete] 删除 data\，卸载绝不能删除用户密码库。

#ifndef AppVersion
  #define AppVersion "2.0.1"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\windows"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "PMVault-" + AppVersion + "-setup"
#endif

#define AppName "账号密码管理"
#define AppExe "pmvault.exe"
#define AppPublisher "phis123"
#define AppURL "https://github.com/phis123/pmvault"

[Setup]
; 必须保留末尾双右大括号 }}，与本机已装 1.0.4 注册表项 {8F3C2A54-9B71-4D2E-A6C8-5E1F7B0D3A92}}_is1 完全一致
AppId={{8F3C2A54-9B71-4D2E-A6C8-5E1F7B0D3A92}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; 覆盖旧版 1.0.4 时清理旧 Tkinter 运行时文件，绝不触碰 data\ 下的任何用户密码库
Type: files; Name: "{app}\PMVaultTk.exe"
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
