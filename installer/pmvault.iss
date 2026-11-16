; PMVault Windows 安装包（Inno Setup 6 脚本）
;
; 用法（CI 里由 .github/workflows/release.yml 调用）：
;   iscc "installer/pmvault.iss" /DAppVersion="1.0.0" ^
;        /DSourceDir="<仓库根>\build\windows" /DOutputDir="<仓库根>\dist"
; 本地手动编译（需先跑过 flet build windows，见 README）：
;   iscc installer/pmvault.iss
;   不传参数时用下面的默认值：版本 1.0.0、源目录 ..\build\windows、
;   输出到 ..\dist，安装包名为 pmvault-1.0.0-windows-setup.exe。
;
; 关键约定（来自 Flet 官方文档 + 本仓库实际配置）：
; - 主程序名是 pmvault.exe：pmvault 没有配 [tool.flet.windows].artifact，
;   也没有传 --artifact，按 Flet 的解析顺序 artifact 取 [project].name，
;   即 pmvault；[tool.flet].product = "PMVault" 只是显示名，不决定 exe 名。
; - build/windows/ 里除了 pmvault.exe，还有 flutter_windows.dll、data/
;   等运行时文件，[Files] 一节用 recursesubdirs 把整个目录一起打包，
;   只拷 exe 是跑不起来的。
; - 如果将来改了 artifact 名或 exe 名，只改下面两处：AppExe 的值，
;   以及 [Icons] / [Run] 里引用的 {#AppExe}（它们都用 AppExe 这个宏，
;   所以实际上只改 AppExe 一处即可）；如果 flet build 的输出目录变了，
;   改 SourceDir 的默认值或编译时传 /DSourceDir。
; - 本仓库没有放 Windows 图标文件（assets 里没有 icon_windows.ico），
;   所以这里不指定 SetupIconFile / UninstallDisplayIcon 用 exe 自带图标，
;   编译不会因缺图标文件而失败。以后放了图标再加这两行。

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\windows"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "pmvault-" + AppVersion + "-windows-setup"
#endif

#define AppName "PMVault"
#define AppExe "pmvault.exe"
#define AppPublisher "phis123"
#define AppURL "https://github.com/phis123/pmvault"

[Setup]
; AppId 固定不变：换了它，Windows 会把新版本当成另一个程序，旧版
; 卸载信息就对不上了。首次生成后不要改。
AppId={{05B74963-963E-4C1C-9299-169776D2C17A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
; 单用户安装，不要求管理员权限；装到当前用户的程序目录。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 只要 64 位 Windows（Flutter Windows 构建只出 x64）。
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Flutter 要求 Windows 10 及以上。
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
; 安装包本身不签名：没有买代码签名证书，Windows SmartScreen
; 首次运行时可能提示“未知发布者”，点“更多信息 → 仍要运行”即可。
; 以后有了证书，在 CI 里给 setup.exe 签名再发 Release。

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 整个 build/windows 目录原样装过去（含 pmvault.exe、flutter_windows.dll、data/）。
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
