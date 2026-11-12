# -*- coding: utf-8 -*-
"""
Dateigröße verkleinern (Fenster im PDF-Betrachter).

Die eingebetteten Bilder werden neu als JPEG gespeichert, bei Bedarf mit geringerer Auflösung.
Zwei Wege: Zielgröße in MB eingeben (das Fenster sucht Auflösung und Qualität dazu) oder
Auflösung und Qualität selbst einstellen (die entstehende Größe wird vorausberechnet).

Die Vorhersage kodiert eine Stichprobe von Bildern wirklich und rechnet auf alle hoch,
sie ist deshalb nur ein Näherungswert (meist wenige Prozent daneben). Nach dem Speichern
steht die tatsächliche Größe da; im Zielmodus wird bei Überschreitung nachgebessert.
Das Original bleibt unverändert, gespeichert wird immer unter neuem Namen.
Bilder mit Transparenzmaske, 1-Bit-Bilder (Textscans, schon klein) und winzige Bilder
bleiben unangetastet; Text, Schrift und Lesezeichen bleiben erhalten.
"""

import io
import queue
import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import fitz
from PIL import Image

from pdf_einstellungen import C_HINT, C_OK, C_ERR, C_WARN

APP_TITLE = "PDF-Scan-Werkzeug"
DPI_STUFEN = [300, 200, 150, 120, 100, 72]
Q_MIN, Q_MAX = 15, 95
SAMPLE_N = 5


def mb_text(n: float) -> str:
    return f"{n / 1048576:.2f} MB".replace(".", ",")


class Analyse:
    """Sucht die ersetzbaren Bilder einer PDF und schätzt Größen."""

    def __init__(self, data: bytes):
        self.data = data
        self.doc = fitz.open("pdf", data)
        self.items = {}                      # xref -> dict(page, dpi, old)
        for pno in range(len(self.doc)):
            page = self.doc[pno]
            for img in page.get_images(full=True):
                xref, smask, w, h, bpc = img[0], img[1], img[2], img[3], img[4]
                if xref in self.items or smask or bpc == 1 or w * h < 10000:
                    continue
                if self.doc.xref_get_key(xref, "Mask")[0] != "null" or                         self.doc.xref_get_key(xref, "ImageMask")[1] == "true":
                    continue
                try:
                    old = len(self.doc.xref_stream_raw(xref))
                except Exception:
                    continue
                if old < 5000:
                    continue
                self.items[xref] = {"page": pno, "dpi": 0.0, "old": old, "w": w}
            try:
                infos = page.get_image_info(xrefs=True)
            except Exception:
                infos = []
            for info in infos:
                it = self.items.get(info.get("xref"))
                bw = info["bbox"][2] - info["bbox"][0]
                if it and bw > 1:
                    it["dpi"] = max(it["dpi"], info["width"] / (bw / 72.0))
        for it in self.items.values():
            if it["dpi"] <= 0:
                it["dpi"] = 300.0
        # Grundgröße: Datei, wie sie ohne Änderung der Bilder gespeichert würde
        # (eigene Kopie: garbage=4 nummeriert die Objekte der geöffneten Datei um)
        _kopie = fitz.open("pdf", data)
        self.base = len(_kopie.tobytes(garbage=4, deflate=True))
        _kopie.close()
        self.img_old = sum(it["old"] for it in self.items.values())
        self.max_dpi = max((it["dpi"] for it in self.items.values()), default=0.0)
        xs = sorted(self.items)
        step = max(1, len(xs) // SAMPLE_N)
        self.sample = xs[::step][:SAMPLE_N]
        self._cache = {}
        self._rcache = {}

    def load(self, xref):
        if xref in self._cache:
            return self._cache[xref]
        im = None
        try:
            d = self.doc.extract_image(xref)
            im = Image.open(io.BytesIO(d["image"]))
            im.load()
            if im.mode == "1":
                im = None
            elif im.mode != "L":
                im = im.convert("RGB")
        except Exception:
            im = None
        return im

    def load_cached(self, xref):
        if xref not in self._cache:
            self._cache[xref] = self.load(xref)
        return self._cache[xref]

    @staticmethod
    def encode(im, img_dpi, dpi, q, with_info=False):
        if dpi and img_dpi > dpi * 1.02:
            f = dpi / img_dpi
            im = im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))),
                           Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=int(q), optimize=True)
        if with_info:
            return buf.getvalue(), im.width, im.height, im.mode
        return buf.getvalue()

    def predict(self, dpi, q) -> int:
        """Voraussichtliche Dateigröße in Byte (aus der Stichprobe hochgerechnet)."""
        if not self.items:
            return self.base
        alt = neu = 0
        for xref in self.sample:
            it = self.items[xref]
            im = self.load_cached(xref)
            if im is None:
                continue
            alt += it["old"]
            key = (xref, dpi)
            if key not in self._rcache:      # verkleinerte Fassung je Stufe merken
                f = dpi / it["dpi"] if dpi and it["dpi"] > dpi * 1.02 else 1.0
                self._rcache[key] = im if f >= 1 else im.resize(
                    (max(1, round(im.width * f)), max(1, round(im.height * f))), Image.LANCZOS)
            neu += min(it["old"], len(self.encode(self._rcache[key], 0, 0, q)))
        if alt == 0:
            return self.base
        return int(self.base - self.img_old * (1 - neu / alt))

    def search(self, target: int):
        """Findet (dpi, q) mit Vorhersage <= target. Rückgabe (dpi, q, erreicht)."""
        stufen = [0] + [d for d in DPI_STUFEN if d < self.max_dpi]   # 0 = Original
        for dpi in stufen:
            if self.predict(dpi, 60) <= target:
                lo, hi = 60, Q_MAX
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if self.predict(dpi, mid) <= target:
                        lo = mid
                    else:
                        hi = mid - 1
                return dpi, lo, True
        dpi = stufen[-1]
        lo, hi = Q_MIN, 60
        if self.predict(dpi, lo) > target:
            return dpi, lo, False
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.predict(dpi, mid) <= target:
                lo = mid
            else:
                hi = mid - 1
        return dpi, lo, True

    def build(self, dpi, q, dst: Path, progress=None) -> int:
        """Schreibt die verkleinerte PDF nach dst, gibt die Dateigröße zurück."""
        doc = fitz.open("pdf", self.data)
        try:
            n = len(self.items)
            for i, (xref, it) in enumerate(self.items.items()):
                if progress:
                    progress(i, n)
                im = self.load(xref)
                if im is None:
                    continue
                neu, w, h, mode = self.encode(im, it["dpi"], dpi, q, True)
                if len(neu) < it["old"] * 0.95:
                    try:
                        doc.update_stream(xref, neu, compress=False)
                        doc.xref_set_key(xref, "Filter", "/DCTDecode")
                        doc.xref_set_key(xref, "Width", str(w))
                        doc.xref_set_key(xref, "Height", str(h))
                        doc.xref_set_key(xref, "BitsPerComponent", "8")
                        doc.xref_set_key(xref, "ColorSpace",
                                         "/DeviceGray" if mode == "L" else "/DeviceRGB")
                        for k in ("DecodeParms", "Decode"):
                            doc.xref_set_key(xref, k, "null")
                    except Exception:
                        pass
            tmp = dst.with_name(dst.name + ".tmp")
            doc.save(tmp, garbage=4, deflate=True, deflate_fonts=True)
        finally:
            doc.close()
        tmp.replace(dst)
        return dst.stat().st_size


class ShrinkDialog(tk.Toplevel):
    def __init__(self, master, viewer_path, doc):
        super().__init__(master)
        self.path = Path(viewer_path)
        self.title(f"Dateigröße verkleinern – {self.path.name}")
        self.transient(master)
        self.resizable(False, False)
        self._q = queue.Queue()
        self._gen = 0
        self._busy = False
        self.an = None
        self._data = doc.tobytes(garbage=3, deflate=True)

        self.var_mode = tk.StringVar(value="ziel")
        self.var_target = tk.StringVar(value="")
        self.var_dpi = tk.StringVar(value="150")
        self.var_q = tk.IntVar(value=70)
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())
        self.after(100, self._poll)
        self.info.config(text="Analysiere Bilder …")
        self._set_enabled(False)
        threading.Thread(target=self._analyse, daemon=True).start()

    # ---------- Aufbau ----------

    def _build_ui(self):
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        self.info = ttk.Label(frm, text="", foreground=C_HINT, justify="left")
        self.info.grid(row=0, column=0, columnspan=3, sticky="w")

        zf = ttk.Labelframe(frm, text="Zielgröße", padding=6)
        zf.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.rb_ziel = ttk.Radiobutton(zf, text="Datei soll höchstens", value="ziel",
                                       variable=self.var_mode, command=self._changed)
        self.rb_ziel.grid(row=0, column=0, sticky="w")
        self.ent_target = ttk.Entry(zf, textvariable=self.var_target, width=8)
        self.ent_target.grid(row=0, column=1, padx=4)
        ttk.Label(zf, text="MB groß sein").grid(row=0, column=2, sticky="w")
        self.ent_target.bind("<FocusIn>", lambda e: self.var_mode.set("ziel"))
        self.var_target.trace_add("write", lambda *a: self._changed(delay=True))

        mf = ttk.Labelframe(frm, text="Oder Qualität selbst wählen", padding=6)
        mf.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        mf.columnconfigure(2, weight=1)
        self.rb_man = ttk.Radiobutton(mf, text="Auflösung (dpi):", value="manuell",
                                      variable=self.var_mode, command=self._changed)
        self.rb_man.grid(row=0, column=0, sticky="w")
        self.cb_dpi = ttk.Combobox(mf, textvariable=self.var_dpi, width=9, state="readonly",
                                   values=["Original", "300", "200", "150", "120", "100", "72"])
        self.cb_dpi.grid(row=0, column=1, padx=4, sticky="w")
        self.cb_dpi.bind("<<ComboboxSelected>>",
                         lambda e: (self.var_mode.set("manuell"), self._changed()))
        ttk.Label(mf, text="JPEG-Qualität:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.sc_q = ttk.Scale(mf, from_=Q_MIN, to=Q_MAX, variable=self.var_q,
                              command=self._q_moved, length=200)
        self.sc_q.grid(row=1, column=1, columnspan=2, sticky="ew", padx=4, pady=(6, 0))
        self.lbl_q = ttk.Label(mf, text="70", width=4)
        self.lbl_q.grid(row=1, column=3, pady=(6, 0))
        ttk.Label(mf, text="Hoch = größer und schärfer. Gute Scans: 150 dpi, Qualität 70.",
                  foreground=C_HINT).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        self.result = ttk.Label(frm, text="", font=("Segoe UI", 10, "bold"), justify="left")
        self.result.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.prog = ttk.Progressbar(frm, mode="determinate", length=380)
        self.prog.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        kn = ttk.Frame(frm)
        kn.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.btn_save = ttk.Button(kn, text="💾  Verkleinert speichern unter…", command=self.do_save)
        self.btn_save.pack(side="left")
        ttk.Button(kn, text="Schließen", command=self._close).pack(side="right")

    def _set_enabled(self, on):
        st = "normal" if on else "disabled"
        for w in (self.rb_ziel, self.rb_man, self.ent_target, self.sc_q, self.btn_save):
            w.config(state=st)
        self.cb_dpi.config(state="readonly" if on else "disabled")

    def _close(self):
        self._gen += 1
        self.destroy()

    # ---------- Parameter und Vorhersage ----------

    def _q_moved(self, v):
        self.lbl_q.config(text=str(int(float(v))))
        self.var_mode.set("manuell")
        self._changed(delay=True)

    def _target_bytes(self):
        t = self.var_target.get().strip().replace(",", ".")
        try:
            v = float(t)
        except ValueError:
            return None
        return int(v * 1048576) if v > 0 else None

    def _changed(self, delay=False):
        if self.an is None:
            return
        if getattr(self, "_after", None):
            self.after_cancel(self._after)
        self._after = self.after(450 if delay else 10, self._predict)

    def _predict(self):
        self._after = None
        self._gen += 1
        gen = self._gen
        mode = self.var_mode.get()
        if mode == "ziel":
            t = self._target_bytes()
            if t is None:
                self.result.config(text="Zielgröße in MB eingeben, z. B. 5 oder 2,5", foreground=C_HINT)
                return
            args = ("ziel", t)
        else:
            d = self.var_dpi.get()
            args = ("manuell", 0 if d == "Original" else int(d), int(self.var_q.get()))
        self.result.config(text="Berechne …", foreground=C_HINT)
        threading.Thread(target=self._predict_worker, args=(gen, args), daemon=True).start()

    def _predict_worker(self, gen, args):
        try:
            if args[0] == "ziel":
                dpi, q, ok = self.an.search(args[1])
                size = self.an.predict(dpi, q)
            else:
                dpi, q = args[1], args[2]
                ok = True
                size = self.an.predict(dpi, q)
            self._q.put(("pred", gen, dpi, q, ok, size))
        except Exception as exc:
            self._q.put(("err", gen, str(exc)))

    # ---------- Analyse ----------

    def _analyse(self):
        try:
            an = Analyse(self._data)
            self._q.put(("analysed", an))
        except Exception as exc:
            self._q.put(("err", -1, f"Analyse fehlgeschlagen: {exc}"))

    # ---------- Speichern ----------

    def do_save(self):
        if self._busy or self.an is None:
            return
        mode = self.var_mode.get()
        target = None
        if mode == "ziel":
            target = self._target_bytes()
            if target is None:
                messagebox.showwarning(APP_TITLE, "Bitte eine Zielgröße in MB eingeben.", parent=self)
                return
        ziel = filedialog.asksaveasfilename(
            parent=self, title="Verkleinerte PDF speichern", initialdir=str(self.path.parent),
            initialfile=f"{self.path.stem}_klein.pdf", defaultextension=".pdf",
            filetypes=[("PDF-Dateien", "*.pdf")])
        if not ziel:
            return
        ziel = Path(ziel)
        if ziel.exists() and ziel.resolve() == self.path.resolve():
            messagebox.showwarning(APP_TITLE, "Die geöffnete Datei kann hier nicht überschrieben "
                                   "werden. Bitte einen anderen Namen wählen.", parent=self)
            return
        if mode == "ziel":
            args = ("ziel", target)
        else:
            d = self.var_dpi.get()
            args = ("manuell", 0 if d == "Original" else int(d), int(self.var_q.get()))
        self._busy = True
        self._gen += 1            # verspätete Vorhersagen verwerfen
        self._set_enabled(False)
        self.result.config(text="Verkleinere …", foreground=C_HINT)
        threading.Thread(target=self._save_worker, args=(args, ziel), daemon=True).start()

    def _save_worker(self, args, ziel: Path):
        try:
            prog = lambda i, n: self._q.put(("prog", i, n))
            if args[0] == "ziel":
                target = args[1]
                dpi, q, _ = self.an.search(target)
                for _ in range(4):
                    size = self.an.build(dpi, q, ziel, prog)
                    if size <= target * 1.02 or q <= Q_MIN:
                        break
                    q = max(Q_MIN, q - 8)
            else:
                dpi, q = args[1], args[2]
                size = self.an.build(dpi, q, ziel, prog)
            self._q.put(("done", size, dpi, q, ziel))
        except Exception as exc:
            self._q.put(("fail", str(exc)))

    # ---------- Rückmeldungen aus den Fäden ----------

    def _poll(self):
        try:
            while True:
                m = self._q.get_nowait()
                self._handle(m)
        except queue.Empty:
            pass
        try:
            self.after(100, self._poll)
        except tk.TclError:
            pass

    def _handle(self, m):
        kind = m[0]
        if kind == "analysed":
            self.an = m[1]
            an = self.an
            orig = len(self._data)
            if not an.items:
                self.info.config(text=f"Aktuell {mb_text(orig)}. Keine verkleinerbaren Bilder "
                                      "gefunden (nur Struktur wird optimiert).")
            else:
                self.info.config(text=f"Aktuell {mb_text(orig)}, {len(an.items)} Bilder "
                                      f"({mb_text(an.img_old)}), höchste Auflösung ≈ "
                                      f"{an.max_dpi:.0f} dpi.")
            self.var_target.set(f"{max(0.1, orig / 1048576 / 2):.1f}".replace(".", ","))
            self._set_enabled(True)
            self._changed()
        elif kind == "pred":
            _, gen, dpi, q, ok, size = m
            if gen != self._gen:
                return
            orig = len(self._data)
            txt = (f"Voraussichtlich ≈ {mb_text(size)}  ({(1 - size / orig) * 100:+.0f} %".replace("+", "−")
                   if size < orig else f"Voraussichtlich ≈ {mb_text(size)}  (keine Ersparnis")
            txt += f")\nAuflösung {'Original' if not dpi else str(dpi) + ' dpi'}, Qualität {q}"
            if not ok:
                txt += "\nZielgröße nicht erreichbar – kleinste Einstellung gezeigt"
            if self.var_mode.get() == "ziel":
                self.cb_dpi.set("Original" if not dpi else str(dpi))
                self.var_q.set(q)
                self.lbl_q.config(text=str(q))
            self.result.config(text=txt, foreground=C_WARN if not ok else C_OK)
        elif kind == "prog":
            self.prog.config(maximum=max(1, m[2]), value=m[1])
        elif kind == "done":
            _, size, dpi, q, ziel = m
            self._busy = False
            self._set_enabled(True)
            self.prog.config(value=0)
            orig = len(self._data)
            self.result.config(
                text=f"Fertig: {mb_text(size)} statt {mb_text(orig)} "
                     f"({(1 - size / orig) * 100:.0f} % kleiner)\n{ziel.name}", foreground=C_OK)
        elif kind == "fail":
            self._busy = False
            self._set_enabled(True)
            self.result.config(text="Fehlgeschlagen", foreground=C_ERR)
            messagebox.showerror(APP_TITLE, f"Verkleinern fehlgeschlagen:\n{m[1]}", parent=self)
        elif kind == "err":
            if m[1] in (-1, self._gen):
                self.result.config(text=m[2], foreground=C_ERR)
