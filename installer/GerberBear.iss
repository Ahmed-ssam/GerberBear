; Gerber Bear - Inno Setup script. Built by build_installer.bat (do not run directly):
; it expects build\python (embedded Python with all packages), build\app (the app code,
; Freerouting and the bundled JRE in app\tools) prepared by build_installer.ps1.

#define AppName "Gerber Bear"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6E7C2A4B-3F1D-4C8E-9B2A-6A9D0C5E7F11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Gerber Bear
DefaultDirName={localappdata}\Programs\Gerber Bear
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=GerberBear-Setup-{#AppVersion}
SetupIconFile=..\app\ai_pcb_designer\resources\branding\gerber_bear.ico
UninstallDisplayIcon={app}\app\ai_pcb_designer\resources\branding\gerber_bear.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
InfoBeforeFile=THIRD_PARTY_NOTICES.txt

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "build\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "build\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m ai_pcb_designer.app.main"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\ai_pcb_designer\resources\branding\gerber_bear.ico"; AppUserModelID: "GerberBear.Designer"
Name: "{group}\Check installation"; Filename: "{app}\python\python.exe"; Parameters: """{app}\app\check_install.py"" --quick --out ""{userdocs}\Gerber Bear\reports"""; WorkingDir: "{app}\app"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m ai_pcb_designer.app.main"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\ai_pcb_designer\resources\branding\gerber_bear.ico"; Tasks: desktopicon; AppUserModelID: "GerberBear.Designer"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m ai_pcb_designer.app.main"; WorkingDir: "{app}\app"; Description: "Start Gerber Bear"; Flags: nowait postinstall skipifsilent

[Code]
function KiCadFound(): Boolean;
var v: String;
begin
  Result := FileExists(ExpandConstant('{commonpf}\KiCad\10.0\bin\kicad-cli.exe')) or
            FileExists(ExpandConstant('{commonpf}\KiCad\9.0\bin\kicad-cli.exe')) or
            FileExists(ExpandConstant('{commonpf}\KiCad\8.0\bin\kicad-cli.exe'));
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and (not KiCadFound()) then
    MsgBox('KiCad 8, 9 or 10 was not found. Gerber Bear needs it to create boards.' + #13#10 +
           'Download it free from https://www.kicad.org/download/windows/ (default options).',
           mbInformation, MB_OK);
end;
