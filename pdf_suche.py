# -*- coding: utf-8 -*-
"""
Volltextsuche im PDF-Betrachter (Mixin).
Strg+F öffnet die Suchleiste, Enter / F3 springt zum nächsten Treffer,
Umschalt+Enter / Umschalt+F3 zum vorherigen, Esc schließt die Leiste.
"""

import tkinter as tk
from tkinter import messagebox, ttk

import fitz

from pdf_anmerken import (apply_range, find_span, match_font, plan_range, query_pattern,
                          replace_all_on_page, replace_spans, word_at)


class ChangeSelectionDialog(tk.Toplevel):
    """Fragt nach dem neuen Wortlaut für den markierten Text."""

    def __init__(self, master, old, font_hint, multiline=False):
        super().__init__(master)
        self.title("Markierten Text ändern")
        self.transient(master)
        self.resizable(False, False)
        self.result = None
        pad = {"padx": 10, "pady": 4}
        ttk.Label(self, text="Markiert:").grid(row=0, column=0, sticky="e", **pad)
        ttk.Label(self, text=old, font=("Segoe UI", 10, "bold")).grid(
            row=0, column=1, sticky="w", **pad)
        ttk.Label(self, text="Ändern in:").grid(row=1, column=0, sticky="e", **pad)
        self.var = tk.StringVar(value=old)
        self.var_all = tk.BooleanVar(value=False)
        self.multiline = multiline
        if multiline:
            self.ent = tk.Text(self, width=60, height=7, wrap="word", undo=True)
            self.ent.insert("1.0", old)
            self.ent.grid(row=1, column=1, sticky="ew", **pad)
            ttk.Label(self, foreground="#606060",
                      text="Strg+Enter übernimmt. Der Rest des Absatzes wird neu umbrochen."
                      ).grid(row=2, column=1, sticky="w", **pad)
        else:
            self.ent = ttk.Entry(self, textvariable=self.var, width=44)
            self.ent.grid(row=1, column=1, sticky="ew", **pad)
            ttk.Checkbutton(self, variable=self.var_all,
                            text="Überall in der PDF ändern (vorher kommt eine Rückfrage mit der Anzahl)"
                            ).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(self, foreground="#606060", text=font_hint).grid(
            row=3, column=0, columnspan=2, sticky="w", **pad)
        bt = ttk.Frame(self)
        bt.grid(row=4, column=0, columnspan=2, sticky="e", **pad)
        ttk.Button(bt, text="Abbrechen", command=self.destroy).pack(side="right")
        ttk.Button(bt, text="Ändern", command=self._ok).pack(side="right", padx=6)
        if multiline:
            self.bind("<Control-Return>", lambda e: self._ok())
            self.ent.focus_set()
            self.ent.tag_add("sel", "1.0", "end-1c")
        else:
            self.bind("<Return>", lambda e: self._ok())
            self.ent.focus_set()
            self.ent.selection_range(0, "end")
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def _ok(self):
        text = self.ent.get("1.0", "end-1c") if self.multiline else self.var.get()
        self.result = (text, self.var_all.get())
        self.destroy()


class SearchMixin:
    """Erwartet: doc, canvas, page_no, _bar, _page_to_canvas, show_page,
    sel_status, _typing, focus_set."""

    def _search_init(self):
        self._search_hits = None        # Liste (Seite, Rechteck in Anzeigekoordinaten)
        self._search_i = -1
        self._search_job = None
        self._search_text = ""
        self.var_q = tk.StringVar()
        self.var_q.trace_add("write", self._search_typed)
        self.var_repl = tk.StringVar()

    def _build_search_bar(self, after):
        self._search_after = after
        self.search_bar = ttk.Frame(self, padding=(6, 0, 6, 4))
        row = ttk.Frame(self.search_bar)
        row.pack(fill="x")
        ttk.Label(row, text="Suchen:").pack(side="left")
        self.ent_search = ttk.Entry(row, textvariable=self.var_q, width=34)
        self.ent_search.pack(side="left", padx=(4, 6))
        self.ent_search.bind("<Return>", lambda e: self.search_step(1))
        self.ent_search.bind("<Shift-Return>", lambda e: self.search_step(-1))
        self.ent_search.bind("<Escape>", lambda e: (self.close_search(), "break")[1])
        ttk.Button(row, text="▲", width=3,
                   command=lambda: self.search_step(-1)).pack(side="left")
        ttk.Button(row, text="▼", width=3,
                   command=lambda: self.search_step(1)).pack(side="left", padx=(2, 8))
        self.lbl_search = ttk.Label(row, text="", foreground="#606060")
        self.lbl_search.pack(side="left")
        ttk.Button(row, text="✕", width=3,
                   command=self.close_search).pack(side="right")
        ttk.Button(row, text="Ersetzen ▾", command=self.toggle_replace).pack(side="right", padx=(0, 8))
        self.rep_row = ttk.Frame(self.search_bar)
        ttk.Label(self.rep_row, text="Ersetzen durch:").pack(side="left")
        self.ent_repl = ttk.Entry(self.rep_row, textvariable=self.var_repl, width=30)
        self.ent_repl.pack(side="left", padx=(4, 6))
        ttk.Button(self.rep_row, text="Ersetzen", command=self.replace_current).pack(side="left")
        ttk.Button(self.rep_row, text="Alle ersetzen", command=self.replace_all
                   ).pack(side="left", padx=(6, 8))
        ttk.Label(self.rep_row, foreground="#606060",
                  text="Der geänderte Textabschnitt erscheint in der ähnlichsten installierten Schrift."
                  ).pack(side="left")

    def search_visible(self):
        return self.search_bar.winfo_ismapped()

    def open_search(self):
        if not self.search_visible():
            self.search_bar.pack(side="top", fill="x", after=self._search_after)
        self.ent_search.focus_set()
        self.ent_search.selection_range(0, "end")
        if self.var_q.get():
            self._search_run(jump=False)

    def toggle_replace(self, show=None):
        zeigen = (not self.rep_row.winfo_ismapped()) if show is None else show
        if zeigen:
            self.rep_row.pack(fill="x", pady=(4, 0))
            self.ent_repl.focus_set()
        else:
            self.rep_row.pack_forget()

    def open_replace(self):
        self.open_search()
        self.toggle_replace(True)

    def close_search(self):
        if self._search_job:
            self.after_cancel(self._search_job)
            self._search_job = None
        self.search_bar.pack_forget()
        self.canvas.delete("searchhit")
        self._search_hits = None
        self._search_i = -1
        self.focus_set()

    # ----- Suchen -----

    def _search_typed(self, *_):
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(300, lambda: self._search_run(jump=True))

    def _search_collect(self):
        """Sucht in allen Seiten; liefert [(Seite, Rechteck)] in Anzeigekoordinaten."""
        q = self.var_q.get().strip()
        self._search_text = q
        hits = []
        if not q or not self.doc:
            return hits
        for pno in range(len(self.doc)):
            page = self.doc[pno]
            rm = page.rotation_matrix
            try:
                found = page.search_for(q)
            except Exception:
                continue
            for r in found:
                hits.append((pno, fitz.Rect(fitz.Point(r.x0, r.y0) * rm,
                                            fitz.Point(r.x1, r.y1) * rm).normalize()))
        return hits

    def _search_run(self, jump=True):
        self._search_job = None
        if not self.doc:
            return
        self._search_hits = self._search_collect()
        self._search_i = -1
        n = len(self._search_hits)
        if not self.var_q.get().strip():
            self.lbl_search.config(text="")
            self.canvas.delete("searchhit")
            return
        if n == 0:
            has_text = any(self.doc[i].get_text("text").strip()
                           for i in range(min(len(self.doc), 20)))
            self.lbl_search.config(
                text="Nichts gefunden" if has_text else
                     "Nichts gefunden – die PDF hat keinen Text (OCR im Bearbeiter nötig?)",
                foreground="#a00000")
            self.canvas.delete("searchhit")
            return
        # erster Treffer ab der aktuellen Seite
        start = next((i for i, (p, _r) in enumerate(self._search_hits)
                      if p >= self.page_no), 0)
        if jump:
            self._search_go(start)
        else:
            self._search_i = start
            self._search_label()
            self._search_draw()

    def search_step(self, delta):
        if not self.search_visible():
            self.open_search()
            return
        if self._search_hits is None or self._search_text != self.var_q.get().strip():
            self._search_run(jump=False)
        if not self._search_hits:
            return
        n = len(self._search_hits)
        i = self._search_i
        if i < 0:
            i = 0 if delta > 0 else n - 1
        else:
            i = (i + delta) % n
        self._search_go(i)

    def _search_label(self):
        n = len(self._search_hits or [])
        self.lbl_search.config(text=f"Treffer {self._search_i + 1} von {n}" if self._search_i >= 0
                               else f"{n} Treffer", foreground="#606060")

    def _search_go(self, i):
        self._search_i = i
        pno, rect = self._search_hits[i]
        if pno != self.page_no:
            self.show_page(pno)
        else:
            self._search_draw()
        self._search_label()
        self._search_scroll_to(rect)

    def _search_scroll_to(self, rect):
        self.update_idletasks()
        x0, y0, x1, y1 = self._page_to_canvas(rect)
        try:
            sr = [float(v) for v in str(self.canvas.cget("scrollregion")).split()]
            hoehe, breite = sr[3] - sr[1], sr[2] - sr[0]
        except (ValueError, IndexError):
            return
        ch, cw = self.canvas.winfo_height(), self.canvas.winfo_width()
        if hoehe > ch:
            self.canvas.yview_moveto(max(0.0, min(1.0, (y0 - ch / 3) / hoehe)))
        if breite > cw:
            self.canvas.xview_moveto(max(0.0, min(1.0, (x0 - cw / 3) / breite)))

    def _search_draw(self):
        """Zeichnet die Treffer der angezeigten Seite (vom Rendern aufgerufen)."""
        if not getattr(self, "canvas", None):
            return
        self.canvas.delete("searchhit")
        if not self._search_hits or not self.search_visible():
            return
        for k, (pno, r) in enumerate(self._search_hits):
            if pno != self.page_no:
                continue
            x0, y0, x1, y1 = self._page_to_canvas(r)
            aktuell = (k == self._search_i)
            self.canvas.create_rectangle(
                x0 - 1, y0 - 1, x1 + 1, y1 + 1,
                fill="#ff9d2e" if aktuell else "#ffe600", stipple="gray50",
                outline="#d96b00" if aktuell else "", width=2 if aktuell else 1,
                tags="searchhit")

    def _search_invalidate(self):
        """Nach Änderungen an der PDF: Treffer neu berechnen."""
        if getattr(self, "search_bar", None) and self.search_visible() and self.var_q.get().strip():
            self._search_hits = None
            if self._search_job:
                self.after_cancel(self._search_job)
            self._search_job = self.after(200, lambda: self._search_run(jump=False))
        else:
            self._search_hits = None


    # ----- Ersetzen -----

    def _repl_ready(self):
        q = self.var_q.get().strip()
        if not q or not self.doc:
            self.lbl_search.config(text="Erst oben den Suchbegriff eingeben.", foreground="#a00000")
            return None
        if self._search_hits is None or self._search_text != q:
            self._search_run(jump=False)
        if not self._search_hits:
            return None
        return q

    def replace_current(self):
        """Ersetzt den aktuellen Treffer (die Stelle im Textabschnitt, nicht nur das Wort)."""
        q = self._repl_ready()
        if q is None:
            return
        if self._search_i < 0:
            self._search_go(0)
        pno, rect = self._search_hits[self._search_i]
        repl = self.var_repl.get()
        page = self.doc[pno]
        mitte = ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        try:
            info = find_span(page, mitte)
        except ValueError as exc:
            messagebox.showinfo("PDF-Scan-Werkzeug", str(exc), parent=self)
            return
        if not info:
            messagebox.showinfo("PDF-Scan-Werkzeug",
                                "An dieser Stelle gibt es keinen änderbaren Text.", parent=self)
            return
        pat = query_pattern(q)
        treffer = list(pat.finditer(info["text"]))
        if not treffer:
            messagebox.showinfo(
                "PDF-Scan-Werkzeug",
                "Der Treffer liegt über mehrere Textabschnitte (z. B. Stilwechsel mitten im Wort). "
                "Hier bitte mit „Text ändern“ von Hand.", parent=self)
            return
        # wievielter Treffer innerhalb des Abschnitts? (nach x geordnet)
        im_abschnitt = sorted((r for p, r in self._search_hits
                               if p == pno and fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
                               in fitz.Rect(info["bbox"].x0 - 1, info["bbox"].y0 - 1,
                                            info["bbox"].x1 + 1, info["bbox"].y1 + 1)),
                              key=lambda r: r.x0)
        n = 0
        for k, r in enumerate(im_abschnitt):
            if r == rect:
                n = k
        m = treffer[min(n, len(treffer) - 1)]
        neu = info["text"][:m.start()] + repl + info["text"][m.end():]
        if self._anno_do(lambda pg: replace_spans(pg, [(info, neu)]), pno, "Ersetzt"):
            self.focus_set()          # damit Strg+Z gleich wirkt
            self._search_hits = None
            self.after(400, lambda: self._search_run(jump=True))

    def replace_all(self):
        q = self._repl_ready()
        if q is None:
            return
        repl = self.var_repl.get()
        seiten = sorted({p for p, _r in self._search_hits})
        n = len(self._search_hits)
        if not messagebox.askyesno(
                "Alle ersetzen",
                f"„{q}“ durch „{repl}“ ersetzen?\n\n{n} Treffer auf {len(seiten)} Seite(n).\n"
                "Die Datei wird sofort geändert (Strg+Z macht alles in einem Schritt "
                "rückgängig, solange das Fenster offen ist).\n"
                "Jeder geänderte Textabschnitt erscheint in der ähnlichsten installierten Schrift.",
                parent=self):
            return
        ergebnis = {"n": 0}

        def job(pg):
            doc = pg.parent
            for p in seiten:
                ergebnis["n"] += replace_all_on_page(doc[p], q, repl)
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            ok = self._anno_do(job, seiten[0], "Ersetzt")
        finally:
            self.config(cursor="")
        if ok:
            self.focus_set()          # damit Strg+Z gleich wirkt
            self._thumbs_reset()
            self._lines_cache.clear()
            rest = n - ergebnis["n"]
            text = f"{ergebnis['n']} von {n} Treffern ersetzt"
            if rest > 0:
                text += f" – {rest} ließen sich nicht ersetzen (über mehrere Textabschnitte verteilt oder senkrechter Text)"
            self.sel_status.config(text="✓ " + text, foreground="#107010" if rest <= 0 else "#a06000")
            self._search_hits = None
            self.after(400, lambda: self._search_run(jump=False))


    # ----- Markierten Text ändern -----

    def change_selection(self):
        """Ändert den mit der Maus markierten Text direkt, ohne ihn erst suchen zu
        müssen: ein Wort oder Stück innerhalb eines Abschnitts, oder ein beliebiger
        Bereich innerhalb eines Absatzes (auch über Stilwechsel und mehrere Zeilen)."""
        titel = "PDF-Scan-Werkzeug"
        if not self.doc or not self._sel_text or not self._sel_page_rects:
            messagebox.showinfo(titel, "Erst den Text markieren, der geändert werden soll "
                                "(Ziehen mit gedrückter Maustaste).", parent=self)
            return
        pno = self._sel_page_no
        page = self.doc[pno]
        rects = list(self._sel_page_rects)
        gesucht = self._sel_text.strip()
        if len(rects) == 1 and gesucht:
            r = rects[0]
            try:
                info = find_span(page, ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
            except ValueError:
                info = None
            if info:
                text = info["text"]
                treffer = [m.start() for m in query_pattern(gesucht).finditer(text)]
                if treffer:
                    self._change_in_span(pno, info, gesucht, treffer, r)
                    return
        self._change_range(pno, page, rects)

    def _change_in_span(self, pno, info, gesucht, treffer, r):
        text = info["text"]
        breite = max(1.0, info["bbox"].width)
        soll = (r.x0 - info["bbox"].x0) / breite * len(text)
        start = min(treffer, key=lambda k: abs(k - soll))
        fam, fett, kursiv = match_font(info["font"], info["flags"])
        dlg = ChangeSelectionDialog(
            self, gesucht, f"Der Abschnitt erscheint in {fam}{' fett' if fett else ''}"
            f"{' kursiv' if kursiv else ''} (ähnlichste installierte Schrift zu {info['font'] or '?'}).")
        self.wait_window(dlg)
        if dlg.result is None:
            return
        neu, alle = dlg.result
        if alle:
            self.var_q.set(gesucht)
            self.var_repl.set(neu)
            self._search_hits = None
            self.replace_all()
            return
        if neu == gesucht:
            return
        ergebnis = text[:start] + neu + text[start + len(gesucht):]
        if self._anno_do(lambda pg: replace_spans(pg, [(info, ergebnis)]), pno, "Text geändert"):
            self._clear_selection()
            self.focus_set()

    def _change_range(self, pno, page, rects):
        titel = "PDF-Scan-Werkzeug"
        try:
            plan = plan_range(page, rects)
        except ValueError as exc:
            messagebox.showinfo(titel, str(exc), parent=self)
            return
        sp = plan["span"]
        fam, fett, kursiv = match_font(sp["font"], sp["flags"])
        dlg = ChangeSelectionDialog(
            self, plan["old"], f"Alles ab hier im Absatz erscheint in {fam}{' fett' if fett else ''}"
            f"{' kursiv' if kursiv else ''}, {sp['size']:.1f} pt, linksbündig neu umbrochen.",
            multiline=True)
        self.wait_window(dlg)
        if dlg.result is None or dlg.result[0] == plan["old"]:
            return
        neu = dlg.result[0]
        zaehler = {}

        def job(pg):
            zaehler["n"] = apply_range(pg, plan, neu)
        if self._anno_do(job, pno, "Bereich geändert"):
            self._clear_selection()
            self.focus_set()
            n_neu, n_alt = zaehler.get("n", (0, 0))
            if n_neu > n_alt:
                self.sel_status.config(
                    text=f"✓ Bereich geändert – der Absatz ist jetzt {n_neu - n_alt} Zeile(n) länger "
                         "und kann in die Nachbarschaft ragen (Strg+Z macht es rückgängig).",
                    foreground="#a06000")

    # ----- Doppelklick: Wort markieren -----

    def _sel_double(self, ev):
        """Doppelklick im Markier-Modus markiert das ganze Wort."""
        if getattr(self, "tool", "select") not in ("select", "edit") or not self.doc:
            return
        cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
        p = self._canvas_to_page(cx, cy)
        try:
            ergebnis = word_at(self.doc[self.page_no], p)
        except Exception:
            ergebnis = None
        if not ergebnis:
            return
        rect, text = ergebnis
        self._sel_start = None
        self._sel_page_rects = [rect]
        self._sel_page_no = self.page_no
        self._sel_text = text
        self._draw_selection_rects([rect])
        self.btn_copy.config(state="normal")
        self.btn_chg.config(state="normal")
        self.copy_selection(silent=True)
        self.sel_status.config(text=f"✓ Wort markiert und in die Zwischenablage kopiert: „{text}“ "
                                    "(F2 = ändern)", foreground="#107010")
