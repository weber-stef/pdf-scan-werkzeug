"""Einstellungsdatei und Farbschema (hell/dunkel) des PDF-Scan-Werkzeugs.

Die Datei liegt unter %APPDATA%\\PDF-Werkzeug\\einstellungen.txt, wird beim
ersten Start mit den Vorgaben angelegt und beim Programmstart gelesen.
Ungültige Werte werden ignoriert (es gilt dann die Vorgabe) und im
Fehlerprotokoll vermerkt. Zugang: Startmenü „Einstellungen“ oder
PDF-Werkzeug.exe --einstellungen.
"""
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk

ORDNER = Path(os.environ.get("APPDATA", str(Path.home()))) / "PDF-Werkzeug"
DATEI = ORDNER / "einstellungen.txt"

# Schlüssel -> (Vorgabe, Kommentar)
VORGABEN = {
    "design": ("hell", "hell oder dunkel"),
    "schriftart": ("Segoe UI", "Schrift der Bedienelemente, z. B. Segoe UI, Arial, Calibri"),
    "schriftgroesse": ("9", "Schriftgröße in Punkt, 8 bis 20"),
    "ausgabe": ("ersetzen", "ersetzen = Original ersetzen, unterordner = Unterordner \"bearbeitet\", gleicher_ordner"),
    "dpi": ("300", "Auflösung beim Verarbeiten: 150, 200, 300 (andere Werte 72 bis 600 gehen auch)"),
    "ocr": ("nein", "OCR-Texterkennung beim Start angehakt: ja oder nein"),
    "ocr_sprache": ("deu+eng", "Sprachen der OCR, z. B. deu+eng"),
    "gradationskurven": ("nein", "Gradationskurven beim Start angehakt: ja oder nein"),
    "gerade_ausrichten": ("nein", "automatisches Geraderichten beim Start angehakt: ja oder nein"),
    "datum_anfuegen": ("nein", "Datum an den Dateinamen anfügen: ja oder nein"),
    "datum_quelle": ("datei", "datei = Änderungsdatum der Datei, heute = heutiges Datum"),
    "dreh_regler_schritt": ("0.25", "Schrittweite des Dreh-Reglers in Grad (Pfeiltasten und Maus)"),
    # Farben (Hex, z. B. #2b2b2b). Die ersten sechs gelten nur im Dunkel-Design.
    "farbe_hintergrund": ("#2b2b2b", "dunkel: Fensterhintergrund"),
    "farbe_text": ("#e6e6e6", "dunkel: Textfarbe"),
    "farbe_felder": ("#1f1f1f", "dunkel: Eingabefelder und Listen"),
    "farbe_knoepfe": ("#3c3c3c", "dunkel: Knöpfe"),
    "farbe_auswahl": ("#2f6fbd", "dunkel: Auswahl und Markierung"),
    "farbe_hinweis": ("#a8a8a8", "dunkel: Hinweistexte (grau)"),
    "farbe_vorschau_betrachter": ("#4a4a4a", "Hintergrund der Seitenansicht im Betrachter (hell und dunkel)"),
    "farbe_vorschau_bearbeiter": ("#606060", "Hintergrund der Vorschau im Bearbeiter (hell und dunkel)"),
    "farbe_seitenleiste": ("#2e2e2e", "Hintergrund der Seitenleisten mit den Vorschaubildern"),
}

KOPF = """# Einstellungen des PDF-Scan-Werkzeugs
# Zeilen mit # sind Kommentare. Aufbau: schluessel = wert
# Änderungen gelten ab dem nächsten Start des Programms.
# Ein ungültiger Wert wird ignoriert, es gilt dann die Vorgabe.
# Löschst du diese Datei, wird sie mit den Vorgaben neu angelegt.
"""

warnungen: list[str] = []
S: dict[str, str] = {k: v[0] for k, v in VORGABEN.items()}


def _hex_ok(w: str) -> bool:
    w = w.strip()
    return len(w) == 7 and w[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in w[1:])


def _ja(w: str, vorgabe: bool) -> bool:
    w = w.strip().lower()
    if w in ("ja", "j", "an", "1", "true", "wahr"):
        return True
    if w in ("nein", "n", "aus", "0", "false", "falsch"):
        return False
    warnungen.append(f"Ja/Nein erwartet, gefunden: {w!r}")
    return vorgabe


def datei_anlegen():
    """Legt die Datei mit allen Vorgaben und Erklärungen an, falls es sie nicht gibt."""
    if DATEI.exists():
        return
    try:
        ORDNER.mkdir(parents=True, exist_ok=True)
        zeilen = [KOPF]
        for k, (v, c) in VORGABEN.items():
            zeilen.append(f"# {c}\n{k} = {v}\n")
        DATEI.write_text("\n".join(zeilen), encoding="utf-8")
    except OSError as exc:
        warnungen.append(f"Einstellungsdatei nicht angelegt: {exc}")


def laden():
    datei_anlegen()
    try:
        text = DATEI.read_text(encoding="utf-8-sig")
    except OSError:
        return
    for nr, zeile in enumerate(text.splitlines(), 1):
        z = zeile.strip()
        if not z or z[0] in "#;" or "=" not in z:
            continue
        k, w = z.split("=", 1)
        k, w = k.strip().lower(), w.strip()
        if k not in VORGABEN:
            warnungen.append(f"Zeile {nr}: unbekannter Schlüssel {k!r}")
            continue
        if not w:
            continue
        S[k] = w
    pruefen()
    if warnungen:
        try:
            ORDNER.mkdir(parents=True, exist_ok=True)
            with open(ORDNER / "fehler.log", "a", encoding="utf-8") as f:
                for w in warnungen:
                    f.write(f"[einstellungen.txt] {w}\n")
        except OSError:
            pass


def pruefen():
    def vorgabe(k):
        return VORGABEN[k][0]

    if S["design"].lower() not in ("hell", "dunkel"):
        warnungen.append(f"design: {S['design']!r} ungültig"); S["design"] = vorgabe("design")
    S["design"] = S["design"].lower()
    try:
        g = int(float(S["schriftgroesse"]))
        if not 8 <= g <= 20:
            raise ValueError
        S["schriftgroesse"] = str(g)
    except ValueError:
        warnungen.append(f"schriftgroesse: {S['schriftgroesse']!r} ungültig")
        S["schriftgroesse"] = vorgabe("schriftgroesse")
    if S["ausgabe"].lower() not in ("ersetzen", "unterordner", "gleicher_ordner"):
        warnungen.append(f"ausgabe: {S['ausgabe']!r} ungültig"); S["ausgabe"] = vorgabe("ausgabe")
    S["ausgabe"] = S["ausgabe"].lower()
    try:
        d = int(float(S["dpi"]))
        if not 72 <= d <= 600:
            raise ValueError
        S["dpi"] = str(d)
    except ValueError:
        warnungen.append(f"dpi: {S['dpi']!r} ungültig"); S["dpi"] = vorgabe("dpi")
    if S["datum_quelle"].lower() not in ("datei", "heute"):
        warnungen.append(f"datum_quelle: {S['datum_quelle']!r} ungültig")
        S["datum_quelle"] = vorgabe("datum_quelle")
    S["datum_quelle"] = S["datum_quelle"].lower()
    try:
        s = float(S["dreh_regler_schritt"].replace(",", "."))
        if not 0.05 <= s <= 5:
            raise ValueError
        S["dreh_regler_schritt"] = str(s)
    except ValueError:
        warnungen.append("dreh_regler_schritt ungültig")
        S["dreh_regler_schritt"] = vorgabe("dreh_regler_schritt")
    for k in VORGABEN:
        if k.startswith("farbe_") and not _hex_ok(S[k]):
            warnungen.append(f"{k}: {S[k]!r} ist keine Farbe wie #2b2b2b")
            S[k] = vorgabe(k)
    for k in ("ocr", "gradationskurven", "gerade_ausrichten", "datum_anfuegen"):
        S[k] = "ja" if _ja(S[k], vorgabe(k) == "ja") else "nein"


def ja(schluessel: str) -> bool:
    return S[schluessel] == "ja"


def ausgabe_wert() -> str:
    return {"ersetzen": "replace", "unterordner": "subfolder",
            "gleicher_ordner": "same"}[S["ausgabe"]]


laden()
DUNKEL = S["design"] == "dunkel"

# Farben für Beschriftungen, im Hell-Design wie bisher fest im Code
C_HINT = S["farbe_hinweis"] if DUNKEL else "#606060"
C_OK = "#6fd46f" if DUNKEL else "#107010"
C_ERR = "#ff7b72" if DUNKEL else "#a00000"
C_WARN = "#e0a040" if DUNKEL else "#a06000"
C_TXT = S["farbe_text"] if DUNKEL else "#202020"
C_VIEW = S["farbe_vorschau_betrachter"]
C_EDIT = S["farbe_vorschau_bearbeiter"]
C_SIDE = S["farbe_seitenleiste"]


def einstellungen_oeffnen():
    """Öffnet die Einstellungsdatei im Editor."""
    import subprocess
    datei_anlegen()
    subprocess.Popen(["notepad.exe", str(DATEI)])


def _titelleiste_dunkel(fenster):
    try:
        import ctypes
        fenster.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(fenster.winfo_id())
        wert = ctypes.c_int(1)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 neuere / ältere Fassung)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(wert), ctypes.sizeof(wert)) == 0:
                break
    except Exception:
        pass


def anwenden(root: tk.Tk):
    """Setzt Schrift und (bei Design = dunkel) die Farben für alle Fenster.
    Muss gleich nach dem Erzeugen des Hauptfensters laufen, vor dem Aufbau."""
    fam, gr = S["schriftart"], int(S["schriftgroesse"])
    try:
        for n in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                  "TkCaptionFont", "TkIconFont", "TkTooltipFont"):
            tkfont.nametofont(n).configure(family=fam, size=gr)
    except tk.TclError:
        pass
    if not DUNKEL:
        return
    bg, fg = S["farbe_hintergrund"], S["farbe_text"]
    feld, knopf, sel = S["farbe_felder"], S["farbe_knoepfe"], S["farbe_auswahl"]
    hint = S["farbe_hinweis"]
    rand = "#555555"
    root.configure(background=bg)
    for muster, wert in (
            ("*Background", bg), ("*Foreground", fg),
            ("*Entry.background", feld), ("*Listbox.background", feld),
            ("*Text.background", feld), ("*Spinbox.background", feld),
            ("*Listbox.selectBackground", sel), ("*Entry.selectBackground", sel),
            ("*Text.selectBackground", sel), ("*selectForeground", "#ffffff"),
            ("*Entry.insertBackground", fg), ("*Text.insertBackground", fg),
            ("*Scale.troughColor", feld), ("*Scale.activeBackground", knopf),
            ("*Scale.highlightBackground", bg), ("*Button.background", knopf),
            ("*Button.activeBackground", sel), ("*Button.activeForeground", "#ffffff"),
            ("*Menu.background", knopf), ("*Menu.foreground", fg),
            ("*Menu.activeBackground", sel), ("*Menu.activeForeground", "#ffffff"),
            ("*TCombobox*Listbox.background", feld),
            ("*TCombobox*Listbox.foreground", fg),
            ("*TCombobox*Listbox.selectBackground", sel),
            ("*TCombobox*Listbox.selectForeground", "#ffffff"),
            ("*highlightBackground", bg)):
        root.option_add(muster, wert)
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=bg, foreground=fg, fieldbackground=feld,
                 bordercolor=rand, lightcolor=knopf, darkcolor=knopf, troughcolor=feld,
                 insertcolor=fg, selectbackground=sel, selectforeground="#ffffff",
                 focuscolor=sel)
    st.map(".", foreground=[("disabled", "#777777")],
           background=[("disabled", bg)])
    st.configure("TButton", background=knopf, foreground=fg, padding=(6, 3))
    st.map("TButton", background=[("pressed", sel), ("active", "#4a4a4a"), ("disabled", "#333333")],
           foreground=[("disabled", "#777777")])
    for n in ("TCheckbutton", "TRadiobutton"):
        st.configure(n, background=bg, foreground=fg, indicatorcolor=feld,
                     indicatorbackground=feld)
        st.map(n, background=[("active", bg)],
               indicatorcolor=[("selected", sel), ("!selected", feld)],
               foreground=[("disabled", "#777777")])
    st.configure("TEntry", fieldbackground=feld, foreground=fg)
    st.configure("TCombobox", fieldbackground=feld, background=knopf, foreground=fg,
                 arrowcolor=fg, selectbackground=feld, selectforeground=fg)
    st.map("TCombobox", fieldbackground=[("readonly", feld)],
           foreground=[("readonly", fg)], selectbackground=[("readonly", feld)],
           selectforeground=[("readonly", fg)])
    st.configure("TScrollbar", background=knopf, troughcolor=feld, arrowcolor=fg,
                 bordercolor=bg)
    st.map("TScrollbar", background=[("active", "#555555")])
    st.configure("TLabelframe", background=bg, bordercolor=rand)
    st.configure("TLabelframe.Label", background=bg, foreground=fg)
    st.configure("TSeparator", background=rand)
    st.configure("TSpinbox", fieldbackground=feld, foreground=fg, arrowcolor=fg)
    st.configure("TProgressbar", background=sel, troughcolor=feld)
    st.configure("Treeview", background=feld, fieldbackground=feld, foreground=fg)
    st.map("Treeview", background=[("selected", sel)])

    def beim_zeigen(ev):
        w = ev.widget
        if isinstance(w, (tk.Tk, tk.Toplevel)) and not getattr(w, "_dunkle_leiste", False):
            w._dunkle_leiste = True
            w.configure(background=bg)
            _titelleiste_dunkel(w)
    root.bind_all("<Map>", beim_zeigen, add="+")
