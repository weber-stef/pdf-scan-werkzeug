# -*- coding: utf-8 -*-
"""
Beschneidungswerkzeug für den PDF-Betrachter (Mixin).

Rechteck auf der Seite aufziehen (Anfasser zum Nachziehen, Ziehen im Rechteck verschiebt),
„Zuschneiden“ setzt verlustfrei die CropBox der Seite – diese Seite oder alle Seiten.
Die Datei wird sofort geändert, Strg+Z macht es rückgängig (Mechanik von AnnotMixin._anno_do).
Hinweis: Die CropBox blendet den Rest nur aus, der Inhalt bleibt in der Datei.
"""

import tkinter as tk
from tkinter import ttk
from pdf_einstellungen import C_HINT, C_OK, C_ERR, C_WARN, C_TXT

import fitz

MM = 25.4 / 72.0
HINWEIS = ("Beschneiden: Rechteck auf der Seite aufziehen, Ecken/Kanten ziehen ändert es, "
           "Ziehen im Rechteck verschiebt – Enter schneidet zu, Esc hebt die Auswahl auf")
MIN_PX = 8


def display_to_cropbox(page, rect):
    """Rechteck in Anzeigekoordinaten (gedreht, Ursprung links oben an der sichtbaren Seite)
    -> CropBox in Koordinaten der MediaBox (ungedreht), wie set_cropbox sie erwartet."""
    rel = fitz.Rect(rect) * page.derotation_matrix
    pos = page.cropbox_position
    box = fitz.Rect(rel.x0 + pos.x, rel.y0 + pos.y, rel.x1 + pos.x, rel.y1 + pos.y)
    box.normalize()
    return box & page.mediabox


class CropMixin:
    """Erwartet: doc, path, page_no, canvas, sel_status, anno_bar, var_tool, _render_scale,
    _img_offset, _canvas_to_page, _anno_do, _anno_commit_text, _tool_changed,
    _thumbs_reset, _lines_cache, _search_invalidate, anno_undo."""

    # ----- Aufbau -----

    def _crop_init(self):
        self._crop_on = False
        self._crop_rect = None        # fitz.Rect in Anzeigekoordinaten der aktuellen Seite
        self._crop_mode = None        # None | ("new", start) | ("move", start, rect) | ("h", i, rect)
        self.var_crop_scope = tk.StringVar(value="page")

    def _build_crop_bar(self, after):
        self._crop_after = after
        self.crop_bar = ttk.Frame(self, padding=(6, 0, 6, 4))
        r = ttk.Frame(self.crop_bar)
        r.pack(fill="x")
        ttk.Radiobutton(r, text="Diese Seite", value="page",
                        variable=self.var_crop_scope).pack(side="left")
        ttk.Radiobutton(r, text="Alle Seiten (gleiche Anteile)", value="all",
                        variable=self.var_crop_scope).pack(side="left", padx=(8, 14))
        self.btn_crop_go = ttk.Button(r, text="✂ Zuschneiden (Enter)", command=self.crop_apply,
                                      state="disabled")
        self.btn_crop_go.pack(side="left")
        ttk.Button(r, text="Auswahl aufheben", command=self.crop_clear).pack(side="left", padx=6)
        ttk.Button(r, text="Zuschnitt entfernen", command=self.crop_reset).pack(side="left")
        ttk.Button(r, text="↶ Rückgängig (Strg+Z)", command=self.anno_undo).pack(side="right")
        self.lbl_crop = ttk.Label(self.crop_bar, text=HINWEIS, foreground=C_HINT)
        self.lbl_crop.pack(fill="x", pady=(3, 0))

    def toggle_crop_bar(self):
        if self._crop_on:
            self._crop_close()
            return
        if self.anno_bar.winfo_ismapped():
            self.toggle_anno_bar()
        self._anno_commit_text()
        self._clear_selection()
        self._anno_sel = None
        self._crop_on = True
        self.crop_bar.pack(side="top", fill="x", after=self._crop_after)
        self.canvas.config(cursor="crosshair")
        self._hover_cur = "crosshair"
        self.sel_status.config(text=HINWEIS, foreground=C_HINT)
        self._crop_update()

    def _crop_close(self):
        self._crop_on = False
        self._crop_rect = None
        self._crop_mode = None
        self.canvas.delete("crop_ov")
        self.crop_bar.pack_forget()
        self.canvas.config(cursor="xterm")
        self._hover_cur = "xterm"
        self.sel_status.config(text="Text markieren: Ziehen mit gedrückter Maustaste",
                               foreground=C_HINT)

    def crop_active(self):
        return self._crop_on

    # ----- Zeichnen -----

    def _crop_handles(self, r):
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        # 0..3 Ecken (oben links, oben rechts, unten rechts, unten links), 4..7 Kanten (o, r, u, l)
        return [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1),
                (cx, r.y0), (r.x1, cy), (cx, r.y1), (r.x0, cy)]

    def _crop_canvas(self, p):
        ox, oy = self._img_offset
        return ox + p[0] * self._render_scale, oy + p[1] * self._render_scale

    def _crop_draw(self):
        self.canvas.delete("crop_ov")
        r = self._crop_rect
        if not (self._crop_on and r and self.doc):
            return
        page = self.doc[self.page_no]
        px0, py0 = self._crop_canvas((0, 0))
        px1, py1 = self._crop_canvas((page.rect.width, page.rect.height))
        x0, y0 = self._crop_canvas((r.x0, r.y0))
        x1, y1 = self._crop_canvas((r.x1, r.y1))
        for a in ((px0, py0, px1, y0), (px0, y1, px1, py1), (px0, y0, x0, y1), (x1, y0, px1, y1)):
            self.canvas.create_rectangle(*a, fill="#000000", stipple="gray50", outline="",
                                         tags="crop_ov")
        self.canvas.create_rectangle(x0, y0, x1, y1, outline="#4da3ff", width=2, tags="crop_ov")
        for hx, hy in self._crop_handles(r):
            cx, cy = self._crop_canvas((hx, hy))
            self.canvas.create_rectangle(cx - 5, cy - 5, cx + 5, cy + 5, fill="#ffffff",
                                         outline="#1a73e8", width=2, tags="crop_ov")

    def _crop_update(self):
        r = self._crop_rect
        self.btn_crop_go.config(state="normal" if r else "disabled")
        if not (self.doc and self._crop_on):
            return
        page = self.doc[self.page_no]
        seite = f"Seite {page.rect.width * MM:.0f} × {page.rect.height * MM:.0f} mm"
        if r:
            self.lbl_crop.config(
                text=f"Auswahl {r.width * MM:.0f} × {r.height * MM:.0f} mm "
                     f"(Rand links {r.x0 * MM:.0f}, oben {r.y0 * MM:.0f}, "
                     f"rechts {(page.rect.width - r.x1) * MM:.0f}, "
                     f"unten {(page.rect.height - r.y1) * MM:.0f} mm) – {seite}",
                foreground=C_TXT)
        else:
            self.lbl_crop.config(text=HINWEIS + f"  [{seite}]", foreground=C_HINT)

    # ----- Maus -----

    def _crop_clamp(self, p):
        page = self.doc[self.page_no]
        return (max(0.0, min(page.rect.width, p[0])), max(0.0, min(page.rect.height, p[1])))

    def _crop_press(self, ev):
        if not self.doc:
            return
        self.canvas.focus_set()
        cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
        p = self._crop_clamp(self._canvas_to_page(cx, cy))
        r = self._crop_rect
        if r:
            for i, h in enumerate(self._crop_handles(r)):
                hx, hy = self._crop_canvas(h)
                if abs(cx - hx) <= 9 and abs(cy - hy) <= 9:
                    self._crop_mode = ("h", i, fitz.Rect(r))
                    return
            if r.x0 <= p[0] <= r.x1 and r.y0 <= p[1] <= r.y1:
                self._crop_mode = ("move", p, fitz.Rect(r))
                self.canvas.config(cursor="fleur")
                return
        self._crop_mode = ("new", p)
        self._crop_rect = None

    def _crop_drag(self, ev):
        m = self._crop_mode
        if not (m and self.doc):
            return
        page = self.doc[self.page_no]
        cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
        p = self._crop_clamp(self._canvas_to_page(cx, cy))
        W, H = page.rect.width, page.rect.height
        if m[0] == "new":
            s = m[1]
            self._crop_rect = fitz.Rect(min(s[0], p[0]), min(s[1], p[1]),
                                        max(s[0], p[0]), max(s[1], p[1]))
        elif m[0] == "move":
            r0 = m[2]
            dx = max(-r0.x0, min(W - r0.x1, p[0] - m[1][0]))
            dy = max(-r0.y0, min(H - r0.y1, p[1] - m[1][1]))
            self._crop_rect = fitz.Rect(r0.x0 + dx, r0.y0 + dy, r0.x1 + dx, r0.y1 + dy)
        else:
            i, r0 = m[1], m[2]
            x0, y0, x1, y1 = r0.x0, r0.y0, r0.x1, r0.y1
            if i in (0, 3, 7):
                x0 = p[0]
            if i in (1, 2, 5):
                x1 = p[0]
            if i in (0, 1, 4):
                y0 = p[1]
            if i in (2, 3, 6):
                y1 = p[1]
            self._crop_rect = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self._crop_draw()
        self._crop_update()

    def _crop_release(self, ev):
        if not self._crop_mode:
            return
        self._crop_drag(ev)
        self._crop_mode = None
        self.canvas.config(cursor="crosshair")
        r = self._crop_rect
        if r and (r.width * self._render_scale < MIN_PX or r.height * self._render_scale < MIN_PX):
            self._crop_rect = None
        self._crop_draw()
        self._crop_update()

    def crop_clear(self):
        self._crop_rect = None
        self._crop_mode = None
        self._crop_draw()
        if self._crop_on:
            self._crop_update()

    def crop_escape(self):
        """Esc: erst die Auswahl aufheben, dann das Werkzeug schließen."""
        if self._crop_rect:
            self.crop_clear()
        else:
            self._crop_close()

    # ----- Anwenden -----

    def crop_apply(self, _ev=None):
        if not (self._crop_on and self._crop_rect and self.doc):
            return
        r = fitz.Rect(self._crop_rect)
        pno = self.page_no
        alle = self.var_crop_scope.get() == "all"
        page = self.doc[pno]
        W, H = page.rect.width, page.rect.height
        anteil = (r.x0 / W, r.y0 / H, r.x1 / W, r.y1 / H)

        def job(_pg):
            if not alle:
                self.doc[pno].set_cropbox(display_to_cropbox(self.doc[pno], r))
                return
            for pg in self.doc:
                w, h = pg.rect.width, pg.rect.height
                d = fitz.Rect(anteil[0] * w, anteil[1] * h, anteil[2] * w, anteil[3] * h)
                pg.set_cropbox(display_to_cropbox(pg, d))
        self._crop_rect = None
        meldung = ("Alle Seiten zugeschnitten" if alle
                   else f"Seite {pno + 1} zugeschnitten (CropBox, Strg+Z macht es rückgängig)")
        if self._anno_do(job, pno, meldung):
            self._crop_after_change(alle)
        else:
            self._crop_draw()

    def crop_reset(self):
        """Hebt den Zuschnitt dieser Seite (oder aller) auf: CropBox = MediaBox."""
        if not (self._crop_on and self.doc):
            return
        pno = self.page_no
        alle = self.var_crop_scope.get() == "all"

        def job(_pg):
            seiten = list(self.doc) if alle else [self.doc[pno]]
            for pg in seiten:
                pg.set_cropbox(pg.mediabox)
        self._crop_rect = None
        if self._anno_do(job, pno, "Zuschnitt aufgehoben" + (" (alle Seiten)" if alle else "")):
            self._crop_after_change(alle)

    def _crop_after_change(self, alle):
        self._lines_cache.clear()
        if alle:
            self._thumbs_reset()
        self._crop_draw()
        self._crop_update()
