; Setup fuer das PDF-Scan-Werkzeug (Bearbeiten, Betrachten, Zusammenfassen).
;
; Gebaut wird das ueber bauen.ps1 im uebergeordneten Ordner, nicht von Hand -
; das Skript legt vorher den Programmordner unter paket\programm an.
;
; Installiert wird fuer alle Benutzer unter Program Files (einmalige Bestaetigung der
; Benutzerkontensteuerung, PrivilegesRequired=admin). Kontextmenue-Eintraege und die
; Registrierung als waehlbares Standardprogramm laufen deshalb ueber HKA, also
; maschinenweit (HKLM) - dort zeigt Windows' "Andere App auswaehlen"-Dialog sie zuverlaessig an.
; Frueher gab es dafuer die Unteraufgabe "maschinenweit eintragen"; sie entfaellt.
; Nur MultipleInvokePromptMinimum bleibt benutzerbezogen (HKCU).
;
; Der frueher noetige Wrapper PDF-Betrachten.exe entfaellt: PyInstaller baut
; hier eine "echte" .exe, die Windows nicht wie pythonw.exe aus dem "Andere
; App auswaehlen"-Dialog ausblendet. Beide Rollen (Bearbeiten/Betrachten)
; laufen ueber dieselbe PDF-Werkzeug.exe mit/ohne --view.

#define Programm    "PDF-Scan-Werkzeug"
#define Kurzname    "PDF-Werkzeug"
#define Fassung     "1.6.0"
#define Urheber     "Stefan Wruck"
#define Quelle      "programm\PDF-Werkzeug"
#define QuellOrdner "..\..\_2026-08-21-PDF-Scan-Werkzeug"
#define ExeName     "PDF-Werkzeug.exe"

[Setup]
AppId={{43047EC4-0968-49E4-969F-7AFD9B644EDF}
AppName={#Programm}
AppVersion={#Fassung}
AppVerName={#Programm} {#Fassung}
AppPublisher={#Urheber}
DefaultDirName={autopf}\{#Kurzname}
DefaultGroupName={#Programm}
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=admin
OutputBaseFilename={#Kurzname}-Setup-{#Fassung}
SetupIconFile={#QuellOrdner}\pdf_werkzeug.ico
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#Programm}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
AppReadmeFile={app}\Anleitung.txt

[Languages]
Name: "deutsch"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopsymbol"; Description: "Verknuepfung auf dem Desktop anlegen"; GroupDescription: "Zusaetzliche Verknuepfungen:"
Name: "kontextmenue"; Description: "Rechtsklick-Menue einrichten (PDF bearbeiten / PDF betrachten / Ausgewaehlte in eine PDF zusammenfassen)"; GroupDescription: "Windows-Integration:"
Name: "standardprogramm"; Description: "Als waehlbares Standardprogramm fuer PDF-Dateien eintragen (""Oeffnen mit"" -> ""Andere App auswaehlen"")"; GroupDescription: "Windows-Integration:"

[Files]
; Das gepackte Programm samt Python, tkinter, PyMuPDF, Pillow, numpy,
; pywin32 und den Tesseract-Sprachdateien (tessdata).
Source: "{#Quelle}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Die Anleitung liegt eine Ebene hoeher im Projektordner
Source: "..\Anleitung.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme
; Icon fuer den Kontextmenue-Eintrag "PDF bearbeiten" (separat, weil
; PyInstaller nur das Programm-Icon selbst mitpackt)
Source: "{#QuellOrdner}\pdf-bearbeiten.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#Programm}"; Filename: "{app}\{#ExeName}"; IconFilename: "{app}\pdf_werkzeug.ico"
Name: "{group}\{#Programm} (nur Betrachten)"; Filename: "{app}\{#ExeName}"; Parameters: "--view"; IconFilename: "{app}\pdf_werkzeug.ico"
Name: "{group}\{#Programm} Einstellungen"; Filename: "{app}\{#ExeName}"; Parameters: "--einstellungen"; IconFilename: "{app}\pdf_werkzeug.ico"
Name: "{group}\Anleitung"; Filename: "{app}\Anleitung.txt"
Name: "{group}\{#Programm} deinstallieren"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#Programm}"; Filename: "{app}\{#ExeName}"; IconFilename: "{app}\pdf_werkzeug.ico"; Tasks: desktopsymbol

[Run]
Filename: "{app}\{#ExeName}"; Description: "{#Programm} jetzt starten"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Der Entpackordner von PyInstaller
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Registry]
; --- Kontextmenue "PDF bearbeiten" (fuer .pdf-Dateien) -------------------
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFBearbeiten"; \
    ValueType: string; ValueName: ""; ValueData: "PDF bearbeiten"; \
    Tasks: kontextmenue; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFBearbeiten"; \
    ValueType: string; ValueName: "Icon"; ValueData: "{app}\pdf-bearbeiten.ico"; \
    Tasks: kontextmenue
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFBearbeiten\command"; \
    ValueType: string; ValueName: ""; ValueData: """{app}\{#ExeName}"" ""%1"""; \
    Tasks: kontextmenue

; --- Kontextmenue "PDF betrachten" (fuer .pdf-Dateien) --------------------
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFBetrachten"; \
    ValueType: string; ValueName: ""; ValueData: "PDF betrachten"; \
    Tasks: kontextmenue; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFBetrachten"; \
    ValueType: string; ValueName: "Icon"; ValueData: "{app}\pdf_werkzeug.ico"; \
    Tasks: kontextmenue
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\PDFBetrachten\command"; \
    ValueType: string; ValueName: ""; ValueData: """{app}\{#ExeName}"" ""%1"" --view"; \
    Tasks: kontextmenue

; --- Kontextmenue "Ausgewaehlte in eine PDF zusammenfassen" (alle Dateien) -
; Unter HKCU\...\Classes\*\shell, nicht SystemFileAssociations\.pdf: Bei
; gemischter Auswahl (PDF + Bild) zeigt der Explorer nur Befehle, die fuer
; alle markierten Dateitypen gelten - siehe README des Werkzeugs.
Root: HKA; Subkey: "Software\Classes\*\shell\PDFZusammenfassen"; \
    ValueType: string; ValueName: ""; ValueData: "Ausgewählte in eine PDF zusammenfassen"; \
    Tasks: kontextmenue; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\*\shell\PDFZusammenfassen"; \
    ValueType: string; ValueName: "Icon"; ValueData: "{app}\pdf_werkzeug.ico"; \
    Tasks: kontextmenue
Root: HKA; Subkey: "Software\Classes\*\shell\PDFZusammenfassen\command"; \
    ValueType: string; ValueName: ""; ValueData: """{app}\{#ExeName}"" ""%1"" --merge"; \
    Tasks: kontextmenue

; Ab wie vielen markierten Dateien der Explorer Kontextmenue-Befehle
; ausblendet, die je Datei einmal gestartet wuerden (auch "Oeffnen"). Windows-
; Vorgabe ist 15, zu wenig fuer "Zusammenfassen" mit mehr Dateien. Wird nur
; angehoben, nie abgesenkt (siehe InvokeLimitNiedrig unten).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Explorer"; \
    ValueType: dword; ValueName: "MultipleInvokePromptMinimum"; ValueData: "100"; \
    Tasks: kontextmenue; Check: InvokeLimitNiedrig

; --- Als waehlbares Standardprogramm registrieren (benutzerbezogen) -------
Root: HKA; Subkey: "Software\Classes\Applications\PDF-Werkzeug-Bearbeiten.exe"; \
    ValueType: string; ValueName: ""; ValueData: "PDF bearbeiten (PDF-Scan-Werkzeug)"; \
    Tasks: standardprogramm; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\PDF-Werkzeug-Bearbeiten.exe"; \
    ValueType: string; ValueName: "FriendlyAppName"; ValueData: "PDF bearbeiten (PDF-Scan-Werkzeug)"; \
    Tasks: standardprogramm
Root: HKA; Subkey: "Software\Classes\Applications\PDF-Werkzeug-Bearbeiten.exe\DefaultIcon"; \
    ValueType: string; ValueName: ""; ValueData: "{app}\pdf-bearbeiten.ico"; \
    Tasks: standardprogramm
Root: HKA; Subkey: "Software\Classes\Applications\PDF-Werkzeug-Bearbeiten.exe\shell\open\command"; \
    ValueType: string; ValueName: ""; ValueData: """{app}\{#ExeName}"" ""%1"""; \
    Tasks: standardprogramm
Root: HKA; Subkey: "Software\Classes\Applications\PDF-Werkzeug-Bearbeiten.exe\SupportedTypes"; \
    ValueType: string; ValueName: ".pdf"; ValueData: ""; \
    Tasks: standardprogramm
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithList\PDF-Werkzeug-Bearbeiten.exe"; \
    ValueType: string; ValueName: ""; ValueData: ""; \
    Tasks: standardprogramm; Flags: uninsdeletekey

Root: HKA; Subkey: "Software\Classes\Applications\PDF-Werkzeug-Betrachten.exe"; \
    ValueType: string; ValueName: ""; ValueData: "PDF betrachten (PDF-Scan-Werkzeug)"; \
    Tasks: standardprogramm; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\PDF-Werkzeug-Betrachten.exe"; \
    ValueType: string; ValueName: "FriendlyAppName"; ValueData: "PDF betrachten (PDF-Scan-Werkzeug)"; \
    Tasks: standardprogramm
Root: HKA; Subkey: "Software\Classes\Applications\PDF-Werkzeug-Betrachten.exe\DefaultIcon"; \
    ValueType: string; ValueName: ""; ValueData: "{app}\pdf_werkzeug.ico"; \
    Tasks: standardprogramm
Root: HKA; Subkey: "Software\Classes\Applications\PDF-Werkzeug-Betrachten.exe\shell\open\command"; \
    ValueType: string; ValueName: ""; ValueData: """{app}\{#ExeName}"" ""%1"" --view"; \
    Tasks: standardprogramm
Root: HKA; Subkey: "Software\Classes\Applications\PDF-Werkzeug-Betrachten.exe\SupportedTypes"; \
    ValueType: string; ValueName: ".pdf"; ValueData: ""; \
    Tasks: standardprogramm
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithList\PDF-Werkzeug-Betrachten.exe"; \
    ValueType: string; ValueName: ""; ValueData: ""; \
    Tasks: standardprogramm; Flags: uninsdeletekey

[Messages]
deutsch.WelcomeLabel2=Damit wird [name/ver] eingerichtet.%n%nDas Werkzeug bearbeitet und betrachtet PDFs (OCR, Gradationskurven, Geradeausrichten, Zuschneiden, Seiten drehen/löschen, Zusammenfassen mehrerer Dateien) und lässt sich als Rechtsklick-Eintrag sowie als wählbares Standardprogramm einrichten.

[Code]
{ Hebt MultipleInvokePromptMinimum nur an, senkt einen bereits hoeheren
  Wert (falls der Benutzer ihn aus anderem Grund gesetzt hat) nicht ab. }
function InvokeLimitNiedrig(): Boolean;
var
  aktuell: Cardinal;
begin
  if not RegQueryDWordValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Explorer',
                             'MultipleInvokePromptMinimum', aktuell) then
    Result := True
  else
    Result := aktuell < 100;
end;
