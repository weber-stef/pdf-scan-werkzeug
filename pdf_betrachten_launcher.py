"""
Quellcode fuer PDF-Betrachten.exe.

Windows blendet bekannte Skript-Interpreter (pythonw.exe, cmd.exe, ...) aus
dem "Andere App auswaehlen"-Dialog aus, egal wie die Registry drumherum
aussieht. Deshalb dieser winzige, mit PyInstaller kompilierte Wrapper statt
einer direkten Registrierung von pythonw.exe als Standardprogramm.

Neu bauen nach Aenderungen an diesem Skript oder nach Verschieben des
Projektordners:

    python -m pip install pyinstaller
    python -m PyInstaller --onefile --noconsole --name PDF-Betrachten ^
        --icon pdf_werkzeug.ico pdf_betrachten_launcher.py

Die fertige PDF-Betrachten.exe aus dist\ hierher kopieren (ueberschreiben).
"""
import subprocess
import sys

PYTHONW = r"C:\Python314\pythonw.exe"
SCRIPT = r"G:\_Projekte\_Projekte-Wissen\_0000_Scripting-with-Claude-2026\_2026-08-21-PDF-Scan-Werkzeug\pdf_tool.py"

subprocess.Popen([PYTHONW, SCRIPT, *sys.argv[1:], "--view"])
