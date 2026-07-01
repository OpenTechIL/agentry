; packaging/windows/agy.iss — Inno Setup script for the `agy` CLI installer.
;
; Consumed by release-binaries.yml. The PyInstaller binary must exist at
; ..\..\dist\agy.exe first. Version is passed on the command line:
;
;   iscc /DMyAppVersion=1.2.3 packaging\windows\agy.iss
;
; Produces: dist\agy-<version>-windows-x86_64-setup.exe
;
; This is a per-user install (no admin required): it drops agy.exe under
; %LOCALAPPDATA%\Programs\agentry and adds that directory to the user PATH,
; matching the behaviour of install.ps1.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "agentry"
#define MyAppExe "agy.exe"
#define MyAppPublisher "OpenTech"
#define MyAppURL "https://github.com/OpenTechIL/agentry"

[Setup]
; A stable, generated AppId keeps upgrades/uninstalls consistent across versions.
AppId={{6F3A9C2E-8B4D-4E7A-9C1F-2D5E7A0B3C48}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\agentry
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ChangesEnvironment=yes
OutputDir=..\..\dist
OutputBaseFilename=agy-{#MyAppVersion}-windows-x86_64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
; Prepend the install dir to the user PATH if it is not already present.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{app};{olddata}"; Check: NeedsAddPath(ExpandConstant('{app}'))

[Code]
function NeedsAddPath(Param: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  { Look for the exact dir, delimited by semicolons, case-insensitively. }
  Result := Pos(';' + Lowercase(Param) + ';', ';' + Lowercase(OrigPath) + ';') = 0;
end;
