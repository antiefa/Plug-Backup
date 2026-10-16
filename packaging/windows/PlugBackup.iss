#ifndef AppVersion
  #error AppVersion must be provided by build.ps1
#endif
#ifndef AppNumericVersion
  #error AppNumericVersion must be provided by build.ps1
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif

[Setup]
AppId={{C7B8A9D5-90C6-4DF7-A8F8-AE6881E1171B}
AppName=Plug & Backup
AppVersion={#AppVersion}
AppVerName=Plug & Backup {#AppVersion}
AppPublisher=Plug & Backup
DefaultDirName={localappdata}\Programs\Plug & Backup
DefaultGroupName=Plug & Backup
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=6.1sp1
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
OutputDir={#OutputDir}
OutputBaseFilename=PlugBackup-{#AppVersion}-setup
UninstallDisplayName=Plug & Backup
UninstallDisplayIcon={app}\PlugBackup.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
VersionInfoVersion={#AppNumericVersion}
VersionInfoProductVersion={#AppVersion}
SetupManifestFile=windows.manifest

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные ярлыки:"; Flags: unchecked

[Files]
Source: "{#OutputDir}\PlugBackup\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#OutputDir}\plugbackup-cli\*"; DestDir: "{app}\cli"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Plug & Backup"; Filename: "{app}\PlugBackup.exe"
Name: "{autodesktop}\Plug & Backup"; Filename: "{app}\PlugBackup.exe"; Tasks: desktopicon
Name: "{group}\Удалить Plug & Backup"; Filename: "{uninstallexe}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\cli"

[Code]
function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if MsgBox('Удалить также настройки Plug & Backup из этой учётной записи?',
       mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then begin
    DelTree(ExpandConstant('{userappdata}\PlugBackup'), True, True, True);
    DelTree(ExpandConstant('{localappdata}\PlugBackup'), True, True, True);
  end;
end;
