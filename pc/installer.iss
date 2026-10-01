; AMUNTCHI PC V5 - programme d'installation Windows (Inno Setup 6)
#define MyAppVersion GetEnv("AMX_VERSION")

[Setup]
AppId={{EE4A9770-E090-488B-BA15-A753E9D6F7D5}
AppName=AMUNTCHI - Gestion de quincaillerie
AppVersion={#MyAppVersion}
AppVerName=AMUNTCHI {#MyAppVersion}
AppPublisher=Mini Quincaillerie AMUNTCHI
DefaultDirName={autopf}\AMUNTCHI
DefaultGroupName=AMUNTCHI
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=AMUNTCHI-PC-V5-Installation
SetupIconFile=assets\amuntchi.ico
UninstallDisplayIcon={app}\Amuntchi.exe
UninstallDisplayName=AMUNTCHI - Gestion de quincaillerie
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\Amuntchi\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "GUIDE_INSTALLATION_AMUNTCHI_V5.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "supabase_amuntchi.sql"; DestDir: "{app}"; Flags: ignoreversion
Source: "CHANGELOG_V5.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\AMUNTCHI"; Filename: "{app}\Amuntchi.exe"; IconFilename: "{app}\Amuntchi.exe"
Name: "{group}\Guide d'installation PC + Android"; Filename: "{app}\GUIDE_INSTALLATION_AMUNTCHI_V5.txt"
Name: "{group}\Désinstaller AMUNTCHI"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AMUNTCHI"; Filename: "{app}\Amuntchi.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Amuntchi.exe"; Description: "{cm:LaunchProgram,AMUNTCHI}"; Flags: nowait postinstall skipifsilent

[Messages]
french.WelcomeLabel2=Ce programme va installer AMUNTCHI {#MyAppVersion} (gestion de stock et de ventes) sur votre ordinateur.%n%nVos données existantes (produits, ventes, clients...) sont conservées : elles se trouvent dans votre dossier utilisateur et ne sont jamais effacées par l'installation ni par la désinstallation.
