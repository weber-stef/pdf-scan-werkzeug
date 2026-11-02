# -*- coding: utf-8 -*-
"""
PDF-Scan-Werkzeug mit GUI
=========================
Funktionen:
  - OCR-Texterkennung (eingebautes Tesseract von PyMuPDF, Sprachdateien
    werden bei Bedarf automatisch heruntergeladen)
  - Gradationskurven (Schwarzpunkt / Weißpunkt / Gamma)
  - Gerade ausrichten (automatische Schräglagen-Korrektur)
  - Bereich freistellen (Rechteck in der Vorschau aufziehen; wahlweise
    für alle Seiten gemeinsam oder je Seite individuell)
  - Einzelne Seiten löschen oder drehen (+90°/180°/−90°)
  - Umbenennen
  - Datum an Dateinamen anfügen (_YYYY-MM-DD)
  - Vorschau mit Seitennavigation (◀/▶, Mausrad, Bild-auf/Bild-ab)
  - Integrierter PDF-Viewer (Blättern, Zoom, Vollbild mit F11/Esc)
  - Drucken aus dem Viewer (Strg+P: Drucker, Seitenbereich, Exemplare)

Benötigt: Python 3.10+, pymupdf, Pillow, numpy (zum Drucken pywin32)
Start:    python pdf_tool.py
"""

import io
import os
import queue
import re
import shutil
import socket
import sys
import threading
import datetime
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import ordnerwahl
from pdf_anmerken_ui import AnnotMixin
from pdf_suche import SearchMixin
from pdf_links import LinkMixin

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageTk

APP_TITLE = "PDF-Scan-Werkzeug"
# Als PyInstaller-onedir-Paket liegt tessdata (per --add-data mitgepackt)
# unter sys._MEIPASS (= der _internal-Ordner neben der .exe), nicht neben
# __file__ -- das ist hier in Ordnung, weil der Installationsordner selbst
# beschreibbar ist (kein Program Files) und tessdata sonst nur gelesen wird.
TESSDATA_DIR = (Path(getattr(sys, "_MEIPASS", "")) / "tessdata"
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent / "tessdata")
TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{lang}.traineddata"
INSTANCE_PORT = 48293   # lokaler Port zur Dateiübergabe an eine laufende Instanz


def _install_error_log(widget):
    """Schreibt Fehler in Tk-Rückrufen nach %LOCALAPPDATA%\\PDF-Werkzeug\\fehler.log
    (unter pythonw gibt es sonst keine Ausgabe, Fehler würden unsichtbar verschluckt)."""
    root = widget._root()
    if getattr(root, "_errlog_installed", False):
        return
    root._errlog_installed = True
    import time
    import traceback
    logdir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PDF-Werkzeug"

    def handler(exc, val, tb):
        try:
            logdir.mkdir(parents=True, exist_ok=True)
            with open(logdir / "fehler.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n"
                        + "".join(traceback.format_exception(exc, val, tb)) + "\n")
        except Exception:
            pass
        try:
            traceback.print_exception(exc, val, tb)
        except Exception:
            pass
    root.report_callback_exception = handler
    _install_hang_watchdog(root, logdir)


def _install_hang_watchdog(root, logdir):
    """Merkt, wenn die Oberfläche länger als 6 Sekunden nicht reagiert, und schreibt dann die
    Aufrufstapel aller Fäden nach %LOCALAPPDATA%\\PDF-Werkzeug\\haenger.log – damit lässt sich
    ein „eingefrorenes“ Fenster nachträglich erklären."""
    import faulthandler
    import threading
    import time
    herz = [time.time()]

    def schlag():
        herz[0] = time.time()
        try:
            root.after(500, schlag)
        except Exception:
            pass

    def wache():
        gemeldet = False
        while True:
            time.sleep(2)
            still = time.time() - herz[0]
            if still > 6 and not gemeldet:
                gemeldet = True
                try:
                    logdir.mkdir(parents=True, exist_ok=True)
                    with open(logdir / "haenger.log", "a", encoding="utf-8") as f:
                        f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}: Oberfläche reagiert seit "
                                f"{still:.0f} s nicht ===\n")
                        f.flush()
                        faulthandler.dump_traceback(file=f, all_threads=True)
                except Exception:
                    pass
            elif still <= 6:
                gemeldet = False

    try:
        root.after(500, schlag)
        threading.Thread(target=wache, daemon=True, name="haenger-wache").start()
    except Exception:
        pass


def forward_to_running_instance(paths, view: bool = False,
                                merge: bool = False) -> bool:
    """Reicht Dateipfade an eine bereits laufende Instanz weiter.

    Liefert True, wenn eine laufende Instanz die Pfade übernommen hat –
    dann darf sich dieser Prozess sofort wieder beenden. Bei view=True
    wird der laufenden Instanz signalisiert, die Datei(en) direkt im
    Viewer zu öffnen statt sie nur in die Liste aufzunehmen. Bei merge=True
    sammelt die laufende Instanz die Pfade zum Zusammenfassen ein.
    """
    try:
        with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=0.5) as s:
            marker = ["MERGE"] if merge else (["VIEW"] if view else [])
            lines = marker + [str(Path(p).resolve()) for p in paths]
            data = "\n".join(lines)
            s.sendall(data.encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
            # Die laufende Instanz quittiert, sobald ihre Oberfläche die Dateien
            # übernommen hat. Hängt sie, kommt nichts: dann selbst öffnen.
            s.settimeout(4.0)
            return s.recv(8) == b"1"
    except OSError:
        return False


# --------------------------------------------------------------------------
# Bildverarbeitung
# --------------------------------------------------------------------------

def build_lut(black: int, white: int, gamma: float) -> np.ndarray:
    """Erzeugt eine 256er-Lookup-Tabelle für die Gradationskurve."""
    x = np.arange(256, dtype=np.float64)
    span = max(1.0, float(white) - float(black))
    y = np.clip((x - black) / span, 0.0, 1.0)
    y = y ** (1.0 / max(0.05, gamma))
    return (y * 255.0 + 0.5).astype(np.uint8)


def apply_curves(img: Image.Image, black: int, white: int, gamma: float) -> Image.Image:
    lut = build_lut(black, white, gamma)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img.point(list(lut) * 3)


def estimate_skew_angle(img: Image.Image, max_angle: float = 6.0) -> float:
    """Schätzt den Schräglagenwinkel (Grad) über Projektionsprofile.

    Positiver Rückgabewert = Bild muss um diesen Winkel gedreht werden
    (PIL-Konvention: positiv = gegen den Uhrzeigersinn).
    """
    gray = img.convert("L")
    gray.thumbnail((900, 900), Image.Resampling.BILINEAR)
    inv = Image.eval(gray, lambda p: 255 - p)

    def score(angle: float) -> float:
        rot = inv.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=0)
        prof = np.asarray(rot, dtype=np.float64).sum(axis=1)
        return float(np.sum(np.diff(prof) ** 2))

    best = 0.0
    best_score = score(0.0)
    for a in np.arange(-max_angle, max_angle + 0.01, 1.0):
        s = score(float(a))
        if s > best_score:
            best_score, best = s, float(a)
    for a in np.arange(best - 0.9, best + 0.91, 0.15):
        if abs(a) > max_angle:
            continue
        s = score(float(a))
        if s > best_score:
            best_score, best = s, float(a)
    return round(best, 2)


def pil_to_pixmap(img: Image.Image) -> fitz.Pixmap:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return fitz.Pixmap(buf.getvalue())


# --------------------------------------------------------------------------
# OCR-Sprachdateien
# --------------------------------------------------------------------------

def _download(url: str, target: Path) -> None:
    """Download mit Fallback auf PowerShell (falls Pythons SSL-Prüfung scheitert,
    z. B. wegen Antivirus-/Proxy-Zertifikaten nutzt Windows den Systemspeicher)."""
    try:
        urllib.request.urlretrieve(url, target)
        return
    except Exception:
        if sys.platform != "win32":
            raise
    import subprocess
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Invoke-WebRequest -Uri '{url}' -OutFile '{target}' -UseBasicParsing"],
        capture_output=True, text=True)
    if r.returncode != 0 or not target.exists():
        raise RuntimeError(f"Download fehlgeschlagen: {r.stderr.strip()[:300]}")


def ensure_tessdata(langs: str, log) -> Path:
    """Lädt fehlende Tesseract-Sprachdateien (tessdata_fast) herunter."""
    TESSDATA_DIR.mkdir(exist_ok=True)
    for lang in re.split(r"[+,\s]+", langs.strip()):
        if not lang:
            continue
        target = TESSDATA_DIR / f"{lang}.traineddata"
        if target.exists() and target.stat().st_size > 100_000:
            continue
        url = TESSDATA_URL.format(lang=lang)
        log(f"Lade OCR-Sprachdatei '{lang}' herunter …")
        tmp = target.with_suffix(".part")
        _download(url, tmp)
        tmp.replace(target)
        log(f"  → {target.name} ({target.stat().st_size // 1024} kB)")
    os.environ["TESSDATA_PREFIX"] = str(TESSDATA_DIR)
    return TESSDATA_DIR


# --------------------------------------------------------------------------
# Verarbeitung einer Datei
# --------------------------------------------------------------------------

def _apply_page_order(doc, order, del_pages):
    """Bringt die verbliebenen Seiten in die Reihenfolge order (Originalindizes)."""
    if not order:
        return
    kept = [p for p in range(len(order)) if p not in del_pages]
    if len(kept) != len(doc) or sorted(order) != list(range(len(order))):
        return
    pos = {p: i for i, p in enumerate(kept)}
    wanted = [pos[p] for p in order if p in pos]
    if wanted != list(range(len(doc))):
        doc.select(wanted)


def process_pdf(src: Path, dst: Path, opts: dict, log, page_cb=None) -> None:
    """Wendet die gewählten Bild-/OCR-Operationen an und schreibt nach dst."""
    do_ocr = opts["ocr"]
    do_curves = opts["curves"]
    do_deskew = opts["deskew"]
    # crop: None oder {"default": Rechteck|None, "pages": {Seitenindex: Rechteck}}
    # Rechteck = (fx0, fy0, fx1, fy1) als Anteile der (ggf. gedrehten) Seitengröße
    crop = opts["crop"]
    rot_pages = opts.get("rot_pages") or {}    # {Seitenindex: 90|180|270}
    del_pages = opts.get("del_pages") or set()  # {Seitenindex, …}
    page_order = opts.get("page_order")         # None oder Originalindizes in neuer Reihenfolge
    curve_pages = opts.get("curve_pages") or {}  # {Seitenindex: (Schwarz, Weiß, Gamma)}
    dpi = opts["dpi"]

    def page_crop(pno: int):
        """Freistell-Bereich, der für Seite pno gilt (oder None)."""
        if not crop:
            return None
        return crop["pages"].get(pno, crop["default"])

    doc = fitz.open(src)

    if len(doc) and set(range(len(doc))) <= set(del_pages):
        doc.close()
        raise ValueError("Alle Seiten wären gelöscht – mindestens eine muss bleiben.")

    # Drehungen verlustfrei als Seiteneigenschaft setzen; auch die
    # Raster-Ausgabe (get_pixmap) berücksichtigt diese Drehung
    for pno, rot in rot_pages.items():
        if 0 <= pno < len(doc) and rot % 360:
            doc[pno].set_rotation((doc[pno].rotation + rot) % 360)

    raster_ops = do_ocr or do_curves or do_deskew
    # Die CropBox arbeitet im ungedrehten Koordinatensystem – verlustfrei
    # zuschneiden geht daher nur auf Seiten ohne Drehung
    croppable = all(doc[pno].rotation == 0
                    for pno in range(len(doc)) if page_crop(pno))

    # Nur Drehen/Löschen/Zuschneiden: verlustfrei ohne Neu-Rastern
    if not raster_ops and croppable:
        for pno, page in enumerate(doc):
            c = page_crop(pno)
            if c and pno not in del_pages:
                r = page.rect
                box = fitz.Rect(r.x0 + c[0] * r.width, r.y0 + c[1] * r.height,
                                r.x0 + c[2] * r.width, r.y0 + c[3] * r.height)
                page.set_cropbox(box)
            if page_cb:
                page_cb()
        if del_pages:
            doc.delete_pages([p for p in sorted(del_pages) if 0 <= p < len(doc)])
        _apply_page_order(doc, page_order, del_pages)
        doc.save(dst, garbage=3, deflate=True)
        doc.close()
        return

    out = fitz.open()
    for pno, page in enumerate(doc):
        if pno in del_pages:
            if page_cb:
                page_cb()
            continue
        has_text = bool(page.get_text().strip())
        c = page_crop(pno)

        # Seite hat schon Text und braucht keine Bildbearbeitung: unverändert übernehmen
        if not (do_curves or do_deskew or c) and do_ocr and has_text:
            out.insert_pdf(doc, from_page=pno, to_page=pno)
            if page_cb:
                page_cb()
            continue

        pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

        if do_deskew:
            angle = estimate_skew_angle(img)
            if abs(angle) >= 0.1:
                log(f"  Seite {pno + 1}: um {angle:+.2f}° gedreht")
                img = img.rotate(angle, resample=Image.Resampling.BICUBIC,
                                 expand=False, fillcolor=(255, 255, 255))
        if c:
            w, h = img.size
            img = img.crop((int(c[0] * w), int(c[1] * h),
                            int(c[2] * w), int(c[3] * h)))
        if do_curves:
            b, w_, g = curve_pages.get(pno, (opts["black"], opts["white"], opts["gamma"]))
            img = apply_curves(img, b, w_, g)

        if do_ocr:
            pix2 = pil_to_pixmap(img)
            pix2.set_dpi(dpi, dpi)
            pdf_bytes = pix2.pdfocr_tobytes(compress=True, language=opts["lang"],
                                            tessdata=str(TESSDATA_DIR))
            ocr_doc = fitz.open("pdf", pdf_bytes)
            out.insert_pdf(ocr_doc)
            ocr_doc.close()
        else:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            w_pt = img.width * 72.0 / dpi
            h_pt = img.height * 72.0 / dpi
            new_page = out.new_page(width=w_pt, height=h_pt)
            new_page.insert_image(fitz.Rect(0, 0, w_pt, h_pt), stream=buf.getvalue())

        if page_cb:
            page_cb()

    _apply_page_order(out, page_order, del_pages)
    out.save(dst, garbage=3, deflate=True)
    out.close()
    doc.close()


# --------------------------------------------------------------------------
# Integrierter PDF-Viewer
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Zusammenfassen mehrerer Dateien zu einer PDF
# --------------------------------------------------------------------------

def natural_key(path):
    """Sortierschlüssel, der Zahlen im Namen als Zahlen nimmt, damit
    Seite2 vor Seite10 einsortiert wird."""
    name = Path(path).name.lower()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def unique_path(path: Path) -> Path:
    """Hängt bei Bedarf _2, _3 … an, damit nichts überschrieben wird."""
    if not path.exists():
        return path
    for n in range(2, 1000):
        cand = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not cand.exists():
            return cand
    return path


def page_count_of(path) -> int:
    """Seitenzahl einer Datei, 0 wenn sie sich nicht öffnen lässt.

    Bilder, die erst Pillow lesen kann, zählen als eine Seite.
    """
    try:
        with fitz.open(path) as doc:
            return 0 if doc.needs_pass else doc.page_count
    except Exception:
        try:
            with Image.open(path) as img:
                return getattr(img, "n_frames", 1)
        except Exception:
            return 0


def _text_als_pdf(path: Path):
    """Setzt eine reine Textdatei als PDF-Seiten, sonst None.

    Damit lassen sich auch .md, .csv oder .log zusammenfassen, die MuPDF an
    der Endung nicht als Text erkennt.
    """
    try:
        rohdaten = path.read_bytes()
    except OSError:
        return None
    if not rohdaten.strip():
        return None
    for kodierung in ("utf-8", "cp1252"):
        try:
            text = rohdaten.decode(kodierung)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None
    # Steuerzeichen deuten auf eine Binärdatei hin
    steuer = sum(1 for z in text if ord(z) < 32 and z not in "\t\r\n")
    if steuer > len(text) / 100:
        return None
    try:
        doc = fitz.open(stream=text.encode("utf-8"), filetype="txt")
        pdf_bytes = doc.convert_to_pdf()   # als Textdokument nicht anfügbar
        doc.close()
        return fitz.open("pdf", pdf_bytes)
    except Exception:
        return None


def open_as_pdf(path: Path):
    """Öffnet eine Datei als PDF-Dokument.

    Bilddateien werden dabei in PDF-Seiten gewandelt. Kommt MuPDF mit einem
    Bild nicht zurecht (etwa bei PSD-Dateien mit Sonderfarben), springt Pillow
    ein – das liest die zusammengerechnete Ansicht solcher Dateien. Reine
    Textdateien mit ungewohnter Endung (.md, .csv, .log …) werden zuletzt als
    Text gesetzt; `.txt` und `.svg` kennt MuPDF von sich aus.
    """
    def zumachen(doc):
        try:
            doc.close()
        except Exception:
            pass

    doc = None
    try:
        doc = fitz.open(path)
        if doc.needs_pass:
            raise PermissionError("Kennwort nötig")
        if doc.is_pdf:
            return doc
        pdf_bytes = doc.convert_to_pdf()
        zumachen(doc)
        return fitz.open("pdf", pdf_bytes)
    except PermissionError:
        if doc is not None:
            zumachen(doc)
        raise
    except Exception:
        if doc is not None:
            zumachen(doc)
        try:
            img = Image.open(path)
        except Exception:
            text_doc = _text_als_pdf(path)
            if text_doc is not None:
                return text_doc
            # Weder als Dokument noch als Bild noch als Text lesbar
            raise ValueError("nicht lesbar") from None
        try:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            puffer = io.BytesIO()
            img.save(puffer, "PDF")
        finally:
            img.close()
        return fitz.open("pdf", puffer.getvalue())


def merge_documents(paths, dst: Path, log=None):
    """Fügt PDFs und Bilddateien in der übergebenen Reihenfolge zu einer PDF
    zusammen.

    Liefert ein Paar aus dem tatsächlich geschriebenen Pfad und der Liste der
    übersprungenen Dateien. Eine unlesbare Datei bricht den Lauf nicht ab,
    sondern wird übersprungen und gemeldet.
    """
    out = fitz.open()
    uebersprungen = []
    try:
        for p in paths:
            src_path = Path(p)
            try:
                src = open_as_pdf(src_path)
            except Exception as e:
                grund = str(e) or type(e).__name__
                uebersprungen.append((src_path.name, grund))
                if log:
                    log(f"Übersprungen: {src_path.name} ({grund})")
                continue
            try:
                out.insert_pdf(src)
                if log:
                    log(f"Übernommen: {src_path.name} ({src.page_count} Seite(n))")
            finally:
                src.close()
        if out.page_count == 0:
            raise ValueError("Keine verwertbaren Seiten gefunden.")
        dst = unique_path(Path(dst))
        dst.parent.mkdir(parents=True, exist_ok=True)
        out.save(dst, garbage=3, deflate=True)
    finally:
        out.close()
    return dst, uebersprungen


# --------------------------------------------------------------------------
# Drucken
# --------------------------------------------------------------------------

PRINT_MAX_DPI = 300     # mehr bringt bei Scans nichts und frisst nur Speicher


def list_printers():
    """Liefert (Liste der Druckernamen, Standarddrucker).

    Ohne pywin32 kommt ([], "") zurueck – das Druckfenster meldet das dann.
    """
    try:
        import win32print
    except ImportError:
        return [], ""
    stufen = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    namen = [p[2] for p in win32print.EnumPrinters(stufen)]
    try:
        standard = win32print.GetDefaultPrinter()
    except Exception:
        standard = ""
    if standard and standard not in namen:
        namen.insert(0, standard)
    return namen, standard


def printer_port(name: str) -> str:
    """Anschluss eines Druckers, z. B. "USB003" oder "PORTPROMPT:".

    PORTPROMPT: und FILE: bedeuten "druckt in eine Datei" (Microsoft
    Print to PDF, XPS Document Writer). Diese Treiber brechen StartDoc
    ab, wenn keine Zieldatei mitgegeben wird.
    """
    try:
        import win32print
        h = win32print.OpenPrinter(name)
        try:
            return win32print.GetPrinter(h, 2)["pPortName"] or ""
        finally:
            win32print.ClosePrinter(h)
    except Exception:
        return ""


def prints_to_file(name: str) -> bool:
    """True, wenn der Drucker eine Datei schreibt statt Papier."""
    return printer_port(name).upper() in ("PORTPROMPT:", "FILE:")


def parse_page_range(text: str, total: int) -> list:
    """Wandelt eine Eingabe wie "1,3,5-8" in 0-basierte Seitenzahlen um.

    Unsinnige Teile werden uebersprungen, Seiten ausserhalb des Dokuments
    abgeschnitten, Wiederholungen entfernt.
    """
    seiten = []
    for teil in re.split(r"[,;]", text):
        teil = teil.strip()
        if not teil:
            continue
        m = re.fullmatch(r"(\d+)\s*(?:[-\u2013]\s*(\d+))?", teil)
        if not m:
            continue
        von = int(m.group(1))
        bis = int(m.group(2)) if m.group(2) else von
        if von > bis:
            von, bis = bis, von
        for n in range(max(1, von), min(total, bis) + 1):
            if n - 1 not in seiten:
                seiten.append(n - 1)
    return seiten


def print_pdf(path, printer: str, pages: list, copies: int = 1,
              auto_rotate: bool = True, page_cb=None, cancel=None,
              output_file=None) -> int:
    """Druckt die angegebenen Seiten (0-basiert) auf printer.

    Laeuft im Arbeitsfaden und oeffnet die PDF deshalb selbst – ein
    fitz-Dokument darf nicht zwischen Faeden geteilt werden. Gerendert wird
    hoechstens mit PRINT_MAX_DPI, den Rest streckt der Druckertreiber.
    output_file ist noetig fuer Treiber, die in eine Datei drucken.
    Liefert die Zahl der gedruckten Seiten.
    """
    import win32con
    import win32ui
    from PIL import ImageWin

    dc = win32ui.CreateDC()
    dc.CreatePrinterDC(printer)
    try:
        dpi = max(dc.GetDeviceCaps(win32con.LOGPIXELSX),
                  dc.GetDeviceCaps(win32con.LOGPIXELSY))
        breite = dc.GetDeviceCaps(win32con.HORZRES)   # bedruckbare Flaeche in Pixeln
        hoehe = dc.GetDeviceCaps(win32con.VERTRES)
        zoom = min(PRINT_MAX_DPI, max(72, dpi)) / 72

        gedruckt = 0
        doc = fitz.open(path)
        try:
            if output_file:
                dc.StartDoc(Path(path).name, str(output_file))
            else:
                dc.StartDoc(Path(path).name)
            try:
                for _ in range(max(1, copies)):
                    for pno in pages:
                        if cancel is not None and cancel.is_set():
                            raise KeyboardInterrupt
                        pix = doc[pno].get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                                                  alpha=False)
                        img = Image.frombytes("RGB", (pix.width, pix.height),
                                              pix.samples)
                        # Querformat auf Hochformat-Papier (und umgekehrt) drehen,
                        # sonst bleibt der halbe Bogen leer
                        if auto_rotate and (img.width > img.height) != (breite > hoehe):
                            img = img.rotate(90, expand=True)
                        faktor = min(breite / img.width, hoehe / img.height)
                        zielb = max(1, int(img.width * faktor))
                        zielh = max(1, int(img.height * faktor))
                        x0 = (breite - zielb) // 2
                        y0 = (hoehe - zielh) // 2
                        dc.StartPage()
                        ImageWin.Dib(img).draw(dc.GetHandleOutput(),
                                               (x0, y0, x0 + zielb, y0 + zielh))
                        dc.EndPage()
                        gedruckt += 1
                        if page_cb:
                            page_cb(gedruckt)
                dc.EndDoc()
            except KeyboardInterrupt:
                dc.AbortDoc()
            except Exception:
                dc.AbortDoc()
                raise
        finally:
            doc.close()
    finally:
        dc.DeleteDC()
    return gedruckt


class FileDrop:
    """Nimmt Dateien an, die aus dem Explorer auf ein Tk-Widget gezogen werden
    (WM_DROPFILES, nur ctypes – keine Zusatzbibliothek). callback(pfade, x, y)
    bekommt die Pfade und die Abwurfstelle in Widget-Pixeln; er läuft über
    after()-Polling, nicht innerhalb der Fensterprozedur (Tk darf dort nicht
    aufgerufen werden)."""

    WM_DROPFILES = 0x0233
    GWLP_WNDPROC = -4

    def __init__(self, widget, callback):
        self.widget = widget
        self.callback = callback
        self.hwnd = None
        self.old = None
        self._proc = None
        self._inbox = []              # die Fensterprozedur ruft kein Tk auf, nur diese Liste
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes
        self._ct = ctypes
        user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
        widget.update_idletasks()
        self.hwnd = wintypes.HWND(widget.winfo_id())
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                     ctypes.c_size_t, ctypes.c_ssize_t)
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.CallWindowProcW.restype = LRESULT
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                           ctypes.c_size_t, ctypes.c_ssize_t]
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                           ctypes.c_wchar_p, wintypes.UINT]
        shell32.DragQueryPoint.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.POINT)]
        shell32.DragFinish.argtypes = [ctypes.c_void_p]
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]

        def proc(hwnd, msg, wparam, lparam):
            if msg == self.WM_DROPFILES:
                try:
                    pfade = []
                    n = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                    for i in range(n):
                        buf = ctypes.create_unicode_buffer(32768)
                        shell32.DragQueryFileW(wparam, i, buf, 32768)
                        pfade.append(buf.value)
                    pt = wintypes.POINT()
                    shell32.DragQueryPoint(wparam, ctypes.byref(pt))
                    shell32.DragFinish(wparam)
                    self._inbox.append((pfade, pt.x, pt.y))
                except Exception:
                    pass
                return 0
            return user32.CallWindowProcW(self.old, hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(proc)
        shell32.DragAcceptFiles(self.hwnd, True)
        self.old = user32.SetWindowLongPtrW(self.hwnd, self.GWLP_WNDPROC,
                                            ctypes.cast(self._proc, ctypes.c_void_p))
        self._poll()

    def _poll(self):
        if self.old is None:
            return
        while self._inbox:
            pfade, x, y = self._inbox.pop(0)
            try:
                self.callback(pfade, x, y)
            except Exception:
                pass
        try:
            self.widget.after(100, self._poll)
        except tk.TclError:
            pass

    def close(self):
        """Hängt die Fensterprozedur wieder aus (vor dem Zerstören des Widgets)."""
        if self.hwnd is None or self.old is None:
            return
        try:
            self._ct.windll.user32.SetWindowLongPtrW(self.hwnd, self.GWLP_WNDPROC, self.old)
        except Exception:
            pass
        self.old = None


class PdfViewer(LinkMixin, SearchMixin, AnnotMixin, tk.Toplevel):
    """Eigenes Anzeigefenster mit Blättern, Zoom und Vollbildmodus.

    Tasten: F11 Vollbild, Esc Vollbild beenden/Fenster schließen,
    Bild-auf/-ab und Pfeil links/rechts blättern, +/− zoomen,
    Mausrad scrollt (und blättert am Seitenende weiter),
    Strg+Mausrad zoomt, Umschalt+Mausrad scrollt horizontal.
    """

    ZOOM_LEVELS = (0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)
    SCREEN = 96 / 72          # 100 % entspricht 96 dpi Bildschirmauflösung

    def __init__(self, master, path):
        super().__init__(master)
        _install_error_log(self)
        self.doc = None
        self.path = Path(path)
        try:
            doc = fitz.open(self.path)
            if doc.needs_pass:
                doc.close()
                raise ValueError("Die PDF ist passwortgeschützt.")
            self.doc = doc
        except Exception as exc:
            self.destroy()
            messagebox.showerror(APP_TITLE,
                                 f"Kann {self.path.name} nicht öffnen:\n{exc}",
                                 parent=master)
            return
        self.page_no = 0
        self.zoom = 1.0
        self.zoom_mode = "width"      # "width", "page" oder None (fester Faktor)
        self._fullscreen = False
        self._img = None              # ImageTk-Referenz
        self._resize_job = None
        self._img_offset = (0, 0)     # Bild-Position auf dem Canvas (px)
        self._render_scale = 1.0      # Skalierung Seite -> Canvas-Pixel
        self._sel_start = None        # Startpunkt (Seiten-Koordinaten) während des Ziehens
        self._sel_page_rects = None   # markierte Zeilen in Seitenkoordinaten (Liste von fitz.Rect)
        self._sel_page_no = None
        self._sel_text = ""
        self._lines_cache = {}        # Seitenzahl -> Liste von Zeilen-BBoxen (fitz.Rect)
        self._anno_init()
        self._last_click = None
        self._edit_job = None
        self._search_init()
        self._links_init()

        self.title(f"{self.path.name} – {APP_TITLE}")
        self.geometry("1000x800")
        self.minsize(520, 400)
        self._build_viewer_ui()
        self._bind_keys()
        self.focus_set()
        self.after(50, lambda: self.show_page(0))

    # ---------------- Aufbau ----------------

    def _build_viewer_ui(self):
        bar = ttk.Frame(self, padding=(6, 4))
        bar.pack(side="top", fill="x")
        self._bar = bar

        self.btn_first = ttk.Button(bar, text="⏮", width=3,
                                    command=lambda: self.show_page(0))
        self.btn_first.pack(side="left", padx=(0, 2))
        self.btn_prev = ttk.Button(bar, text="◀", width=3,
                                   command=lambda: self.show_page(self.page_no - 1))
        self.btn_prev.pack(side="left")
        self.var_page = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.var_page, width=5, justify="center")
        ent.pack(side="left", padx=(4, 0))
        ent.bind("<Return>", self._goto_page)
        self.lbl_total = ttk.Label(bar, text="/ –")
        self.lbl_total.pack(side="left", padx=(2, 4))
        self.btn_next = ttk.Button(bar, text="▶", width=3,
                                   command=lambda: self.show_page(self.page_no + 1))
        self.btn_next.pack(side="left")
        self.btn_last = ttk.Button(bar, text="⏭", width=3,
                                   command=lambda: self.show_page(len(self.doc) - 1))
        self.btn_last.pack(side="left", padx=(2, 0))
        self.btn_back = ttk.Button(bar, text="↩", width=3, command=self.nav_back,
                                   state="disabled")
        self.btn_back.pack(side="left", padx=(8, 0))

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Button(bar, text="−", width=3,
                   command=lambda: self.zoom_step(-1)).pack(side="left")
        self.var_zoom = tk.StringVar(value="Seitenbreite")
        self.cmb_zoom = ttk.Combobox(bar, textvariable=self.var_zoom, width=13,
                                     state="readonly",
                                     values=("Seitenbreite", "Ganze Seite",
                                             "50 %", "75 %", "100 %", "125 %",
                                             "150 %", "200 %", "300 %", "400 %"))
        self.cmb_zoom.pack(side="left", padx=4)
        self.cmb_zoom.bind("<<ComboboxSelected>>", self._on_zoom_select)
        ttk.Button(bar, text="+", width=3,
                   command=lambda: self.zoom_step(1)).pack(side="left")

        self.btn_full = ttk.Button(bar, text="⛶ Vollbild (F11)",
                                   command=self.toggle_fullscreen)
        self.btn_full.pack(side="right")

        self.btn_copy = ttk.Button(bar, text="⧉ Kopieren", width=11,
                                   command=self.copy_selection, state="disabled")
        self.btn_copy.pack(side="right", padx=(0, 8))

        ttk.Button(bar, text="🔍 Suchen (Strg+F)",
                   command=self.open_search).pack(side="right", padx=(0, 8))

        ttk.Button(bar, text="🖍 Kommentieren & Ändern (Strg+K)",
                   command=self.toggle_anno_bar).pack(side="right", padx=(0, 8))

        ttk.Button(bar, text="🖨 Drucken (Strg+P)",
                   command=self.print_document).pack(side="right", padx=(0, 8))

        ttk.Button(bar, text="▤ Seiten (F4)",
                   command=self.toggle_sidebar).pack(side="right", padx=(0, 8))

        ttk.Button(bar, text="🔖 Lesezeichen (F5)",
                   command=self.toggle_bookmarks).pack(side="right", padx=(0, 8))

        self._build_anno_bar(bar)
        self._build_search_bar(bar)
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        self._build_sidebar(body)
        self._build_bookmarks(body)
        self.canvas = tk.Canvas(body, background="#4a4a4a", highlightthickness=0,
                                cursor="xterm")
        self.canvas.grid(row=0, column=1, sticky="nsew")
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        vsb.grid(row=0, column=2, sticky="ns")
        hsb = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        hsb.grid(row=1, column=1, sticky="ew")
        self.canvas.config(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._sel_press)
        self.canvas.bind("<B1-Motion>", self._sel_drag)
        self.canvas.bind("<ButtonRelease-1>", self._sel_release)
        self.canvas.bind("<Button-3>", self._show_context_menu)
        self.canvas.bind("<Motion>", self._link_hover)

        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Kopieren", command=self.copy_selection)
        self.context_menu.add_command(label="Markierten Text ändern…", command=self.change_selection)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Seite speichern unter…", command=self.save_pages)
        self.context_menu.add_command(label="Seite löschen…", command=self.delete_pages)

        status = ttk.Frame(self, padding=(6, 2))
        status.pack(side="bottom", fill="x")
        ttk.Button(status, text="✎ Bearbeiten-Werkzeug (Strg+E)",
                   command=self._open_in_editor).pack(side="left")
        ttk.Button(status, text="📄 Speichern unter… (Strg+Umschalt+S)",
                   command=self.save_as).pack(side="left", padx=(8, 8))
        self.btn_chg = ttk.Button(status, text="✎ Markierten Text ändern (F2)",
                                  command=self.change_selection, state="disabled")
        self.btn_chg.pack(side="left", padx=(0, 12))
        self.sel_status = ttk.Label(status, text="Text markieren: Ziehen mit gedrückter Maustaste",
                                    foreground="#606060")
        self.sel_status.pack(side="left")

    def _bind_keys(self):
        self.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.bind("<F4>", lambda e: self.toggle_sidebar())
        self.bind("<F5>", lambda e: self.toggle_bookmarks())
        self.bind("<Escape>", self._on_escape)
        self.bind("<Control-z>", self.anno_undo)
        self.bind("<Control-k>", lambda e: self.toggle_anno_bar())
        self.bind("<Control-f>", lambda e: self.open_search())
        self.bind("<Control-h>", lambda e: self.open_replace())
        self.bind("<F2>", lambda e: self.change_selection())
        self.bind("<Alt-Left>", self.nav_back)
        self.bind("<F3>", lambda e: self.search_step(1))
        self.bind("<Shift-F3>", lambda e: self.search_step(-1))
        self.bind("<Prior>", lambda e: self._key_page(-1))
        self.bind("<Next>", lambda e: self._key_page(1))
        self.bind("<Left>", lambda e: self._key_page(-1))
        self.bind("<Right>", lambda e: self._key_page(1))
        self.bind("<Home>", lambda e: None if self._typing() else self.show_page(0))
        self.bind("<End>", lambda e: None if self._typing() else self.show_page(len(self.doc) - 1))
        self.bind("<Up>", lambda e: None if self._typing() else self.canvas.yview_scroll(-3, "units"))
        self.bind("<Down>", lambda e: None if self._typing() else self.canvas.yview_scroll(3, "units"))
        self.bind("<plus>", lambda e: None if self._typing() else self.zoom_step(1))
        self.bind("<minus>", lambda e: None if self._typing() else self.zoom_step(-1))
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Shift-MouseWheel>",
                  lambda e: self.canvas.xview_scroll(-2 if e.delta > 0 else 2, "units"))
        self.bind("<Control-MouseWheel>",
                  lambda e: self.zoom_step(1 if e.delta > 0 else -1))
        self.bind("<Control-c>", lambda e: self.copy_selection())
        self.bind("<Control-p>", lambda e: self.print_document())
        self.bind("<Control-e>", lambda e: self._open_in_editor())
        self.bind("<Control-s>", lambda e: self.save_pages())
        self.bind("<Control-S>", lambda e: self.save_as())
        self.bind("<Delete>", self._key_delete)
        self.bind("<Return>", self._key_enter)

    # ---------------- Anzeige ----------------

    def _key_delete(self, _ev=None):
        """Entf löscht Seiten – nicht, während ein Eingabefeld (Seitenzahl) den Fokus hat."""
        if self._typing():
            return
        if self._anno_selected_rec():
            self._anno_delete_selected()
            return
        self.delete_pages()

    def _key_page(self, delta: int):
        """Blättern per Taste – nicht, während das Seitenfeld den Fokus hat."""
        if self._typing():
            return
        self.show_page(self.page_no + delta)

    def show_page(self, pno: int, at_end: bool = False):
        """Zeigt Seite pno an; at_end=True scrollt ans Seitenende (Rückwärtsblättern)."""
        if not self.doc:
            return
        self._anno_commit_text()
        if pno != self.page_no:
            self._clear_selection()
            self._anno_sel = None
        self.page_no = max(0, min(pno, len(self.doc) - 1))
        self._render()
        self.canvas.yview_moveto(1.0 if at_end else 0.0)
        self.canvas.xview_moveto(0.0)

    def _render(self):
        if not self.doc:
            return
        self._anno_commit_text()
        page = self.doc[self.page_no]
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        pw = max(1.0, page.rect.width * self.SCREEN)
        ph = max(1.0, page.rect.height * self.SCREEN)
        if self.zoom_mode == "width":
            self.zoom = (cw - 4) / pw
        elif self.zoom_mode == "page":
            self.zoom = min((cw - 4) / pw, (ch - 4) / ph)
        self.zoom = max(0.05, min(self.zoom, 6.0))

        scale = self.SCREEN * self.zoom
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale),
                              colorspace=fitz.csRGB, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        self._img = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        x = max(0, (cw - pix.width) // 2)
        y = max(0, (ch - pix.height) // 2)
        self.canvas.create_image(x, y, anchor="nw", image=self._img)
        self.canvas.config(scrollregion=(0, 0, max(cw, pix.width + x),
                                         max(ch, pix.height + y)))
        self._img_offset = (x, y)
        self._render_scale = scale
        if self._sel_page_rects and self._sel_page_no == self.page_no:
            self._draw_selection_rects(self._sel_page_rects)
        self._search_draw()
        self._anno_draw_sel()
        self._update_toolbar()
        self._thumbs_mark()

    def _update_toolbar(self):
        self.var_page.set(str(self.page_no + 1))
        self.lbl_total.config(text=f"/ {len(self.doc)}")
        self.btn_first.config(state="normal" if self.page_no > 0 else "disabled")
        self.btn_prev.config(state="normal" if self.page_no > 0 else "disabled")
        self.btn_next.config(
            state="normal" if self.page_no < len(self.doc) - 1 else "disabled")
        self.btn_last.config(
            state="normal" if self.page_no < len(self.doc) - 1 else "disabled")
        if self.zoom_mode is None:
            self.var_zoom.set(f"{round(self.zoom * 100)} %")

    def _on_resize(self, _ev):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._render)

    # ---------------- Textmarkierung ----------------

    def _page_to_canvas(self, rect: fitz.Rect):
        """Wandelt ein Rechteck in Seitenkoordinaten in Canvas-Koordinaten um."""
        ox, oy = self._img_offset
        s = self._render_scale
        return (ox + rect.x0 * s, oy + rect.y0 * s,
                ox + rect.x1 * s, oy + rect.y1 * s)

    def _canvas_to_page(self, cx, cy):
        ox, oy = self._img_offset
        s = max(0.001, self._render_scale)
        return ((cx - ox) / s, (cy - oy) / s)

    def _get_page_lines(self, page):
        """Liefert die Zeilen-BBoxen der Seite (von oben nach unten), gecacht."""
        lines = self._lines_cache.get(self.page_no)
        if lines is not None:
            return lines
        lines = []
        rm = page.rotation_matrix
        mrot = fitz.Matrix(rm.a, rm.b, rm.c, rm.d, 0, 0)
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                # nur Zeilen, die auf der Anzeige waagerecht stehen (senkrechte Schrift,
                # z. B. große Jahreszahlen am Rand, deckt sonst die ganze Seitenhöhe ab)
                dx, dy = line.get("dir", (1, 0))
                v = fitz.Point(dx, dy) * mrot
                if abs(v.y) > 0.1 or v.x < 0:
                    continue
                b = fitz.Rect(line["bbox"])
                r = fitz.Rect(fitz.Point(b.x0, b.y0) * rm, fitz.Point(b.x1, b.y1) * rm).normalize()
                if not r.is_empty:
                    lines.append(r)
        lines.sort(key=lambda r: (round(r.y0, 1), r.x0))
        self._lines_cache[self.page_no] = lines
        return lines

    def _line_at(self, lines, x, y):
        """Zeile unter (x, y); bei mehreren Spalten die waagerecht nächste."""
        if not lines:
            return None
        kand = [i for i, r in enumerate(lines) if r.y0 - 1 <= y <= r.y1 + 1]
        if kand:
            def abstand(i):
                r = lines[i]
                return 0 if r.x0 <= x <= r.x1 else min(abs(x - r.x0), abs(x - r.x1))
            return min(kand, key=abstand)
        best_i, best_d = 0, None
        for i, r in enumerate(lines):
            d = abs((r.y0 + r.y1) / 2 - y)
            if best_d is None or d < best_d:
                best_d, best_i = d, i
        return best_i

    @staticmethod
    def _clip_rect(page, r):
        """Anzeigekoordinaten -> Koordinaten für get_text(clip=...)."""
        m = page.derotation_matrix
        return fitz.Rect(fitz.Point(r.x0, r.y0) * m, fitz.Point(r.x1, r.y1) * m).normalize()

    def _selection_rects(self, page, p0, p1):
        """Baut zeilenweise Markierungs-Rechtecke zwischen zwei Seitenpunkten,
        wie bei der Textauswahl in einem Textprogramm (erste/letzte Zeile
        anteilig, dazwischen ganze Zeilen)."""
        lines = self._get_page_lines(page)
        if not lines:
            r = (fitz.Rect(min(p0[0], p1[0]), min(p0[1], p1[1]),
                           max(p0[0], p1[0]), max(p0[1], p1[1])) & page.rect)
            return [] if r.is_empty else [r]

        i0 = self._line_at(lines, p0[0], p0[1])
        i1 = self._line_at(lines, p1[0], p1[1])
        if i0 is None or i1 is None:
            return []
        if i0 <= i1:
            lo_i, lo_x, hi_i, hi_x = i0, p0[0], i1, p1[0]
        else:
            lo_i, lo_x, hi_i, hi_x = i1, p1[0], i0, p0[0]

        rects = []
        if lo_i == hi_i:
            r = lines[lo_i]
            x0, x1 = sorted((lo_x, hi_x))
            x0, x1 = max(x0, r.x0), min(x1, r.x1)
            if x1 > x0:
                rects.append(fitz.Rect(x0, r.y0, x1, r.y1))
        else:
            top = lines[lo_i]
            x0 = min(max(lo_x, top.x0), top.x1)
            rects.append(fitz.Rect(x0, top.y0, top.x1, top.y1))
            for i in range(lo_i + 1, hi_i):
                rects.append(lines[i])
            bot = lines[hi_i]
            x1 = max(min(hi_x, bot.x1), bot.x0)
            rects.append(fitz.Rect(bot.x0, bot.y0, x1, bot.y1))
        return [r for r in rects if not r.is_empty]

    def _draw_selection_rects(self, rects):
        self.canvas.delete("selrect")
        for r in rects:
            x0, y0, x1, y1 = self._page_to_canvas(r)
            self.canvas.create_rectangle(x0, y0, x1, y1, outline="",
                                         fill="#2060ff", stipple="gray25",
                                         tags="selrect")

    def _edit_click_now(self, p, shift):
        self._edit_job = None
        self._anno_edit_at(p, shift)

    def _sel_press(self, ev):
        if not self.doc:
            return
        if not self._anno_editor:
            # Tastatur (Entf, Enter, Esc) gehört ab jetzt der Seitenansicht, nicht mehr
            # einem Feld der Werkzeugleiste, in das zuletzt getippt wurde
            self.canvas.focus_set()
        if self.tool in ("edit", "select", "pick"):
            # ein Kommentar unter der Maus wird ausgewählt/verschoben, sonst markiert der Betrachter Text
            if self._anno_press(ev):
                return
        else:
            return self._anno_press(ev)
        zeit = getattr(ev, "time", 0)
        lc = self._last_click
        if (lc and zeit and lc[0] and 0 <= zeit - lc[0] < 450
                and abs(ev.x - lc[1]) < 5 and abs(ev.y - lc[2]) < 5):
            self._last_click = None
            self._sel_start = None
            if self._edit_job:
                self.after_cancel(self._edit_job)
                self._edit_job = None
            self._sel_double(ev)           # zweiter Klick kurz nach einem Klick: Wort markieren
            return
        cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
        self._sel_start = self._canvas_to_page(cx, cy)
        self.canvas.delete("selrect")

    def _sel_drag(self, ev):
        if self._anno_routing():
            return self._anno_drag(ev)
        if not self._sel_start or not self.doc:
            return
        cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
        p1 = self._canvas_to_page(cx, cy)
        page = self.doc[self.page_no]
        rects = self._selection_rects(page, self._sel_start, p1)
        self._draw_selection_rects(rects)

    def _sel_release(self, ev):
        if self._anno_routing():
            return self._anno_release(ev)
        if not self._sel_start or not self.doc:
            self._sel_start = None
            return
        cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
        p1 = self._canvas_to_page(cx, cy)
        p0 = self._sel_start
        self._sel_start = None

        if abs(p1[0] - p0[0]) * self._render_scale < 3 and \
           abs(p1[1] - p0[1]) * self._render_scale < 3:
            if self.tool == "edit":
                # Klick: Abschnitt ändern – mit kurzer Verzögerung, damit ein
                # Doppelklick (Wort markieren) nicht vorher das Fenster öffnet
                self._last_click = (getattr(ev, "time", 0), ev.x, ev.y)
                shift = bool(ev.state & 1)
                if self._edit_job:
                    self.after_cancel(self._edit_job)
                self._edit_job = self.after(
                    450, lambda p=p1, sh=shift: self._edit_click_now(p, sh))
                return
            if self.follow_link_at(p1):
                return
            self._last_click = (getattr(ev, "time", 0), ev.x, ev.y)
            self._clear_selection()
            return

        page = self.doc[self.page_no]
        rects = self._selection_rects(page, p0, p1)
        if not rects:
            self._clear_selection()
            return

        parts = [page.get_text("text", clip=self._clip_rect(page, r)).strip() for r in rects]
        text = "\n".join(p for p in parts if p)

        self._sel_page_rects = rects
        self._sel_page_no = self.page_no
        self._sel_text = text
        self._draw_selection_rects(rects)

        if text:
            self.btn_copy.config(state="normal")
            self.btn_chg.config(state="normal")
            self.copy_selection(silent=True)
            preview = text.replace("\n", " ")
            if len(preview) > 60:
                preview = preview[:57] + "…"
            self.sel_status.config(
                text=f"✓ In Zwischenablage kopiert: „{preview}“", foreground="#107010")
        else:
            self.btn_copy.config(state="disabled")
            self.btn_chg.config(state="disabled")
            self.sel_status.config(text="Kein Text im markierten Bereich gefunden.",
                                   foreground="#a00000")

    def _clear_selection(self):
        self.canvas.delete("selrect")
        self._sel_page_rects = None
        self._sel_page_no = None
        self._sel_text = ""
        self.btn_copy.config(state="disabled")
        self.btn_chg.config(state="disabled")
        self.sel_status.config(text="Text markieren: Ziehen mit gedrückter Maustaste",
                               foreground="#606060")

    def copy_selection(self, silent: bool = False):
        if not self._sel_text:
            return
        self.clipboard_clear()
        self.clipboard_append(self._sel_text)
        if not silent:
            self.sel_status.config(text="✓ In Zwischenablage kopiert.",
                                   foreground="#107010")

    def _show_context_menu(self, ev):
        self.context_menu.entryconfig(
            "Kopieren", state="normal" if self._sel_text else "disabled")
        self.context_menu.entryconfig(
            "Markierten Text ändern…", state="normal" if self._sel_text else "disabled")
        try:
            self.context_menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            self.context_menu.grab_release()

    # ---------------- Navigation / Zoom ----------------

    def _goto_page(self, _ev=None):
        try:
            pno = int(self.var_page.get()) - 1
        except ValueError:
            self._update_toolbar()
            return
        self.show_page(pno)
        self.focus_set()

    def _on_zoom_select(self, _ev=None):
        value = self.var_zoom.get()
        if value == "Seitenbreite":
            self.zoom_mode = "width"
        elif value == "Ganze Seite":
            self.zoom_mode = "page"
        else:
            self.zoom_mode = None
            self.zoom = int(value.rstrip(" %")) / 100.0
        self._render()
        self.focus_set()

    def zoom_step(self, direction: int):
        """Zoomt eine Stufe hinein (direction > 0) oder heraus."""
        z = self.zoom
        if direction > 0:
            z = next((lv for lv in self.ZOOM_LEVELS if lv > z * 1.01),
                     self.ZOOM_LEVELS[-1])
        else:
            z = next((lv for lv in reversed(self.ZOOM_LEVELS) if lv < z * 0.99),
                     self.ZOOM_LEVELS[0])
        self.zoom_mode = None
        self.zoom = z
        self._render()

    def _on_wheel(self, ev):
        y0, y1 = self.canvas.yview()
        if ev.delta > 0:
            if y0 <= 0.0 and self.page_no > 0:
                self.show_page(self.page_no - 1, at_end=True)
            else:
                self.canvas.yview_scroll(-3, "units")
        else:
            if y1 >= 1.0 and self.page_no < len(self.doc) - 1:
                self.show_page(self.page_no + 1)
            else:
                self.canvas.yview_scroll(3, "units")

    # ---------------- Drucken ----------------

    def print_document(self):
        """Oeffnet das Druckfenster fuer die angezeigte PDF."""
        if not self.doc:
            return
        PrintDialog(self, self.path, len(self.doc), self.page_no)

    # ---------------- Seite speichern ----------------

    def save_pages(self):
        """Oeffnet das Fenster zum Speichern einzelner Seiten als eigene Dateien."""
        if not self.doc:
            return
        SavePagesDialog(self, self.path, len(self.doc), self.page_no)

    def save_as(self):
        """Speichert den aktuellen Stand der PDF (mit allen Änderungen) unter neuem Namen
        oder – bei gleichem Namen – über die geöffnete Datei."""
        if not self.doc:
            return
        ziel = filedialog.asksaveasfilename(
            parent=self, title="PDF speichern unter", initialdir=str(self.path.parent),
            initialfile=self.path.name, defaultextension=".pdf",
            filetypes=[("PDF-Dateien", "*.pdf")])
        if not ziel:
            return
        ziel = Path(ziel)
        if ziel.exists() and ziel.resolve() == self.path.resolve():
            # Die geöffnete Datei selbst überschreiben (Dateiauswahl hat schon gefragt)
            tmp = self.path.with_name(f"{self.path.stem}.speichern-tmp.pdf")
            try:
                self.doc.save(tmp, garbage=3, deflate=True)
                pno = self.page_no
                self.release_document()
                os.replace(tmp, self.path)
                self.doc = fitz.open(self.path)
                self.page_no = min(pno, len(self.doc) - 1)
            except Exception as exc:
                try:
                    if tmp.exists():
                        tmp.unlink()
                    if not self.doc:
                        self.doc = fitz.open(self.path)
                except Exception:
                    pass
                self._render()
                messagebox.showerror(APP_TITLE, f"Speichern fehlgeschlagen:\n{exc}", parent=self)
                return
            self._lines_cache = {}
            self._render()
            self.sel_status.config(text=f"✓ Geöffnete Datei überschrieben: {self.path.name}")
            return
        try:
            self.doc.save(ziel, garbage=3, deflate=True)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Speichern fehlgeschlagen:\n{exc}", parent=self)
            return
        self.sel_status.config(text=f"Gespeichert als {ziel}")

    # ---------------- Seiten löschen ----------------

    def delete_pages(self):
        """Öffnet das Fenster zum Löschen einzelner Seiten."""
        if not self.doc:
            return
        if len(self.doc) < 2:
            messagebox.showinfo(APP_TITLE,
                                "Diese PDF hat nur eine Seite. Eine PDF braucht mindestens eine Seite.",
                                parent=self)
            return
        DeletePagesDialog(self, self.path, len(self.doc), self.page_no)

    def release_document(self):
        """Gibt die Datei frei (Windows sperrt sie, solange sie geöffnet ist)."""
        doc, self.doc = self.doc, None
        if doc:
            try:
                doc.close()
            except Exception:
                pass

    def reload_document(self, page_no: int = 0, keep_thumbs=None):
        """Öffnet die Datei nach einer Änderung neu und zeigt die Seite page_no an.
        keep_thumbs: bereits gerenderte Vorschaubilder in neuer Reihenfolge weiterverwenden."""
        self._lines_cache.clear()
        self._clear_selection()
        self._anno_cancel_editor()
        self._anno_sel = None
        self._links_cache.clear()
        self._names = None
        self._nav_history.clear()
        self._update_back_button()
        self._search_invalidate()
        self._anno_cleanup()
        self.btn_undo.config(state="disabled")
        try:
            self.doc = fitz.open(self.path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Kann {self.path.name} nicht mehr öffnen:\n{exc}",
                                 parent=self)
            self.destroy()
            return
        if keep_thumbs is not None and len(keep_thumbs) == len(self.doc):
            self._thumbs = keep_thumbs
            self._thumbs_layout()
            self._thumb_gen += 1
            self._thumb_chain(self._thumb_gen)
        else:
            self._thumbs_reset()
        self.page_no = -1
        self._bookmarks_fill()
        self.show_page(max(0, min(page_no, len(self.doc) - 1)))

    # ---------------- Seitenleiste (Vorschaubilder, Reihenfolge) ----------------

    THUMB_W = 110
    THUMB_H = 150
    SLOT_H = 184                  # Höhe eines Eintrags (Bild + Seitenzahl)
    SIDEBAR_W = 150

    def _build_sidebar(self, body):
        self._thumbs = []             # PhotoImage je Seite oder None (noch nicht gerendert)
        self._thumb_job = None
        self._thumb_gen = 0           # macht laufende Render-Ketten ungültig
        self._drag_src = None
        self._drag_target = None
        self._drag_active = False
        self._drag_y0 = 0
        self._sidebar_shown = True
        self._reorder_backup_done = False

        self.sidebar = ttk.Frame(body, width=self.SIDEBAR_W)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="ns")
        self.sidebar.grid_propagate(False)
        self.sidebar.rowconfigure(0, weight=1)
        self.sidebar.columnconfigure(0, weight=1)
        self.side = tk.Canvas(self.sidebar, background="#2e2e2e", highlightthickness=0,
                              width=self.SIDEBAR_W - 16)
        self.side.grid(row=0, column=0, sticky="nsew")
        ssb = ttk.Scrollbar(self.sidebar, orient="vertical", command=self.side.yview)
        ssb.grid(row=0, column=1, sticky="ns")
        self.side.config(yscrollcommand=ssb.set)
        self.side.bind("<ButtonPress-1>", self._side_press)
        self.side.bind("<B1-Motion>", self._side_drag)
        self.side.bind("<ButtonRelease-1>", self._side_release)
        self.side.bind("<Button-3>", self._side_menu)
        self.side.bind("<MouseWheel>", self._side_wheel)

        self.side_menu = tk.Menu(self, tearoff=0)
        self.side_menu.add_command(label="Eine Position nach oben",
                                   command=lambda: self._move_by(-1))
        self.side_menu.add_command(label="Eine Position nach unten",
                                   command=lambda: self._move_by(1))
        self.side_menu.add_command(label="An den Anfang",
                                   command=lambda: self.move_page(self._side_ctx, 0))
        self.side_menu.add_command(label="Ans Ende",
                                   command=lambda: self.move_page(self._side_ctx, 10 ** 9))
        self.side_menu.add_separator()
        self.side_menu.add_command(label="Seite löschen…", command=self.delete_pages)
        self._side_ctx = 0
        self._drops = []
        self._insert_backup_done = False
        self.after(80, self._thumbs_reset)
        self.after(100, self._enable_drops)

    def _enable_drops(self):
        """Dateien aus dem Explorer lassen sich auf die Seitenleiste (an die
        Abwurfstelle) und auf die Seitenansicht (hinter die aktuelle Seite) ziehen."""
        try:
            self._drops = [FileDrop(self.side, self._drop_on_side),
                           FileDrop(self.canvas, self._drop_on_page)]
        except Exception:
            self._drops = []

    def _drop_on_side(self, pfade, x, y):
        cy = self.side.canvasy(y)
        self.insert_files(pfade, max(0, min(int(round(cy / self.SLOT_H)), len(self._thumbs))))

    def _drop_on_page(self, pfade, x, y):
        self.insert_files(pfade, self.page_no + 1)

    def insert_files(self, pfade, pos: int):
        """Fügt PDFs (und Bilder/Textdateien) vor Seite pos (0-basiert) in diese
        PDF ein und schreibt die Datei neu. Vor der ersten Änderung dieser Art
        entsteht eine Sicherung „…_vor-Einfuegen.pdf“."""
        if not self.doc:
            return
        pos = max(0, min(pos, len(self.doc)))
        eigene = self.path.resolve()
        quellen, uebersprungen = [], []
        for p in pfade:
            p = Path(p)
            if p.is_dir():
                continue
            if p.resolve() == eigene:
                uebersprungen.append(f"{p.name} (ist die geöffnete Datei)")
                continue
            try:
                quellen.append((p, open_as_pdf(p)))
            except Exception as exc:
                uebersprungen.append(f"{p.name} ({str(exc) or type(exc).__name__})")
        if not quellen:
            if uebersprungen:
                messagebox.showwarning(APP_TITLE, "Nichts eingefügt:\n" + "\n".join(uebersprungen),
                                       parent=self)
            return
        thumbs = list(self._thumbs)
        zwischen = self.path.with_name(f"{self.path.stem}.einfuegen-tmp.pdf")
        sicherung = None
        neu = 0
        self.release_document()
        try:
            d = fitz.open(self.path)
            try:
                at = pos
                for _p, src in quellen:
                    d.insert_pdf(src, start_at=at)
                    at += src.page_count
                    neu += src.page_count
                d.save(zwischen, garbage=3, deflate=True)
            finally:
                d.close()
            if not self._insert_backup_done:
                sicherung = unique_path(self.path.with_name(f"{self.path.stem}_vor-Einfuegen.pdf"))
                shutil.copy2(self.path, sicherung)
            os.replace(zwischen, self.path)
        except Exception as exc:
            try:
                if zwischen.exists():
                    zwischen.unlink()
                if sicherung and sicherung.exists():
                    sicherung.unlink()
            except OSError:
                pass
            self.reload_document(self.page_no, keep_thumbs=thumbs)
            messagebox.showerror(APP_TITLE, f"Einfügen fehlgeschlagen:\n{exc}", parent=self)
            return
        finally:
            for _p, src in quellen:
                try:
                    src.close()
                except Exception:
                    pass
        if sicherung:
            self._insert_backup_done = True
        thumbs[pos:pos] = [None] * neu
        self.reload_document(pos, keep_thumbs=thumbs)
        text = f"{neu} Seite{'n' if neu != 1 else ''} eingefügt"
        if sicherung:
            text += f", Sicherung: {sicherung.name}"
        self.sel_status.config(text=text)
        if uebersprungen:
            messagebox.showwarning(APP_TITLE, "Übersprungen:\n" + "\n".join(uebersprungen),
                                   parent=self)

    def toggle_sidebar(self):
        if self._bm_shown:
            self.toggle_bookmarks()       # zurück zu den Vorschaubildern
            return
        self._sidebar_shown = not self._sidebar_shown
        if self._sidebar_shown:
            self.sidebar.grid()
        else:
            self.sidebar.grid_remove()

    def _side_wheel(self, ev):
        self.side.yview_scroll(-3 if ev.delta > 0 else 3, "units")
        return "break"

    def _thumbs_reset(self):
        """Verwirft alle Vorschaubilder und rendert sie neu (nach und nach)."""
        if not self.doc:
            return
        self._thumbs = [None] * len(self.doc)
        self._thumbs_layout()
        self._thumb_gen += 1
        self._thumb_chain(self._thumb_gen)

    def _thumb_chain(self, gen):
        """Rendert je Durchgang einige Vorschaubilder, damit das Fenster bedienbar bleibt."""
        if gen != self._thumb_gen or not self.doc:
            return
        todo = [i for i, t in enumerate(self._thumbs) if t is None]
        # zuerst die Seiten nahe der aktuellen
        todo.sort(key=lambda i: abs(i - max(0, self.page_no)))
        for i in todo[:3]:
            try:
                page = self.doc[i]
                r = page.rect
                s = min(self.THUMB_W / max(1.0, r.width), self.THUMB_H / max(1.0, r.height))
                pix = page.get_pixmap(matrix=fitz.Matrix(s, s), colorspace=fitz.csRGB,
                                      alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                self._thumbs[i] = ImageTk.PhotoImage(img)
            except Exception:
                self._thumbs[i] = ImageTk.PhotoImage(Image.new("RGB", (40, 56), "#808080"))
            self._thumb_draw_slot(i)
        if len(todo) > 3:
            self._thumb_job = self.after(5, lambda: self._thumb_chain(gen))
        else:
            self._thumbs_mark()

    def _thumbs_layout(self):
        n = len(self._thumbs)
        self.side.delete("all")
        for i in range(n):
            self._thumb_draw_slot(i)
        self.side.config(scrollregion=(0, 0, self.SIDEBAR_W - 16, n * self.SLOT_H))
        self._thumbs_mark()

    def _thumb_draw_slot(self, i):
        self.side.delete(f"slot{i}")
        cx = (self.SIDEBAR_W - 16) // 2
        y0 = i * self.SLOT_H
        img = self._thumbs[i] if i < len(self._thumbs) else None
        if img is not None:
            self.side.create_image(cx, y0 + 6 + self.THUMB_H // 2, image=img,
                                   tags=(f"slot{i}",))
        else:
            self.side.create_rectangle(cx - 40, y0 + 6, cx + 40, y0 + 6 + 110,
                                       fill="#505050", outline="", tags=(f"slot{i}",))
        self.side.create_text(cx, y0 + 6 + self.THUMB_H + 12, text=str(i + 1),
                              fill="#d0d0d0", tags=(f"slot{i}",))

    def _thumbs_mark(self):
        """Rahmen um die aktuelle Seite; Seitenleiste dorthin scrollen."""
        if not getattr(self, "side", None) or not self.doc:
            return
        self.side.delete("mark")
        i = self.page_no
        if not (0 <= i < len(self._thumbs)):
            return
        w = self.SIDEBAR_W - 16
        y0 = i * self.SLOT_H
        self.side.create_rectangle(4, y0 + 2, w - 4, y0 + self.SLOT_H - 4,
                                   outline="#4da3ff", width=3, tags=("mark",))
        n = len(self._thumbs)
        total = max(1, n * self.SLOT_H)
        top, bottom = self.side.yview()
        if y0 / total < top or (y0 + self.SLOT_H) / total > bottom:
            self.side.yview_moveto(max(0.0, (y0 - self.SLOT_H) / total))

    def _side_index_at(self, y):
        cy = self.side.canvasy(y)
        return max(0, min(int(cy // self.SLOT_H), len(self._thumbs) - 1))

    def _side_press(self, ev):
        if not self.doc:
            return
        self._drag_src = self._side_index_at(ev.y)
        self._drag_y0 = ev.y
        self._drag_active = False
        self._drag_target = None
        self.show_page(self._drag_src)

    def _side_drag(self, ev):
        if self._drag_src is None or not self.doc:
            return
        if not self._drag_active and abs(ev.y - self._drag_y0) < 6:
            return
        self._drag_active = True
        h = self.side.winfo_height()
        if ev.y < 20:
            self.side.yview_scroll(-1, "units")
        elif ev.y > h - 20:
            self.side.yview_scroll(1, "units")
        cy = self.side.canvasy(ev.y)
        t = max(0, min(int(round(cy / self.SLOT_H)), len(self._thumbs)))
        self._drag_target = t
        self.side.delete("dropline")
        y = t * self.SLOT_H
        self.side.create_line(4, y, self.SIDEBAR_W - 20, y, fill="#ff9d2e", width=4,
                              tags=("dropline",))

    def _side_release(self, ev):
        src, t, aktiv = self._drag_src, self._drag_target, self._drag_active
        self._drag_src = self._drag_target = None
        self._drag_active = False
        self.side.delete("dropline")
        if src is None or not aktiv or t is None:
            return
        self.move_page(src, t if t <= src else t - 1)

    def _side_menu(self, ev):
        if not self.doc:
            return
        self._side_ctx = self._side_index_at(ev.y)
        self.show_page(self._side_ctx)
        n = len(self.doc)
        self.side_menu.entryconfig(0, state="normal" if self._side_ctx > 0 else "disabled")
        self.side_menu.entryconfig(1, state="normal" if self._side_ctx < n - 1 else "disabled")
        self.side_menu.tk_popup(ev.x_root, ev.y_root)

    def _move_by(self, delta):
        self.move_page(self._side_ctx, self._side_ctx + delta)

    def move_page(self, src: int, dst: int):
        """Verschiebt Seite src so, dass sie danach an Position dst steht
        (0-basiert) – und schreibt die Datei neu. Vor der ersten Änderung
        dieser Sitzung wird eine Sicherung „…_vor-Umordnung.pdf“ angelegt."""
        if not self.doc:
            return
        n = len(self.doc)
        dst = max(0, min(dst, n - 1))
        if not (0 <= src < n) or src == dst:
            return
        thumbs = list(self._thumbs)
        zwischen = self.path.with_name(f"{self.path.stem}.umordnen-tmp.pdf")
        sicherung = None
        self.release_document()
        try:
            d = fitz.open(self.path)
            try:
                # move_page(pno, to): vor Seite „to“ einfügen, -1 = ans Ende
                if dst > src:
                    d.move_page(src, dst + 1 if dst + 1 < n else -1)
                else:
                    d.move_page(src, dst)
                d.save(zwischen, garbage=3, deflate=True)
            finally:
                d.close()
            if not self._reorder_backup_done:
                sicherung = unique_path(self.path.with_name(f"{self.path.stem}_vor-Umordnung.pdf"))
                shutil.copy2(self.path, sicherung)
            os.replace(zwischen, self.path)
        except Exception as exc:
            try:
                if zwischen.exists():
                    zwischen.unlink()
                if sicherung and sicherung.exists():
                    sicherung.unlink()
            except OSError:
                pass
            self.reload_document(self.page_no, keep_thumbs=thumbs)
            messagebox.showerror(APP_TITLE, f"Umordnen fehlgeschlagen:\n{exc}", parent=self)
            return
        if sicherung:
            self._reorder_backup_done = True
            self.sel_status.config(text=f"Sicherung: {sicherung.name}")
        thumbs.insert(dst, thumbs.pop(src))
        self.reload_document(dst, keep_thumbs=thumbs)

    # ---------------- Bearbeiten ----------------

    def _open_in_editor(self):
        """Öffnet das PDF-Scan-Werkzeug mit dieser Datei (Vorgabe: Original
        ersetzen, 300 dpi) und schließt den reinen Betrachter."""
        self.master.edit_from_viewer(self.path)
        self.destroy()

    # ---------------- Vollbild / Schließen ----------------

    def toggle_fullscreen(self):
        """Schaltet den Vollbildmodus ein bzw. aus (F11)."""
        self._fullscreen = not self._fullscreen
        self.attributes("-fullscreen", self._fullscreen)
        self.btn_full.config(text="Vollbild beenden (Esc)" if self._fullscreen
                             else "⛶ Vollbild (F11)")

    def _on_escape(self, _ev=None):
        if self._anno_sel:
            self._anno_select(None)
            return
        if self.search_visible():
            self.close_search()
            return
        if self._fullscreen:
            self.toggle_fullscreen()
        else:
            self.destroy()

    def destroy(self):
        try:
            self._anno_cleanup()
        except Exception:
            pass
        for fd in getattr(self, "_drops", []):
            fd.close()
        self._drops = []
        doc, self.doc = getattr(self, "doc", None), None
        if doc:
            try:
                doc.close()
            except Exception:
                pass
        master = self.master
        super().destroy()
        # Reine Betrachten-Instanz (Hauptfenster verborgen): beendet sich,
        # sobald der letzte Viewer geschlossen wird und nicht "Bearbeiten"
        # gewählt wurde.
        if isinstance(master, PdfToolApp) and getattr(master, "_view_only", False):
            other_viewers = [w for w in master.winfo_children()
                             if isinstance(w, PdfViewer) and w.winfo_exists()]
            if not other_viewers:
                master.destroy()


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class PrintDialog(tk.Toplevel):
    """Druckfenster des Betrachters: Drucker, Seitenbereich, Exemplare.

    Der Druck selbst laeuft im Arbeitsfaden, Rueckmeldungen kommen ueber
    self.after ins Fenster – Tkinter wird nie aus dem Faden angefasst.
    """

    def __init__(self, master, path, total_pages: int, current_page: int = 0):
        super().__init__(master)
        self.path = Path(path)
        self.total = max(1, total_pages)
        self.current = current_page
        self.cancel = threading.Event()
        self._laeuft = False

        self.printers, standard = list_printers()
        if not self.printers:
            self.destroy()
            messagebox.showerror(
                APP_TITLE,
                "Es wurde kein Drucker gefunden.\n\n"
                "Falls Drucker eingerichtet sind, fehlt pywin32:\n"
                "C:\\Python314\\python.exe -m pip install pywin32",
                parent=master)
            return

        self.title(f"Drucken – {self.path.name}")
        self.transient(master)
        self.resizable(False, False)

        self.var_printer = tk.StringVar(value=standard or self.printers[0])
        self.var_scope = tk.StringVar(value="alle")
        self.var_range = tk.StringVar(value=str(current_page + 1))
        self.var_copies = tk.StringVar(value="1")
        self.var_rotate = tk.BooleanVar(value=True)

        self._build_ui()
        self.bind("<Escape>", lambda e: self._close())
        self.bind("<Return>", lambda e: self.do_print())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.grab_set()
        self.btn_print.focus_set()

    def _build_ui(self):
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Drucker:").grid(row=0, column=0, sticky="w")
        self.cmb_printer = ttk.Combobox(frm, textvariable=self.var_printer,
                                        values=self.printers, state="readonly",
                                        width=44)
        self.cmb_printer.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(6, 0))

        ttk.Separator(frm).grid(row=1, column=0, columnspan=3, sticky="ew", pady=8)

        seiten = ttk.Labelframe(frm, text="Seiten", padding=6)
        seiten.grid(row=2, column=0, columnspan=3, sticky="ew")
        seiten.columnconfigure(1, weight=1)
        ttk.Radiobutton(seiten, text=f"Alle ({self.total})", value="alle",
                        variable=self.var_scope)\
            .grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(seiten, text=f"Nur die angezeigte Seite ({self.current + 1})",
                        value="aktuell", variable=self.var_scope)\
            .grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(seiten, text="Bereich:", value="bereich",
                        variable=self.var_scope).grid(row=2, column=0, sticky="w")
        ent = ttk.Entry(seiten, textvariable=self.var_range, width=18)
        ent.grid(row=2, column=1, sticky="w", padx=(4, 0))
        # Tippen im Feld waehlt den passenden Knopf gleich mit aus
        ent.bind("<FocusIn>", lambda e: self.var_scope.set("bereich"))
        ttk.Label(seiten, text="z. B. 1,3,5-8", foreground="gray")\
            .grid(row=3, column=1, sticky="w", padx=(4, 0))

        unten = ttk.Frame(frm)
        unten.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(unten, text="Exemplare:").pack(side="left")
        ttk.Spinbox(unten, from_=1, to=99, width=4,
                    textvariable=self.var_copies).pack(side="left", padx=(6, 16))
        ttk.Checkbutton(unten, text="Automatisch drehen, damit die Seite passt",
                        variable=self.var_rotate).pack(side="left")

        self.progress = ttk.Progressbar(frm, mode="determinate")
        self.progress.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 2))
        self.status = ttk.Label(frm, text="", foreground="gray")
        self.status.grid(row=5, column=0, columnspan=3, sticky="w")

        knoepfe = ttk.Frame(frm)
        knoepfe.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.btn_print = ttk.Button(knoepfe, text="🖨  Drucken", command=self.do_print)
        self.btn_print.pack(side="left")
        self.btn_close = ttk.Button(knoepfe, text="Schließen", command=self._close)
        self.btn_close.pack(side="right")

    # ---------------- Druck ----------------

    def _selected_pages(self) -> list:
        wahl = self.var_scope.get()
        if wahl == "aktuell":
            return [self.current]
        if wahl == "bereich":
            return parse_page_range(self.var_range.get(), self.total)
        return list(range(self.total))

    def do_print(self):
        if self._laeuft:
            return
        seiten = self._selected_pages()
        if not seiten:
            messagebox.showwarning(APP_TITLE,
                                   "Der Seitenbereich ergibt keine Seite.\n"
                                   f"Das Dokument hat {self.total} Seiten.",
                                   parent=self)
            return
        try:
            exemplare = max(1, min(99, int(self.var_copies.get())))
        except ValueError:
            exemplare = 1
        self.var_copies.set(str(exemplare))

        ziel = None
        drucker = self.var_printer.get()
        if prints_to_file(drucker):
            # Microsoft Print to PDF & Co. brauchen die Zieldatei vorab,
            # sonst bricht StartDoc wortlos ab
            endung = ".xps" if "XPS" in drucker.upper() else ".pdf"
            ziel = filedialog.asksaveasfilename(
                parent=self, title=f"{drucker}: Ziel",
                initialfile=self.path.stem + endung,
                defaultextension=endung,
                filetypes=[(endung[1:].upper(), "*" + endung)])
            if not ziel:
                return

        self.cancel.clear()
        self._laeuft = True
        self.progress.config(maximum=len(seiten) * exemplare, value=0)
        self.btn_print.config(state="disabled")
        self.btn_close.config(text="Abbrechen")
        self.status.config(text=f"Sende an {drucker} …")
        threading.Thread(target=self._worker,
                         args=(drucker, seiten, exemplare,
                               self.var_rotate.get(), ziel),
                         daemon=True).start()

    def _worker(self, printer, seiten, exemplare, drehen, ziel=None):
        try:
            n = print_pdf(self.path, printer, seiten, exemplare,
                          auto_rotate=drehen, output_file=ziel,
                          page_cb=lambda k: self.after(0, self._fortschritt, k),
                          cancel=self.cancel)
        except Exception as exc:
            self.after(0, self._fertig, 0, str(exc))
        else:
            self.after(0, self._fertig, n, None)

    def _fortschritt(self, k: int):
        if not self.winfo_exists():
            return
        self.progress.config(value=k)
        self.status.config(text=f"Seite {k} von {int(self.progress['maximum'])} …")

    def _fertig(self, n: int, fehler):
        self._laeuft = False
        if not self.winfo_exists():
            return
        self.btn_print.config(state="normal")
        self.btn_close.config(text="Schließen")
        if fehler:
            self.status.config(text="Fehler beim Drucken.")
            messagebox.showerror(APP_TITLE, f"Drucken fehlgeschlagen:\n{fehler}",
                                 parent=self)
            return
        if self.cancel.is_set():
            self.status.config(text=f"Abgebrochen nach {n} Seiten.")
            return
        wort = "Seite" if n == 1 else "Seiten"
        self.status.config(text=f"{n} {wort} an {self.var_printer.get()} gesendet.")
        self.after(1500, self.destroy)

    def _close(self):
        """Waehrend des Drucks bricht der Knopf ab, sonst schliesst er."""
        if self._laeuft:
            self.cancel.set()
            self.status.config(text="Abbruch – warte auf den Drucker …")
            return
        self.destroy()


class SavePagesDialog(tk.Toplevel):
    """Speichert eine oder mehrere Seiten der angezeigten PDF als eigene Dateien.

    Läuft ohne Arbeitsfaden im Hauptfaden – das Herauslösen einzelner
    Seiten mit PyMuPDF ist schnell genug, um das Fenster nicht spürbar
    zu blockieren.
    """

    def __init__(self, master, path, total_pages: int, current_page: int = 0):
        super().__init__(master)
        self.path = Path(path)
        self.total = max(1, total_pages)
        self.current = current_page

        self.title(f"Seite speichern – {self.path.name}")
        self.transient(master)
        self.resizable(False, False)

        self.var_scope = tk.StringVar(value="aktuell")
        self.var_range = tk.StringVar(value=str(current_page + 1))
        self.var_folder = tk.StringVar(value=str(self.path.parent))
        self.var_name = tk.StringVar(value=self.path.stem)
        self.var_mode = tk.StringVar(value="gemeinsam")

        self._build_ui()
        self.var_folder.trace_add("write", lambda *a: self._refresh_file_list())
        self._refresh_file_list()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self.do_save())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.btn_save.focus_set()

    def _build_ui(self):
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        seiten = ttk.Labelframe(frm, text="Seiten", padding=6)
        seiten.grid(row=0, column=0, columnspan=3, sticky="ew")
        seiten.columnconfigure(1, weight=1)
        ttk.Radiobutton(seiten, text=f"Nur die angezeigte Seite ({self.current + 1})",
                        value="aktuell", variable=self.var_scope)\
            .grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(seiten, text="Bereich:", value="bereich",
                        variable=self.var_scope).grid(row=1, column=0, sticky="w")
        ent = ttk.Entry(seiten, textvariable=self.var_range, width=18)
        ent.grid(row=1, column=1, sticky="w", padx=(4, 0))
        # Tippen im Feld waehlt den passenden Knopf gleich mit aus
        ent.bind("<FocusIn>", lambda e: self.var_scope.set("bereich"))
        ttk.Label(seiten, text="z. B. 1,3,5-8", foreground="gray")\
            .grid(row=2, column=1, sticky="w", padx=(4, 0))
        ttk.Radiobutton(seiten, text=f"Alle ({self.total})", value="alle",
                        variable=self.var_scope)\
            .grid(row=3, column=0, columnspan=2, sticky="w")

        ausgabe = ttk.Labelframe(frm, text="Ausgabe", padding=6)
        ausgabe.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Radiobutton(ausgabe, text="Jede Seite als eigene Datei",
                        value="einzeln", variable=self.var_mode)\
            .grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(ausgabe, text="Alle ausgewählten Seiten in einer Datei",
                        value="gemeinsam", variable=self.var_mode)\
            .grid(row=1, column=0, sticky="w")

        ttk.Separator(frm).grid(row=2, column=0, columnspan=3, sticky="ew", pady=8)

        name_frame = ttk.Labelframe(frm, text="Dateiname", padding=6)
        name_frame.grid(row=3, column=0, columnspan=3, sticky="ew")
        name_frame.columnconfigure(0, weight=1)
        ttk.Entry(name_frame, textvariable=self.var_name).grid(row=0, column=0, sticky="ew")
        ttk.Label(name_frame,
                  text="Bei einer Seite oder \"in einer Datei\" wird genau dieser Name "
                       "verwendet, bei eigenen Dateien je Seite wird _S1, _S2 … angehängt.",
                  foreground="gray").grid(row=1, column=0, sticky="w", pady=(4, 0))

        ziel = ttk.Labelframe(frm, text="Zielordner", padding=6)
        ziel.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ziel.columnconfigure(0, weight=1)
        ttk.Entry(ziel, textvariable=self.var_folder).grid(row=0, column=0, sticky="ew")
        ttk.Button(ziel, text="Durchsuchen…", command=self._pick_folder)\
            .grid(row=0, column=1, padx=(6, 0))

        ttk.Label(ziel, text="Bereits vorhandene Dateien:", foreground="gray")\
            .grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        liste = ttk.Frame(ziel)
        liste.grid(row=2, column=0, columnspan=2, sticky="ew")
        liste.columnconfigure(0, weight=1)
        self.lst_files = tk.Listbox(liste, height=6, activestyle="none")
        self.lst_files.grid(row=0, column=0, sticky="ew")
        sb = ttk.Scrollbar(liste, orient="vertical", command=self.lst_files.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.lst_files.config(yscrollcommand=sb.set)

        self.status = ttk.Label(frm, text="", foreground="gray")
        self.status.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))

        knoepfe = ttk.Frame(frm)
        knoepfe.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.btn_save = ttk.Button(knoepfe, text="💾  Speichern", command=self.do_save)
        self.btn_save.pack(side="left")
        ttk.Button(knoepfe, text="Schließen", command=self.destroy).pack(side="right")

    def _pick_folder(self):
        ordner = ordnerwahl.ordner_waehlen(self, "Zielordner wählen", self.var_folder.get() or str(self.path.parent))
        if ordner:
            self.var_folder.set(ordner)

    def _refresh_file_list(self):
        """Zeigt die Dateien, die bereits im gewählten Zielordner liegen."""
        if not hasattr(self, "lst_files"):
            return
        self.lst_files.delete(0, "end")
        ordner = Path(self.var_folder.get())
        try:
            dateien = sorted((p.name for p in ordner.iterdir() if p.is_file()),
                             key=str.lower)
        except OSError:
            self.lst_files.insert("end", "(Ordner nicht gefunden)")
            return
        if not dateien:
            self.lst_files.insert("end", "(leer)")
            return
        for name in dateien:
            self.lst_files.insert("end", name)

    def _selected_pages(self) -> list:
        wahl = self.var_scope.get()
        if wahl == "aktuell":
            return [self.current]
        if wahl == "bereich":
            return parse_page_range(self.var_range.get(), self.total)
        return list(range(self.total))

    def _base_name(self) -> str:
        """Vom Feld übernommener Dateiname, um unzulässige Zeichen bereinigt."""
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", self.var_name.get().strip())
        name = name.rstrip(" .")
        return name or self.path.stem

    def do_save(self):
        seiten = self._selected_pages()
        if not seiten:
            messagebox.showwarning(APP_TITLE,
                                   "Der Seitenbereich ergibt keine Seite.\n"
                                   f"Das Dokument hat {self.total} Seiten.",
                                   parent=self)
            return
        basisname = self._base_name()
        ordner = Path(self.var_folder.get())
        try:
            ordner.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Zielordner nicht erreichbar:\n{exc}",
                                 parent=self)
            return

        try:
            src = fitz.open(self.path)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Kann {self.path.name} nicht öffnen:\n{exc}",
                                 parent=self)
            return

        ziele = []
        try:
            if len(seiten) == 1 or self.var_mode.get() == "gemeinsam":
                # Eine Seite, oder mehrere zusammen in einem neuen Dokument
                out = fitz.open()
                for pno in seiten:
                    out.insert_pdf(src, from_page=pno, to_page=pno)
                ziel = unique_path(ordner / f"{basisname}.pdf")
                out.save(ziel)
                out.close()
                ziele.append(ziel)
            else:
                pad = len(str(self.total))
                for pno in seiten:
                    out = fitz.open()
                    out.insert_pdf(src, from_page=pno, to_page=pno)
                    dateiname = f"{basisname}_S{pno + 1:0{pad}d}.pdf"
                    ziel = unique_path(ordner / dateiname)
                    out.save(ziel)
                    out.close()
                    ziele.append(ziel)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Speichern fehlgeschlagen:\n{exc}",
                                 parent=self)
            return
        finally:
            src.close()

        wort = "Seite" if len(ziele) == 1 else "Seiten"
        self.status.config(text=f"{len(ziele)} {wort} gespeichert in {ordner}")
        self._refresh_file_list()
        if ziele:
            import subprocess
            # Als einzelner String uebergeben, nicht als Liste – sonst quotet
            # subprocess "/select,PFAD" bei Leerzeichen im Pfad komplett als
            # einen Block, den Explorer nicht parsen kann.
            subprocess.run(f'explorer /select,"{ziele[0]}"')
        self.after(1200, self.destroy)


class DeletePagesDialog(tk.Toplevel):
    """Löscht eine oder mehrere Seiten aus der angezeigten PDF.

    Entweder wird die Datei selbst geändert (auf Wunsch mit Sicherungskopie des
    bisherigen Stands) oder das Ergebnis als neue Datei daneben gespeichert.
    Beim Ändern gibt der Betrachter die Datei kurz frei, die Datei wird neben
    dem Original in eine Zwischendatei geschrieben und erst danach ersetzt,
    sodass bei einem Fehler das Original unverändert bleibt.
    """

    def __init__(self, viewer, path, total_pages: int, current_page: int = 0):
        super().__init__(viewer)
        self.viewer = viewer
        self.path = Path(path)
        self.total = max(1, total_pages)
        self.current = current_page

        self.title(f"Seite löschen – {self.path.name}")
        self.transient(viewer)
        self.resizable(False, False)

        self.var_scope = tk.StringVar(value="aktuell")
        self.var_range = tk.StringVar(value=str(current_page + 1))
        self.var_mode = tk.StringVar(value="aendern")
        self.var_backup = tk.BooleanVar(value=True)
        self.var_name = tk.StringVar(value=f"{self.path.stem}_gekuerzt")

        self._build_ui()
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.btn_delete.focus_set()

    def _build_ui(self):
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)

        seiten = ttk.Labelframe(frm, text="Welche Seiten", padding=6)
        seiten.grid(row=0, column=0, sticky="ew")
        seiten.columnconfigure(1, weight=1)
        ttk.Radiobutton(seiten, text=f"Nur die angezeigte Seite ({self.current + 1} von {self.total})",
                        value="aktuell", variable=self.var_scope)\
            .grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(seiten, text="Bereich:", value="bereich",
                        variable=self.var_scope).grid(row=1, column=0, sticky="w")
        ent = ttk.Entry(seiten, textvariable=self.var_range, width=18)
        ent.grid(row=1, column=1, sticky="w", padx=(4, 0))
        ent.bind("<FocusIn>", lambda e: self.var_scope.set("bereich"))
        ttk.Label(seiten, text="z. B. 1,3,5-8", foreground="gray")\
            .grid(row=2, column=1, sticky="w", padx=(4, 0))

        modus = ttk.Labelframe(frm, text="Was soll passieren", padding=6)
        modus.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        modus.columnconfigure(0, weight=1)
        ttk.Radiobutton(modus, text="Diese Datei ändern (die Seiten sind danach weg)",
                        value="aendern", variable=self.var_mode, command=self._mode_changed)\
            .grid(row=0, column=0, sticky="w")
        self.chk_backup = ttk.Checkbutton(
            modus, text="Vorher eine Sicherungskopie anlegen (…_vor-Loeschung.pdf)",
            variable=self.var_backup)
        self.chk_backup.grid(row=1, column=0, sticky="w", padx=(22, 0))
        ttk.Radiobutton(modus, text="Als neue Datei speichern, das Original bleibt unverändert",
                        value="kopie", variable=self.var_mode, command=self._mode_changed)\
            .grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.ent_name = ttk.Entry(modus, textvariable=self.var_name)
        self.ent_name.grid(row=3, column=0, sticky="ew", padx=(22, 0), pady=(2, 0))
        ttk.Label(modus, text=f"Wird im gleichen Ordner gespeichert ({self.path.parent}).",
                  foreground="gray", wraplength=420, justify="left")\
            .grid(row=4, column=0, sticky="w", padx=(22, 0))

        self.status = ttk.Label(frm, text="", foreground="gray")
        self.status.grid(row=2, column=0, sticky="w", pady=(8, 0))

        knoepfe = ttk.Frame(frm)
        knoepfe.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.btn_delete = ttk.Button(knoepfe, text="🗑  Seiten löschen", command=self.do_delete)
        self.btn_delete.pack(side="left")
        ttk.Button(knoepfe, text="Abbrechen", command=self.destroy).pack(side="right")
        self._mode_changed()

    def _mode_changed(self):
        aendern = self.var_mode.get() == "aendern"
        self.chk_backup.config(state="normal" if aendern else "disabled")
        self.ent_name.config(state="disabled" if aendern else "normal")

    def _selected_pages(self) -> list:
        if self.var_scope.get() == "aktuell":
            return [self.current]
        return sorted(parse_page_range(self.var_range.get(), self.total))

    def _base_name(self) -> str:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", self.var_name.get().strip())
        name = name.rstrip(" .")
        if name.lower().endswith(".pdf"):
            name = name[:-4]
        return name or f"{self.path.stem}_gekuerzt"

    @staticmethod
    def _seiten_text(seiten: list) -> str:
        """[0, 1, 2, 5] -> '1-3, 6'"""
        teile, start, vorher = [], None, None
        for n in [s + 1 for s in seiten]:
            if start is None:
                start = vorher = n
            elif n == vorher + 1:
                vorher = n
            else:
                teile.append(str(start) if start == vorher else f"{start}-{vorher}")
                start = vorher = n
        if start is not None:
            teile.append(str(start) if start == vorher else f"{start}-{vorher}")
        return ", ".join(teile)

    def do_delete(self):
        seiten = self._selected_pages()
        if not seiten:
            messagebox.showwarning(APP_TITLE,
                                   "Der Seitenbereich ergibt keine Seite.\n"
                                   f"Das Dokument hat {self.total} Seiten.", parent=self)
            return
        if len(seiten) >= self.total:
            messagebox.showwarning(APP_TITLE,
                                   "Es können nicht alle Seiten gelöscht werden, "
                                   "mindestens eine muss übrig bleiben.", parent=self)
            return
        text = self._seiten_text(seiten)
        wort = "Seite" if len(seiten) == 1 else "Seiten"
        aendern = self.var_mode.get() == "aendern"
        if aendern:
            frage = f"{wort} {text} aus „{self.path.name}“ löschen?\n\nDie Datei wird geändert"
            frage += (" (eine Sicherungskopie des bisherigen Stands wird angelegt)."
                      if self.var_backup.get() else " und der bisherige Stand geht verloren.")
        else:
            frage = f"{wort} {text} entfernen und das Ergebnis als neue Datei speichern?"
        if not messagebox.askyesno(APP_TITLE, frage, parent=self):
            return
        try:
            if aendern:
                ergebnis = self._datei_aendern(seiten)
            else:
                ergebnis = self._als_kopie(seiten)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Löschen fehlgeschlagen:\n{exc}", parent=self)
            return
        self.status.config(text=ergebnis, foreground="#006000")
        self.btn_delete.config(state="disabled")
        self.after(1400, self.destroy)

    def _datei_aendern(self, seiten: list) -> str:
        viewer = self.viewer
        zwischen = self.path.with_name(f"{self.path.stem}.loeschen-tmp.pdf")
        sicherung = None
        viewer.release_document()            # Windows: geöffnete Datei lässt sich nicht ersetzen
        try:
            src = fitz.open(self.path)
            try:
                if self.var_backup.get():
                    sicherung = unique_path(self.path.with_name(f"{self.path.stem}_vor-Loeschung.pdf"))
                src.delete_pages(seiten)
                src.save(zwischen, garbage=3, deflate=True)
            finally:
                src.close()
            if sicherung:
                shutil.copy2(self.path, sicherung)
            os.replace(zwischen, self.path)
        except Exception:
            try:
                if zwischen.exists():
                    zwischen.unlink()
                if sicherung and sicherung.exists():
                    sicherung.unlink()
            except OSError:
                pass
            viewer.reload_document(self.current)
            raise
        viewer.reload_document(min(seiten[0], self.total - len(seiten) - 1))
        gelöscht = f"{len(seiten)} Seite{'n' if len(seiten) != 1 else ''} gelöscht"
        return gelöscht + (f", Sicherung: {sicherung.name}" if sicherung else "") + "."

    def _als_kopie(self, seiten: list) -> str:
        ziel = unique_path(self.path.with_name(self._base_name() + ".pdf"))
        src = fitz.open(self.path)
        try:
            src.delete_pages(seiten)
            src.save(ziel, garbage=3, deflate=True)
        finally:
            src.close()
        return f"Gespeichert als {ziel.name} ({self.total - len(seiten)} Seiten)."


class MergeDialog(tk.Toplevel):
    """Fenster zum Zusammenfassen mehrerer Dateien zu einer PDF.

    Die Reihenfolge lässt sich vorher ändern: Beim Aufruf über das
    Kontextmenü startet der Explorer den Befehl einmal je markierter Datei,
    die Reihenfolge des Eintreffens ist deshalb nicht verlässlich.
    """

    def __init__(self, master, paths):
        super().__init__(master)
        self.app = master
        self.title("Ausgewählte in eine PDF zusammenfassen")
        self.geometry("640x480")
        self.minsize(520, 380)
        self.transient(master)

        self.paths: list[Path] = []
        for p in paths:
            fp = Path(p)
            if fp.is_file() and fp not in self.paths:
                self.paths.append(fp)

        self.var_target = tk.StringVar()
        self.var_show = tk.BooleanVar(value=True)
        self._build_ui()
        self._fill_list()
        self._suggest_target()

    def _build_ui(self):
        frm = ttk.Frame(self, padding=8)
        frm.pack(fill="both", expand=True)
        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)

        ttk.Label(frm, text="Reihenfolge der Seiten (von oben nach unten):")            .grid(row=0, column=0, columnspan=2, sticky="w")

        self.listbox = tk.Listbox(frm, selectmode="extended")
        self.listbox.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        sb = ttk.Scrollbar(frm, command=self.listbox.yview)
        sb.grid(row=1, column=1, sticky="ns", pady=(4, 0))
        self.listbox.config(yscrollcommand=sb.set)

        btns = ttk.Frame(frm)
        btns.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(btns, text="▲ nach oben", command=lambda: self._move(-1))            .pack(side="left")
        ttk.Button(btns, text="▼ nach unten", command=lambda: self._move(1))            .pack(side="left", padx=4)
        ttk.Button(btns, text="Nach Namen sortieren", command=self._sort)            .pack(side="left")
        ttk.Button(btns, text="Hinzufügen…", command=self._add)            .pack(side="right")
        ttk.Button(btns, text="Entfernen", command=self._remove)            .pack(side="right", padx=4)

        ttk.Separator(frm).grid(row=3, column=0, columnspan=2, sticky="ew", pady=8)

        ziel = ttk.Frame(frm)
        ziel.grid(row=4, column=0, columnspan=2, sticky="ew")
        ziel.columnconfigure(1, weight=1)
        ttk.Label(ziel, text="Ergebnis:").grid(row=0, column=0, sticky="w")
        ttk.Entry(ziel, textvariable=self.var_target)            .grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(ziel, text="Speichern unter…", command=self._choose_target)            .grid(row=0, column=2)

        ttk.Checkbutton(frm, text="Ergebnis danach anzeigen", variable=self.var_show)            .grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        unten = ttk.Frame(frm)
        unten.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.btn_merge = ttk.Button(unten, text="▶  Zusammenfassen", command=self.do_merge)
        self.btn_merge.pack(side="left")
        ttk.Button(unten, text="Abbrechen", command=self.destroy).pack(side="right")
        self.info = ttk.Label(unten, text="", foreground="gray")
        self.info.pack(side="left", padx=10)

    # ---------------- Liste ----------------

    def _fill_list(self, keep: int | None = None):
        self.listbox.delete(0, "end")
        gesamt = 0
        for fp in self.paths:
            seiten = page_count_of(fp)
            gesamt += seiten
            if not seiten:
                zusatz = "  (nicht lesbar)"
            else:
                zusatz = f"  ({seiten} Seite)" if seiten == 1 else f"  ({seiten} Seiten)"
            self.listbox.insert("end", fp.name + zusatz)
        if len(self.paths) < 2:
            self.info.config(
                text="Nur eine Datei – weitere über „Hinzufügen…“ wählen")
        else:
            self.info.config(
                text=f"{len(self.paths)} Dateien, {gesamt} "
                     + ("Seite" if gesamt == 1 else "Seiten"))
        if keep is not None and 0 <= keep < len(self.paths):
            self.listbox.selection_set(keep)
            self.listbox.see(keep)

    def _move(self, delta: int):
        sel = list(self.listbox.curselection())
        if len(sel) != 1:
            return
        i = sel[0]
        j = i + delta
        if not (0 <= j < len(self.paths)):
            return
        self.paths[i], self.paths[j] = self.paths[j], self.paths[i]
        self._fill_list(keep=j)

    def _sort(self):
        self.paths.sort(key=natural_key)
        self._fill_list()

    def _remove(self):
        for i in sorted(self.listbox.curselection(), reverse=True):
            del self.paths[i]
        self._fill_list()
        self._suggest_target()

    def _add(self):
        neu = filedialog.askopenfilenames(
            title="Dateien hinzufügen",
            filetypes=[("PDF, Bilder und Text",
                        "*.pdf *.jpg *.jpeg *.png *.tif *.tiff *.psd *.bmp *.gif "
                        "*.webp *.ppm *.svg *.txt"),
                       ("Alle Dateien", "*.*")])
        for p in neu:
            fp = Path(p)
            if fp.is_file() and fp not in self.paths:
                self.paths.append(fp)
        self._fill_list()
        self._suggest_target()

    # ---------------- Ziel ----------------

    def _suggest_target(self):
        if self.var_target.get() or not self.paths:
            return
        erste = self.paths[0]
        self.var_target.set(str(erste.with_name(f"{erste.stem}_zusammengefasst.pdf")))

    def _choose_target(self):
        vorschlag = Path(self.var_target.get()) if self.var_target.get() else None
        ziel = filedialog.asksaveasfilename(
            title="Ergebnis speichern unter",
            defaultextension=".pdf",
            filetypes=[("PDF-Dateien", "*.pdf")],
            initialdir=str(vorschlag.parent) if vorschlag else None,
            initialfile=vorschlag.name if vorschlag else "zusammengefasst.pdf")
        if ziel:
            self.var_target.set(ziel)

    # ---------------- Zusammenfassen ----------------

    def do_merge(self):
        if len(self.paths) < 2:
            messagebox.showwarning(
                APP_TITLE,
                "Zum Zusammenfassen werden mindestens zwei Dateien gebraucht.\n"
                "Weitere lassen sich über „Hinzufügen…“ wählen.",
                parent=self)
            return
        if not self.var_target.get().strip():
            self._suggest_target()
        ziel = Path(self.var_target.get().strip())
        if ziel.suffix.lower() != ".pdf":
            ziel = ziel.with_suffix(".pdf")

        self.btn_merge.config(state="disabled", text="Bitte warten…")
        self.update_idletasks()
        try:
            ergebnis, uebersprungen = merge_documents(self.paths, ziel,
                                                     log=self.app.log)
        except Exception as e:
            self.btn_merge.config(state="normal", text="▶  Zusammenfassen")
            messagebox.showerror(APP_TITLE, f"Zusammenfassen fehlgeschlagen:\n{e}",
                                 parent=self)
            self.app.log(f"FEHLER beim Zusammenfassen: {e}")
            return

        if uebersprungen:
            liste = chr(10).join(f"- {name} ({grund})" for name, grund in uebersprungen)
            messagebox.showwarning(
                APP_TITLE,
                f"{len(uebersprungen)} Datei(en) konnten nicht gelesen werden "
                f"und fehlen im Ergebnis:{chr(10)}{chr(10)}{liste}",
                parent=self)
        self.app.log(f"Zusammengefasst ({len(self.paths) - len(uebersprungen)} "
                     f"von {len(self.paths)} Dateien): {ergebnis}")
        self.app.note_output(ergebnis)
        zeigen = self.var_show.get()
        self.destroy()
        if zeigen:
            PdfViewer(self.app, ergebnis)


class PdfToolApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(980, 640)

        self.files: list[Path] = []
        self.preview_page = 0             # 0-basierter Index der Vorschau-Seite
        self._preview_file: Path | None = None
        self._view_only = False           # True: Hauptfenster verborgen, nur Viewer aktiv
        self._page_count = 0
        self.crop_frac: tuple | None = None                  # Bereich für alle Seiten
        self.crop_pages: dict[Path, dict[int, tuple]] = {}   # Bereiche je Datei/Seite
        self.page_rot: dict[Path, dict[int, int]] = {}       # Drehung je Datei/Seite
        self.page_curves: dict[Path, dict[int, tuple]] = {}  # eigene Gradation je Seite
        self._global_curves = (0, 255, 1.0)                  # Werte für alle anderen Seiten
        self.page_del: dict[Path, set[int]] = {}             # zu löschende Seiten
        self.page_order: dict[Path, list[int]] = {}          # neue Seitenreihenfolge (Originalindizes)
        self._crop_start = None
        self._crop_rect_id = None
        self._preview_img = None          # ImageTk-Referenz
        self._preview_box = None          # (x, y, w, h) des Bildes im Canvas
        self._busy = False
        self._last_outputs: list[Path] = []   # Ergebnisse des letzten Laufs
        self._merge_pending: list[str] = []    # gesammelte Pfade aus dem Kontextmenü
        self._merge_timer = None

        self._build_ui()

    # ---------------- UI-Aufbau ----------------

    def _build_ui(self):
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)
        root.columnconfigure(2, weight=1)
        root.rowconfigure(0, weight=1)

        # --- Dateiliste ---
        files_frame = ttk.LabelFrame(root, text=" 1. PDF-Dateien ", padding=6)
        files_frame.grid(row=0, column=0, sticky="nsw", padx=(0, 8))
        files_frame.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(files_frame, width=38, selectmode="extended")
        self.listbox.grid(row=0, column=0, columnspan=3, sticky="nsew")
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.update_preview())
        self.listbox.bind("<Double-Button-1>", lambda e: self.view_selected())
        sb = ttk.Scrollbar(files_frame, command=self.listbox.yview)
        sb.grid(row=0, column=3, sticky="ns")
        self.listbox.config(yscrollcommand=sb.set)

        ttk.Button(files_frame, text="Hinzufügen…", command=self.add_files)\
            .grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(files_frame, text="Entfernen", command=self.remove_selected)\
            .grid(row=1, column=1, sticky="ew", pady=(6, 0), padx=4)
        ttk.Button(files_frame, text="Leeren", command=self.clear_files)\
            .grid(row=1, column=2, sticky="ew", pady=(6, 0))
        ttk.Button(files_frame, text="PDF anzeigen (Doppelklick)",
                   command=self.view_selected)\
            .grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        btn_merge = ttk.Button(files_frame, text="In eine PDF zusammenfassen…",
                               command=self.merge_files)
        btn_merge.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0))

        # --- Optionen ---
        opts = ttk.LabelFrame(root, text=" 2. Optionen ", padding=6)
        opts.grid(row=0, column=1, sticky="nsw", padx=(0, 8))

        self.var_ocr = tk.BooleanVar(value=False)
        self.var_curves = tk.BooleanVar(value=False)
        self.var_deskew = tk.BooleanVar(value=False)
        self.var_crop = tk.BooleanVar(value=False)
        self.var_rename = tk.BooleanVar(value=False)
        self.var_date = tk.BooleanVar(value=False)

        row = 0
        ttk.Checkbutton(opts, text="OCR-Texterkennung", variable=self.var_ocr)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1
        lang_row = ttk.Frame(opts); lang_row.grid(row=row, column=0, columnspan=2, sticky="w"); row += 1
        ttk.Label(lang_row, text="    Sprache:").pack(side="left")
        self.var_lang = tk.StringVar(value="deu+eng")
        ttk.Entry(lang_row, textvariable=self.var_lang, width=10).pack(side="left")

        ttk.Separator(opts).grid(row=row, column=0, columnspan=2, sticky="ew", pady=4); row += 1

        ttk.Checkbutton(opts, text="Gradationskurven", variable=self.var_curves,
                        command=self.update_preview)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1

        self.var_black = tk.IntVar(value=0)
        self.var_white = tk.IntVar(value=255)
        self.var_gamma = tk.DoubleVar(value=1.0)
        for label, var, lo, hi, res in (
                ("Schwarzpunkt", self.var_black, 0, 120, 1),
                ("Weißpunkt", self.var_white, 135, 255, 1),
                ("Gamma", self.var_gamma, 0.30, 3.00, 0.05)):
            ttk.Label(opts, text=f"    {label}").grid(row=row, column=0, sticky="w")
            s = tk.Scale(opts, variable=var, from_=lo, to=hi, resolution=res,
                         orient="horizontal", length=150, showvalue=True)
            s.grid(row=row, column=1, sticky="ew")
            s.bind("<ButtonRelease-1>", lambda e: self._on_curve_slider())
            row += 1
        self.btn_pagecurve = ttk.Button(opts, text="Diese Seite abweichend einstellen",
                                        command=self.toggle_page_curves)
        self.btn_pagecurve.grid(row=row, column=0, columnspan=2, sticky="ew",
                                padx=(20, 0), pady=(2, 0)); row += 1
        self.pagecurve_label = ttk.Label(opts, text="", foreground="#b05000")
        self.pagecurve_label.grid(row=row, column=0, columnspan=2, sticky="w"); row += 1

        ttk.Separator(opts).grid(row=row, column=0, columnspan=2, sticky="ew", pady=4); row += 1

        ttk.Checkbutton(opts, text="Gerade ausrichten (automatisch)",
                        variable=self.var_deskew, command=self.update_preview)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1

        ttk.Checkbutton(opts, text="Bereich freistellen", variable=self.var_crop,
                        command=self._on_crop_toggle)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1
        self.var_crop_scope = tk.StringVar(value="all")
        ttk.Radiobutton(opts, text="    alle Seiten gleich", value="all",
                        variable=self.var_crop_scope, command=self._on_crop_scope)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1
        ttk.Radiobutton(opts, text="    je Seite individuell", value="page",
                        variable=self.var_crop_scope, command=self._on_crop_scope)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1
        self.crop_label = ttk.Label(opts, text="    (Rechteck in der Vorschau aufziehen)",
                                    foreground="gray")
        self.crop_label.grid(row=row, column=0, columnspan=2, sticky="w"); row += 1

        ttk.Separator(opts).grid(row=row, column=0, columnspan=2, sticky="ew", pady=4); row += 1

        ttk.Checkbutton(opts, text="Umbenennen in:", variable=self.var_rename)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1
        self.var_newname = tk.StringVar()
        ttk.Entry(opts, textvariable=self.var_newname, width=28)\
            .grid(row=row, column=0, columnspan=2, sticky="ew", padx=(20, 0)); row += 1
        ttk.Label(opts, text="    (mehrere Dateien werden nummeriert)",
                  foreground="gray").grid(row=row, column=0, columnspan=2, sticky="w"); row += 1

        ttk.Checkbutton(opts, text="Datum anfügen (_YYYY-MM-DD)", variable=self.var_date)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1
        self.var_datesrc = tk.StringVar(value="file")
        ttk.Radiobutton(opts, text="    Datei-Änderungsdatum", value="file",
                        variable=self.var_datesrc)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1
        ttk.Radiobutton(opts, text="    heutiges Datum", value="today",
                        variable=self.var_datesrc)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1

        ttk.Separator(opts).grid(row=row, column=0, columnspan=2, sticky="ew", pady=4); row += 1

        ttk.Label(opts, text="Ausgabe:").grid(row=row, column=0, columnspan=2, sticky="w"); row += 1
        self.var_output = tk.StringVar(value="subfolder")
        ttk.Radiobutton(opts, text="    Unterordner „bearbeitet“", value="subfolder",
                        variable=self.var_output)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1
        ttk.Radiobutton(opts, text="    gleicher Ordner", value="same",
                        variable=self.var_output)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1
        ttk.Radiobutton(opts, text="    Original ersetzen", value="replace",
                        variable=self.var_output)\
            .grid(row=row, column=0, columnspan=2, sticky="w"); row += 1

        dpi_row = ttk.Frame(opts); dpi_row.grid(row=row, column=0, columnspan=2,
                                                sticky="w", pady=(4, 0)); row += 1
        ttk.Label(dpi_row, text="Auflösung (dpi):").pack(side="left")
        self.var_dpi = tk.IntVar(value=200)
        ttk.Combobox(dpi_row, textvariable=self.var_dpi, values=(150, 200, 300),
                     width=5, state="readonly").pack(side="left", padx=4)

        self.btn_run = ttk.Button(opts, text="▶  Verarbeiten", command=self.run)
        self.btn_run.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 0)); row += 1

        self.btn_open = ttk.Button(opts, text="Bearbeitetes PDF öffnen",
                                   command=self.open_result, state="disabled")
        self.btn_open.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(4, 0)); row += 1

        self.btn_open_folder = ttk.Button(opts, text="Ordner im Explorer öffnen",
                                          command=self.open_result_folder, state="disabled")
        self.btn_open_folder.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(4, 0)); row += 1

        # --- Vorschau + Protokoll ---
        right = ttk.Frame(root)
        right.grid(row=0, column=2, sticky="nsew")
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        prev_frame = ttk.LabelFrame(right, text=" Vorschau (mit Einstellungen) ",
                                    padding=4)
        prev_frame.grid(row=0, column=0, sticky="nsew")
        prev_frame.rowconfigure(0, weight=1)
        prev_frame.columnconfigure(1, weight=1)
        self._build_pside(prev_frame)
        self.canvas = tk.Canvas(prev_frame, background="#606060", highlightthickness=0)
        self.canvas.grid(row=0, column=1, sticky="nsew")
        self.canvas.bind("<Configure>", lambda e: self.update_preview())
        self.canvas.bind("<ButtonPress-1>", self._crop_press)
        self.canvas.bind("<B1-Motion>", self._crop_drag)
        self.canvas.bind("<ButtonRelease-1>", self._crop_release)
        self.canvas.bind("<MouseWheel>",
                         lambda e: self.change_page(-1 if e.delta > 0 else 1))

        nav = ttk.Frame(prev_frame)
        nav.grid(row=1, column=1, pady=(4, 0))
        self.btn_prev_page = ttk.Button(nav, text="◀", width=3, state="disabled",
                                        command=lambda: self.change_page(-1))
        self.btn_prev_page.pack(side="left")
        self.page_label = ttk.Label(nav, text="Seite –/–", width=14, anchor="center")
        self.page_label.pack(side="left", padx=6)
        self.btn_next_page = ttk.Button(nav, text="▶", width=3, state="disabled",
                                        command=lambda: self.change_page(1))
        self.btn_next_page.pack(side="left")

        ttk.Separator(nav, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(nav, text="⟲ −90°", width=7,
                   command=lambda: self.rotate_page(-90)).pack(side="left")
        ttk.Button(nav, text="180°", width=5,
                   command=lambda: self.rotate_page(180)).pack(side="left", padx=2)
        ttk.Button(nav, text="⟳ +90°", width=7,
                   command=lambda: self.rotate_page(90)).pack(side="left")
        ttk.Separator(nav, orient="vertical").pack(side="left", fill="y", padx=8)
        self.btn_delpage = ttk.Button(nav, text="Seite löschen", width=20,
                                      command=self.toggle_delete_page)
        self.btn_delpage.pack(side="left")
        ttk.Button(nav, text="💾 Seite speichern…",
                   command=self.save_page_from_editor).pack(side="left", padx=(8, 0))
        self.page_status = ttk.Label(nav, text="", foreground="#b00000")
        self.page_status.pack(side="left", padx=8)

        self.bind("<Prior>", lambda e: self.change_page(-1))   # Bild-auf
        self.bind("<Next>", lambda e: self.change_page(1))     # Bild-ab

        log_frame = ttk.LabelFrame(right, text=" Protokoll ", padding=4)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=8, state="disabled", wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        lsb = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        lsb.grid(row=0, column=1, sticky="ns")
        self.log_text.config(yscrollcommand=lsb.set)

        self.progress = ttk.Progressbar(right, mode="determinate")
        self.progress.grid(row=2, column=0, sticky="ew", pady=(6, 0))

    # ---------------- Dateiliste ----------------

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="PDF-Dateien auswählen",
            filetypes=[("PDF-Dateien", "*.pdf"), ("Alle Dateien", "*.*")])
        self.add_paths(paths)

    def add_paths(self, paths):
        """Fügt Dateien der Liste hinzu (Dialog oder Kommandozeile)."""
        for p in paths:
            p = Path(p)
            if p.is_file() and p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", p.name)
        if paths:
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set("end")   # neueste Datei anzeigen
            self.listbox.see("end")
            self.update_preview()

    def remove_selected(self):
        for idx in sorted(self.listbox.curselection(), reverse=True):
            self.listbox.delete(idx)
            self.crop_pages.pop(self.files[idx], None)
            self.page_rot.pop(self.files[idx], None)
            self.page_curves.pop(self.files[idx], None)
            self.page_del.pop(self.files[idx], None)
            self.page_order.pop(self.files[idx], None)
            del self.files[idx]
        self.update_preview()

    def clear_files(self):
        self.listbox.delete(0, "end")
        self.files.clear()
        self.crop_pages.clear()
        self.page_rot.clear()
        self.page_curves.clear()
        self.page_del.clear()
        self.page_order.clear()
        self.update_preview()

    def _current_file(self) -> Path | None:
        sel = self.listbox.curselection()
        if sel:
            return self.files[sel[0]]
        return self.files[0] if self.files else None

    # ---------------- Vorschau ----------------

    def _order(self, f) -> list:
        """Aktuelle Seitenreihenfolge der Datei f als Liste von Originalindizes."""
        n = self._page_count
        o = self.page_order.get(f)
        if not o or sorted(o) != list(range(n)):
            return list(range(n))
        return o

    def _reordered(self, f) -> bool:
        o = self.page_order.get(f)
        return bool(o) and o != sorted(o)

    # ---------------- Seitenleiste (Bearbeiter) ----------------

    PSIDE_W = 132
    PSLOT_H = 150
    PTHUMB = (96, 124)

    def _build_pside(self, parent):
        fr = ttk.Frame(parent, width=self.PSIDE_W)
        fr.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 4))
        fr.grid_propagate(False)
        fr.rowconfigure(0, weight=1)
        fr.columnconfigure(0, weight=1)
        self.pside = tk.Canvas(fr, background="#2e2e2e", highlightthickness=0)
        self.pside.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(fr, orient="vertical", command=self.pside.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.pside.config(yscrollcommand=sb.set)
        self._pt_file = None
        self._pt_n = 0
        self._pt_base: dict[int, Image.Image] = {}
        self._pt_photo: dict[tuple, ImageTk.PhotoImage] = {}
        self._pt_gen = 0
        self._pt_drag_src = None
        self._pt_drag_target = None
        self._pt_drag_active = False
        self._pt_drag_y0 = 0
        self._pt_ctx = 0
        self.pside.bind("<ButtonPress-1>", self._pside_press)
        self.pside.bind("<B1-Motion>", self._pside_drag)
        self.pside.bind("<ButtonRelease-1>", self._pside_release)
        self.pside.bind("<Button-3>", self._pside_menu)
        self.pside.bind("<MouseWheel>",
                        lambda e: self.pside.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        m = tk.Menu(self, tearoff=False)
        m.add_command(label="Eine Position nach oben", command=lambda: self._pside_move_by(-1))
        m.add_command(label="Eine Position nach unten", command=lambda: self._pside_move_by(1))
        m.add_command(label="An den Anfang", command=lambda: self._pside_move(self._pt_ctx, 0))
        m.add_command(label="Ans Ende",
                      command=lambda: self._pside_move(self._pt_ctx, 10 ** 9))
        m.add_separator()
        m.add_command(label="Drehen −90°", command=lambda: self.rotate_page(-90))
        m.add_command(label="Drehen +90°", command=lambda: self.rotate_page(90))
        m.add_command(label="Löschen / Wiederherstellen", command=self.toggle_delete_page)
        m.add_separator()
        m.add_command(label="Reihenfolge zurücksetzen", command=self._pside_reset_order)
        self._pside_menu_w = m

    def _pside_refresh(self):
        """Gleicht die Seitenleiste mit Datei, Reihenfolge und Markierungen ab."""
        f = self._current_file() if self._page_count else None
        if f != self._pt_file or self._page_count != self._pt_n:
            self._pt_file = f
            self._pt_n = self._page_count
            self._pt_base = {}
            self._pt_photo = {}
            self._pt_gen += 1
            self._pside_layout()
            if f:
                self._pside_chain(self._pt_gen)
            return
        self._pside_layout()

    def _pside_chain(self, gen):
        """Rendert je Durchgang einige Vorschaubilder, damit das Fenster bedienbar bleibt."""
        f = self._pt_file
        if gen != self._pt_gen or not f:
            return
        todo = [i for i in range(self._pt_n) if i not in self._pt_base]
        todo.sort(key=lambda i: abs(i - self.preview_page))
        try:
            doc = fitz.open(f)
        except Exception:
            return
        try:
            for i in todo[:3]:
                try:
                    r = doc[i].rect
                    sc = min(self.PTHUMB[0] / max(1.0, r.width),
                             self.PTHUMB[1] / max(1.0, r.height))
                    pix = doc[i].get_pixmap(matrix=fitz.Matrix(sc, sc),
                                            colorspace=fitz.csRGB, alpha=False)
                    self._pt_base[i] = Image.frombytes("RGB", (pix.width, pix.height),
                                                       pix.samples)
                except Exception:
                    self._pt_base[i] = Image.new("RGB", (40, 56), "#808080")
        finally:
            doc.close()
        self._pside_layout()
        if len(todo) > 3:
            self.after(5, lambda: self._pside_chain(gen))

    def _pside_layout(self):
        c = self.pside
        c.delete("all")
        f = self._pt_file
        if not f:
            c.config(scrollregion=(0, 0, 1, 1))
            return
        order = self._order(f)
        rots = self.page_rot.get(f, {})
        dels = self.page_del.get(f, set())
        cx = (self.PSIDE_W - 16) // 2
        w = self.PSIDE_W - 16
        for pos, orig in enumerate(order):
            y0 = pos * self.PSLOT_H
            base = self._pt_base.get(orig)
            rot = rots.get(orig, 0)
            if base is not None:
                key = (orig, rot)
                ph = self._pt_photo.get(key)
                if ph is None:
                    ph = ImageTk.PhotoImage(base.rotate(-rot, expand=True) if rot else base)
                    self._pt_photo[key] = ph
                c.create_image(cx, y0 + 6 + self.PTHUMB[1] // 2, image=ph)
            else:
                c.create_rectangle(cx - 40, y0 + 6, cx + 40, y0 + 6 + 110,
                                   fill="#505050", outline="")
            if orig in dels:
                top, bot = y0 + 6, y0 + 6 + self.PTHUMB[1]
                c.create_line(cx - 44, top, cx + 44, bot, fill="red", width=3)
                c.create_line(cx + 44, top, cx - 44, bot, fill="red", width=3)
            label = str(pos + 1)
            if orig != pos:
                label += f" (war {orig + 1})"
            if rot:
                label += f"  ⟳{rot}°"
            c.create_text(cx, y0 + 6 + self.PTHUMB[1] + 12, text=label,
                          fill="#ff7070" if orig in dels else "#d0d0d0",
                          font=("Segoe UI", 8))
        c.config(scrollregion=(0, 0, w, len(order) * self.PSLOT_H))
        if self.preview_page in order:
            pos = order.index(self.preview_page)
            y0 = pos * self.PSLOT_H
            c.create_rectangle(4, y0 + 2, w - 4, y0 + self.PSLOT_H - 4,
                               outline="#4da3ff", width=3)
            total = max(1, len(order) * self.PSLOT_H)
            top, bottom = c.yview()
            if y0 / total < top or (y0 + self.PSLOT_H) / total > bottom:
                c.yview_moveto(max(0.0, (y0 - self.PSLOT_H) / total))

    def _pside_pos_at(self, y):
        cy = self.pside.canvasy(y)
        return max(0, min(int(cy // self.PSLOT_H), self._pt_n - 1))

    def _pside_press(self, ev):
        f = self._pt_file
        if not f:
            return
        self._pt_drag_src = self._pside_pos_at(ev.y)
        self._pt_drag_y0 = ev.y
        self._pt_drag_active = False
        self._pt_drag_target = None
        self.preview_page = self._order(f)[self._pt_drag_src]
        self.update_preview()

    def _pside_drag(self, ev):
        if self._pt_drag_src is None or not self._pt_file:
            return
        if not self._pt_drag_active and abs(ev.y - self._pt_drag_y0) < 6:
            return
        self._pt_drag_active = True
        h = self.pside.winfo_height()
        if ev.y < 20:
            self.pside.yview_scroll(-1, "units")
        elif ev.y > h - 20:
            self.pside.yview_scroll(1, "units")
        cy = self.pside.canvasy(ev.y)
        t = max(0, min(int(round(cy / self.PSLOT_H)), self._pt_n))
        self._pt_drag_target = t
        self.pside.delete("dropline")
        y = t * self.PSLOT_H
        self.pside.create_line(4, y, self.PSIDE_W - 20, y, fill="#ff9d2e", width=4,
                               tags=("dropline",))

    def _pside_release(self, ev):
        src, t, aktiv = self._pt_drag_src, self._pt_drag_target, self._pt_drag_active
        self._pt_drag_src = self._pt_drag_target = None
        self._pt_drag_active = False
        self.pside.delete("dropline")
        if src is None or not aktiv or t is None:
            return
        self._pside_move(src, t if t <= src else t - 1)

    def _pside_move(self, src: int, dst: int):
        """Verschiebt die Seite an Position src nach Position dst (nur vorgemerkt)."""
        f = self._pt_file
        if not f:
            return
        order = list(self._order(f))
        dst = max(0, min(dst, len(order) - 1))
        if src == dst:
            return
        orig = order.pop(src)
        order.insert(dst, orig)
        if order == list(range(len(order))):
            self.page_order.pop(f, None)
        else:
            self.page_order[f] = order
        self.preview_page = orig
        self.update_preview()

    def _pside_move_by(self, delta: int):
        self._pside_move(self._pt_ctx, self._pt_ctx + delta)

    def _pside_reset_order(self):
        f = self._pt_file
        if f:
            self.page_order.pop(f, None)
            self.update_preview()

    def _pside_menu(self, ev):
        f = self._pt_file
        if not f:
            return
        self._pt_ctx = self._pside_pos_at(ev.y)
        self.preview_page = self._order(f)[self._pt_ctx]
        self.update_preview()
        m = self._pside_menu_w
        m.entryconfig(0, state="normal" if self._pt_ctx > 0 else "disabled")
        m.entryconfig(1, state="normal" if self._pt_ctx < self._pt_n - 1 else "disabled")
        m.entryconfig(2, state="normal" if self._pt_ctx > 0 else "disabled")
        m.entryconfig(3, state="normal" if self._pt_ctx < self._pt_n - 1 else "disabled")
        m.entryconfig(9, state="normal" if self._reordered(f) else "disabled")
        m.tk_popup(ev.x_root, ev.y_root)

    def change_page(self, delta: int):
        """Blättert in der Vorschau um delta Seiten vor oder zurück."""
        f = self._current_file()
        if not f or not self._page_count:
            return
        order = self._order(f)
        pos = order.index(self.preview_page) + delta
        if 0 <= pos < len(order):
            self.preview_page = order[pos]
            self.update_preview()

    def _update_page_nav(self):
        f = self._current_file()
        pos = (self._order(f).index(self.preview_page)
               if f and self._page_count and self.preview_page < self._page_count else 0)
        if self._page_count:
            self.page_label.config(text=f"Seite {pos + 1}/{self._page_count}")
        else:
            self.page_label.config(text="Seite –/–")
        self.btn_prev_page.config(state="normal" if pos > 0 else "disabled")
        self.btn_next_page.config(
            state="normal" if pos < self._page_count - 1 else "disabled")

        rot = self.page_rot.get(f, {}).get(self.preview_page, 0) if f else 0
        deleted = f is not None and self.preview_page in self.page_del.get(f, set())
        parts = []
        if rot:
            parts.append(f"gedreht {({90: '+90', 180: '180', 270: '−90'}[rot])}°")
        if deleted:
            parts.append("wird gelöscht")
        if f and self._page_count and self._reordered(f):
            parts.append(f"ursprünglich Seite {self.preview_page + 1}")
        self.page_status.config(text=" · ".join(parts))
        self.btn_delpage.config(
            text="Seite wiederherstellen" if deleted else "Seite löschen")

    def rotate_page(self, delta: int):
        """Merkt für die angezeigte Seite eine Drehung um delta Grad vor."""
        f = self._current_file()
        if not f:
            return
        d = self.page_rot.setdefault(f, {})
        rot = (d.get(self.preview_page, 0) + delta) % 360
        if rot:
            d[self.preview_page] = rot
        else:
            d.pop(self.preview_page, None)
        self.update_preview()

    def save_page_from_editor(self):
        """Öffnet das Fenster „Seite speichern“ für die angezeigte Seite der
        gewählten Datei (arbeitet auf der Datei auf der Platte)."""
        f = self._current_file()
        if not f or not self._page_count:
            messagebox.showwarning(APP_TITLE, "Bitte zuerst eine PDF-Datei hinzufügen.")
            return
        SavePagesDialog(self, f, self._page_count, self.preview_page)

    # ---------------- Gradation je Seite ----------------

    def _slider_values(self) -> tuple:
        return (self.var_black.get(), self.var_white.get(), self.var_gamma.get())

    def _page_curve(self):
        """Eigene Gradation der angezeigten Seite oder None."""
        f = self._current_file()
        return self.page_curves.get(f, {}).get(self.preview_page) if f else None

    def _on_curve_slider(self):
        """Regler losgelassen: Wert gehört zur Seite (wenn abweichend) oder zu allen."""
        f = self._current_file()
        if f and self._page_curve() is not None:
            self.page_curves[f][self.preview_page] = self._slider_values()
        else:
            self._global_curves = self._slider_values()
        self.update_preview()

    def _load_curve_sliders(self):
        """Stellt die Regler auf die Werte der angezeigten Seite."""
        vals = self._page_curve() or self._global_curves
        b, w, g = vals
        if self._slider_values() != (b, w, g):
            self.var_black.set(b)
            self.var_white.set(w)
            self.var_gamma.set(g)
        if self._page_curve() is not None:
            self.btn_pagecurve.config(text="Diese Seite wie die anderen")
            self.pagecurve_label.config(text="    Diese Seite hat eigene Werte")
        else:
            self.btn_pagecurve.config(text="Diese Seite abweichend einstellen")
            self.pagecurve_label.config(text="")

    def toggle_page_curves(self):
        """Schaltet für die angezeigte Seite eigene Gradationswerte ein bzw. aus."""
        f = self._current_file()
        if not f or not self._page_count:
            return
        if self._page_curve() is None:
            self.page_curves.setdefault(f, {})[self.preview_page] = self._slider_values()
            self.var_curves.set(True)
        else:
            self.page_curves[f].pop(self.preview_page, None)
        self.update_preview()

    def toggle_delete_page(self):
        """Markiert die angezeigte Seite zum Löschen bzw. hebt das wieder auf."""
        f = self._current_file()
        if not f:
            return
        s = self.page_del.setdefault(f, set())
        if self.preview_page in s:
            s.discard(self.preview_page)
        else:
            s.add(self.preview_page)
        self.update_preview()

    def _draw_delete_mark(self):
        """Rotes Kreuz über der Vorschau, wenn die Seite gelöscht werden soll."""
        f = self._current_file()
        if not (f and self._preview_box
                and self.preview_page in self.page_del.get(f, set())):
            return
        x, y, w, h = self._preview_box
        self.canvas.create_rectangle(x, y, x + w, y + h, fill="#802020",
                                     stipple="gray25", outline="")
        self.canvas.create_line(x, y, x + w, y + h, fill="red", width=3)
        self.canvas.create_line(x + w, y, x, y + h, fill="red", width=3)
        self.canvas.create_text(x + w / 2, y + 14, text="Seite wird gelöscht",
                                fill="red", font=("Segoe UI", 11, "bold"))

    def update_preview(self):
        self.canvas.delete("all")
        self._crop_rect_id = None
        f = self._current_file()
        if f != self._preview_file:
            self._preview_file = f
            self.preview_page = 0
        if not f:
            self._page_count = 0
            self._update_page_nav()
            self._pside_refresh()
            return
        try:
            doc = fitz.open(f)
            self._page_count = len(doc)
            self.preview_page = min(self.preview_page, self._page_count - 1)
            page = doc[self.preview_page]
            pix = page.get_pixmap(dpi=100, colorspace=fitz.csRGB, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            doc.close()
        except Exception as exc:
            self._page_count = 0
            self._update_page_nav()
            self._pside_refresh()
            self.log(f"Vorschau-Fehler bei {f.name}: {exc}")
            return
        self._update_page_nav()

        rot = self.page_rot.get(f, {}).get(self.preview_page, 0)
        if rot:
            # PDF-Drehung ist im Uhrzeigersinn, PIL dreht gegen den Uhrzeigersinn
            img = img.rotate(-rot, expand=True)

        if self.var_deskew.get():
            angle = estimate_skew_angle(img)
            if abs(angle) >= 0.1:
                img = img.rotate(angle, resample=Image.Resampling.BICUBIC,
                                 expand=False, fillcolor=(255, 255, 255))
        self._load_curve_sliders()
        if self.var_curves.get():
            img = apply_curves(img, self.var_black.get(),
                               self.var_white.get(), self.var_gamma.get())

        cw = max(50, self.canvas.winfo_width())
        ch = max(50, self.canvas.winfo_height())
        scale = min((cw - 12) / img.width, (ch - 12) / img.height, 1.5)
        disp = img.resize((max(1, int(img.width * scale)),
                           max(1, int(img.height * scale))), Image.Resampling.BILINEAR)
        self._preview_img = ImageTk.PhotoImage(disp)
        x = (cw - disp.width) // 2
        y = (ch - disp.height) // 2
        self._preview_box = (x, y, disp.width, disp.height)
        self.canvas.create_image(x, y, anchor="nw", image=self._preview_img)
        self._draw_crop_rect()
        self._draw_delete_mark()
        self._update_crop_label()
        self._pside_refresh()

    def _draw_crop_rect(self):
        if self._crop_rect_id:
            self.canvas.delete(self._crop_rect_id)
            self._crop_rect_id = None
        c = self._current_page_crop()
        if not (self.var_crop.get() and c and self._preview_box):
            return
        x, y, w, h = self._preview_box
        fx0, fy0, fx1, fy1 = c
        self._crop_rect_id = self.canvas.create_rectangle(
            x + fx0 * w, y + fy0 * h, x + fx1 * w, y + fy1 * h,
            outline="red", width=2, dash=(5, 3))

    # ---------------- Freistellen (Rechteck ziehen) ----------------

    def _current_page_crop(self):
        """Der Freistell-Bereich, der für die angezeigte Seite gilt (oder None)."""
        if self.var_crop_scope.get() == "page":
            f = self._current_file()
            return self.crop_pages.get(f, {}).get(self.preview_page) if f else None
        return self.crop_frac

    def _set_current_crop(self, rect):
        """Setzt oder löscht (rect=None) den Bereich für die angezeigte Seite."""
        if self.var_crop_scope.get() == "page":
            f = self._current_file()
            if not f:
                return
            if rect is None:
                self.crop_pages.get(f, {}).pop(self.preview_page, None)
            else:
                self.crop_pages.setdefault(f, {})[self.preview_page] = rect
        else:
            self.crop_frac = rect

    def _update_crop_label(self):
        if not self.var_crop.get():
            self.crop_label.config(text="    (Rechteck in der Vorschau aufziehen)")
            return
        c = self._current_page_crop()
        if self.var_crop_scope.get() == "page":
            f = self._current_file()
            n = len(self.crop_pages.get(f, {})) if f else 0
            if c:
                self.crop_label.config(
                    text=f"    Seite {self.preview_page + 1}: {c[0]*100:.0f}%/{c[1]*100:.0f}% – "
                         f"{c[2]*100:.0f}%/{c[3]*100:.0f}%  ({n} Seite(n) mit Bereich)")
            else:
                self.crop_label.config(
                    text=f"    Seite {self.preview_page + 1}: kein Bereich  "
                         f"({n} Seite(n) mit Bereich)")
        elif c:
            self.crop_label.config(
                text=f"    Bereich: {c[0]*100:.0f}%/{c[1]*100:.0f}% – "
                     f"{c[2]*100:.0f}%/{c[3]*100:.0f}%")
        else:
            self.crop_label.config(text="    (Rechteck in der Vorschau aufziehen)")

    def _on_crop_toggle(self):
        if not self.var_crop.get():
            self.crop_frac = None
            self.crop_pages.clear()
        self._update_crop_label()
        self.update_preview()

    def _on_crop_scope(self):
        self._update_crop_label()
        self.update_preview()

    def _canvas_to_frac(self, cx, cy):
        x, y, w, h = self._preview_box
        return (min(max((cx - x) / w, 0.0), 1.0), min(max((cy - y) / h, 0.0), 1.0))

    def _crop_press(self, ev):
        if not (self.var_crop.get() and self._preview_box):
            return
        self._crop_start = (ev.x, ev.y)

    def _crop_drag(self, ev):
        if not (self._crop_start and self._preview_box):
            return
        fx0, fy0 = self._canvas_to_frac(*self._crop_start)
        fx1, fy1 = self._canvas_to_frac(ev.x, ev.y)
        self._set_current_crop((min(fx0, fx1), min(fy0, fy1),
                                max(fx0, fx1), max(fy0, fy1)))
        self._draw_crop_rect()

    def _crop_release(self, ev):
        if not self._crop_start:
            return
        self._crop_drag(ev)
        self._crop_start = None
        c = self._current_page_crop()
        # Zu kleines Rechteck = Bereich entfernen (z. B. einfacher Klick)
        if c and ((c[2] - c[0]) < 0.02 or (c[3] - c[1]) < 0.02):
            self._set_current_crop(None)
            self._draw_crop_rect()
        self._update_crop_label()

    # ---------------- Protokoll ----------------

    def log(self, msg: str):
        def _append():
            self.log_text.config(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.after(0, _append)

    # ---------------- Verarbeitung ----------------

    def run(self):
        if self._busy:
            return
        if not self.files:
            messagebox.showwarning(APP_TITLE, "Bitte zuerst PDF-Dateien hinzufügen.")
            return

        crop_active = self.var_crop.get()
        crop_scope = self.var_crop_scope.get()
        if crop_scope == "page":
            has_crop = any(self.crop_pages.get(f) for f in self.files)
        else:
            has_crop = bool(self.crop_frac)

        has_rotdel = any(self.page_rot.get(f) or self.page_del.get(f)
                         or self._reordered(f) for f in self.files)
        content_ops = (self.var_ocr.get() or self.var_curves.get()
                       or self.var_deskew.get()
                       or (crop_active and has_crop) or has_rotdel)
        name_ops = (self.var_rename.get() and self.var_newname.get().strip()) \
            or self.var_date.get()
        if not content_ops and not name_ops:
            messagebox.showwarning(APP_TITLE, "Bitte mindestens eine Option wählen.")
            return
        if crop_active and not has_crop:
            messagebox.showwarning(
                APP_TITLE, "„Bereich freistellen“ ist aktiviert, aber es wurde kein "
                           "Rechteck in der Vorschau aufgezogen.")
            return
        if self.var_output.get() == "replace" and content_ops:
            if not messagebox.askyesno(
                    APP_TITLE, "Die Originaldateien werden überschrieben bzw. ersetzt.\n"
                               "Fortfahren?"):
                return

        opts = {
            "ocr": self.var_ocr.get(),
            "lang": self.var_lang.get().strip() or "deu",
            "curves": self.var_curves.get(),
            "black": self.var_black.get(),
            "white": self.var_white.get(),
            "gamma": self.var_gamma.get(),
            "deskew": self.var_deskew.get(),
            "crop_default": self.crop_frac if (crop_active and crop_scope == "all") else None,
            "crop_pages": ({f: dict(d) for f, d in self.crop_pages.items() if d}
                           if crop_active and crop_scope == "page" else {}),
            "rot_pages": {f: dict(d) for f, d in self.page_rot.items() if d},
            "curve_pages": {f: dict(d) for f, d in self.page_curves.items() if d},
            "del_pages": {f: set(s) for f, s in self.page_del.items() if s},
            "page_order": {f: list(o) for f, o in self.page_order.items()
                           if self._reordered(f)},
            "dpi": self.var_dpi.get(),
            "rename": self.var_newname.get().strip() if self.var_rename.get() else None,
            "date": self.var_date.get(),
            "datesrc": self.var_datesrc.get(),
            "output": self.var_output.get(),
            "content_ops": bool(content_ops),
        }

        self._busy = True
        self.btn_run.config(state="disabled", text="Verarbeite …")
        threading.Thread(target=self._worker, args=(list(self.files), opts),
                         daemon=True).start()

    def _output_path(self, src: Path, opts: dict, index: int, total: int) -> Path:
        stem = src.stem
        if opts["rename"]:
            stem = opts["rename"]
            if total > 1:
                stem = f"{stem}_{index + 1:02d}"
        if opts["date"]:
            if opts["datesrc"] == "file":
                d = datetime.date.fromtimestamp(src.stat().st_mtime)
            else:
                d = datetime.date.today()
            suffix = d.strftime("_%Y-%m-%d")
            if not stem.endswith(suffix):
                stem += suffix

        if opts["output"] == "subfolder":
            folder = src.parent / "bearbeitet"
            folder.mkdir(exist_ok=True)
        else:
            folder = src.parent

        dst = folder / f"{stem}.pdf"
        if opts["output"] == "replace":
            return dst
        # Kollisionen vermeiden (auch mit dem Original selbst)
        n = 2
        while dst.exists() or dst == src:
            if dst == src and not opts["content_ops"]:
                break  # reiner Namens-Noop, wird unten abgefangen
            dst = folder / f"{stem} ({n}).pdf"
            n += 1
        return dst

    def _worker(self, files: list, opts: dict):
        total_pages = 0
        page_counts = []
        for f in files:
            try:
                with fitz.open(f) as d:
                    page_counts.append(len(d))
                    total_pages += len(d)
            except Exception:
                page_counts.append(1)
                total_pages += 1

        def set_progress(v):
            self.after(0, lambda: self.progress.config(value=v))
        self.after(0, lambda: self.progress.config(maximum=max(1, total_pages), value=0))
        done_pages = [0]

        def page_done():
            done_pages[0] += 1
            set_progress(done_pages[0])

        try:
            if opts["ocr"]:
                ensure_tessdata(opts["lang"], self.log)
        except Exception as exc:
            self.log(f"FEHLER beim Laden der OCR-Sprachdateien: {exc}")
            self.log("Prüfe die Internetverbindung oder lege die .traineddata-Dateien "
                     f"manuell in {TESSDATA_DIR} ab.")
            self.after(0, self._done)
            return

        ok = err = 0
        outputs: list[Path] = []
        for i, src in enumerate(files):
            # Seitenbezogene Änderungen, die für diese Datei gelten
            crop_pages = opts["crop_pages"].get(src, {})
            if opts["crop_default"] or crop_pages:
                file_crop = {"default": opts["crop_default"], "pages": crop_pages}
            else:
                file_crop = None
            file_rot = opts["rot_pages"].get(src, {})
            file_del = opts["del_pages"].get(src, set())
            file_order = opts["page_order"].get(src)
            file_ops = (opts["ocr"] or opts["curves"] or opts["deskew"]
                        or file_crop is not None or bool(file_rot) or bool(file_del)
                        or bool(file_order))
            dst = self._output_path(src, opts, i, len(files))
            try:
                if not file_ops:
                    if opts["content_ops"] and not (opts["rename"] or opts["date"]):
                        # Datei ohne seitenbezogene Änderungen unangetastet lassen
                        self.log(f"{src.name}: keine Änderungen definiert, übersprungen.")
                        done_pages[0] += page_counts[i]
                        set_progress(done_pages[0])
                        continue
                    # Nur umbenennen / Datum anfügen
                    if dst == src:
                        self.log(f"{src.name}: Name unverändert, übersprungen.")
                        done_pages[0] += page_counts[i]
                        set_progress(done_pages[0])
                        continue
                    if opts["output"] == "replace":
                        src.rename(dst)
                        self.log(f"{src.name} → {dst.name} (umbenannt)")
                    else:
                        import shutil
                        shutil.copy2(src, dst)
                        self.log(f"{src.name} → {dst} (kopiert)")
                    done_pages[0] += page_counts[i]
                    set_progress(done_pages[0])
                else:
                    self.log(f"Verarbeite {src.name} ({page_counts[i]} Seiten) …")
                    tmp = dst.with_suffix(".tmp.pdf")
                    process_pdf(src, tmp,
                                dict(opts, crop=file_crop,
                                     rot_pages=file_rot, del_pages=file_del,
                                     page_order=file_order,
                                     curve_pages=opts["curve_pages"].get(src, {})),
                                self.log, page_cb=page_done)
                    if opts["output"] == "replace":
                        if dst != src and src.exists():
                            src.unlink()
                        os.replace(tmp, dst)
                    else:
                        os.replace(tmp, dst)
                    self.log(f"  → gespeichert als {dst}")
                ok += 1
                outputs.append(dst)
            except Exception as exc:
                err += 1
                self.log(f"FEHLER bei {src.name}: {exc}")
                tmp = dst.with_suffix(".tmp.pdf")
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

        self.log(f"Fertig: {ok} Datei(en) verarbeitet, {err} Fehler.")
        self._last_outputs = outputs
        self.after(0, self._done)

    def _done(self):
        self._busy = False
        self.btn_run.config(state="normal", text="▶  Verarbeiten")
        self.btn_open.config(state="normal" if self._last_outputs else "disabled")
        self.btn_open_folder.config(state="normal" if self._last_outputs else "disabled")

    # ---------------- Einzelinstanz (Übergabe von VueScan & Co.) ----------------

    def start_instance_server(self):
        """Nimmt Dateipfade von später gestarteten Instanzen entgegen."""
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.bind(("127.0.0.1", INSTANCE_PORT))
            srv.listen(5)
        except OSError:
            return False   # Port belegt – eine andere Instanz hört bereits
        self._instance_server = srv
        self._inbox: queue.Queue[list[str]] = queue.Queue()
        threading.Thread(target=self._instance_listen, args=(srv,),
                         daemon=True).start()
        self.after(200, self._poll_inbox)
        return True

    def _instance_listen(self, srv):
        # Läuft im Hintergrund-Thread: nur die Queue befüllen, kein Tk-Zugriff
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                data = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                paths = [p.strip() for p in data.decode("utf-8", "replace").splitlines()
                         if p.strip()]
                quittung = threading.Event()
                self._inbox.put((paths, quittung))
                if quittung.wait(3.5):
                    try:
                        conn.sendall(b"1")
                    except OSError:
                        pass

    def _poll_inbox(self):
        try:
            while True:
                paths, quittung = self._inbox.get_nowait()
                try:
                    self._receive_files(paths)
                finally:
                    quittung.set()
        except queue.Empty:
            pass
        self.after(200, self._poll_inbox)

    def _receive_files(self, paths):
        """Übernimmt extern übergebene Dateien und holt das Fenster nach vorn."""
        if paths and paths[0] == "MERGE":
            self.receive_merge(paths[1:])
            return
        view = bool(paths) and paths[0] == "VIEW"
        if view:
            paths = paths[1:]
        if paths and view:
            self.add_paths(paths)
            for p in paths:
                fp = Path(p)
                if fp.exists():
                    PdfViewer(self, fp)
            self.log(f"{len(paths)} Datei(en) im Viewer geöffnet (externer Aufruf).")
            return   # Hauptfenster bleibt verborgen, nur der Viewer wird gezeigt
        if paths:
            self.add_paths(paths)
            self.log(f"{len(paths)} Datei(en) übernommen (externer Aufruf).")
        self._view_only = False
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))

    # ---------------- Zusammenfassen ----------------

    def merge_files(self):
        """Öffnet das Fenster zum Zusammenfassen: die in der Liste markierten
        Dateien, sonst alle Dateien der Liste."""
        idx = self.listbox.curselection()
        paths = [self.files[i] for i in idx] if len(idx) > 1 else list(self.files)
        if len(paths) < 2:
            messagebox.showwarning(
                APP_TITLE, "Zum Zusammenfassen werden mindestens zwei Dateien gebraucht.")
            return
        MergeDialog(self, paths)

    def receive_merge(self, paths):
        """Sammelt die Aufrufe des Kontextmenüs ein.

        Der Explorer startet den Befehl einmal je markierter Datei. Deshalb
        wird nach jedem eintreffenden Pfad kurz gewartet und das Fenster erst
        geöffnet, wenn nichts mehr nachkommt."""
        self._merge_pending.extend(paths)
        if self._merge_timer is not None:
            self.after_cancel(self._merge_timer)
        self._merge_timer = self.after(1500, self._merge_ready)

    def _merge_ready(self):
        self._merge_timer = None
        paths = sorted(set(self._merge_pending), key=natural_key)
        self._merge_pending = []
        if not paths:
            return
        self._view_only = False
        self.deiconify()
        self.lift()
        self.log(f"{len(paths)} Datei(en) zum Zusammenfassen übernommen.")
        MergeDialog(self, paths)

    def note_output(self, path: Path):
        """Merkt sich ein Ergebnis, damit die Knöpfe zum Öffnen darauf zeigen."""
        self._last_outputs = [path]
        self.btn_open.config(state="normal")
        self.btn_open_folder.config(state="normal")

    def edit_from_viewer(self, path: Path):
        """Wird vom „Bearbeiten“-Button des Viewers aufgerufen: zeigt das
        Hauptfenster und setzt Original ersetzen / 300 dpi als Vorgabe für
        diesen Bearbeitungsvorgang."""
        self.var_output.set("replace")
        self.var_dpi.set(300)
        self.add_paths([str(path)])
        self._view_only = False
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))

    def view_selected(self):
        """Zeigt die in der Liste ausgewählte PDF im integrierten Viewer an."""
        f = self._current_file()
        if not f:
            messagebox.showwarning(APP_TITLE, "Bitte zuerst eine PDF-Datei hinzufügen.")
            return
        PdfViewer(self, f)

    def open_result(self):
        """Öffnet die zuletzt bearbeitete PDF im integrierten Viewer."""
        existing = [p for p in self._last_outputs if p.exists()]
        if not existing:
            messagebox.showwarning(APP_TITLE, "Keine bearbeitete PDF gefunden.")
            self.btn_open.config(state="disabled")
            return
        # Bei mehreren Ergebnissen nur die zuletzt erzeugte Datei öffnen
        target = existing[-1]
        PdfViewer(self, target)
        self.log(f"Geöffnet: {target}")

    def open_result_folder(self):
        """Öffnet den Ordner der zuletzt bearbeiteten PDF im Explorer (Datei markiert)."""
        existing = [p for p in self._last_outputs if p.exists()]
        if not existing:
            messagebox.showwarning(APP_TITLE, "Keine bearbeitete PDF gefunden.")
            self.btn_open_folder.config(state="disabled")
            return
        target = existing[-1]
        import subprocess
        # Wichtig: als einzelner String übergeben, nicht als Liste – sonst
        # quotet subprocess "/select,PFAD" bei Leerzeichen im Pfad komplett
        # als einen Block, den Explorer nicht parsen kann (öffnet dann
        # stattdessen die Standardansicht "Dokumente").
        subprocess.run(f'explorer /select,"{target}"')
        self.log(f"Ordner geöffnet: {target.parent}")


if __name__ == "__main__":
    cli_args = sys.argv[1:]
    cli_view = "--view" in cli_args
    cli_merge = "--merge" in cli_args
    cli_paths = [a for a in cli_args if a not in ("--view", "--merge")]
    # Läuft schon eine Instanz, Dateien dorthin weiterreichen und beenden
    if forward_to_running_instance(cli_paths, view=cli_view, merge=cli_merge):
        sys.exit(0)
    app = PdfToolApp()
    if not app.start_instance_server():
        # Zwischenzeitlich hat eine andere Instanz den Port belegt – das passiert
        # beim Kontextmenü, wenn der Explorer mehrere Aufrufe gleichzeitig startet
        if forward_to_running_instance(cli_paths, view=cli_view, merge=cli_merge):
            app.destroy()
            sys.exit(0)
    if cli_merge:
        app.after(100, lambda: app.receive_merge(cli_paths))
    if cli_view:
        app.withdraw()   # nur der Viewer soll erscheinen, nicht das Hauptfenster
        app._view_only = True
    if cli_paths:
        app.add_paths(cli_paths)
        if cli_view:
            for p in cli_paths:
                fp = Path(p)
                if fp.exists():
                    app.after(200, lambda fp=fp: PdfViewer(app, fp))
    app.mainloop()
