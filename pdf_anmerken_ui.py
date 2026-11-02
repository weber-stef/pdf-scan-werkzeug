# -*- coding: utf-8 -*-
"""
Bedienoberfläche der Werkzeuge zum Kommentieren und Ändern für den PDF-Betrachter (Mixin).
Die eigentliche Zeichenlogik steht in pdf_anmerken.py.
"""

import math
import os
import shutil
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk, colorchooser, messagebox

import fitz

from pdf_anmerken import (add_arrow, add_shape, add_text, edit_text, find_span,
                          delete_record, font_families, hex_to_rgb, hit_record, load_records,
                          match_font, move_record, record_box, record_display,
                          replace_text, restyle_shape, rgb_to_hex, shape_points,
                          snapshot_copy, burned_text_at, delete_burned)

TITEL = "PDF-Scan-Werkzeug"
GELB = (1.0, 0.97, 0.6)
# True = vor der ersten Änderung einer Datei einmal „<Name>_vor-Aenderung.pdf“ danebenlegen.
# Aus: Änderungen sind trotzdem mit Strg+Z rückgängig zu machen, solange das Fenster offen ist.
SICHERUNGSKOPIE = False
WERKZEUGE = (("↖ Auswählen", "pick"), ("I Text markieren", "select"),
             ("T Schreibmaschine", "text"),
             ("➚ Pfeil", "arrow"), ("▭ Rechteck", "rect"),
             ("◯ Kreis / Ellipse", "oval"), ("✎ Text ändern", "edit"))
HINWEISE = {
    "pick": "Auswählen: Kommentar, Pfeil oder Form anklicken – Entf löscht, Ziehen verschiebt, Ecken/Enden ziehen ändert die Größe, Doppelklick auf einen Text (oder Enter) bearbeitet ihn",
    "select": "Text markieren: Ziehen mit gedrückter Maustaste (ein Klick auf einen Kommentar wählt ihn aus, Entf löscht)",
    "text": "Schreibmaschine: klicken oder Rahmen aufziehen, tippen – Strg+Enter übernimmt, Esc bricht ab",
    "arrow": "Pfeil: vom Kommentar aus zum Bereich ziehen (Spitze am Ende); Umschalt = gerade Linie. Vorhandenes anfassen und ziehen verschiebt es",
    "rect": "Rechteck: Bereich aufziehen (Umschalt = Quadrat)",
    "oval": "Kreis: Bereich aufziehen (Umschalt = Kreis statt Ellipse)",
    "edit": "Text ändern: auf einen Text klicken (Umschalt+Klick = ganze Zeile)",
}
CURSOREN = {"pick": "arrow", "select": "xterm", "text": "xterm", "arrow": "crosshair",
            "rect": "crosshair", "oval": "crosshair", "edit": "hand2"}


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
    return path


def _num(var, default, lo, hi):
    try:
        v = float(str(var.get()).replace(",", "."))
    except (ValueError, tk.TclError):
        return default
    return max(lo, min(hi, v))


class TextEditDialog(tk.Toplevel):
    """Fragt den neuen Wortlaut samt Schrift für einen vorhandenen Text ab."""

    def __init__(self, master, info, family, bold, italic):
        super().__init__(master)
        self.title("Text ändern")
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        self.color = info["color"]
        pad = {"padx": 8, "pady": 4}
        ttk.Label(self, text=f"Ursprüngliche Schrift: {info['font'] or '?'}, "
                             f"{info['size']:.1f} pt", foreground="#606060").grid(
            row=0, column=0, columnspan=6, sticky="w", **pad)
        self.var_text = tk.StringVar(value=info["text"])
        self.ent = ttk.Entry(self, textvariable=self.var_text, width=60)
        self.ent.grid(row=1, column=0, columnspan=6, sticky="ew", **pad)

        self.var_font = tk.StringVar(value=family)
        ttk.Label(self, text="Schrift:").grid(row=2, column=0, sticky="e", **pad)
        ttk.Combobox(self, textvariable=self.var_font, values=font_families(),
                     width=24, state="readonly").grid(row=2, column=1, columnspan=2,
                                                      sticky="w", **pad)
        self.var_size = tk.StringVar(value=f"{info['size']:.1f}")
        ttk.Label(self, text="Größe:").grid(row=2, column=3, sticky="e", **pad)
        ttk.Spinbox(self, textvariable=self.var_size, from_=3, to=300, increment=0.5,
                    width=6).grid(row=2, column=4, sticky="w", **pad)
        self.var_b = tk.BooleanVar(value=bold)
        self.var_i = tk.BooleanVar(value=italic)
        ttk.Checkbutton(self, text="Fett", variable=self.var_b).grid(row=3, column=1, sticky="w", **pad)
        ttk.Checkbutton(self, text="Kursiv", variable=self.var_i).grid(row=3, column=2, sticky="w", **pad)
        ttk.Label(self, text="Farbe:").grid(row=3, column=3, sticky="e", **pad)
        self.btn_col = tk.Button(self, width=4, relief="groove",
                                 bg=rgb_to_hex(self.color), command=self._pick)
        self.btn_col.grid(row=3, column=4, sticky="w", **pad)
        bt = ttk.Frame(self)
        bt.grid(row=4, column=0, columnspan=6, sticky="e", **pad)
        ttk.Button(bt, text="Abbrechen", command=self.destroy).pack(side="right")
        ttk.Button(bt, text="Übernehmen", command=self._ok).pack(side="right", padx=6)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.ent.focus_set()
        self.ent.selection_range(0, "end")
        self.grab_set()

    def _pick(self):
        c = colorchooser.askcolor(color=rgb_to_hex(self.color), parent=self)
        if c and c[1]:
            self.color = hex_to_rgb(c[1])
            self.btn_col.config(bg=c[1])

    def _ok(self):
        self.result = {"text": self.var_text.get(), "family": self.var_font.get(),
                       "bold": self.var_b.get(), "italic": self.var_i.get(),
                       "size": _num(self.var_size, 12, 3, 300), "color": self.color}
        self.destroy()


class AnnotMixin:
    """Werkzeuge zum Kommentieren und Ändern für den PdfViewer. Erwartet: doc, path, canvas,
    page_no, _render_scale, _img_offset, _canvas_to_page, _page_to_canvas,
    _thumbs, _thumb_draw_slot, _thumb_chain, _thumb_gen, _lines_cache,
    release_document, _render, _clear_selection, sel_status."""

    # ----- Aufbau -----

    def _anno_init(self):
        self.tool = "select"
        self.var_tool = tk.StringVar(value="select")
        self.anno_color = (0.85, 0.0, 0.0)
        fams = font_families()
        self.var_font = tk.StringVar(value="Arial" if "Arial" in fams else (fams[0] if fams else "Arial"))
        self.var_size = tk.StringVar(value="14")
        self.var_bold = tk.BooleanVar(value=False)
        self.var_italic = tk.BooleanVar(value=False)
        self.var_hint = tk.BooleanVar(value=False)
        self.var_linew = tk.StringVar(value="2")
        self.var_filled = tk.BooleanVar(value=False)
        self._anno_editor = None
        self._anno_start = None
        self._anno_move = None
        self._anno_sel = None          # ausgewählter Pfeil/ausgewählte Form (Datensatz)
        self._anno_handle = None
        self._anno_width_job = None
        self.var_linew.trace_add("write", self._anno_width_trace)
        self._anno_undo = []
        self._anno_backup_done = False
        self._anno_backup_name = None

    def _build_anno_bar(self, after):
        self._anno_after = after
        self.anno_bar = ttk.Frame(self, padding=(6, 0, 6, 4))
        r1 = ttk.Frame(self.anno_bar)
        r1.pack(fill="x")
        for text, val in WERKZEUGE:
            ttk.Radiobutton(r1, text=text, value=val, variable=self.var_tool,
                            style="Toolbutton", command=self._tool_changed
                            ).pack(side="left", padx=(0, 3))
        self.btn_undo = ttk.Button(r1, text="↶ Rückgängig (Strg+Z)", command=self.anno_undo,
                                   state="disabled")
        self.btn_undo.pack(side="right")
        r2 = ttk.Frame(self.anno_bar)
        r2.pack(fill="x", pady=(4, 0))
        ttk.Label(r2, text="Farbe:").pack(side="left")
        self.btn_color = tk.Button(r2, width=4, relief="groove",
                                   bg=rgb_to_hex(self.anno_color), command=self._pick_color)
        self.btn_color.pack(side="left", padx=(3, 10))
        ttk.Label(r2, text="Schrift:").pack(side="left")
        ttk.Combobox(r2, textvariable=self.var_font, values=font_families(), width=22,
                     state="readonly").pack(side="left", padx=(3, 6))
        ttk.Label(r2, text="Größe:").pack(side="left")
        ttk.Spinbox(r2, textvariable=self.var_size, from_=4, to=200, increment=1,
                    width=5).pack(side="left", padx=(3, 6))
        ttk.Checkbutton(r2, text="F", variable=self.var_bold, style="Toolbutton",
                        width=2).pack(side="left")
        ttk.Checkbutton(r2, text="K", variable=self.var_italic, style="Toolbutton",
                        width=2).pack(side="left", padx=(2, 6))
        ttk.Checkbutton(r2, text="Gelb hinterlegt", variable=self.var_hint
                        ).pack(side="left", padx=(0, 10))
        ttk.Label(r2, text="Linienstärke:").pack(side="left")
        ttk.Spinbox(r2, textvariable=self.var_linew, from_=0.5, to=30, increment=0.5,
                    width=5).pack(side="left", padx=(3, 6))
        ttk.Checkbutton(r2, text="Füllung (durchscheinend)", variable=self.var_filled,
                        command=self._anno_style_changed).pack(side="left")

    def toggle_anno_bar(self):
        if self.anno_bar.winfo_ismapped():
            self.var_tool.set("select")
            self._tool_changed()
            self.anno_bar.pack_forget()
        else:
            self.anno_bar.pack(side="top", fill="x", after=self._anno_after)

    def _pick_color(self):
        c = colorchooser.askcolor(color=rgb_to_hex(self.anno_color), parent=self)
        if c and c[1]:
            self.anno_color = hex_to_rgb(c[1])
            self.btn_color.config(bg=c[1])
            self._anno_style_changed()

    def _tool_changed(self):
        self._anno_commit_text()
        self.tool = self.var_tool.get()
        self.canvas.config(cursor=CURSOREN.get(self.tool, "xterm"))
        self._link_cursor = False
        self._hover_cur = CURSOREN.get(self.tool, "xterm")
        self.canvas.delete("anno_tmp")
        if self.tool in ("select", "pick"):
            self._anno_select(None)
        else:
            self._clear_selection()
        self.sel_status.config(text=HINWEISE[self.tool], foreground="#606060")

    def _typing(self):
        try:
            w = self.focus_get()
        except Exception:
            return False
        return isinstance(w, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text, ttk.Spinbox))

    # ----- Maus -----

    def _anno_busy(self):
        return bool(self._anno_move or self._anno_handle or self._anno_editor)

    def _anno_press(self, ev):
        """Liefert True, wenn der Druck etwas ergriffen hat; False nur im Werkzeug
        „Text ändern“, wenn nichts Eigenes unter der Maus liegt (dann markiert der
        Betrachter Text)."""
        if self._anno_editor:
            self._anno_commit_text()
            return True
        cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
        start = self._canvas_to_page(cx, cy)
        if self.doc:
            page = self.doc[self.page_no]
            sel = self._anno_selected_rec()
            if sel:
                hs = self._anno_handles(page, sel)
                for i, h in enumerate(hs):
                    hx, hy = self._pt_canvas(h)
                    if math.hypot(cx - hx, cy - hy) <= 9:
                        self._anno_start = None
                        self._anno_handle = {"rec": sel, "i": i, "handles": hs}
                        return True
            rec = hit_record(page, start, 6.0 / max(self._render_scale, 0.05))
            if not rec:
                self._anno_select(None)
            if rec:
                self._anno_start = None
                self._anno_move = {"rec": rec, "start": start,
                                   "box": record_box(page, rec),
                                   "xy": (ev.x, ev.y), "moved": False}
                return True
        if self.tool in ("edit", "select"):
            self._anno_start = None
            return False
        if self.tool == "pick":
            self._anno_start = None      # nichts getroffen: nur Auswahl aufheben
            if self.doc:
                fest = burned_text_at(self.doc[self.page_no], start)
                if fest:
                    self._anno_select(fest)
            return True
        self._anno_start = start
        return True

    def _key_enter(self, _ev=None):
        """Enter öffnet den ausgewählten Text-Kommentar zum Bearbeiten."""
        if self._typing() or self._anno_editor:
            return
        rec = self._anno_selected_rec()
        if rec and rec.get("kind", "text") == "text":
            self._anno_edit_record(rec)

    def _anno_hit_at(self, ev):
        """Kommentar unter dem Mauszeiger (für den Zeiger) oder None."""
        if not self.doc or self._anno_busy():
            return None
        try:
            cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
            return hit_record(self.doc[self.page_no], self._canvas_to_page(cx, cy),
                              6.0 / max(self._render_scale, 0.05))
        except Exception:
            return None

    def _anno_routing(self):
        """True, wenn Maus-Ereignisse an die Anmerkungswerkzeuge gehen (Zeichnen,
        Verschieben, Anfasser); „Markieren“ und „Text ändern“ lassen sonst dem
        Betrachter die Textmarkierung."""
        if self._anno_busy():
            return True
        return self.tool not in ("select", "edit", "pick")

    def _anno_end_point(self, ev):
        cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
        p0 = self._anno_start
        p1 = self._canvas_to_page(cx, cy)
        if ev.state & 1:
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            if self.tool in ("rect", "oval"):
                m = max(abs(dx), abs(dy))
                p1 = (p0[0] + math.copysign(m, dx or 1), p0[1] + math.copysign(m, dy or 1))
            elif self.tool == "arrow":
                p1 = (p1[0], p0[1]) if abs(dx) >= abs(dy) else (p0[0], p1[1])
        return p0, p1

    def _pt_canvas(self, p):
        ox, oy = self._img_offset
        return ox + p[0] * self._render_scale, oy + p[1] * self._render_scale

    def _anno_move_delta(self, ev):
        m = self._anno_move
        cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
        p = self._canvas_to_page(cx, cy)
        page = self.doc[self.page_no]
        dx, dy = p[0] - m["start"][0], p[1] - m["start"][1]
        box = m["box"]
        # innerhalb der Seite halten
        dx = max(-box.x0, min(page.rect.width - box.x1, dx))
        dy = max(-box.y0, min(page.rect.height - box.y1, dy))
        return dx, dy

    def _anno_handle_pts(self, h, ev):
        cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
        p = self._canvas_to_page(cx, cy)
        r = self.doc[self.page_no].rect
        p = (max(0.0, min(r.width, p[0])), max(0.0, min(r.height, p[1])))
        hs = h["handles"]
        if h["rec"]["kind"] == "arrow":
            pts = [(hs[0].x, hs[0].y), (hs[1].x, hs[1].y)]
            pts[h["i"]] = p
            return pts
        o = hs[(h["i"] + 2) % 4]
        return [p, (o.x, o.y)]

    def _anno_drag(self, ev):
        h = self._anno_handle
        if h:
            pts = self._anno_handle_pts(h, ev)
            c0, c1 = self._pt_canvas(pts[0]), self._pt_canvas(pts[1])
            self.canvas.delete("anno_tmp")
            kind = h["rec"]["kind"]
            col = rgb_to_hex(h["rec"]["color"])
            sw = max(1.0, h["rec"]["width"] * self._render_scale)
            if kind == "arrow":
                self.canvas.create_line(*c0, *c1, fill=col, width=sw, arrow="last",
                                        arrowshape=(sw * 4 + 8, sw * 4 + 8, sw * 1.8 + 3),
                                        tags="anno_tmp")
            elif kind == "oval":
                self.canvas.create_oval(*c0, *c1, outline=col, width=sw, tags="anno_tmp")
            else:
                self.canvas.create_rectangle(*c0, *c1, outline=col, width=sw, tags="anno_tmp")
            return
        m = self._anno_move
        if m:
            if not m["moved"] and max(abs(ev.x - m["xy"][0]), abs(ev.y - m["xy"][1])) < 5:
                return
            m["moved"] = True
            dx, dy = self._anno_move_delta(ev)
            b = m["box"]
            c0 = self._pt_canvas((b.x0 + dx, b.y0 + dy))
            c1 = self._pt_canvas((b.x1 + dx, b.y1 + dy))
            self.canvas.delete("anno_tmp")
            self.canvas.create_rectangle(*c0, *c1, outline="#4da3ff", width=2,
                                         dash=(5, 3), tags="anno_tmp")
            self.canvas.config(cursor="fleur")
            return
        if not self._anno_start:
            return
        p0, p1 = self._anno_end_point(ev)
        c0, c1 = self._pt_canvas(p0), self._pt_canvas(p1)
        self.canvas.delete("anno_tmp")
        col = rgb_to_hex(self.anno_color)
        sw = max(1.0, _num(self.var_linew, 2, 0.5, 30) * self._render_scale)
        if self.tool == "arrow":
            self.canvas.create_line(*c0, *c1, fill=col, width=sw, arrow="last",
                                    arrowshape=(sw * 4 + 8, sw * 4 + 8, sw * 1.8 + 3),
                                    tags="anno_tmp")
        elif self.tool == "oval":
            self.canvas.create_oval(*c0, *c1, outline=col, width=sw, tags="anno_tmp")
        elif self.tool == "rect":
            self.canvas.create_rectangle(*c0, *c1, outline=col, width=sw, tags="anno_tmp")
        elif self.tool == "text":
            self.canvas.create_rectangle(*c0, *c1, outline="#4da3ff", dash=(4, 3),
                                         tags="anno_tmp")

    def _anno_release(self, ev):
        h, self._anno_handle = self._anno_handle, None
        if h:
            self.canvas.delete("anno_tmp")
            if self.doc:
                pts = self._anno_handle_pts(h, ev)
                self._anno_restyle(h["rec"], "Größe geändert", pts=pts)
            return
        m, self._anno_move = self._anno_move, None
        if m:
            self.canvas.delete("anno_tmp")
            self.canvas.config(cursor=CURSOREN.get(self.tool, "xterm"))
            self._hover_cur = None
            if not self.doc:
                return
            if not m["moved"]:
                jetzt = time.monotonic()
                letzter = getattr(self, "_anno_klick", None)
                self._anno_klick = (jetzt, m["rec"]["id"])
                doppelt = bool(letzter and letzter[1] == m["rec"]["id"]
                               and jetzt - letzter[0] < 0.5)
                if m["rec"].get("kind", "text") == "text" and doppelt:
                    # Doppelklick auf einen Text: bearbeiten (ein einfacher Klick wählt nur aus)
                    self._anno_klick = None
                    self._anno_edit_record(m["rec"])
                else:
                    self._anno_select(m["rec"])
                return
            self._anno_move = m
            dx, dy = self._anno_move_delta(ev)
            self._anno_move = None
            rec = m["rec"]
            if abs(dx) < 0.5 and abs(dy) < 0.5:
                return
            neu = {}

            def job(pg):
                neu["rec"] = move_record(pg, rec, dx, dy)
            if self._anno_do(job, self.page_no, {"text": "Text", "arrow": "Pfeil"}.get(
                    rec.get("kind", "text"), "Form") + " verschoben"):
                if neu.get("rec"):
                    self._anno_select(neu["rec"])
            return
        if not self._anno_start or not self.doc:
            self._anno_start = None
            return
        p0, p1 = self._anno_end_point(ev)
        self._anno_start = None
        self.canvas.delete("anno_tmp")
        pno = self.page_no
        farbe = self.anno_color
        w = _num(self.var_linew, 2, 0.5, 30)
        if self.tool == "text":
            x0, x1 = sorted((p0[0], p1[0]))
            y0 = min(p0[1], p1[1])
            if (x1 - x0) * self._render_scale < 12:
                x0 = p0[0]
                y0 = p0[1]
                breite = max(40.0, min(260.0, self.doc[pno].rect.width - x0 - 12))
            else:
                breite = x1 - x0
            self._anno_open_editor((x0, y0), breite)
        elif self.tool == "arrow":
            def job(pg):
                if not add_arrow(pg, p0, p1, farbe, w):
                    raise ValueError("Der Pfeil ist zu kurz.")
            self._anno_do(job, pno, "Pfeil gesetzt")
        elif self.tool in ("rect", "oval"):
            kind, gefuellt = self.tool, self.var_filled.get()

            def job(pg):
                if not add_shape(pg, kind, p0, p1, farbe, w, gefuellt):
                    raise ValueError("Der Bereich ist zu klein.")
            self._anno_do(job, pno, "Rechteck gesetzt" if kind == "rect" else "Kreis gesetzt")

    # ----- Auswahl von Pfeilen und Formen -----

    def _anno_selected_rec(self):
        sel = self._anno_sel
        if not sel or not self.doc:
            return None
        if sel.get("kind") == "burned":
            return sel
        for r in load_records(self.doc[self.page_no]):
            if r["id"] == sel["id"]:
                return r
        return None

    def _anno_handles(self, page, rec):
        if rec.get("kind", "text") in ("text", "burned"):
            return []          # Texte haben keine Anfasser (Verschieben per Ziehen)
        pts = shape_points(page, rec)
        if rec["kind"] == "arrow":
            return pts
        x0, x1 = sorted((pts[0].x, pts[1].x))
        y0, y1 = sorted((pts[0].y, pts[1].y))
        return [fitz.Point(x0, y0), fitz.Point(x1, y0), fitz.Point(x1, y1), fitz.Point(x0, y1)]

    def _anno_select(self, rec):
        """Wählt einen Pfeil/eine Form aus (None = Auswahl aufheben) und holt
        deren Farbe, Stärke und Füllung in die Leiste."""
        self._anno_sel = rec
        if rec and rec.get("kind") == "burned":
            self.sel_status.config(
                text="Fester Text im Seiteninhalt ausgewählt: Entf löscht die Zeile (der Scan "
                     "bleibt), Strg+Z macht es rückgängig", foreground="#606060")
        elif rec and rec.get("kind", "text") == "text":
            self.sel_status.config(
                text="Text ausgewählt: Entf löscht ihn, Ziehen verschiebt, Doppelklick oder "
                     "Enter bearbeitet, Esc hebt die Auswahl auf", foreground="#606060")
        elif rec:
            self.anno_color = tuple(rec["color"])
            self.btn_color.config(bg=rgb_to_hex(self.anno_color))
            self.var_linew.set(f"{rec['width']:g}")
            self.var_filled.set(rec["filled"])
            self.sel_status.config(
                text="Ausgewählt: Ecken/Enden ziehen = Größe, Farbe/Stärke/Füllung oben "
                     "ändern, Entf löscht, Esc hebt die Auswahl auf", foreground="#606060")
        self._anno_draw_sel()

    def _anno_draw_sel(self):
        self.canvas.delete("anno_sel")
        if not self._anno_sel:
            return
        rec = self._anno_selected_rec()
        if not rec:
            self._anno_sel = None
            return
        page = self.doc[self.page_no]
        box = record_box(page, rec)
        c0, c1 = self._pt_canvas((box.x0, box.y0)), self._pt_canvas((box.x1, box.y1))
        self.canvas.create_rectangle(*c0, *c1, outline="#4da3ff", dash=(4, 3),
                                     tags="anno_sel")
        for h in self._anno_handles(page, rec):
            x, y = self._pt_canvas(h)
            self.canvas.create_rectangle(x - 5, y - 5, x + 5, y + 5, fill="#ffffff",
                                         outline="#1a73e8", width=2, tags="anno_sel")

    def _anno_width_trace(self, *_):
        if self._anno_width_job:
            self.after_cancel(self._anno_width_job)
        self._anno_width_job = self.after(600, self._anno_style_changed)

    def _anno_style_changed(self):
        self._anno_width_job = None
        rec = self._anno_selected_rec()
        if not rec or rec.get("kind", "text") in ("text", "burned"):
            return
        farbe = self.anno_color
        w = _num(self.var_linew, rec["width"], 0.5, 30)
        gef = self.var_filled.get()
        if (tuple(round(c, 3) for c in farbe) == tuple(round(c, 3) for c in rec["color"])
                and abs(w - rec["width"]) < 1e-6 and gef == rec["filled"]):
            return
        self._anno_restyle(rec, "Darstellung geändert", color=farbe, width=w, filled=gef)

    def _anno_restyle(self, rec, meldung, **kw):
        neu = {}

        def job(pg):
            neu["rec"] = restyle_shape(pg, rec, **kw)
        if self._anno_do(job, self.page_no, meldung) and neu.get("rec"):
            self._anno_select(neu["rec"])

    def _anno_delete_selected(self):
        rec = self._anno_selected_rec()
        if rec and rec.get("kind") == "burned":
            if self._anno_do(lambda pg: delete_burned(pg, rec), self.page_no,
                             "Fester Text gelöscht"):
                self._anno_select(None)
            return
        if rec and self._anno_do(lambda pg: delete_record(pg, rec), self.page_no,
                                 "Text gelöscht" if rec.get("kind", "text") == "text"
                                 else "Objekt gelöscht"):
            self._anno_select(None)

    # ----- Schreibmaschine -----

    def _anno_edit_record(self, rec):
        """Öffnet einen früher gesetzten Text wieder zum Bearbeiten."""
        page = self.doc[self.page_no]
        pos, box = record_display(page, rec)
        self.var_font.set(rec["family"])
        self.var_size.set(f"{rec['size']:g}")
        self.var_bold.set(rec["bold"])
        self.var_italic.set(rec["italic"])
        self.var_hint.set(rec["fill"])
        self.anno_color = tuple(rec["color"])
        self.btn_color.config(bg=rgb_to_hex(self.anno_color))
        self._anno_open_editor((pos.x, pos.y), rec["width"] or box.width,
                               rec=rec, text=rec["text"], min_h=box.height)
        self.sel_status.config(
            text="Text bearbeiten – Schrift/Farbe oben ändern, Strg+Enter übernimmt, "
                 "Esc bricht ab, leerer Text löscht ihn", foreground="#606060")

    def _anno_open_editor(self, pos, breite, rec=None, text="", min_h=0.0):
        s = self._render_scale
        size = _num(self.var_size, 14, 4, 200)
        fam, bold, italic = self.var_font.get(), self.var_bold.get(), self.var_italic.get()
        f = tkfont.Font(family=fam, size=-max(4, round(size * s)),
                        weight="bold" if bold else "normal",
                        slant="italic" if italic else "roman")
        x, y = self._pt_canvas(pos)
        txt = tk.Text(self.canvas, wrap="word", font=f, fg=rgb_to_hex(self.anno_color),
                      bg="#ffffe0" if self.var_hint.get() else "#ffffff",
                      insertbackground=rgb_to_hex(self.anno_color), borderwidth=0,
                      highlightthickness=1, highlightbackground="#4da3ff",
                      highlightcolor="#4da3ff", padx=0, pady=0, undo=True)
        ls = f.metrics("linespace")
        if text:
            txt.insert("1.0", text)
        mh = min_h * s
        win = self.canvas.create_window(x, y, window=txt, anchor="nw",
                                        width=max(30, breite * s),
                                        height=max(ls * 2 + 4, mh))
        self._anno_editor = {"w": txt, "win": win, "pno": self.page_no, "pos": pos,
                             "width": breite, "family": fam, "bold": bold,
                             "italic": italic, "size": size, "color": self.anno_color,
                             "hint": self.var_hint.get(), "rec": rec}

        def regrow(_e=None):
            try:
                n = txt.count("1.0", "end-1c", "displaylines")
                n = max(2, (n[0] if n else 1) + 1)
                self.canvas.itemconfigure(win, height=max(n * ls + 4, mh))
            except tk.TclError:
                pass
        txt.bind("<KeyRelease>", regrow)
        txt.bind("<Control-Return>", lambda e: (self._anno_commit_text(), "break")[1])
        txt.bind("<Escape>", lambda e: (self._anno_cancel_editor(), "break")[1])
        txt.focus_set()

    def _anno_cancel_editor(self):
        ed, self._anno_editor = self._anno_editor, None
        if ed:
            try:
                self.canvas.delete(ed["win"])
                ed["w"].destroy()
            except tk.TclError:
                pass
            self.focus_set()

    def _anno_commit_text(self):
        ed = self._anno_editor
        if not ed:
            return
        self._anno_editor = None
        try:
            text = ed["w"].get("1.0", "end-1c").rstrip()
            self.canvas.delete(ed["win"])
            ed["w"].destroy()
        except tk.TclError:
            return
        self.focus_set()
        if not self.doc:
            return
        rec = ed.get("rec")
        if rec:
            # Bearbeiten: Schrift, Größe, Farbe gelten so, wie sie jetzt oben stehen
            fam, bold, italic = self.var_font.get(), self.var_bold.get(), self.var_italic.get()
            size = _num(self.var_size, rec["size"], 4, 200)
            self._anno_do(lambda pg: edit_text(
                pg, rec, text, fam, bold, italic, size, self.anno_color,
                width=ed["width"], fill=GELB if self.var_hint.get() else None),
                ed["pno"], "Text geändert" if text.strip() else "Text gelöscht")
            return
        if not text.strip():
            return
        self._anno_do(lambda pg: add_text(
            pg, ed["pos"], text, ed["family"], ed["bold"], ed["italic"], ed["size"],
            ed["color"], width=ed["width"], fill=GELB if ed["hint"] else None),
            ed["pno"], "Text gesetzt")

    # ----- Text ändern -----

    def _anno_edit_at(self, p, ganze_zeile):
        if not self.doc:
            return
        page = self.doc[self.page_no]
        try:
            info = find_span(page, p, ganze_zeile)
        except ValueError as exc:
            messagebox.showinfo(TITEL, str(exc), parent=self)
            return
        if not info:
            self.sel_status.config(
                text="Hier gibt es keinen Text. (Bei reinen Scans ohne Texterkennung "
                     "ist nichts änderbar – dort stattdessen die Schreibmaschine nehmen.)",
                foreground="#a00000")
            return
        fam, bold, italic = match_font(info["font"], info["flags"])
        dlg = TextEditDialog(self, info, fam, bold, italic)
        self.wait_window(dlg)
        r = dlg.result
        if r is None:
            return
        self._anno_do(lambda pg: replace_text(
            pg, info, r["text"], r["family"], r["bold"], r["italic"], r["size"],
            r["color"]), self.page_no, "Text geändert")

    # ----- Schreiben, Sichern, Rückgängig -----

    def _anno_begin(self):
        # Dauerhafte Sicherungskopie „…_vor-Aenderung.pdf“ nur auf ausdrücklichen Wunsch;
        # Strg+Z arbeitet immer mit Zwischenkopien im Temp-Ordner.
        if SICHERUNGSKOPIE and not self._anno_backup_done:
            sich = _unique(self.path.with_name(f"{self.path.stem}_vor-Aenderung.pdf"))
            shutil.copy2(self.path, sich)
            self._anno_backup_done = True
            self._anno_backup_name = sich.name
        return ("copy", snapshot_copy(self.path))

    def _anno_do(self, job, pno, meldung):
        if not self.doc:
            return False
        try:
            entry = self._anno_begin()
        except Exception as exc:
            messagebox.showerror(TITEL, f"Sicherung fehlgeschlagen:\n{exc}", parent=self)
            return False
        try:
            job(self.doc[pno])
            if self.doc.can_save_incrementally():
                self.doc.saveIncr()
            else:
                tmp = self.path.with_name(f"{self.path.stem}.anmerken-tmp.pdf")
                self.doc.save(tmp, garbage=3, deflate=True)
                self.release_document()
                os.replace(tmp, self.path)
                self.doc = fitz.open(self.path)
        except Exception as exc:
            try:
                self.release_document()
                shutil.copy2(entry[1], self.path)
                self.doc = fitz.open(self.path)
                self._anno_drop(entry)
            except Exception:
                pass
            self._render()
            messagebox.showerror(TITEL, f"Das ging nicht:\n{exc}", parent=self)
            return False
        self._anno_undo.append(entry + (pno,))
        self._anno_trim()
        self.btn_undo.config(state="normal")
        self._anno_refresh(pno)
        hinweis = meldung
        if self._anno_backup_name and len(self._anno_undo) == 1:
            hinweis += f" – Sicherung: {self._anno_backup_name}"
        self.sel_status.config(text="✓ " + hinweis, foreground="#107010")
        return True

    @staticmethod
    def _anno_drop(entry):
        try:
            shutil.rmtree(Path(entry[1]).parent, ignore_errors=True)
        except Exception:
            pass

    def _anno_trim(self, maxn=30, maxbytes=1_500_000_000):
        """Hält die Rückgängig-Kopien in Grenzen (Anzahl und Gesamtgröße)."""
        def gesamt():
            t = 0
            for e in self._anno_undo:
                try:
                    t += Path(e[1]).stat().st_size
                except OSError:
                    pass
            return t
        while len(self._anno_undo) > 1 and (len(self._anno_undo) > maxn or gesamt() > maxbytes):
            self._anno_drop(self._anno_undo.pop(0))

    def _anno_cleanup(self):
        for e in self._anno_undo:
            self._anno_drop(e)
        self._anno_undo.clear()

    def _anno_refresh(self, pno):
        self._lines_cache.pop(pno, None)
        self._search_invalidate()
        if 0 <= pno < len(self._thumbs):
            self._thumbs[pno] = None
            self._thumb_draw_slot(pno)
            self._thumb_gen += 1
            self._thumb_chain(self._thumb_gen)
        self._render()

    def anno_undo(self, _ev=None):
        if self._typing() or not self._anno_undo or not self.doc:
            return
        self._anno_commit_text()
        kind, wert, pno = self._anno_undo.pop()
        try:
            self.release_document()
            shutil.copy2(wert, self.path)
        except Exception as exc:
            messagebox.showerror(TITEL, f"Rückgängig fehlgeschlagen:\n{exc}", parent=self)
        self._anno_drop((kind, wert))
        self.doc = fitz.open(self.path)
        self.btn_undo.config(state="normal" if self._anno_undo else "disabled")
        self._anno_refresh(min(pno, len(self.doc) - 1))
        self.sel_status.config(text="↶ Rückgängig gemacht", foreground="#107010")
