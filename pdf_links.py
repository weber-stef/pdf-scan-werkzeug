# -*- coding: utf-8 -*-
"""
Sprungmarken (Links) im PDF-Betrachter (Mixin), wie im Acrobat Reader:
Klick auf einen Eintrag im Inhaltsverzeichnis springt zur Seite, Links zu
Webadressen öffnen den Browser. Mauszeiger wird über Links zur Hand.
Alt+Links (oder der Knopf ↩) springt zurück an die Stelle vor dem Sprung.
"""

import re
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import ttk
from pdf_einstellungen import C_HINT, C_OK, C_ERR, C_WARN, C_TXT

import fitz

TITEL = "PDF-Scan-Werkzeug"


class LinkMixin:
    """Erwartet: doc, canvas, page_no, show_page, sel_status, tool, _page_to_canvas,
    _canvas_to_page, _render_scale, update_idletasks."""

    def _links_init(self):
        self._links_cache = {}
        self._nav_history = []          # (Seite, y-Anteil) vor einem Linksprung
        self._names = None
        self._link_cursor = False
        self._bm_shown = False
        self._bm_items = {}

    # ----- Links der Seite -----

    def _page_links(self, pno):
        """Links der Seite als [(Rechteck in Anzeigekoordinaten, Link-Dict)]."""
        if pno in self._links_cache:
            return self._links_cache[pno]
        liste = []
        try:
            for lk in self.doc[pno].get_links():
                r = fitz.Rect(lk["from"])
                if not r.is_empty:
                    liste.append((r, lk))
        except Exception:
            liste = []
        self._links_cache[pno] = liste
        return liste

    def _link_at(self, pt):
        if not self.doc or self.page_no < 0:
            return None
        p = fitz.Point(pt)
        for r, lk in reversed(self._page_links(self.page_no)):
            if p in r:
                return lk
        return None

    def _link_hover(self, ev):
        """Hand-Zeiger über Links (nur im normalen Markier-Modus)."""
        tool = getattr(self, "tool", "select")
        if getattr(self, "_crop_on", False) or tool not in ("select", "pick") or not self.doc:
            return
        cx, cy = self.canvas.canvasx(ev.x), self.canvas.canvasy(ev.y)
        if self._anno_hit_at(ev):
            cur = "fleur"                      # Kommentar: anfassen/verschieben
        elif tool == "pick":
            cur = "arrow"
        elif self._link_at(self._canvas_to_page(cx, cy)) is not None:
            cur = "hand2"
        else:
            cur = "xterm"
        if cur != getattr(self, "_hover_cur", None):
            self._hover_cur = cur
            self._link_cursor = cur == "hand2"
            self.canvas.config(cursor=cur)

    # ----- Folgen -----

    def follow_link_at(self, pt):
        """Folgt dem Link an pt (Anzeigekoordinaten); True, wenn dort einer war."""
        lk = self._link_at(pt)
        if not lk:
            return False
        self._follow(lk)
        return True

    def _dest_names(self):
        if self._names is None:
            try:
                self._names = self.doc.resolve_names() or {}
            except Exception:
                self._names = {}
        return self._names

    def _follow(self, lk):
        kind = lk.get("kind")
        if kind == fitz.LINK_GOTO:
            self._jump(lk.get("page", -1), lk.get("to"))
        elif kind == fitz.LINK_URI:
            uri = (lk.get("uri") or "").strip()
            if re.match(r"^(https?://|mailto:|ftp://)", uri, re.I):
                webbrowser.open(uri)
                self.sel_status.config(text=f"Geöffnet im Browser: {uri}", foreground=C_OK)
            else:
                self.sel_status.config(text=f"Link nicht geöffnet (Art nicht erlaubt): {uri[:80]}",
                                       foreground=C_ERR)
        elif kind == fitz.LINK_NAMED:
            self._named_action(lk)
        elif kind == fitz.LINK_GOTOR:
            self.sel_status.config(
                text=f"Verweis auf eine andere Datei ({lk.get('file', '?')}) wird nicht unterstützt.",
                foreground=C_ERR)
        else:
            nd = lk.get("nameddest")
            if nd:
                ziel = self._dest_names().get(nd)
                if ziel:
                    self._jump(ziel.get("page", -1), ziel.get("to") or ziel.get("rect"))
                    return
            self.sel_status.config(text="Dieser Link-Typ wird nicht unterstützt.",
                                   foreground=C_ERR)

    def _named_action(self, lk):
        name = (lk.get("name") or lk.get("nameddest") or "")
        n = len(self.doc)
        if isinstance(lk.get("page"), int) and 0 <= lk["page"] < n:
            self._jump(lk["page"], None)      # Ziel bereits aufgelöst
            return
        ziele = {"NextPage": self.page_no + 1, "PrevPage": self.page_no - 1,
                 "FirstPage": 0, "LastPage": n - 1}
        if name in ziele:
            self._jump(max(0, min(ziele[name], n - 1)), None)
            return
        ziel = self._dest_names().get(lk.get("nameddest") or name)
        if ziel:
            self._jump(ziel.get("page", -1), ziel.get("to") or ziel.get("rect"))
        else:
            self.sel_status.config(text=f"Unbekanntes Linkziel „{name}“.", foreground=C_ERR)

    def _jump(self, pno, to):
        if not isinstance(pno, int) or pno < 0 or pno >= len(self.doc):
            self.sel_status.config(text="Das Ziel des Links gibt es nicht (mehr).",
                                   foreground=C_ERR)
            return
        y0, _y1 = self.canvas.yview()
        self._nav_history.append((self.page_no, y0))
        del self._nav_history[:-50]
        self._update_back_button()
        if pno != self.page_no:
            self.show_page(pno)
        if to is not None:
            try:
                pt = fitz.Point(to[0], to[1]) if not isinstance(to, fitz.Rect) else to.tl
            except Exception:
                pt = None
            if pt is not None:
                self._scroll_to_point(pt)
        self.sel_status.config(text=f"Gesprungen zu Seite {pno + 1} (Alt+Links: zurück)",
                               foreground=C_OK)

    def _scroll_to_point(self, pt):
        self.update_idletasks()
        _x0, y0, _x1, _y1 = self._page_to_canvas(fitz.Rect(pt.x, pt.y, pt.x, pt.y))
        try:
            sr = [float(v) for v in str(self.canvas.cget("scrollregion")).split()]
            hoehe = sr[3] - sr[1]
        except (ValueError, IndexError):
            return
        if hoehe > self.canvas.winfo_height():
            self.canvas.yview_moveto(max(0.0, min(1.0, (y0 - 30) / hoehe)))

    def nav_back(self, _ev=None):
        if not self._nav_history or not self.doc or self._typing():
            return
        pno, y = self._nav_history.pop()
        self._update_back_button()
        if pno >= len(self.doc):
            return
        if pno != self.page_no:
            self.show_page(pno)
        self.update_idletasks()
        self.canvas.yview_moveto(y)

    def _update_back_button(self):
        btn = getattr(self, "btn_back", None)
        if btn is not None:
            btn.config(state="normal" if self._nav_history else "disabled")


    # ----- Lesezeichen (Inhaltsverzeichnis der PDF) -----

    BM_W = 320

    def _build_bookmarks(self, body):
        self.bm_frame = ttk.Frame(body, width=self.BM_W)
        self.bm_frame.grid(row=0, column=0, rowspan=2, sticky="ns")
        self.bm_frame.grid_propagate(False)
        self.bm_frame.rowconfigure(0, weight=1)
        self.bm_frame.columnconfigure(0, weight=1)
        self.bm_tree = ttk.Treeview(self.bm_frame, show="tree", selectmode="browse")
        self.bm_tree.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(self.bm_frame, orient="vertical", command=self.bm_tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb = ttk.Scrollbar(self.bm_frame, orient="horizontal", command=self.bm_tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self.bm_tree.config(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.bm_tree.column("#0", width=900, stretch=False)
        self.bm_tree.bind("<ButtonRelease-1>", self._bm_click)
        self.bm_tree.bind("<Return>", self._bm_click)
        self.bm_frame.grid_remove()
        self._bookmarks_fill()

    def _bookmarks_fill(self):
        """Füllt die Baumansicht aus den Lesezeichen der PDF."""
        tree = self.bm_tree
        tree.delete(*tree.get_children())
        self._bm_items = {}
        try:
            toc = self.doc.get_toc(simple=False) if self.doc else []
        except Exception:
            toc = []
        if not toc:
            tree.insert("", "end", text="Diese PDF hat keine Lesezeichen.")
            return
        stack = []                    # (Ebene, iid)
        for lvl, title, page, dest in toc:
            lvl = max(1, lvl)
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            parent = stack[-1][1] if stack else ""
            text = (title or "").replace("\n", " ").strip() or "(ohne Titel)"
            iid = tree.insert(parent, "end", text=text, open=(lvl <= 1))
            self._bm_items[iid] = (page - 1 if page and page > 0 else -1,
                                   dest.get("to") if isinstance(dest, dict) else None,
                                   dest.get("uri") if isinstance(dest, dict) else None)
            stack.append((lvl, iid))

    def _bm_click(self, _ev=None):
        sel = self.bm_tree.selection()
        if not sel or sel[0] not in self._bm_items:
            return
        pno, to, uri = self._bm_items[sel[0]]
        if uri and re.match(r"^(https?://|mailto:)", uri, re.I):
            webbrowser.open(uri)
        elif pno >= 0:
            self._jump(pno, to)

    def toggle_bookmarks(self):
        """Lesezeichen anstelle der Seitenleiste ein-/ausblenden (F5)."""
        self._bm_shown = not self._bm_shown
        if self._bm_shown:
            self.sidebar.grid_remove()
            self.bm_frame.grid()
        else:
            self.bm_frame.grid_remove()
            self._sidebar_shown = True
            self.sidebar.grid()
