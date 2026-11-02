# Baut aus dem PDF-Scan-Werkzeug ein Programm, das auf fremden Windows-
# Rechnern laeuft.
#
# Schritt eins packt Python samt tkinter, PyMuPDF, Pillow, numpy und pywin32
# sowie die Tesseract-Sprachdateien (tessdata) mit PyInstaller in einen
# Ordner. Schritt zwei macht daraus mit Inno Setup eine Setup.exe. Der
# Zielrechner braucht danach nichts weiter - kein Python, keine Zusatzpakete.
#
# Anders als beim bisherigen Handbetrieb (.reg-Dateien mit fest verdrahtetem
# G:\...-Pfad) traegt der Installer selbst die Kontextmenue- und
# Standardprogramm-Eintraege in die Registry ein, mit dem tatsaechlichen
# Installationspfad. Der bisherige Wrapper PDF-Betrachten.exe entfaellt:
# PyInstaller baut hier eine "echte" .exe, die Windows nicht mehr wie
# pythonw.exe aus dem "Andere App auswaehlen"-Dialog ausblendet.
#
# Aufruf:  powershell -ExecutionPolicy Bypass -File bauen.ps1
#          bauen.ps1 -NurProgramm   baut nur den Ordner, kein Setup
#          bauen.ps1 -Sauber        wirft vorherige Bauergebnisse weg

param(
    [switch]$NurProgramm,
    [switch]$Sauber
)

$ErrorActionPreference = 'Stop'

$Wurzel   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Quellen  = Join-Path (Split-Path -Parent $Wurzel) '_2026-08-21-PDF-Scan-Werkzeug'
$Paket    = Join-Path $Wurzel 'paket'
$Bauen    = Join-Path $Paket 'zwischenstand'
$Fertig   = Join-Path $Paket 'programm'
$Setup    = Join-Path $Paket 'setup'
$Skript   = Join-Path $Quellen 'pdf_tool.py'
$Symbol   = Join-Path $Quellen 'pdf_werkzeug.ico'
$Tessdata = Join-Path $Quellen 'tessdata'
$Iss      = Join-Path $Paket 'PDF-Werkzeug.iss'
$Python   = 'C:\Python314\python.exe'

function Sagen($text) { Write-Host "  $text" -ForegroundColor Cyan }

Write-Host ''
Write-Host 'PDF-Scan-Werkzeug paketieren' -ForegroundColor White
Write-Host '-----------------------------'

# ---------------------------------------------------------------- Vorpruefung

if (-not (Test-Path $Python)) {
    $gefunden = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $gefunden) { throw "Python wurde nicht gefunden ($Python)." }
    $Python = $gefunden.Source
}
Sagen "Python: $Python"

& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Sagen 'PyInstaller fehlt, wird nachgeholt ...'
    & $Python -m pip install --user pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller liess sich nicht installieren.' }
}

& $Python -c "import win32print" 2>$null
if ($LASTEXITCODE -ne 0) { throw 'pywin32 fehlt - das Drucken im Viewer braucht es (pip install pywin32).' }

foreach ($noetig in @('pdf_tool.py', 'pdf_werkzeug.ico', 'pdf-bearbeiten.ico')) {
    if (-not (Test-Path (Join-Path $Quellen $noetig))) {
        throw "Im Quellordner fehlt $noetig ($Quellen)."
    }
}
if (-not (Test-Path (Join-Path $Tessdata 'deu.traineddata')) -or
    -not (Test-Path (Join-Path $Tessdata 'eng.traineddata'))) {
    throw "tessdata\deu.traineddata / eng.traineddata fehlen unter $Tessdata - " +
          "einmal das Werkzeug starten und eine Seite mit OCR bearbeiten, dann " +
          "laedt es sie automatisch nach, oder von Hand ablegen."
}
Sagen "Quellen: $Quellen"

# Vor dem Bauen wenigstens die Syntax pruefen - ein Paket aus kaputtem
# Stand nuetzt niemandem. Ein echter GUI-Selbsttest existiert in pdf_tool.py
# nicht (kein --selbsttest-Schalter).
Sagen 'Syntaxpruefung ...'
& $Python -m py_compile $Skript
if ($LASTEXITCODE -ne 0) { throw 'pdf_tool.py hat einen Syntaxfehler.' }
Sagen 'bestanden'

if ($Sauber) {
    Sagen 'Vorherige Bauergebnisse werden geloescht ...'
    foreach ($ordner in @($Bauen, $Fertig, $Setup)) {
        if (-not (Test-Path $ordner)) { continue }
        # Der Virenscanner haelt eben geschriebene DLLs manchmal noch kurz
        # fest. Ein paar Anlaeufe mit Pause reichen dagegen aus.
        for ($versuch = 1; $versuch -le 5; $versuch++) {
            try {
                Remove-Item $ordner -Recurse -Force -ErrorAction Stop
                break
            } catch {
                if ($versuch -eq 5) { throw }
                Start-Sleep -Seconds 3
            }
        }
    }
}

# ------------------------------------------------------------- PyInstaller

Write-Host ''
Write-Host 'Schritt 1: Programm packen' -ForegroundColor White

# --onedir, nicht --onefile: onefile entpackt bei jedem Start in den
# Temp-Ordner und laesst den Viewer spuerbar spaeter erscheinen.
# tessdata wird mitgepackt, damit OCR auf dem Zielrechner offline
# funktioniert (kein Nachladen von github.com noetig).
$argumente = [System.Collections.Generic.List[string]]@(
    '-m', 'PyInstaller',
    '--noconfirm', '--clean',
    '--onedir', '--windowed',
    '--name', 'PDF-Werkzeug',
    '--distpath', $Fertig,
    '--workpath', $Bauen,
    '--specpath', $Bauen,
    '--paths', $Quellen,
    '--icon', $Symbol,
    '--add-data', "$Tessdata;tessdata",
    '--exclude-module', 'matplotlib',
    '--exclude-module', 'pytest',
    '--exclude-module', 'IPython',
    '--exclude-module', 'unittest'
)
$argumente.Add($Skript)

& $Python @argumente
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller ist ausgestiegen.' }

$Programm = Join-Path $Fertig 'PDF-Werkzeug'
if (-not (Test-Path (Join-Path $Programm 'PDF-Werkzeug.exe'))) {
    throw 'PyInstaller hat keine exe hinterlassen.'
}
# PyInstaller legt per --add-data mitgepackte Daten im onedir-Modus unter
# _internal ab, nicht neben die .exe - pdf_tool.py sucht dort ueber
# sys._MEIPASS auch danach (siehe TESSDATA_DIR).
if (-not (Test-Path (Join-Path $Programm '_internal\tessdata\deu.traineddata'))) {
    throw 'tessdata wurde nicht mitverpackt - --add-data pruefen.'
}

$groesse = (Get-ChildItem $Programm -Recurse -File | Measure-Object Length -Sum).Sum/1MB
Sagen ("fertig: {0}  ({1:N0} MB)" -f $Programm, $groesse)

# --------------------------------------------------------------- Rauchtest

Write-Host ''
Write-Host 'Schritt 1b: Rauchtest' -ForegroundColor White

$TestPdf = Join-Path $env:TEMP 'pdf_werkzeug_rauchtest.pdf'
& $Python -c @"
import fitz
d = fitz.open()
d.new_page().insert_text((72, 72), 'Rauchtest')
d.save(r'$TestPdf')
"@
if ($LASTEXITCODE -ne 0) { throw 'Konnte keine Test-PDF erzeugen.' }

$exePfad = Join-Path $Programm 'PDF-Werkzeug.exe'
$proc = Start-Process -FilePath $exePfad -ArgumentList "`"$TestPdf`"", '--view' -PassThru
Start-Sleep -Seconds 3
if ($proc.HasExited) {
    throw "PDF-Werkzeug.exe ist sofort wieder beendet (Exitcode $($proc.ExitCode)) - Rauchtest fehlgeschlagen."
}
Sagen 'startet und laeuft - wird jetzt wieder beendet'
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Remove-Item $TestPdf -Force -ErrorAction SilentlyContinue

if ($NurProgramm) {
    Write-Host ''
    Write-Host 'Nur der Programmordner war gewuenscht. Fertig.' -ForegroundColor Green
    exit 0
}

# ----------------------------------------------------------------- Inno Setup

Write-Host ''
Write-Host 'Schritt 2: Setup bauen' -ForegroundColor White

$Iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    'C:\Program Files (x86)\Inno Setup 7\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe',
    'C:\Program Files\Inno Setup 7\ISCC.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $Iscc) {
    $Iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
}
if (-not $Iscc) {
    Write-Host ''
    Write-Host 'Inno Setup ist nicht installiert - ohne das gibt es keine Setup.exe.' -ForegroundColor Yellow
    Write-Host 'Zu holen bei https://jrsoftware.org/isdl.php (kostenlos, offline).' -ForegroundColor Yellow
    Write-Host "Der Programmordner liegt trotzdem fertig unter $Programm." -ForegroundColor Yellow
    exit 1
}
Sagen "Inno Setup: $Iscc"

New-Item -ItemType Directory -Force -Path $Setup | Out-Null
& $Iscc $Iss "/O$Setup"
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup ist ausgestiegen.' }

$exe = Get-ChildItem $Setup -Filter '*.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host ''
Write-Host ("Fertig: {0}  ({1:N0} MB)" -f $exe.FullName, ($exe.Length/1MB)) -ForegroundColor Green
Write-Host 'Diese Datei laesst sich auf jeden Windows-Rechner mitnehmen.'
Write-Host 'Beim ersten Start meldet SmartScreen "Unbekannter Herausgeber" -'
Write-Host 'das Paket ist nicht signiert. Weitere Informationen, dann Trotzdem ausfuehren.'
