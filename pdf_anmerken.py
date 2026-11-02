# -*- coding: utf-8 -*-
"""
Kommentieren und Ändern im PDF-Betrachter
=============================
Schreibmaschine (Text mit Farbe, Schrift, Größe), Pfeile, Rechtecke, Ellipsen
und das Ändern vorhandenen Textes.

Alles wird als echter Seiteninhalt in die PDF geschrieben (inkrementell, also
ohne die Datei neu aufzubauen). Rückgängig schneidet die Datei einfach auf die
alte Größe zurück. Der erste Eingriff einer Sitzung legt eine Sicherung
„<Name>_vor-Aenderung.pdf“ an.

Der Teil bis „Betrachter-Erweiterung“ ist reines PyMuPDF und braucht kein Tk.
"""

import base64
import json
import math
import os
import re
import shutil
import tempfile
import uuid

GELB = (1.0, 0.97, 0.6)
from pathlib import Path

import fitz
import numpy as np

# ---------------------------------------------------------------------------
# Schriften
# ---------------------------------------------------------------------------

_STIL_WOERTER = {"bold": "b", "italic": "i", "oblique": "i", "regular": "",
                 "fett": "b", "kursiv": "i", "standard": "", "normal": ""}
_katalog = None          # {Familie: {(fett, kursiv): Pfad}}
_font_cache = {}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def font_catalog() -> dict:
    """Installierte TrueType-/OpenType-Schriften aus der Windows-Registry,
    gruppiert nach Familie und Stil."""
    global _katalog
    if _katalog is not None:
        return _katalog
    kat = {}
    quellen = []
    try:
        import winreg
        sub = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for hive, basis in ((winreg.HKEY_LOCAL_MACHINE, windir),
                            (winreg.HKEY_CURRENT_USER,
                             Path(os.environ.get("LOCALAPPDATA", "")) /
                             "Microsoft" / "Windows" / "Fonts")):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    i = 0
                    while True:
                        try:
                            name, wert, _t = winreg.EnumValue(k, i)
                        except OSError:
                            break
                        i += 1
                        quellen.append((name, str(wert), basis))
            except OSError:
                pass
    except ImportError:
        pass
    for name, datei, basis in quellen:
        p = Path(datei)
        if not p.is_absolute():
            p = basis / datei
        if p.suffix.lower() not in (".ttf", ".otf") or not p.exists():
            continue
        name = re.sub(r"\s*\((TrueType|OpenType)\)\s*$", "", name).strip()
        if "&" in name:
            continue
        fett = kursiv = False
        rest = []
        for w in name.split():
            st = _STIL_WOERTER.get(w.lower())
            if st is None:
                rest.append(w)
            else:
                fett = fett or st == "b"
                kursiv = kursiv or st == "i"
        fam = " ".join(rest).strip()
        if fam:
            kat.setdefault(fam, {})[(fett, kursiv)] = str(p)
    _katalog = kat
    return kat


def font_families() -> list:
    # Nur Familien mit Normalschnitt: sonst käme „ohne Fett/Kursiv“ als Fett/Kursiv heraus.
    return sorted((f for f, v in font_catalog().items() if (False, False) in v),
                  key=str.lower)


def font_file(family: str, bold: bool, italic: bool) -> str:
    """Pfad der Schriftdatei; fehlt der Stil, wird der nächstbeste genommen."""
    kat = font_catalog()
    stile = kat.get(family)
    if not stile:
        for ersatz in ("Arial", "Segoe UI", "Tahoma"):
            if ersatz in kat:
                stile = kat[ersatz]
                break
        else:
            stile = next(iter(kat.values()))
    for key in ((bold, italic), (bold, False), (False, italic), (False, False)):
        if key in stile:
            return stile[key]
    return next(iter(stile.values()))


def font_has_style(family: str, bold: bool, italic: bool) -> bool:
    return (bold, italic) in font_catalog().get(family, {})


def _font_obj(path: str) -> "fitz.Font":
    f = _font_cache.get(path)
    if f is None:
        f = _font_cache[path] = fitz.Font(fontfile=path)
    return f


_ALIAS = {"helvetica": "Arial", "arialmt": "Arial", "times": "Times New Roman",
          "courier": "Courier New", "couriernewpsmt": "Courier New",
          "timesnewromanpsmt": "Times New Roman"}


def match_font(span_font: str, flags: int = 0):
    """Wählt zu einer PDF-Schrift die ähnlichste installierte.
    Rückgabe: (Familie, fett, kursiv)."""
    name = re.sub(r"^[A-Z]{6}\+", "", span_font or "")
    n = _norm(name)
    fett = bool(flags & 16) or any(w in n for w in ("bold", "black", "heavy"))
    kursiv = bool(flags & 2) or any(w in n for w in ("italic", "oblique"))
    kat = font_catalog()
    beste, laenge = None, 0
    for fam in kat:
        fn = _norm(fam)
        if fn and n.startswith(fn) and len(fn) > laenge:
            beste, laenge = fam, len(fn)
    if beste is None:
        for alias, fam in _ALIAS.items():
            if n.startswith(alias) and fam in kat:
                beste = fam
                break
    if beste is None:
        if flags & 8:
            wunsch = "Courier New"
        elif flags & 4:
            wunsch = "Times New Roman"
        else:
            wunsch = "Arial"
        beste = wunsch if wunsch in kat else (next(iter(kat)) if kat else wunsch)
    return beste, fett, kursiv


# ---------------------------------------------------------------------------
# Zeichnen (Koordinaten immer so, wie die Seite angezeigt wird)
# ---------------------------------------------------------------------------

def _unrot(page, p):
    return fitz.Point(p) * page.derotation_matrix


def _unrot_rect(page, r):
    m = page.derotation_matrix
    rr = fitz.Rect(fitz.Point(r.x0, r.y0) * m, fitz.Point(r.x1, r.y1) * m)
    rr.normalize()
    return rr


def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(c) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v * 255))) for v in c)


def wrap_text(text: str, font, size: float, maxw):
    """Bricht text an Wortgrenzen auf maxw (pt) um; maxw=None: nur harte Umbrüche."""
    zeilen = []
    for absatz in text.split("\n"):
        if not maxw:
            zeilen.append(absatz)
            continue
        akt = ""
        for wort in absatz.split(" "):
            probe = wort if not akt else akt + " " + wort
            if font.text_length(probe, fontsize=size) <= maxw or not akt:
                akt = probe
                # überlanges einzelnes Wort zeichenweise trennen
                while font.text_length(akt, fontsize=size) > maxw and len(akt) > 1:
                    k = len(akt) - 1
                    while k > 1 and font.text_length(akt[:k], fontsize=size) > maxw:
                        k -= 1
                    zeilen.append(akt[:k])
                    akt = akt[k:]
            else:
                zeilen.append(akt)
                akt = wort
        zeilen.append(akt)
    return zeilen


def text_metrics(family, bold, italic, size):
    """(Ascender, Descender, Zeilenhöhe) in pt."""
    f = _font_obj(font_file(family, bold, italic))
    asc, desc = f.ascender * size, f.descender * size
    return asc, desc, asc - desc


def draw_text_lines(page, x, baseline, zeilen, family, bold, italic, size, color,
                    fill=None, pad=2.0):
    """Schreibt Zeilen ab (x, erste Grundlinie) in Anzeigekoordinaten."""
    pfad = font_file(family, bold, italic)
    f = _font_obj(pfad)
    asc, desc, lh = text_metrics(family, bold, italic, size)
    fname = "AF" + re.sub(r"[^A-Za-z0-9]", "", Path(pfad).stem)[:20]
    if fill is not None and zeilen:
        breite = max(f.text_length(z, fontsize=size) for z in zeilen)
        box = fitz.Rect(x - pad, baseline - asc - pad,
                        x + breite + pad,
                        baseline + (len(zeilen) - 1) * lh - desc + pad)
        page.draw_rect(_unrot_rect(page, box), color=None, fill=fill, width=0)
    for i, z in enumerate(zeilen):
        if not z.strip():
            continue
        page.insert_text(_unrot(page, (x, baseline + i * lh)), z, fontsize=size,
                         fontname=fname, fontfile=pfad, color=color,
                         rotate=page.rotation)


KEY = "AnnoWerkzeug"     # Seitenschlüssel mit den Daten der gesetzten Texte


def load_records(page) -> list:
    """Daten der mit der Schreibmaschine gesetzten Texte dieser Seite."""
    try:
        typ, wert = page.parent.xref_get_key(page.xref, KEY)
        if typ != "string":
            return []
        return json.loads(base64.b64decode(wert).decode("utf-8"))
    except Exception:
        return []


def save_records(page, recs):
    roh = base64.b64encode(json.dumps(recs, ensure_ascii=True).encode("ascii")).decode("ascii")
    page.parent.xref_set_key(page.xref, KEY, f"({roh})")


def record_display(page, rec):
    """(Position, Rahmen) eines gesetzten Textes in Anzeigekoordinaten."""
    rm = page.rotation_matrix
    pos = fitz.Point(rec["pos"]) * rm
    b = rec["bbox"]
    box = fitz.Rect(fitz.Point(b[0], b[1]) * rm, fitz.Point(b[2], b[3]) * rm).normalize()
    return pos, box


def text_record_at(page, pt):
    """Der oberste gesetzte Text an Punkt pt (Anzeigekoordinaten) oder None."""
    for rec in reversed(load_records(page)):
        if rec.get("kind", "text") == "text" and fitz.Point(pt) in record_display(page, rec)[1]:
            return rec
    return None


def remove_streams(page, xrefs):
    """Nimmt Inhaltsströme aus der Seite und liefert die neu geladene Seite."""
    doc = page.parent
    weg = set(xrefs)
    bleibt = [x for x in page.get_contents() if x not in weg]
    doc.xref_set_key(page.xref, "Contents",
                     "[" + " ".join(f"{x} 0 R" for x in bleibt) + "]")
    return doc.reload_page(page)


def purge_record(page, rec):
    """Wie remove_streams für einen Datensatz. Waren seine Ströme inzwischen mit dem
    Seiteninhalt verschmolzen (z. B. durch Schwärzen/„Text ändern“ oder Neuschreiben der
    Seite), bliebe die Zeichnung sonst stehen; dann wird sie im Rahmen herausgenommen."""
    vorhanden = set(page.get_contents())
    verschmolzen = any(x not in vorhanden for x in rec.get("xrefs", []))
    page = remove_streams(page, rec["xrefs"])
    if verschmolzen:
        b = rec["bbox"]
        pad = 1.0 if rec.get("kind", "text") == "text" else 0.0
        r = fitz.Rect(b[0] + 2, b[1] + 3, b[2] - 2, b[3] - 3) if pad else fitz.Rect(b)
        page.add_redact_annot(r)
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
            text=fitz.PDF_REDACT_TEXT_REMOVE if pad else fitz.PDF_REDACT_TEXT_NONE)
        page = page.parent.reload_page(page)
    return page


def add_text(page, pos, text, family, bold, italic, size, color,
             width=None, fill=None):
    """Setzt text mit linker oberer Ecke pos (Anzeigekoordinaten) und merkt
    sich die Daten, damit er später geändert werden kann. Liefert den Datensatz."""
    page.wrap_contents()
    vorher = set(page.get_contents())
    pfad = font_file(family, bold, italic)
    f = _font_obj(pfad)
    asc, _d, lh = text_metrics(family, bold, italic, size)
    zeilen = wrap_text(text, f, size, width)
    draw_text_lines(page, pos[0], pos[1] + asc, zeilen, family, bold, italic,
                    size, color, fill)
    neu = [x for x in page.get_contents() if x not in vorher]
    breite = max([f.text_length(z, fontsize=size) for z in zeilen] or [0])
    pad = 2.0
    box = fitz.Rect(pos[0] - pad, pos[1] - pad, pos[0] + breite + pad,
                    pos[1] + len(zeilen) * lh + pad)
    pu = _unrot(page, pos)
    bu = _unrot_rect(page, box)
    rec = {"id": uuid.uuid4().hex[:8], "kind": "text", "xrefs": neu, "pos": [pu.x, pu.y],
           "bbox": [bu.x0, bu.y0, bu.x1, bu.y1], "width": width, "text": text,
           "family": family, "bold": bool(bold), "italic": bool(italic),
           "size": size, "color": list(color), "fill": fill is not None}
    recs = load_records(page)
    recs.append(rec)
    save_records(page, recs)
    return rec


def edit_text(page, rec, text, family, bold, italic, size, color,
              width=None, fill=None, pos=None):
    """Ersetzt einen gesetzten Text; leerer Text löscht ihn. Der Text bleibt an
    seiner Stelle (linke obere Ecke), außer pos (Anzeigekoordinaten) ist gegeben."""
    if pos is None:
        pos, _box = record_display(page, rec)
    pos = fitz.Point(pos)
    recs = [r for r in load_records(page) if r["id"] != rec["id"]]
    page = purge_record(page, rec)
    save_records(page, recs)
    if text.strip():
        return add_text(page, (pos.x, pos.y), text, family, bold, italic, size, color,
                        width if width is not None else rec.get("width"), fill)
    return None


def _shape_record(page, kind, vorher, pts, color, width, filled):
    """Merkt Pfeil oder Form (Punkte in Anzeigekoordinaten) für späteres Verschieben."""
    neu = [x for x in page.get_contents() if x not in vorher]
    pu = [_unrot(page, p) for p in pts]
    rec = {"id": uuid.uuid4().hex[:8], "kind": kind, "xrefs": neu,
           "pts": [[p.x, p.y] for p in pu], "color": list(color),
           "width": float(width), "filled": bool(filled)}
    rec["bbox"] = list(_shape_box(page, rec, unrotated=True))
    recs = load_records(page)
    recs.append(rec)
    save_records(page, recs)
    return rec


def _shape_box(page, rec, unrotated=False):
    pts = [fitz.Point(p) for p in rec["pts"]]
    if not unrotated:
        pts = [p * page.rotation_matrix for p in pts]
    r = fitz.Rect(min(p.x for p in pts), min(p.y for p in pts),
                  max(p.x for p in pts), max(p.y for p in pts))
    m = rec["width"] / 2 + (8 + 4.5 * rec["width"] if rec["kind"] == "arrow" else 1)
    return fitz.Rect(r.x0 - m, r.y0 - m, r.x1 + m, r.y1 + m)


def record_box(page, rec):
    """Umrandung eines gesetzten Objekts in Anzeigekoordinaten."""
    if rec.get("kind") == "burned":
        return fitz.Rect(rec["bbox"])
    if rec.get("kind", "text") == "text":
        return record_display(page, rec)[1]
    return _shape_box(page, rec)


def _dist_segment(p, a, b):
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def hit_record(page, pt, tol=4.0):
    """Das oberste gesetzte Objekt unter pt: Texte innerhalb ihres Rahmens,
    Pfeile und Formen nur am Rand bzw. an der Linie."""
    pt = fitz.Point(pt)
    rm = page.rotation_matrix
    for rec in reversed(load_records(page)):
        kind = rec.get("kind", "text")
        if kind == "text":
            if pt in record_display(page, rec)[1]:
                return rec
            continue
        pts = [fitz.Point(p) * rm for p in rec["pts"]]
        t = tol + rec["width"] / 2
        if kind == "arrow":
            nahe_spitze = math.hypot(pt.x - pts[1].x, pt.y - pts[1].y) <= 8 + 3 * rec["width"]
            if _dist_segment(pt, pts[0], pts[1]) <= t or nahe_spitze:
                return rec
        else:
            r = fitz.Rect(min(p.x for p in pts), min(p.y for p in pts),
                          max(p.x for p in pts), max(p.y for p in pts))
            if kind == "rect":
                outer = fitz.Rect(r.x0 - t, r.y0 - t, r.x1 + t, r.y1 + t)
                inner = fitz.Rect(r.x0 + t, r.y0 + t, r.x1 - t, r.y1 - t)
                if pt in outer and (inner.is_empty or pt not in inner):
                    return rec
            else:
                a, b = max(r.width / 2, 1e-3), max(r.height / 2, 1e-3)
                cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
                v = math.hypot((pt.x - cx) / a, (pt.y - cy) / b)
                if abs(v - 1) * min(a, b) <= t:
                    return rec
    return None


def delete_record(page, rec):
    """Entfernt ein gesetztes Objekt ganz."""
    recs = [r for r in load_records(page) if r["id"] != rec["id"]]
    page = purge_record(page, rec)
    save_records(page, recs)


def burned_text_at(page, pt):
    """Fest im Seiteninhalt stehende, sichtbare Textzeile an pt (Anzeigekoordinaten)
    oder None. Die unsichtbare OCR-Schicht (GlyphLessFont) zählt nicht. Das ist der
    Rest früherer Kommentare, deren Datensatz verloren ging."""
    pt = fitz.Point(pt)
    try:
        bloecke = page.get_text("dict")["blocks"]
    except Exception:
        return None
    for b in bloecke:
        for ln in b.get("lines", []):
            spans = [s for s in ln["spans"]
                     if "glyphless" not in s["font"].lower() and s["text"].strip()]
            if not spans:
                continue
            r = fitz.Rect(ln["bbox"])
            r = fitz.Rect(r.x0 - 3, r.y0 - 2, r.x1 + 3, r.y1 + 2)
            if pt in r:
                return {"id": "fest:%d,%d" % (round(r.x0), round(r.y0)), "kind": "burned",
                        "bbox": [r.x0, r.y0, r.x1, r.y1],
                        "text": "".join(s["text"] for s in spans).strip()}
    return None


def delete_burned(page, rec):
    """Entfernt festen Text im Rahmen der Zeile; Bilder (der Scan) bleiben unberührt."""
    b = rec["bbox"]
    # Rand wieder abziehen, den burned_text_at zum leichteren Treffen addiert hat;
    # sonst erwischt es Nachbarzeilen
    r = fitz.Rect(b[0] + 3, b[1] + 4, b[2] - 3, b[3] - 4)
    page.add_redact_annot(r)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                          graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                          text=fitz.PDF_REDACT_TEXT_REMOVE)


def shape_points(page, rec):
    """Definierende Punkte eines Pfeils/einer Form in Anzeigekoordinaten."""
    rm = page.rotation_matrix
    return [fitz.Point(p) * rm for p in rec["pts"]]


def restyle_shape(page, rec, pts=None, color=None, width=None, filled=None):
    """Zeichnet Pfeil/Form neu: andere Punkte (Größe/Lage), Farbe, Stärke, Füllung.
    Nicht Angegebenes bleibt. Liefert den neuen Datensatz."""
    kind = rec["kind"]
    pts = [(p.x, p.y) for p in shape_points(page, rec)] if pts is None         else [tuple(p) for p in pts]
    color = tuple(rec["color"]) if color is None else tuple(color)
    width = rec["width"] if width is None else float(width)
    filled = rec["filled"] if filled is None else bool(filled)
    if kind == "arrow":
        if math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]) < 8:
            raise ValueError("Der Pfeil wäre zu kurz.")
    elif abs(pts[1][0] - pts[0][0]) < 2 or abs(pts[1][1] - pts[0][1]) < 2:
        raise ValueError("Die Form wäre zu klein.")
    recs = [r for r in load_records(page) if r["id"] != rec["id"]]
    page = purge_record(page, rec)
    save_records(page, recs)
    if kind == "arrow":
        return add_arrow(page, pts[0], pts[1], color, width)
    return add_shape(page, kind, pts[0], pts[1], color, width, filled)


def move_record(page, rec, dx, dy):
    """Verschiebt ein gesetztes Objekt (Text, Pfeil oder Form) um (dx, dy).
    Liefert den neuen Datensatz."""
    if rec.get("kind", "text") == "text":
        pos, _b = record_display(page, rec)
        return edit_text(page, rec, rec["text"], rec["family"], rec["bold"],
                         rec["italic"], rec["size"], tuple(rec["color"]),
                         width=rec["width"], fill=GELB if rec["fill"] else None,
                         pos=(pos.x + dx, pos.y + dy))
    pts = [(p.x + dx, p.y + dy) for p in shape_points(page, rec)]
    return restyle_shape(page, rec, pts=pts)


def add_shape(page, kind, p0, p1, color, width, filled=False):
    """kind: 'rect' oder 'oval' (p0/p1 = gegenüberliegende Ecken)."""
    r = fitz.Rect(min(p0[0], p1[0]), min(p0[1], p1[1]),
                  max(p0[0], p1[0]), max(p0[1], p1[1]))
    if r.width < 2 or r.height < 2:
        return False
    page.wrap_contents()
    vorher = set(page.get_contents())
    ru = _unrot_rect(page, r)
    sh = page.new_shape()
    if kind == "oval":
        sh.draw_oval(ru)
    else:
        sh.draw_rect(ru)
    sh.finish(color=color, fill=color if filled else None, width=width,
              fill_opacity=0.25, lineJoin=1)
    sh.commit()
    return _shape_record(page, kind, vorher, [r.tl, r.br], color, width, filled)


def arrow_points(p0, p1, width):
    """(Schaftende, Spitze, Flügel1, Flügel2) in Anzeigekoordinaten."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    laenge = math.hypot(dx, dy)
    if laenge < 1e-6:
        return None
    ux, uy = dx / laenge, dy / laenge
    kopf = min(max(10.0, 4.5 * width + 6.0), laenge * 0.5)
    halb = kopf * 0.38
    bx, by = p1[0] - ux * kopf, p1[1] - uy * kopf
    nx, ny = -uy, ux
    return ((bx, by), tuple(p1), (bx + nx * halb, by + ny * halb),
            (bx - nx * halb, by - ny * halb))


def add_arrow(page, p0, p1, color, width):
    if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) < 8:
        return False
    page.wrap_contents()
    vorher = set(page.get_contents())
    basis, spitze, w1, w2 = arrow_points(p0, p1, width)
    u = lambda p: _unrot(page, p)
    sh = page.new_shape()
    sh.draw_line(u(p0), u(basis))
    sh.finish(color=color, width=width, lineCap=0)
    sh.draw_polyline([u(w1), u(spitze), u(w2)])
    sh.finish(color=color, fill=color, width=1, closePath=True, lineJoin=1)
    sh.commit()
    return _shape_record(page, "arrow", vorher, [p0, p1], color, width, False)


# ---------------------------------------------------------------------------
# Vorhandenen Text ändern
# ---------------------------------------------------------------------------

def find_span(page, pt, ganze_zeile=False):
    """Sucht an Punkt pt (Anzeigekoordinaten) den Textabschnitt.
    Rückgabe: dict(text, bbox, origin, size, font, flags, color) oder None.
    Wirft ValueError bei senkrechter Schrift."""
    # Die Textextraktion liefert ungedrehte Koordinaten – Punkt umrechnen,
    # Ergebnis wieder in Anzeigekoordinaten zurückgeben.
    pt = _unrot(page, pt)
    rm = page.rotation_matrix
    d = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE |
                      fitz.TEXT_MEDIABOX_CLIP)
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "") != ""]
            for s in spans:
                r = fitz.Rect(s["bbox"])
                r.x0 -= 1
                r.x1 += 1
                if pt in r:
                    dx, dy = line.get("dir", (1, 0))
                    v = fitz.Point(dx, dy) * fitz.Matrix(rm.a, rm.b, rm.c, rm.d, 0, 0)
                    if abs(v.y) > 0.1 or v.x < 0:
                        raise ValueError("Senkrechte oder gedrehte Schrift wird nicht unterstützt.")
                    if ganze_zeile:
                        box = fitz.Rect(spans[0]["bbox"])
                        for t in spans[1:]:
                            box |= fitz.Rect(t["bbox"])
                        text = "".join(t["text"] for t in spans)
                        origin = spans[0]["origin"]
                    else:
                        box, text, origin = fitz.Rect(s["bbox"]), s["text"], s["origin"]
                    c = s.get("color", 0)
                    box = fitz.Rect(fitz.Point(box.x0, box.y0) * rm,
                                    fitz.Point(box.x1, box.y1) * rm).normalize()
                    origin = fitz.Point(origin) * rm
                    return {"text": text, "bbox": box, "origin": tuple(origin),
                            "size": float(s["size"]), "font": s.get("font", ""),
                            "flags": int(s.get("flags", 0)),
                            "color": ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255,
                                      (c & 255) / 255)}
    return None


def word_at(page, pt):
    """Das Wort unter pt (Anzeigekoordinaten): (Rechteck in Anzeigekoordinaten, Text)
    oder None. Satzzeichen am Rand gehören nicht dazu, Bindestrich/Apostroph
    zwischen Buchstaben schon."""
    p = _unrot(page, pt)
    rm = page.rotation_matrix
    d = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            lb = fitz.Rect(line["bbox"])
            if not (lb.y0 - 1 <= p.y <= lb.y1 + 1 and lb.x0 - 1 <= p.x <= lb.x1 + 1):
                continue
            chars = [c for s in line.get("spans", []) for c in s.get("chars", [])]
            hit = None
            for i, c in enumerate(chars):
                cb = fitz.Rect(c["bbox"])
                if cb.x0 - 0.5 <= p.x <= cb.x1 + 0.5:
                    hit = i
                    break
            if hit is None:
                continue

            def wortzeichen(i):
                if not 0 <= i < len(chars):
                    return False
                z = chars[i]["c"]
                if z.isalnum() or z == "_":
                    return True
                if z in "-'’" and 0 < i < len(chars) - 1:
                    return chars[i - 1]["c"].isalnum() and chars[i + 1]["c"].isalnum()
                return False

            if not wortzeichen(hit):
                return None
            lo = hi = hit
            while wortzeichen(lo - 1):
                lo -= 1
            while wortzeichen(hi + 1):
                hi += 1
            box = fitz.Rect(chars[lo]["bbox"])
            for c in chars[lo:hi + 1]:
                box |= fitz.Rect(c["bbox"])
            box = fitz.Rect(fitz.Point(box.x0, box.y0) * rm, fitz.Point(box.x1, box.y1) * rm).normalize()
            return box, "".join(c["c"] for c in chars[lo:hi + 1])
    return None


def _line_records(page):
    """Alle waagerechten Textzeilen mit Zeichen (Anzeigekoordinaten)."""
    rm = page.rotation_matrix
    mrot = fitz.Matrix(rm.a, rm.b, rm.c, rm.d, 0, 0)
    d = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)
    out = []

    def disp(r):
        r = fitz.Rect(r)
        return fitz.Rect(fitz.Point(r.x0, r.y0) * rm, fitz.Point(r.x1, r.y1) * rm).normalize()

    for bi, block in enumerate(d.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1, 0))
            v = fitz.Point(dx, dy) * mrot
            if abs(v.y) > 0.1 or v.x < 0:
                continue
            spans, chars = [], []
            for si, s in enumerate(line.get("spans", [])):
                c = s.get("color", 0)
                spans.append({"font": s.get("font", ""), "size": float(s["size"]),
                              "flags": int(s.get("flags", 0)),
                              "color": ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255),
                              "origin": fitz.Point(s["origin"]) * rm})
                for ch in s.get("chars", []):
                    chars.append((ch["c"], disp(ch["bbox"]), si))
            if chars:
                out.append({"block": bi, "bbox": disp(line["bbox"]), "chars": chars,
                            "spans": spans})
    return out


def _txt(chars):
    return "".join(c[0] for c in chars)


def _join_lines(teile):
    """Fügt Zeilen zu Fließtext zusammen (Trennstrich am Zeilenende vor Kleinbuchstaben
    wird aufgelöst)."""
    out = ""
    for t in teile:
        if not t:
            continue
        if not out:
            out = t
        elif len(out) > 1 and out.endswith("-") and out[-2].isalpha() and t[:1].islower():
            out = out[:-1] + t
        else:
            out = out + (" " if not out.endswith(" ") else "") + t.lstrip()
    return out


def plan_range(page, rects):
    """Bereitet das Ersetzen eines markierten Bereichs vor (auch über mehrere
    Textabschnitte und Zeilen eines Absatzes). Wirft ValueError mit Erklärung."""
    lines = _line_records(page)

    def line_of(r):
        c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        best, bd = None, None
        for i, ln in enumerate(lines):
            b = ln["bbox"]
            if b.y0 - 1 <= c.y <= b.y1 + 1 and b.x0 - 2 <= c.x <= b.x1 + 2:
                dd = abs((b.y0 + b.y1) / 2 - c.y)
                if bd is None or dd < bd:
                    best, bd = i, dd
        return best

    l0, l1 = line_of(rects[0]), line_of(rects[-1])
    if l0 is None or l1 is None:
        raise ValueError("Der markierte Bereich liegt nicht auf änderbarem Text.")
    if lines[l0]["block"] != lines[l1]["block"] or l1 < l0:
        raise ValueError("Die Markierung reicht über mehrere Absätze bzw. Textblöcke. "
                         "Bitte nur innerhalb eines Absatzes markieren.")
    c0, c1 = lines[l0]["chars"], lines[l1]["chars"]
    s = next((i for i, c in enumerate(c0) if (c[1].x0 + c[1].x1) / 2 >= rects[0].x0 - 0.5), None)
    e = next((i for i in range(len(c1) - 1, -1, -1)
              if (c1[i][1].x0 + c1[i][1].x1) / 2 <= rects[-1].x1 + 0.5), None)
    if s is None or e is None or (l0 == l1 and e < s):
        raise ValueError("Die Markierung enthält keinen änderbaren Text.")
    blk = lines[l0]["block"]
    last = l1
    while last + 1 < len(lines) and lines[last + 1]["block"] == blk:
        last += 1
    if l0 == l1:
        old = _txt(c0[s:e + 1])
    else:
        old = _join_lines([_txt(c0[s:])] + [_txt(lines[k]["chars"]) for k in range(l0 + 1, l1)]
                          + [_txt(c1[:e + 1])])
    prefix = _txt(c0[:s])
    rest = [_txt(c1[e + 1:])] + [_txt(lines[k]["chars"]) for k in range(l1 + 1, last + 1)]
    suffix = _join_lines(rest) if rest[0] else _join_lines(rest[1:])
    joiner = " " if (not rest[0] and suffix) else ""
    span = lines[l0]["spans"][c0[min(s, len(c0) - 1)][2]]
    first_span = lines[l0]["spans"][0]
    size = span["size"]
    base0 = first_span["origin"].y
    if l0 + 1 <= last:
        x_left = lines[l0 + 1]["spans"][0]["origin"].x
        leading = lines[l0 + 1]["spans"][0]["origin"].y - base0
        if leading < size * 0.5:
            leading = size * 1.2
    else:
        x_left, leading = first_span["origin"].x, size * 1.2
    erase = []
    for k in range(l0, last + 1):
        b = fitz.Rect(lines[k]["chars"][0][1])
        for ch in lines[k]["chars"]:
            b |= ch[1]
        erase.append(b)
    right = max(b.x1 for b in erase)
    return {"old": old, "prefix": prefix, "suffix": suffix, "joiner": joiner,
            "x_first": first_span["origin"].x, "base0": base0, "x_left": x_left,
            "leading": leading, "right": right, "span": span, "erase": erase,
            "n_old": last - l0 + 1}


def wrap_variable(text, font, size, w_first, w_rest):
    """Greedy-Umbruch; die erste Zeile darf schmaler/breiter sein als die übrigen."""
    zeilen, akt = [], ""
    for wort in text.split(" "):
        lim = w_first if not zeilen else w_rest
        probe = wort if not akt else akt + " " + wort
        if not akt or font.text_length(probe, fontsize=size) <= lim:
            akt = probe
        else:
            zeilen.append(akt)
            akt = wort
    zeilen.append(akt)
    return zeilen


def apply_range(page, plan, neu):
    """Ersetzt den vorbereiteten Bereich durch neu; der Rest des Absatzes wird neu
    umbrochen. Liefert (neue Zeilenzahl, alte Zeilenzahl)."""
    farben = [sample_background(page, b) for b in plan["erase"]]
    for b, bg in zip(plan["erase"], farben):
        rot = fitz.Rect(b.x0 - 0.3, b.y0 + b.height * 0.12, b.x1 + 0.3, b.y1 - b.height * 0.12)
        page.add_redact_annot(_unrot_rect(page, rot), fill=bg)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                          graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    sp = plan["span"]
    fam, fett, kursiv = match_font(sp["font"], sp["flags"])
    pfad = font_file(fam, fett, kursiv)
    f = _font_obj(pfad)
    size = sp["size"]
    voll = plan["prefix"] + neu + plan["joiner"] + plan["suffix"]
    w_first = plan["right"] - plan["x_first"]
    w_rest = plan["right"] - plan["x_left"]
    y, n, erste = plan["base0"], 0, True
    for absatz in voll.split("\n"):
        for k, ln in enumerate(wrap_variable(absatz, f, size, w_first if erste else w_rest, w_rest)):
            x = plan["x_first"] if (erste and k == 0) else plan["x_left"]
            if ln.strip():
                draw_text_lines(page, x, y, [ln], fam, fett, kursiv, size, sp["color"])
            y += plan["leading"]
            n += 1
        erste = False
    return n, plan["n_old"]


def iter_spans(page):
    """Alle waagerechten Textabschnitte der Seite (Anzeigekoordinaten), Format wie find_span."""
    rm = page.rotation_matrix
    mrot = fitz.Matrix(rm.a, rm.b, rm.c, rm.d, 0, 0)
    d = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1, 0))
            v = fitz.Point(dx, dy) * mrot
            if abs(v.y) > 0.1 or v.x < 0:
                continue
            for s in line.get("spans", []):
                if not s.get("text", "").strip():
                    continue
                b = fitz.Rect(s["bbox"])
                box = fitz.Rect(fitz.Point(b.x0, b.y0) * rm, fitz.Point(b.x1, b.y1) * rm).normalize()
                c = s.get("color", 0)
                yield {"text": s["text"], "bbox": box,
                       "origin": tuple(fitz.Point(s["origin"]) * rm),
                       "size": float(s["size"]), "font": s.get("font", ""),
                       "flags": int(s.get("flags", 0)),
                       "color": ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)}


def replace_spans(page, jobs):
    """jobs: Liste (info, neuer_text). Entfernt die alten Abschnitte (Redaktion mit
    Hintergrundfarbe) und setzt die neuen in der ähnlichsten Schrift."""
    if not jobs:
        return
    farben = [sample_background(page, info["bbox"]) for info, _t in jobs]
    for (info, _t), bg in zip(jobs, farben):
        b = fitz.Rect(info["bbox"])
        rot = fitz.Rect(b.x0 + 0.3, b.y0 + b.height * 0.12, b.x1 - 0.3, b.y1 - b.height * 0.12)
        page.add_redact_annot(_unrot_rect(page, rot), fill=bg)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                          graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    for info, neu in jobs:
        if neu.strip():
            fam, fett, kursiv = match_font(info["font"], info["flags"])
            draw_text_lines(page, info["origin"][0], info["origin"][1], [neu], fam,
                            fett, kursiv, info["size"], info["color"])


def query_pattern(query):
    return re.compile(re.escape(query), re.I)


def replace_all_on_page(page, query, repl):
    """Ersetzt query (ohne Groß-/Kleinschreibung) in allen Textabschnitten der Seite.
    Liefert die Zahl der ersetzten Stellen."""
    pat = query_pattern(query)
    jobs, n = [], 0
    for info in iter_spans(page):
        k = len(pat.findall(info["text"]))
        if k:
            n += k
            jobs.append((info, pat.sub(lambda m: repl, info["text"])))
    replace_spans(page, jobs)
    return n


def sample_background(page, rect):
    """Hintergrundfarbe (Median) im Bereich rect."""
    clip = fitz.Rect(rect) & page.rect
    if clip.is_empty:
        return (1, 1, 1)
    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), clip=clip,
                          colorspace=fitz.csRGB, alpha=False)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(-1, 3)
    return tuple(float(v) / 255 for v in np.median(a, axis=0))


def replace_text(page, info, neu, family, bold, italic, size, color):
    """Ersetzt den Textabschnitt info durch neu: Altes wird entfernt (Redaktion
    mit Hintergrundfarbe), Neues an derselben Grundlinie gesetzt."""
    bg = sample_background(page, info["bbox"])
    b = fitz.Rect(info["bbox"])
    rot = fitz.Rect(b.x0 + 0.3, b.y0 + b.height * 0.12,
                    b.x1 - 0.3, b.y1 - b.height * 0.12)
    page.add_redact_annot(_unrot_rect(page, rot), fill=bg)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                          graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    if neu.strip():
        draw_text_lines(page, info["origin"][0], info["origin"][1], [neu],
                        family, bold, italic, size, color)


# ---------------------------------------------------------------------------
# Rückgängig über Dateigröße
# ---------------------------------------------------------------------------

def truncate_file(path, size):
    with open(path, "r+b") as f:
        f.truncate(size)


def snapshot_copy(path):
    d = Path(tempfile.mkdtemp(prefix="pdfanno_"))
    ziel = d / "vorher.pdf"
    shutil.copy2(path, ziel)
    return ziel
