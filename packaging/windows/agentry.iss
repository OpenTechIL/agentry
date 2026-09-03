; packaging/windows/agentry.iss — Inno Setup script for the `agentry` CLI installer.
;
; Consumed by release-binaries.yml. The PyInstaller binary must exist at
; ..\..\dist\agentry.exe first. Version is passed on the command line:
;
;   iscc /DMyAppVersion=1.2.3 packaging\windows\agentry.iss
;
; Produces: dist\agentry-<version>-windows-x86_64-setup.exe
;
; This is a per-user install (no admin required): it drops agentry.exe under
; %LOCALAPPDATA%\Programs\agentry and adds that directory to the user PATH,
; matching the behaviour of install.ps1.
;
; The short aliases agy.cmd and agyx.cmd are generated next to it: Windows offers no
; reliable symlink for an unprivileged install, so a one-line .cmd shim is the portable
; equivalent. `agy` is kept for back-compat but is also Google's Antigravity CLI command;
; `agyx` is the short name that cannot collide.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "agentry"
#define MyAppExe "agentry.exe"
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
OutputBaseFilename=agentry-{#MyAppVersion}-windows-x86_64-setup
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
{ Alias shims: written after install so they always point at the real exe name. }
procedure WriteAliasShim(const Name: string);
begin
  SaveStringToFile(ExpandConstant('{app}\') + Name + '.cmd',
    '@echo off' + #13#10 + '"%~dp0{#MyAppExe}" %*' + #13#10, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    WriteAliasShim('agy');
    WriteAliasShim('agyx');
  end;
end;

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
