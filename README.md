# PDF-Scan-Werkzeug

Kleines Tkinter-Tool zum Nachbearbeiten gescannter PDFs (z. B. aus VueScan) sowie
zum schnellen Betrachten und Textkopieren aus beliebigen PDFs.

## Funktionen

**Bearbeitung (linke/mittlere Spalte im Hauptfenster)**
- OCR-Texterkennung (eingebautes Tesseract via PyMuPDF; Sprachdateien werden
  bei Bedarf automatisch nach `tessdata/` heruntergeladen)
- Gradationskurven (Schwarzpunkt / Weißpunkt / Gamma) zum Aufhellen/Kontrastieren
  von Scans
- Gerade ausrichten (automatische Schräglagen-Korrektur)
- Bereich freistellen (Rechteck in der Vorschau aufziehen; wahlweise für alle
  Seiten gemeinsam oder je Seite individuell)
- Einzelne Seiten löschen oder drehen (±90°/180°)
- Mehrere Dateien zu einer PDF zusammenfassen (Knopf „In eine PDF
  zusammenfassen…“ unter der Dateiliste; Reihenfolge im Fenster änderbar).
  Mitgewandelt werden: JPG, PNG, TIF (mehrseitige TIFFs mit allen Seiten),
  BMP, GIF, PPM, WebP, PSD, dazu SVG sowie Textdateien (.txt und über den
  Textweg auch .md, .csv, .log). Kommt MuPDF mit einem Bild nicht zurecht (etwa PSD mit Sonderfarben,
  Meldung „Non CMYK spot found in PSD“), springt Pillow ein und liest die
  zusammengerechnete Ansicht; WebP kann ohnehin nur Pillow. Eine unlesbare
  Datei bricht den Lauf nicht ab,
  sie wird übersprungen und danach gemeldet. Bildseiten bekommen die Größe
  des Bildes in Pixeln bei 72 dpi – ein großes Foto wird also eine große
  Seite.
- Umbenennen der Ausgabedatei
- Datum an den Dateinamen anfügen (`_YYYY-MM-DD`, wahlweise Datei- oder
  Verarbeitungsdatum)
- Ausgabe wahlweise in Unterordner „bearbeitet“, im gleichen Ordner oder als
  Ersetzung des Originals
- Nach dem Verarbeiten: bearbeitete PDF direkt öffnen oder ihren **Ordner im
  Explorer öffnen** (Datei wird dort markiert)

**Integrierter PDF-Viewer** (Doppelklick auf eine Datei in der Liste, oder
„Bearbeitetes PDF öffnen“)
- Blättern (Pfeiltasten, Bild-auf/-ab, Mausrad), Zoom (Seitenbreite, ganze
  Seite, feste Prozentstufen), Vollbild (F11 / Esc)
- **Text markieren und kopieren:** Mit gedrückter linker Maustaste einen
  Bereich über dem Seitentext aufziehen – der enthaltene Text wird sofort in
  die Zwischenablage kopiert (Bestätigung in der Statuszeile unten). Über den
  „⧉ Kopieren“-Button, Rechtsklick → „Kopieren“ oder Strg+C lässt sich die
  letzte Markierung erneut kopieren.
- **„🖨 Drucken“-Button (Strg+P):** Druckfenster mit Druckerauswahl,
  Seitenbereich (alle, nur die angezeigte Seite oder eine Eingabe wie
  „1,3,5-8“), Anzahl der Exemplare und automatischer Drehung, wenn Seite und
  Papier unterschiedlich herum liegen. Gerendert wird mit höchstens 300 dpi,
  gedruckt über die Windows-Druckerschnittstelle (benötigt pywin32). Der
  Fortschritt steht im Fenster, „Abbrechen“ stoppt den laufenden Auftrag.
  Bei Druckern, die in eine Datei schreiben (Microsoft Print to PDF, XPS),
  wird vorher nach dem Zielnamen gefragt.
- **„🗑 Seite löschen“-Button (Entf, auch Rechtsklick):** löscht die angezeigte
  Seite oder einen Bereich wie „1,3,5-8“. Entweder wird die Datei selbst geändert
  (vorher auf Wunsch eine Sicherungskopie `…_vor-Loeschung.pdf`) oder das Ergebnis
  als neue Datei im gleichen Ordner gespeichert und das Original bleibt
  unverändert. Es muss mindestens eine Seite übrig bleiben. Geschrieben wird
  erst in eine Zwischendatei, das Original wird erst danach ersetzt; bei einem
  Fehler bleibt es unverändert. Im Bearbeiter markiert „Seite löschen“ unter der
  Vorschau die Seite zum Löschen (rotes Kreuz), gelöscht wird beim Verarbeiten.
- **„✎ Bearbeiten“-Button:** öffnet das volle PDF-Scan-Werkzeug mit dieser
  Datei (die reine Viewer-Ansicht schließt sich dabei). Als Vorgabe für
  diesen Bearbeitungsvorgang sind dann „Original ersetzen“ und 300 dpi
  eingestellt statt der sonstigen Standardwerte.

**Windows-Integration**
- Einzelinstanz-Mechanismus: Läuft das Tool bereits, werden per Doppelklick
  geöffnete PDFs an die laufende Instanz weitergereicht statt ein zweites
  Fenster zu öffnen.
- Kontextmenü-Eintrag „PDF bearbeiten“ öffnet das volle Werkzeug mit dieser
  Datei.
- Kontextmenü-Eintrag „PDF betrachten“ öffnet **nur** den PDF-Viewer (das
  Hauptfenster des Werkzeugs bleibt verborgen); schließt man diesen Viewer
  wieder, ohne auf „✎ Bearbeiten“ zu klicken, beendet sich das Programm
  vollständig. Klickt man auf „✎ Bearbeiten“, erscheint das Hauptfenster mit
  „Original ersetzen“ / 300 dpi vorausgewählt.
- „PDF betrachten“ lässt sich außerdem als echtes **Standardprogramm für
  `.pdf`-Dateien** registrieren (Doppelklick auf eine PDF öffnet dann direkt
  den Viewer), siehe unten.

## Voraussetzungen

- Python 3.10+
- Pakete: `pymupdf`, `Pillow`, `numpy`

Installation z. B. mit:

    pip install pymupdf Pillow numpy

## Start

    python pdf_tool.py
    python pdf_tool.py datei1.pdf datei2.pdf   # Dateien gleich mit hinzufügen

Alternativ per Doppelklick auf `PDF-Werkzeug starten.bat`.

## Windows-Integration einrichten (optional)

Die `.reg`-Dateien enthalten den absoluten Pfad zu diesem Ordner
(`G:\_Projekte\_Projekte-Wissen\_0000_Scripting-with-Claude-2026\_2026-08-21-PDF-Scan-Werkzeug`)
und zu `C:\Python314\pythonw.exe`. Bei Bedarf vor dem Import anpassen.

- **`PDF-Bearbeiten.reg`** — fügt „PDF bearbeiten“ zum Rechtsklick-Kontextmenü
  von PDF-Dateien hinzu. Rückgängig machen mit `PDF-Bearbeiten-Remove.reg`.
- **`PDF-Betrachten.reg`** — fügt „PDF betrachten“ zum Rechtsklick-Kontextmenü
  hinzu; öffnet die Datei direkt im Viewer (Vollbild-Ansicht mit F11), ohne
  den Bearbeitungsdialog anzuzeigen. Läuft das Tool schon, wird die Datei in
  der laufenden Instanz im Viewer geöffnet statt ein zweites Programm zu
  starten. Rückgängig machen mit `PDF-Betrachten-Remove.reg`.
- **`PDF-Zusammenfassen.reg`** — fügt „Ausgewählte in eine PDF
  zusammenfassen“ zum Rechtsklick-Kontextmenü von PDF- und Bilddateien hinzu
  (Ersatz für den weggefallenen Acrobat-Eintrag). Mehrere Dateien markieren,
  Rechtsklick, Eintrag wählen: Es öffnet sich ein Fenster, in dem die
  Reihenfolge geändert und das Ergebnis benannt wird. Rückgängig machen mit
  `PDF-Zusammenfassen-Remove.reg`.

  Technischer Hintergrund: Der Explorer startet den Befehl **einmal je
  markierter Datei**. Die erste Instanz belegt Port 48293, alle weiteren
  reichen ihren Pfad dorthin weiter (`--merge`). Die laufende Instanz wartet
  nach dem letzten eintreffenden Pfad 1,5 Sekunden und öffnet dann ein
  einziges Fenster mit allen Dateien. Wichtig ist dabei
  `MultiSelectModel=Document`: Nur damit ruft der Explorer den Befehl je Datei
  auf. Mit `Player` startet Windows ihn **einmal mit der ersten Datei** – im
  Fenster landet dann nur eine Datei und es gibt nichts zusammenzufassen
  (dieser Fehler war am 20.08.2026 kurz drin).

  **Ab 15 markierten Dateien** blendet der Explorer von sich aus alle Befehle
  aus, die je Datei einmal gestartet würden – dann fehlen im Kontextmenü nicht
  nur dieser Eintrag, sondern auch „PDF bearbeiten“, „PDF betrachten“ und
  „Öffnen“; stehen bleiben nur Shell-Erweiterungen wie Bulk Rename. Die Grenze
  heißt `MultipleInvokePromptMinimum` und steht über
  `PDF-Zusammenfassen-VieleDateien.reg` auf 100 (Windows-Vorgabe ist 15,
  zurück mit `…-Remove.reg`). Nach dem Import muss der Explorer neu gestartet
  werden. Getestet mit 20 gleichzeitigen Aufrufen: Es blieb eine Instanz übrig,
  alle 20 Dateien standen in einem Fenster.
  Registriert ist der Eintrag unter `HKCU\Software\Classes\*\shell`, also
  für **alle** Dateien. Das ist Absicht: Bei gemischter Auswahl (etwa PDFs und
  ein PSD) zeigt der Explorer nur Befehle, die für alle markierten Dateitypen
  gelten – typgebundene Einträge unter `SystemFileAssociations\.pdf` bzw.
  `…\image` fallen dann weg und der Eintrag fehlt scheinbar grundlos. Genau
  daran lag es am 20.08.2026. Nebenwirkung: Der Eintrag steht nun auch an
  Dateien, die sich nicht zusammenfassen lassen; solche Dateien werden beim
  Zusammenfassen übersprungen und gemeldet.

- **`PDF-Standardprogramm-Betrachten.reg`** — registriert „PDF betrachten
  (Scan-Werkzeug)“ als wählbares Standardprogramm für `.pdf` (danach:
  Rechtsklick auf eine PDF → „Öffnen mit“ → „Andere App auswählen“ → „PDF
  betrachten (Scan-Werkzeug)“ → Haken bei „Immer diese App verwenden“
  setzen, oder über die Windows-Einstellungen „Standard-Apps“). Rückgängig
  machen mit `PDF-Standardprogramm-Betrachten-Remove.reg`.
- **`PDF-Standardprogramm.reg`** — dieselbe Registrierung für „PDF
  bearbeiten“; taucht auf diesem Rechner aus technischen Gründen (siehe
  unten) aber nicht in Windows’ Auswahldialog auf und dient nur als
  Grundlage, falls das für „PDF betrachten“ genutzte Wrapper-Verfahren
  später auch dafür gebraucht wird. Für „PDF bearbeiten“ reicht der
  Kontextmenü-Eintrag oben.

Diese vier Dateien schreiben in `HKEY_CURRENT_USER` und brauchen **keine**
Adminrechte. Die beiden `PDF-Standardprogramm*.reg`-Dateien schreiben dagegen
in `HKEY_LOCAL_MACHINE` (maschinenweit) und lösen beim Doppelklick eine
UAC-Abfrage aus, die bestätigt werden muss — das ist nötig, weil Windows’
„Andere App auswählen“-Dialog auf diesem Rechner Einträge unter
`HKEY_CURRENT_USER` nicht anzeigt.

Nach jedem Import den Explorer neu starten, damit die Liste der verfügbaren
Apps aktualisiert wird (z. B. `taskkill /f /im explorer.exe`, Explorer
startet automatisch neu) — ein einfaches Neuladen des Fensters reicht nicht
immer.

**Warum eine `.exe` (`PDF-Betrachten.exe`) statt direkt `pythonw.exe`?**
Windows blendet bekannte Skript-Interpreter (`pythonw.exe`, `cmd.exe`, …) aus
dem „Andere App auswählen“-Dialog aus, unabhängig davon, wie korrekt die
Registry drumherum aussieht. `PDF-Betrachten.exe` ist ein winziger, mit
PyInstaller aus `pdf_betrachten_launcher.py` gebauter Wrapper, der dahinter
ganz normal `pythonw.exe pdf_tool.py --view` aufruft — der eigentliche Code
bleibt vollständig in `pdf_tool.py`. Nach Änderungen an
`pdf_betrachten_launcher.py` oder nach Verschieben des Projektordners neu
bauen (Anleitung im Kopfkommentar des Skripts) und die neue `.exe` hierher
kopieren.

Das Icon von `PDF-Betrachten.exe` selbst zeigt das Werkzeug-Icon, aber
`PDF-Standardprogramm-Betrachten.reg` trägt für die Datei-Icon-Anzeige
bewusst das klassische rote PDF-Icon ein (`_PDFFile.ico`, dasselbe, das
Acrobat als Standard genutzt hat), damit sich beim Umstellen des
Standardprogramms nur das Öffnen-Verhalten ändert, nicht das gewohnte Icon
aller `.pdf`-Dateien im Explorer. Nach dem Import ggf. den Icon-Cache
auffrischen: `ie4uinit.exe -show`, dann Explorer neu starten.

## Dateien in diesem Ordner

| Datei | Zweck |
|---|---|
| `pdf_tool.py` | Das Programm selbst |
| `tessdata/` | Heruntergeladene Tesseract-Sprachdateien (OCR) |
| `pdf-bearbeiten.ico` | Icon für Kontextmenü / Standardprogramm |
| `pdf_werkzeug.ico` | Icon für Verknüpfungen (Desktop/Startmenü) |
| `pdf_werkzeug_preview.png` | Vorschaubild des Programms |
| `PDF-Betrachten.exe` | Wrapper-.exe für „PDF betrachten“ als Standardprogramm (siehe oben) |
| `pdf_betrachten_launcher.py` | Quellcode von `PDF-Betrachten.exe` |
| `PDF-Bearbeiten*.reg` | Kontextmenü-Eintrag „PDF bearbeiten“ registrieren/entfernen |
| `PDF-Betrachten*.reg` | Kontextmenü-Eintrag „PDF betrachten“ registrieren/entfernen |
| `PDF-Zusammenfassen*.reg` | Kontextmenü-Eintrag „Ausgewählte in eine PDF zusammenfassen“ registrieren/entfernen |
| `PDF-Zusammenfassen-VieleDateien*.reg` | Hebt die 15-Dateien-Grenze des Explorers auf 100 an / setzt sie zurück |
| `PDF-Standardprogramm*.reg` | „PDF bearbeiten“ als Standardprogramm (siehe oben, zeigt aktuell nicht im Dialog) |
| `PDF-Standardprogramm-Betrachten*.reg` | „PDF betrachten“ als Standardprogramm registrieren/entfernen |
| `PDF-Werkzeug starten.bat` | Startet das Tool ohne Konsolenfenster |

**Hinweis:** Waren die `.reg`-Dateien bereits vor dem Verschieben dieses
Ordners importiert, zeigen die bestehenden Registry-Einträge noch auf den
alten Pfad. Die aktualisierten `.reg`-Dateien in diesem Ordner müssen dann
einmal erneut importiert werden, damit Kontextmenü und Standardprogramm
wieder auf `pdf_tool.py` an seinem neuen Ort verweisen.

## Stand

Am 21.08.2026 gesichert nach T:. Zuletzt kam das Zusammenfassen mehrerer Dateien zu einer PDF dazu, samt Kontextmenü-Eintrag „Ausgewählte in eine PDF zusammenfassen“.
Das Werkzeug wird weiter benutzt, der Ordner ist also nicht eingefroren – die Sicherung gibt nur diesen Stand wieder.

## Seitenleiste mit Vorschaubildern (Betrachter)

Links im Betrachter zeigt eine Leiste die Vorschaubilder aller Seiten (Knopf „▤ Seiten“ oder F4 blendet sie ein und aus). Ein Klick springt zur Seite. Mit gedrückter Maustaste lässt sich eine Seite an eine andere Stelle ziehen, eine orange Linie zeigt die Einfügestelle. Rechtsklick bietet „eine Position nach oben/unten“, „an den Anfang“, „ans Ende“ und „Seite löschen…“.
Das Umordnen schreibt die Datei sofort neu. Vor der ersten Änderung je Betrachter-Sitzung entsteht eine Sicherung `<Name>_vor-Umordnung.pdf`.

### Weitere Dateien in die PDF ziehen

Aus dem Explorer lassen sich PDFs (auch Bilder und Textdateien) auf die Seitenleiste ziehen. Ihre Seiten werden an der Abwurfstelle in die geöffnete PDF eingefügt, also zwischen den Vorschaubildern, auf die du sie fallen lässt. Ein Abwurf auf die große Seitenansicht fügt sie hinter der aktuellen Seite ein. Die Datei wird sofort neu geschrieben, vor der ersten Änderung dieser Art entsteht `<Name>_vor-Einfuegen.pdf`. Nicht lesbare Dateien werden übersprungen und gemeldet. Technisch läuft das über WM_DROPFILES per ctypes (Klasse `FileDrop`), ohne Zusatzbibliothek.

### Speichern unter

Der Knopf „📄 Speichern unter…“ (Strg+Umschalt+S) legt den aktuellen Stand der PDF, also mit Umordnung, eingefügten und gelöschten Seiten, als neue Datei ab. Vorschlag: `<Name>_geaendert.pdf`. Die geöffnete Datei bleibt unverändert, da Änderungen im Betrachter ohnehin sofort in sie geschrieben werden.

### Seitenleiste im Bearbeiter

Auch im Hauptfenster (Bearbeiter) steht links neben der Vorschau eine Leiste mit Vorschaubildern der Seiten der gewählten Datei. Ein Klick wählt die Seite, ein Ziehen ordnet sie um (orange Linie = Einfügestelle). Rechtsklick bietet Verschieben, Drehen, Löschen/Wiederherstellen und „Reihenfolge zurücksetzen“.
Wie bei Drehen und Löschen wird nur vorgemerkt: Die neue Reihenfolge wird erst beim Verarbeiten angewendet, gelöschte Seiten sind dann schon entfernt. Rote Kreuze und „(war 3)“ unter dem Bild zeigen die vorgemerkten Änderungen. Die Pfeile ◀ ▶ blättern in der neuen Reihenfolge.


## Kommentieren und Ändern im Betrachter (seit 01.11.2026)

Knopf „🖍 Kommentieren & Ändern“ (Strg+K) blendet eine zweite Leiste ein: Schreibmaschine, Pfeil, Rechteck, Kreis/Ellipse und „Text ändern“, dazu Farbe, Schrift (alle installierten TrueType-/OpenType-Schriften), Größe, Fett/Kursiv, „Gelb hinterlegt“, Linienstärke und „Füllung (durchscheinend)“. Code: `pdf_anmerken.py` (Zeichnen, PyMuPDF) und `pdf_anmerken_ui.py` (Leiste, Maus, Dialog).
- **Schreibmaschine:** klicken (Zeile bis höchstens 260 pt Breite) oder Rahmen aufziehen, tippen, Strg+Enter übernimmt, Esc bricht ab. Der Text wird mit der gewählten Schrift eingebettet.
- **Pfeil:** vom Kommentar zum gemeinten Bereich ziehen, die Spitze sitzt am Ende; Umschalt macht ihn waagerecht/senkrecht. **Rechteck/Kreis:** Bereich aufziehen, Umschalt = gleichseitig. Mit Füllung entsteht eine Markierung wie mit einem Textmarker.
- **Text ändern:** auf einen Textabschnitt klicken (Umschalt = ganze Zeile). Das Fenster schlägt die ähnlichste installierte Schrift samt Größe und Farbe vor; der alte Text wird entfernt (Redaktion, Hintergrundfarbe wird aus der Seite entnommen), der neue sitzt auf derselben Grundlinie. Geht nur bei Seiten mit Textebene (nicht bei reinen Scans) und nur bei waagerechtem Text.
- Alles wird sofort in die Datei geschrieben (inkrementell). Der erste Eingriff je Sitzung legt `<Name>_vor-Aenderung.pdf` an. **Rückgängig** (Strg+Z) stellt über Dateikopien im Temp-Ordner wieder her (höchstens 30 Schritte, 1,5 GB); nach Löschen/Umordnen/Einfügen ist die Liste leer.
- Gedrehte Seiten werden berücksichtigt. Die Schriftdatei wird je Seite einmal eingebettet (Arial etwa 1 MB).
- Nicht im Installer (Setup 1.2.0 enthält es noch nicht) und nicht auf B:/T:.
- **Gesetzte Texte später ändern:** Mit Schreibmaschine oder „Text ändern“ auf einen eigenen Kommentar klicken, dann steht er wieder im Schreibfeld. Schrift, Größe, Farbe und „Gelb hinterlegt“ gelten so, wie sie oben gerade stehen; leerer Text löscht den Kommentar. Technik: jeder Text liegt als eigene Inhaltsströme in der Seite, deren Nummern samt Einstellungen im Seitenschlüssel `AnnoWerkzeug` (Base64-JSON) stehen; Ändern tauscht diese Ströme aus. Kommentare aus früheren Läufen (vor dem 01.11.2026) haben diesen Eintrag nicht und lassen sich nur als normaler PDF-Text ändern. Pfeile und Formen sind nicht nachträglich änderbar, nur per Strg+Z zurück.
- **Gesetzte Texte verschieben:** Mit Schreibmaschine oder „Text ändern“ den Kommentar anfassen und ziehen (gestrichelter Rahmen zeigt das Ziel, der Text bleibt auf der Seite). Ein Klick ohne Ziehen öffnet ihn zum Bearbeiten. Strg+Z setzt ihn zurück.
- **Pfeile und Formen verschieben:** Mit jedem Werkzeug dieser Leiste (außer „Markieren“) Pfeil, Rechteck oder Kreis an der Linie anfassen und ziehen; bei Rechteck und Kreis zählt nur der Rand, damit man innen weiter schreiben kann. Gilt für Objekte, die mit der Fassung vom 01.11.2026 gesetzt wurden. Größe oder Farbe ändern geht noch nicht (Strg+Z und neu setzen).
- **Pfeile und Formen auswählen und ändern:** Ein Klick (ohne Ziehen) auf die Linie bzw. den Rand wählt das Objekt aus (gestrichelter Rahmen, weiße Anfasser). Anfasser ziehen = Größe (Rechteck/Kreis: Ecken, Pfeil: Anfang und Spitze). Farbe, Linienstärke und „Füllung“ oben in der Leiste wirken sofort auf das ausgewählte Objekt (Stärke nach kurzer Pause). Entf löscht das ausgewählte Objekt (statt der Seite), Esc hebt die Auswahl auf. Jede Änderung ist ein Schritt für Strg+Z.

## Suche im Betrachter (seit 01.11.2026)

Knopf „🔍 Suchen“ oder Strg+F öffnet eine Suchleiste unter der Werkzeugleiste. Beim Tippen wird nach kurzer Pause die ganze PDF durchsucht (Groß-/Kleinschreibung egal, Teilwörter zählen), die Treffer sind gelb markiert, der aktuelle orange. Enter, F3 oder ▼ springen zum nächsten Treffer (auch über Seitengrenzen und ringsum), Umschalt+Enter, Umschalt+F3 oder ▲ zurück. Esc oder ✕ schließt die Leiste. Funktioniert nur bei PDFs mit Textebene; bei reinen Scans meldet die Leiste das und empfiehlt OCR im Bearbeiter. Code: `pdf_suche.py` (Mixin `SearchMixin`).

## Sprungmarken und Lesezeichen (seit 01.11.2026)

Code: `pdf_links.py` (Mixin `LinkMixin`).
- **Links in der Seite** (Inhaltsverzeichnis, Register, Webadressen): ein Klick im normalen Markier-Modus folgt ihnen, der Mauszeiger wird über Links zur Hand. Unterstützt: Sprung zu Seite/Stelle, benannte Ziele (InDesign-Register), Aktionen wie nächste/vorherige Seite, Webadressen und Mailadressen im Browser. Verweise auf andere Dateien und Programmstarts werden bewusst nicht ausgeführt.
- **↩ / Alt+Links:** springt zurück an die Stelle vor dem Linksprung (bis 50 Schritte).
- **🔖 Lesezeichen (F5):** zeigt das Lesezeichen-Verzeichnis der PDF als aufklappbaren Baum anstelle der Vorschaubilder; Klick springt zur Seite. F4 oder erneutes F5 bringt die Vorschaubilder zurück.

## Suchen und Ersetzen (seit 01.11.2026)

In der Suchleiste „Ersetzen ▾“ (oder Strg+H) blendet eine zweite Zeile ein: „Ersetzen durch“, **Ersetzen** (aktueller Treffer, danach weiter zum nächsten) und **Alle ersetzen** (nach Rückfrage, ein Schritt für Strg+Z). Ersetzt wird in den Textabschnitten der PDF, ohne Beachtung der Groß-/Kleinschreibung; der ganze betroffene Abschnitt erscheint in der ähnlichsten installierten Schrift. Treffer, die über mehrere Abschnitte (Stilwechsel mitten im Wort) oder senkrechten Text laufen, bleiben unverändert und werden gemeldet. Code: `replace_spans`, `replace_all_on_page` in `pdf_anmerken.py`, Bedienung in `pdf_suche.py`.

## Markierten Text ändern, Doppelklick (seit 01.11.2026)

- **Doppelklick** auf ein Wort markiert es ganz (ohne Satzzeichen am Rand; Bindestrich-Wörter wie „Wau-Wau“ zählen als ein Wort). Das Wort wird wie jede Markierung in die Zwischenablage kopiert.
- **Markierten Text ändern:** Text markieren (Ziehen oder Doppelklick), dann F2, der Knopf „✎ Markierten Text ändern“ unten links oder Rechtsklick → „Markierten Text ändern…“. Im Fenster den neuen Wortlaut eintragen; mit dem Haken „Überall in der PDF ändern“ läuft stattdessen „Alle ersetzen“ (mit Rückfrage zur Anzahl). Die Markierung muss innerhalb einer Zeile und eines Textabschnitts liegen. Gleiche Wörter im selben Abschnitt werden an der markierten Stelle unterschieden. Der betroffene Abschnitt erscheint in der ähnlichsten installierten Schrift.
- **Bereiche über ein Wort hinaus:** Markierungen über mehrere Textabschnitte (Stilwechsel) und über mehrere Zeilen eines Absatzes lassen sich mit F2 ändern. Das Fenster zeigt den markierten Text als Fließtext (Zeilenenden aufgelöst, Trennstriche vor Kleinbuchstaben entfernt), Strg+Enter übernimmt. Der Absatz ab der ersten betroffenen Zeile wird neu gesetzt: eine Schrift (die des Markierungsanfangs, ähnlichste installierte), linksbündig, am rechten Rand des Absatzes neu umbrochen; ist der Text länger, ragt er unten über den Absatz hinaus, das meldet die Statuszeile. Über mehrere Absätze/Textblöcke geht es nicht. Code: `plan_range`, `apply_range` in `pdf_anmerken.py`.

## Markieren in den Werkzeugen (Korrektur 01.11.2026)

- Im Werkzeug **„↖ Markieren“** und im Werkzeug **„✎ Text ändern“** lässt sich Text mit gedrückter Maustaste markieren und kopieren (Strg+C), danach F2 zum Ändern. In „Text ändern“ öffnet ein einfacher Klick ohne Ziehen weiterhin die Änderung des Abschnitts (nach 0,45 s, damit ein Doppelklick vorher das Wort markieren kann). In „Schreibmaschine“, „Pfeil“, „Rechteck“ und „Kreis“ zeichnet das Ziehen; zum Markieren dort auf „↖ Markieren“ umschalten.
- Doppelklick wird jetzt über die Zeit zwischen zwei Klicks erkannt (zweiter Klick innerhalb 0,45 s an fast derselben Stelle); schnelle Klick-Zieh-Folgen starten dadurch immer eine normale Markierung.
- Fehler in Bedienfunktionen landen in `%LOCALAPPDATA%\PDF-Werkzeugehler.log` (unter pythonw gäbe es sonst keine Meldung).

## Markieren (Korrektur)
Senkrecht stehende Textzeilen werden bei der Auswahl ignoriert; bei mehreren Spalten gilt die waagerecht nächste Zeile.

## Versionen und Installer

Die Setups (Inno Setup, PyInstaller-Paket, ab Windows 10) liegen als Dateien bei den Releases v1.0.0 bis v1.3.2. Gebaut wird mit installer/bauen.ps1. Das Setup ist nicht signiert, SmartScreen meldet beim ersten Start Unbekannter Herausgeber.

