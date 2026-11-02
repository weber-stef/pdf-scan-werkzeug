#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ordnerwahl mit Inhaltsansicht (Windows und Linux, nur tkinter).

Der eingebaute Dialog "Ordner auswaehlen" zeigt nur Ordner. Dieser hier zeigt
links den Ordnerbaum und rechts, was im markierten Ordner liegt: Unterordner,
Mediendateien und sonstige Dateien (die abgeblendet). Der Pfad laesst sich
oben auch eintippen oder einfuegen (Enter springt dorthin).

Benutzung:
    pfad = ordner_waehlen(fenster, "Quellordner waehlen", start="/home/stef")
    # gibt den gewaehlten Ordner zurueck oder None bei Abbruch

Der Dialog erbt das ttk-Thema des Programms (hell oder dunkel).
"""

import os
import string
import tkinter as tk
from tkinter import ttk

AUDIO = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".aif",
         ".alac", ".ape", ".wv", ".caf", ".mka", ".mp2", ".mpc", ".m3u", ".m3u8", ".pls", ".xspf"}
VIDEO = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg",
         ".ts", ".m2ts", ".3gp", ".vob"}

_zuletzt = {"pfad": None}          # merkt sich den zuletzt gewaehlten Ordner
PLATZHALTER = "\u200b#leer"      # Kennung des Platzhalter-Kindes (kein NUL: Tcl schneidet daran ab)
_UEBERSPRINGEN_WIN = {"$recycle.bin", "system volume information", "$winreg.archive"}


def _groesse(byte):
    if byte < 1024:
        return f"{byte} B"
    for einheit in ("KB", "MB", "GB", "TB"):
        byte /= 1024.0
        if byte < 1024 or einheit == "TB":
            return f"{byte:.1f} {einheit}"


class OrdnerDialog(tk.Toplevel):
    def __init__(self, parent, titel="Ordner auswaehlen", start=None):
        super().__init__(parent)
        self.title(titel)
        self.ergebnis = None
        self.transient(parent)
        self.minsize(700, 420)
        self.geometry(self._geometrie(parent, 940, 580))
        self._aufbauen()
        self._wurzeln_anlegen()
        start = start if start and os.path.isdir(start) else (_zuletzt["pfad"] or os.path.expanduser("~"))
        self.update_idletasks()
        self._gehe_zu(start)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.pfad_feld.focus_set()

    @staticmethod
    def _geometrie(parent, breite, hoehe):
        try:
            x = parent.winfo_rootx() + max(0, (parent.winfo_width() - breite) // 2)
            y = parent.winfo_rooty() + max(0, (parent.winfo_height() - hoehe) // 2)
            return f"{breite}x{hoehe}+{x}+{y}"
        except tk.TclError:
            return f"{breite}x{hoehe}"

    # -------------------------------------------------------------- Aufbau
    def _aufbauen(self):
        oben = ttk.Frame(self)
        oben.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(oben, text="Pfad:").pack(side="left")
        self.pfad_var = tk.StringVar()
        self.pfad_feld = ttk.Entry(oben, textvariable=self.pfad_var)
        self.pfad_feld.pack(side="left", fill="x", expand=True, padx=(6, 6))
        self.pfad_feld.bind("<Return>", lambda _e: self._gehe_zu(self.pfad_var.get()))
        ttk.Button(oben, text="Gehe zu", command=lambda: self._gehe_zu(self.pfad_var.get())).pack(side="left", padx=(0, 4))
        ttk.Button(oben, text="Eine Ebene hoch", command=self._hoch).pack(side="left")

        mitte = ttk.PanedWindow(self, orient="horizontal")
        mitte.pack(fill="both", expand=True, padx=10, pady=4)

        links = ttk.Frame(mitte)
        gl = ttk.Scrollbar(links, orient="vertical")
        self.baum = ttk.Treeview(links, show="tree", selectmode="browse", yscrollcommand=gl.set)
        gl.config(command=self.baum.yview)
        self.baum.pack(side="left", fill="both", expand=True)
        gl.pack(side="right", fill="y")
        self.baum.bind("<<TreeviewOpen>>", self._beim_oeffnen)
        self.baum.bind("<<TreeviewSelect>>", self._beim_waehlen)
        mitte.add(links, weight=2)

        rechts = ttk.Frame(mitte)
        gr = ttk.Scrollbar(rechts, orient="vertical")
        self.liste = ttk.Treeview(rechts, columns=("name", "typ", "groesse"), show="headings",
                                  selectmode="browse", yscrollcommand=gr.set)
        gr.config(command=self.liste.yview)
        for spalte, text, breite, anker in (("name", "Name", 330, "w"), ("typ", "Art", 80, "w"), ("groesse", "Groesse", 90, "e")):
            self.liste.heading(spalte, text=text)
            self.liste.column(spalte, width=breite, anchor=anker, stretch=(spalte == "name"))
        self.liste.tag_configure("andere", foreground="#8a8f98")
        self.liste.tag_configure("ordner", foreground="#d89040")
        self.liste.pack(side="left", fill="both", expand=True)
        gr.pack(side="right", fill="y")
        self.liste.bind("<Double-1>", self._liste_doppelklick)
        mitte.add(rechts, weight=3)

        unten = ttk.Frame(self)
        unten.pack(fill="x", padx=10, pady=(4, 10))
        self.status = ttk.Label(unten, text="")
        self.status.pack(side="left")
        ttk.Button(unten, text="Abbrechen", command=self.destroy).pack(side="right")
        ttk.Button(unten, text="Diesen Ordner waehlen", command=self._uebernehmen).pack(side="right", padx=(0, 8))

    # --------------------------------------------------------- Ordnerbaum
    def _wurzeln_anlegen(self):
        if os.name == "nt":
            for buchstabe in string.ascii_uppercase:
                laufwerk = f"{buchstabe}:\\"
                if os.path.isdir(laufwerk):
                    self._knoten_anlegen("", laufwerk, laufwerk)
        else:
            self._knoten_anlegen("", "/", "/")

    def _knoten_anlegen(self, eltern, iid, text):
        self.baum.insert(eltern, "end", iid=iid, text=text)
        self.baum.insert(iid, "end", iid=iid + PLATZHALTER, text="")   # damit der Pfeil erscheint

    def _unterordner(self, pfad):
        try:
            namen = sorted(os.listdir(pfad), key=str.lower)
        except OSError:
            return []
        ergebnis = []
        for name in namen:
            if os.name == "nt":
                if name.lower() in _UEBERSPRINGEN_WIN:
                    continue
            elif name.startswith("."):
                continue
            voll = os.path.join(pfad, name)
            try:
                if os.path.isdir(voll):
                    ergebnis.append((name, voll))
            except OSError:
                continue
        return ergebnis

    def _fuellen(self, iid):
        platzhalter = iid + PLATZHALTER
        if self.baum.exists(platzhalter):
            self.baum.delete(platzhalter)
            for name, voll in self._unterordner(iid):
                self._knoten_anlegen(iid, voll, name)

    def _beim_oeffnen(self, _event=None):
        iid = self.baum.focus()
        if iid:
            self._fuellen(iid)

    def _beim_waehlen(self, _event=None):
        auswahl = self.baum.selection()
        if auswahl:
            self._zeige(auswahl[0])

    # --------------------------------------------------------- Inhaltsliste
    def _zeige(self, pfad):
        self.pfad_var.set(pfad)
        self.liste.delete(*self.liste.get_children())
        ordner, medien, andere = [], [], []
        try:
            eintraege = sorted(os.scandir(pfad), key=lambda e: e.name.lower())
        except OSError as fehler:
            self.status.config(text=f"Nicht lesbar: {fehler.strerror or fehler}")
            return
        for e in eintraege:
            try:
                if e.is_dir():
                    if not (os.name != "nt" and e.name.startswith(".")):
                        ordner.append(e)
                elif e.is_file():
                    endung = os.path.splitext(e.name)[1].lower()
                    (medien if endung in AUDIO or endung in VIDEO else andere).append((e, endung))
            except OSError:
                continue
        for e in ordner:
            self.liste.insert("", "end", iid="o:" + e.name, values=(e.name, "Ordner", ""), tags=("ordner",))
        for e, endung in medien:
            art = "Video" if endung in VIDEO else "Audio"
            try:
                groesse = _groesse(e.stat().st_size)
            except OSError:
                groesse = ""
            self.liste.insert("", "end", iid="d:" + e.name, values=(e.name, art, groesse))
        for e, _endung in andere:
            try:
                groesse = _groesse(e.stat().st_size)
            except OSError:
                groesse = ""
            self.liste.insert("", "end", iid="x:" + e.name, values=(e.name, "Datei", groesse), tags=("andere",))
        self.status.config(text=f"{len(medien)} Audio-/Videodateien, {len(andere)} sonstige Dateien, {len(ordner)} Unterordner")

    def _liste_doppelklick(self, _event=None):
        auswahl = self.liste.selection()
        if auswahl and auswahl[0].startswith("o:"):
            self._gehe_zu(os.path.join(self.pfad_var.get(), auswahl[0][2:]))

    # ---------------------------------------------------------- Navigation
    def _hoch(self):
        pfad = self.pfad_var.get().strip().rstrip("\\/") or os.sep
        eltern = os.path.dirname(pfad)
        if os.name == "nt" and len(pfad) <= 2:          # "T:" -> Laufwerk selbst
            eltern = pfad + "\\"
        self._gehe_zu(eltern or os.sep)

    def _gehe_zu(self, pfad):
        pfad = os.path.expanduser(pfad.strip().strip('"'))
        if not pfad:
            return
        pfad = os.path.abspath(pfad)
        if not os.path.isdir(pfad):
            self.status.config(text=f"Ordner nicht gefunden: {pfad}")
            return
        anker = os.path.splitdrive(pfad)[0] + os.sep if os.name == "nt" else os.sep
        wurzel = self._finde_kind("", anker) or self._wurzel_nachtragen(anker)
        knoten = wurzel
        rest = os.path.relpath(pfad, anker) if pfad.rstrip("\\/") != anker.rstrip("\\/") else "."
        for teil in ([] if rest == "." else rest.split(os.sep)):
            self._fuellen(knoten)
            kind = self._finde_kind(knoten, os.path.join(knoten, teil))
            if kind is None:
                break
            self.baum.item(knoten, open=True)
            knoten = kind
        self.baum.selection_set(knoten)      # loest _beim_waehlen -> Liste aus
        self.baum.focus(knoten)
        self.baum.see(knoten)
        self._zeige(knoten)

    def _finde_kind(self, eltern, gesucht):
        """Kind mit passendem Pfad; unter Windows ohne Beachtung der Gross-/Kleinschreibung."""
        for kind in self.baum.get_children(eltern):
            if kind == gesucht or (os.name == "nt" and kind.lower() == gesucht.lower()):
                return kind
        return None

    def _wurzel_nachtragen(self, anker):
        self._knoten_anlegen("", anker, anker)
        return anker

    # ------------------------------------------------------------- Ergebnis
    def _uebernehmen(self):
        pfad = os.path.abspath(os.path.expanduser(self.pfad_var.get().strip().strip('"')))
        if os.path.isdir(pfad):
            self.ergebnis = pfad
            _zuletzt["pfad"] = pfad
            self.destroy()
        else:
            self.status.config(text=f"Ordner nicht gefunden: {pfad}")


def ordner_waehlen(parent, titel="Ordner auswaehlen", start=None):
    """Zeigt den Dialog und gibt den gewaehlten Ordner (oder None) zurueck."""
    dialog = OrdnerDialog(parent, titel, start)
    parent.wait_window(dialog)
    return dialog.ergebnis
