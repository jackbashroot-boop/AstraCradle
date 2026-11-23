; AstraCradle Windows Installer Script (Inno Setup 6+)
[Setup]
AppName=AstraCradle
AppVersion=1.0.0
AppPublisher=AstraCradle Local Studio
DefaultDirName={localappdata}\Programs\AstraCradle
DefaultGroupName=AstraCradle
OutputDir=dist
OutputBaseFilename=AstraCradle-Setup-v1.0.0
SetupIconFile=..\..\assets\astracradle.ico
UninstallDisplayIcon={app}\assets\astracradle.ico
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest

[Files]
Source: "..\..\dist\AstraCradle\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AstraCradle"; Filename: "{app}\AstraCradle.exe"; IconFilename: "{app}\assets\astracradle.ico"
Name: "{autodesktop}\AstraCradle"; Filename: "{app}\AstraCradle.exe"; IconFilename: "{app}\assets\astracradle.ico"

[UninstallRun]
Filename: "{app}\AstraCradle.exe"; Parameters: "--uninstall"; RunOnceId: "AstraCradleCleanUninstall"
