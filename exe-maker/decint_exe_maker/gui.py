"""DECINT EXE Maker — the desktop GUI.

Tabs: Build (project → licensed EXE), Licenses (issue + ledger), Verify (inspect
a key), Settings (Venice key/model, build interpreter). Long work (analysis,
pip, PyInstaller) runs on worker threads and reports back through a queue.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__, analyzer, branding, builder, settings, theme, toolchain, vendor
from .runtime import licensing, rsa_lite
from .venice import FALLBACK_MODELS, Venice, VeniceError

AUTO_FIXES = {"pip_install", "hidden_import", "collect_all", "exclude_module", "data_file",
              "app_type", "chdir_to_bundle", "onedir"}


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        self.geometry("1220x820")
        self.minsize(1000, 700)
        theme.apply(self)
        try:
            self.iconbitmap(default=str(branding.default_icon()))
        except tk.TclError:
            pass

        self.cfg = settings.load()
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.report: analyzer.Report | None = None
        self.ai_result: dict | None = None
        self.last_result: builder.BuildResult | None = None
        self.last_diagnosis: dict | None = None
        self.busy = False

        self._header()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.tab_build = ttk.Frame(self.nb, style="Panel.TFrame")
        self.tab_lic = ttk.Frame(self.nb, style="Panel.TFrame")
        self.tab_verify = ttk.Frame(self.nb, style="Panel.TFrame")
        self.tab_settings = ttk.Frame(self.nb, style="Panel.TFrame")
        for tab, name in ((self.tab_build, "  Build  "), (self.tab_lic, "  Licenses  "),
                          (self.tab_verify, "  Verify  "), (self.tab_settings, "  Settings  ")):
            self.nb.add(tab, text=name)
        self._build_tab()
        self._licenses_tab()
        self._verify_tab()
        self._settings_tab()
        self.after(100, self._poll)
        self.after(300, self._startup_checks)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ───────────────────────────── chrome ─────────────────────────────

    def _header(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=16, pady=(12, 8))
        ttk.Label(bar, text="▲", foreground=theme.VIOLET, font=theme.TITLE_FONT).pack(side="left", padx=(0, 8))
        ttk.Label(bar, text="DECINT EXE Maker", style="Title.TLabel").pack(side="left")
        ttk.Label(bar, text="  wrap · license · ship", style="Muted.TLabel").pack(side="left", pady=(6, 0))
        self.ai_status = ttk.Label(bar, text="AI: not configured", style="Muted.TLabel")
        self.ai_status.pack(side="right")

    def _startup_checks(self) -> None:
        if settings.venice_key(self.cfg):
            self.ai_status.configure(text="AI: Venice ready", style="Good.TLabel")
        self._detect_python(quiet=True)
        self._refresh_products()
        last = self.cfg.get("last_project")
        if last and Path(last).is_dir():
            self.v_project.set(last)

    def _on_close(self) -> None:
        if self.busy and not messagebox.askyesno(APP_NAME, "A build is running. Quit anyway?"):
            return
        self.cancel.set()
        self.destroy()

    # ───────────────────────────── helpers ─────────────────────────────

    def _bg(self, fn, on_done=None, on_error=None) -> None:
        """Run fn() on a thread; deliver its result/exception on the Tk thread."""
        def worker():
            try:
                res = fn()
                self.q.put(("done", on_done, res))
            except Exception as ex:  # noqa: BLE001 — surfaced to the user
                self.q.put(("error", on_error, ex))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, cb, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "done":
                    if cb:
                        cb(payload)
                elif kind == "error":
                    if cb:
                        cb(payload)
                    else:
                        self.log(f"ERROR: {payload}")
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def log(self, line: str) -> None:
        """Thread-safe log append."""
        self.q.put(("log", None, line))

    def _append_log(self, line: str) -> None:
        self.txt_log.configure(state="normal")
        tag = None
        low = line.lower()
        if low.startswith(("error", "traceback")) or " error:" in low or "failed" in low:
            tag = "bad"
        elif low.startswith(("warning", "warn")):
            tag = "warn"
        elif low.startswith(("built ", "ok", "✓", "done")):
            tag = "good"
        elif line.startswith("$ "):
            tag = "cmd"
        self.txt_log.insert("end", line + "\n", tag)
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    @staticmethod
    def _csv(var: tk.StringVar) -> list[str]:
        return [s.strip() for s in var.get().replace("\n", ",").split(",") if s.strip()]

    def _venice(self) -> Venice:
        return Venice(settings.venice_key(self.cfg), self.cfg.get("venice_model", ""))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.btn_build.configure(state="disabled" if busy else "normal")
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    # ═══════════════════════════════ BUILD TAB ═══════════════════════════════

    def _build_tab(self) -> None:
        t = self.tab_build
        t.columnconfigure(0, weight=1, uniform="col")
        t.columnconfigure(1, weight=1, uniform="col")
        t.rowconfigure(1, weight=1)

        # ── left: project + product ──
        left = ttk.Frame(t)
        left.grid(row=0, column=0, sticky="nsew", padx=(14, 7), pady=12)
        left.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(left, text="PROJECT", style="Section.TLabel").grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        self.v_project = tk.StringVar()
        self.v_project.trace_add("write", lambda *_: self._project_changed())
        ttk.Label(left, text="Folder").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(left, textvariable=self.v_project).grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Button(left, text="Browse…", command=self._pick_project).grid(row=r, column=2, padx=(6, 0)); r += 1

        row = ttk.Frame(left)
        row.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(2, 8)); r += 1
        ttk.Button(row, text="Analyze", command=self._analyze_static).pack(side="left")
        self.btn_ai = ttk.Button(row, text="Analyze with Venice AI", style="Accent.TButton", command=self._analyze_ai)
        self.btn_ai.pack(side="left", padx=8)
        self.lbl_analysis = ttk.Label(row, text="", style="Muted.TLabel")
        self.lbl_analysis.pack(side="left", padx=4)

        ttk.Label(left, text="Entry script").grid(row=r, column=0, sticky="w", pady=3)
        self.v_entry = tk.StringVar()
        self.cb_entry = ttk.Combobox(left, textvariable=self.v_entry)
        self.cb_entry.grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Button(left, text="Pick…", command=self._pick_entry).grid(row=r, column=2, padx=(6, 0)); r += 1

        ttk.Label(left, text="PRODUCT", style="Section.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", pady=(10, 0)); r += 1
        self.v_name = tk.StringVar()
        self.v_name.trace_add("write", lambda *_: self._name_changed())
        self.v_pid = tk.StringVar()
        self.v_version = tk.StringVar(value="1.0.0")
        self.v_desc = tk.StringVar()
        for label, var in (("Product name", self.v_name), ("Product ID", self.v_pid),
                           ("Version", self.v_version), ("Description", self.v_desc)):
            ttk.Label(left, text=label).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Entry(left, textvariable=var).grid(row=r, column=1, columnspan=2, sticky="ew", pady=3); r += 1
        self.lbl_key = ttk.Label(left, text="Product key: created on first build", style="Muted.TLabel")
        self.lbl_key.grid(row=r, column=1, columnspan=2, sticky="w"); r += 1

        ttk.Label(left, text="App type").grid(row=r, column=0, sticky="w", pady=3)
        self.v_apptype = tk.StringVar(value="gui")
        row = ttk.Frame(left)
        row.grid(row=r, column=1, columnspan=2, sticky="w"); r += 1
        ttk.Radiobutton(row, text="Window app (no console)", variable=self.v_apptype, value="gui").pack(side="left")
        ttk.Radiobutton(row, text="Console app", variable=self.v_apptype, value="console").pack(side="left", padx=12)

        ttk.Label(left, text="Icon (.ico)").grid(row=r, column=0, sticky="w", pady=3)
        self.v_icon = tk.StringVar()
        ttk.Entry(left, textvariable=self.v_icon).grid(row=r, column=1, sticky="ew", pady=3)
        row = ttk.Frame(left)
        row.grid(row=r, column=2, padx=(6, 0)); r += 1
        ttk.Button(row, text="Browse…", command=self._pick_icon).pack(side="left")
        ttk.Label(left, text="(empty = DECINT shield)", style="Muted.TLabel").grid(row=r, column=1, sticky="w"); r += 1

        ttk.Label(left, text="Output folder").grid(row=r, column=0, sticky="w", pady=3)
        self.v_out = tk.StringVar(value=self.cfg.get("output_dir", ""))
        ttk.Entry(left, textvariable=self.v_out).grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Button(left, text="Browse…", command=self._pick_out).grid(row=r, column=2, padx=(6, 0)); r += 1

        # ── right: packaging + licensing ──
        right = ttk.Frame(t)
        right.grid(row=0, column=1, sticky="nsew", padx=(7, 14), pady=12)
        right.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(right, text="PACKAGING", style="Section.TLabel").grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        self.v_onefile = tk.BooleanVar(value=True)
        self.v_splash = tk.BooleanVar(value=True)
        self.v_install = tk.BooleanVar(value=True)
        self.v_chdir = tk.BooleanVar(value=False)
        checks = ttk.Frame(right)
        checks.grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        ttk.Checkbutton(checks, text="Single-file EXE", variable=self.v_onefile).grid(row=0, column=0, sticky="w", padx=(0, 14))
        ttk.Checkbutton(checks, text="DECINT splash screen", variable=self.v_splash).grid(row=0, column=1, sticky="w", padx=(0, 14))
        ttk.Checkbutton(checks, text="Install missing packages", variable=self.v_install).grid(row=1, column=0, sticky="w", padx=(0, 14))
        ttk.Checkbutton(checks, text="Run from bundle folder", variable=self.v_chdir).grid(row=1, column=1, sticky="w")

        self.v_hidden = tk.StringVar()
        self.v_collect = tk.StringVar()
        self.v_exclude = tk.StringVar()
        for label, var in (("Hidden imports", self.v_hidden), ("Collect-all", self.v_collect),
                           ("Exclude modules", self.v_exclude)):
            ttk.Label(right, text=label).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Entry(right, textvariable=var).grid(row=r, column=1, columnspan=2, sticky="ew", pady=3); r += 1

        ttk.Label(right, text="Data files").grid(row=r, column=0, sticky="nw", pady=3)
        box = ttk.Frame(right)
        box.grid(row=r, column=1, columnspan=2, sticky="ew", pady=3); r += 1
        box.columnconfigure(0, weight=1)
        self.lst_data = theme.listbox(box, height=4, selectmode="extended")
        self.lst_data.grid(row=0, column=0, rowspan=3, sticky="nsew")
        ttk.Button(box, text="+ File", style="Ghost.TButton", command=self._add_data_file).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(box, text="+ Folder", style="Ghost.TButton", command=self._add_data_dir).grid(row=1, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(box, text="Remove", style="Ghost.TButton", command=self._remove_data).grid(row=2, column=1, sticky="ew", padx=(4, 0))

        ttk.Label(right, text="LICENSING", style="Section.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", pady=(10, 0)); r += 1
        self.v_lic = tk.BooleanVar(value=True)
        self.v_bind = tk.BooleanVar(value=False)
        checks = ttk.Frame(right)
        checks.grid(row=r, column=0, columnspan=3, sticky="w"); r += 1
        ttk.Checkbutton(checks, text="Require a DECINT license key", variable=self.v_lic).pack(side="left", padx=(0, 14))
        ttk.Checkbutton(checks, text="Bind keys to one machine", variable=self.v_bind).pack(side="left")

        ttk.Label(right, text="Free trial (days)").grid(row=r, column=0, sticky="w", pady=3)
        self.v_trial = tk.StringVar(value="0")
        ttk.Spinbox(right, from_=0, to=365, textvariable=self.v_trial, width=6).grid(row=r, column=1, sticky="w", pady=3); r += 1
        ttk.Label(right, text="Vendor contact").grid(row=r, column=0, sticky="w", pady=3)
        self.v_contact = tk.StringVar(value=self.cfg.get("vendor_contact", ""))
        ttk.Entry(right, textvariable=self.v_contact).grid(row=r, column=1, columnspan=2, sticky="ew", pady=3); r += 1
        ttk.Label(right, text="Activation UI").grid(row=r, column=0, sticky="w", pady=3)
        self.v_ui = tk.StringVar(value="auto")
        ttk.Combobox(right, textvariable=self.v_ui, values=["auto", "tk", "console"], state="readonly", width=10).grid(row=r, column=1, sticky="w", pady=3); r += 1
        ttk.Label(right, text="Customers see: activation window → paste key → app runs. Keys expire; 14-day warning.",
                  style="Muted.TLabel", wraplength=520).grid(row=r, column=0, columnspan=3, sticky="w"); r += 1

        # ── bottom: actions + log ──
        bottom = ttk.Frame(t)
        bottom.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=14, pady=(0, 12))
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1)
        actions = ttk.Frame(bottom)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.btn_build = ttk.Button(actions, text="BUILD EXE", style="Accent.TButton", command=self._start_build)
        self.btn_build.pack(side="left")
        self.btn_cancel = ttk.Button(actions, text="Cancel", command=self.cancel.set, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        ttk.Button(actions, text="Open output", command=self._open_output).pack(side="left", padx=6)
        self.btn_diag = ttk.Button(actions, text="Diagnose failure with AI", command=self._diagnose, state="disabled")
        self.btn_diag.pack(side="left", padx=6)
        self.btn_fix = ttk.Button(actions, text="Apply AI fixes", command=self._apply_fixes, state="disabled")
        self.btn_fix.pack(side="left", padx=6)
        ttk.Button(actions, text="Clear log", style="Ghost.TButton", command=self._clear_log).pack(side="right")
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=160)
        self.progress.pack(side="right", padx=10)

        logf = ttk.Frame(bottom)
        logf.grid(row=1, column=0, sticky="nsew")
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.txt_log = theme.text_widget(logf, state="disabled", height=12, wrap="none")
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logf, command=self.txt_log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.tag_configure("bad", foreground=theme.BAD)
        self.txt_log.tag_configure("warn", foreground=theme.WARN)
        self.txt_log.tag_configure("good", foreground=theme.GOOD)
        self.txt_log.tag_configure("cmd", foreground=theme.MUTED)
        self.txt_log.tag_configure("ai", foreground=theme.VIOLET_HI)

    # ── build tab: pickers ──

    def _pick_project(self) -> None:
        d = filedialog.askdirectory(title="Select the Python project folder")
        if d:
            self.v_project.set(d)
            self._analyze_static()

    def _pick_entry(self) -> None:
        proj = self.v_project.get()
        f = filedialog.askopenfilename(title="Entry script", initialdir=proj or None,
                                       filetypes=[("Python", "*.py *.pyw"), ("All", "*.*")])
        if f:
            try:
                self.v_entry.set(Path(f).relative_to(Path(proj)).as_posix())
            except ValueError:
                messagebox.showerror(APP_NAME, "The entry script must be inside the project folder.")

    def _pick_icon(self) -> None:
        f = filedialog.askopenfilename(title="Icon", filetypes=[("Icon", "*.ico"), ("All", "*.*")])
        if f:
            self.v_icon.set(f)

    def _pick_out(self) -> None:
        d = filedialog.askdirectory(title="Output folder")
        if d:
            self.v_out.set(d)

    def _add_data_file(self) -> None:
        proj = self.v_project.get()
        for f in filedialog.askopenfilenames(title="Data files", initialdir=proj or None):
            self._add_data(f)

    def _add_data_dir(self) -> None:
        d = filedialog.askdirectory(title="Data folder", initialdir=self.v_project.get() or None)
        if d:
            self._add_data(d)

    def _add_data(self, path: str) -> None:
        try:
            rel = Path(path).resolve().relative_to(Path(self.v_project.get()).resolve()).as_posix()
        except ValueError:
            messagebox.showerror(APP_NAME, "Data files must be inside the project folder.")
            return
        if rel not in self.lst_data.get(0, "end"):
            self.lst_data.insert("end", rel)

    def _remove_data(self) -> None:
        for i in reversed(self.lst_data.curselection()):
            self.lst_data.delete(i)

    def _project_changed(self) -> None:
        p = self.v_project.get()
        if p and Path(p).is_dir():
            self.cfg["last_project"] = p

    def _name_changed(self) -> None:
        if not getattr(self, "_pid_manual", False):
            self.v_pid.set(vendor.slugify(self.v_name.get()))
        self._update_key_label()

    def _update_key_label(self) -> None:
        pid = self.v_pid.get().strip()
        k = vendor.load_key(pid) if pid else None
        if k:
            self.lbl_key.configure(text=f"Product key: exists · fingerprint {rsa_lite.fingerprint(k['n'], k['e'])}",
                                   style="Good.TLabel")
        else:
            self.lbl_key.configure(text="Product key: will be created on first build (back it up afterwards)",
                                   style="Muted.TLabel")

    # ── build tab: analysis ──

    def _analyze_static(self) -> None:
        proj = self.v_project.get().strip()
        if not proj or not Path(proj).is_dir():
            messagebox.showwarning(APP_NAME, "Pick a project folder first.")
            return
        try:
            rep = analyzer.analyze(proj)
        except Exception as ex:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"Analysis failed: {ex}")
            return
        self.report = rep
        self.ai_result = None
        self.cb_entry.configure(values=rep.entry_candidates or [f.rel for f in rep.files])
        if rep.entry:
            self.v_entry.set(rep.entry)
        self.v_name.set(rep.suggested_name)
        self.v_version.set(rep.suggested_version)
        self.v_apptype.set(rep.app_type)
        self.v_hidden.set(", ".join(rep.hidden_imports))
        self.v_collect.set(", ".join(rep.collect_all))
        self.lst_data.delete(0, "end")
        for rel in rep.data_files[:200]:
            self.lst_data.insert("end", rel)
        self.v_splash.set(rep.app_type == "gui")
        self.lbl_analysis.configure(text=f"{len(rep.files)} .py files · {rep.app_type}"
                                         f"{' (' + rep.gui_toolkit + ')' if rep.gui_toolkit else ''}")
        self.log(f"Static analysis: entry={rep.entry} type={rep.app_type} third-party={', '.join(rep.third_party) or 'none'}")
        for n in rep.notes:
            self.log(f"WARNING: {n}")
        self._update_key_label()

    def _analyze_ai(self) -> None:
        if not settings.venice_key(self.cfg):
            messagebox.showinfo(APP_NAME, "Add your Venice API key in Settings first.")
            self.nb.select(self.tab_settings)
            return
        if self.report is None or str(self.report.project_dir) != str(Path(self.v_project.get()).resolve()):
            self._analyze_static()
            if self.report is None:
                return
        rep = self.report
        self.btn_ai.configure(state="disabled")
        self.lbl_analysis.configure(text="Venice is reading the project…")
        self.log("AI: sending static report + file excerpts to Venice …")

        def work():
            return self._venice().analyze_project(rep.as_dict(), analyzer.excerpts(rep))

        def done(res: dict):
            self.btn_ai.configure(state="normal")
            self.ai_result = res
            if res.get("entry_point"):
                self.v_entry.set(res["entry_point"])
            self.v_apptype.set(res["app_type"])
            self.v_name.set(res["product_name"])
            self.v_desc.set(res["description"])
            self.v_version.set(res["version"])
            self.v_hidden.set(", ".join(dict.fromkeys(rep.hidden_imports + res["hidden_imports"])))
            self.v_collect.set(", ".join(dict.fromkeys(rep.collect_all + res["collect_all"])))
            self.v_exclude.set(", ".join(res["exclude_modules"]))
            self.v_chdir.set(res["chdir_to_bundle"])
            self.v_splash.set(res["app_type"] == "gui")
            if res["data_files"]:
                self.lst_data.delete(0, "end")
                for rel in res["data_files"]:
                    if (rep.project_dir / rel).exists():
                        self.lst_data.insert("end", rel)
            self.lbl_analysis.configure(text=f"AI: {res['app_type']} app · entry {res['entry_point']}")
            self.log(f"AI: entry={res['entry_point']} type={res['app_type']} name='{res['product_name']}' v{res['version']}")
            if res["pip_requirements"]:
                self.log("AI: pip requirements → " + ", ".join(res["pip_requirements"]))
            for risk in res["risks"]:
                self.log(f"AI risk: {risk}")
            if res["notes"]:
                self.log(f"AI notes: {res['notes']}")

        def err(ex: Exception):
            self.btn_ai.configure(state="normal")
            self.lbl_analysis.configure(text="AI analysis failed")
            self.log(f"ERROR: AI analysis failed — {ex}")

        self._bg(work, done, err)

    # ── build tab: build ──

    def _spec(self) -> builder.BuildSpec | None:
        proj = self.v_project.get().strip()
        entry = self.v_entry.get().strip()
        name = self.v_name.get().strip()
        if not proj or not Path(proj).is_dir():
            messagebox.showwarning(APP_NAME, "Pick a project folder.")
            return None
        if not entry or not (Path(proj) / entry).is_file():
            messagebox.showwarning(APP_NAME, "Pick the entry script (the file you run to start the app).")
            return None
        if not name:
            messagebox.showwarning(APP_NAME, "Give the product a name.")
            return None
        try:
            trial = int(self.v_trial.get() or 0)
        except ValueError:
            trial = 0
        rep = self.report
        third = rep.third_party if rep and str(rep.project_dir) == str(Path(proj).resolve()) else []
        pip_reqs = (self.ai_result or {}).get("pip_requirements") or (rep.pip_requirements if rep else [])
        return builder.BuildSpec(
            project_dir=proj, entry_file=entry, product_name=name,
            product_id=self.v_pid.get().strip() or vendor.slugify(name),
            version=self.v_version.get().strip() or "1.0.0", description=self.v_desc.get().strip(),
            app_type=self.v_apptype.get(), onefile=self.v_onefile.get(), icon_path=self.v_icon.get().strip(),
            splash=self.v_splash.get(), hidden_imports=self._csv(self.v_hidden),
            collect_all=self._csv(self.v_collect), exclude_modules=self._csv(self.v_exclude),
            data_files=list(self.lst_data.get(0, "end")), chdir_to_bundle=self.v_chdir.get(),
            install_missing=self.v_install.get(), pip_requirements=pip_reqs, third_party_imports=third,
            licensing=self.v_lic.get(), bind_machine=self.v_bind.get(), trial_days=trial,
            vendor_contact=self.v_contact.get().strip(), activation_ui=self.v_ui.get(),
            output_dir=self.v_out.get().strip(), python=self.cfg.get("build_python", ""),
            company=self.cfg.get("company") or "DECINT",
            copyright_holder=self.cfg.get("copyright_holder") or "DECINT")

    def _start_build(self) -> None:
        spec = self._spec()
        if not spec:
            return
        self.cancel.clear()
        self._set_busy(True)
        self.btn_diag.configure(state="disabled")
        self.btn_fix.configure(state="disabled")
        self.last_result = None
        self.last_diagnosis = None
        self._current_spec = spec
        self.cfg["output_dir"] = spec.output_dir
        self.cfg["vendor_contact"] = spec.vendor_contact
        settings.save(self.cfg)
        self.log(f"=== Building {spec.product_name} {spec.version} ({spec.product_id}) ===")

        def work():
            return builder.build(spec, self.log, self.cancel)

        def done(res: builder.BuildResult):
            self._set_busy(False)
            self.last_result = res
            self._update_key_label()
            self._refresh_products()
            if res.ok:
                self.log(f"OK — {res.exe_path}")
                self.v_out.set(spec.output_dir)
                if spec.licensing:
                    self.log("Next: Licenses tab → generate a key for each customer (default 6 months).")
                    if messagebox.askyesno(APP_NAME, f"Built:\n{res.exe_path}\n\nGenerate a customer license now?"):
                        self.v_lic_product.set(spec.product_id)
                        self._load_ledger()
                        self.nb.select(self.tab_lic)
            else:
                self.log(f"ERROR: {res.error}")
                if res.log_path:
                    self.log(f"Full log: {res.log_path}")
                self.btn_diag.configure(state="normal" if settings.venice_key(self.cfg) else "disabled")

        def err(ex: Exception):
            self._set_busy(False)
            if isinstance(ex, builder.BuildCancelled):
                self.log("Build cancelled.")
            else:
                self.log(f"ERROR: {ex!r}")

        self._bg(work, done, err)

    def _open_output(self) -> None:
        d = self.v_out.get().strip()
        if not d and self.last_result and self.last_result.exe_path:
            d = str(Path(self.last_result.exe_path).parent)
        if not d or not Path(d).exists():
            messagebox.showinfo(APP_NAME, "Nothing built yet.")
            return
        if sys.platform == "win32":
            os.startfile(d)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", d])
        else:
            subprocess.Popen(["xdg-open", d])

    # ── build tab: AI diagnosis ──

    def _diagnose(self) -> None:
        res = self.last_result
        if not res or not res.log_path or not Path(res.log_path).is_file():
            log_text = self.txt_log.get("1.0", "end")
        else:
            log_text = Path(res.log_path).read_text(encoding="utf-8", errors="replace")
        spec = getattr(self, "_current_spec", None)
        cfg = spec.as_public_dict() if spec else {}
        self.btn_diag.configure(state="disabled")
        self.log("AI: diagnosing the failed build …")

        def work():
            return self._venice().diagnose_build(log_text, cfg)

        def done(d: dict):
            self.last_diagnosis = d
            self.log("AI diagnosis: " + d.get("summary", ""))
            auto = 0
            for f in d.get("fixes", []):
                self.log(f"  fix [{f['type']}] {f['value']} — {f['why']}")
                auto += f["type"] in AUTO_FIXES
            self.btn_diag.configure(state="normal")
            self.btn_fix.configure(state="normal" if auto else "disabled")

        def err(ex: Exception):
            self.btn_diag.configure(state="normal")
            self.log(f"ERROR: diagnosis failed — {ex}")

        self._bg(work, done, err)

    def _apply_fixes(self) -> None:
        d = self.last_diagnosis or {}
        applied, pip_pkgs = 0, []
        for f in d.get("fixes", []):
            t, v = f["type"], f["value"].strip()
            if t == "pip_install":
                pip_pkgs.append(v)
            elif t == "hidden_import":
                self.v_hidden.set(", ".join(dict.fromkeys(self._csv(self.v_hidden) + [v])))
            elif t == "collect_all":
                self.v_collect.set(", ".join(dict.fromkeys(self._csv(self.v_collect) + [v])))
            elif t == "exclude_module":
                self.v_exclude.set(", ".join(dict.fromkeys(self._csv(self.v_exclude) + [v])))
            elif t == "data_file":
                if (Path(self.v_project.get()) / v).exists() and v not in self.lst_data.get(0, "end"):
                    self.lst_data.insert("end", v)
            elif t == "app_type" and v in ("gui", "console"):
                self.v_apptype.set(v)
            elif t == "chdir_to_bundle":
                self.v_chdir.set(v.lower() in ("true", "1", "yes"))
            elif t == "onedir":
                self.v_onefile.set(False)
            else:
                continue
            applied += 1
        self.btn_fix.configure(state="disabled")
        if pip_pkgs:
            py = self.cfg.get("build_python") or toolchain.find_python()
            if py:
                self.log(f"Installing: {', '.join(pip_pkgs)}")
                self._bg(lambda: toolchain.pip_install(py, pip_pkgs, self.log),
                         lambda ok: self.log("pip done" if ok else "WARNING: pip reported errors"))
        self.log(f"Applied {applied} fix(es). Click BUILD EXE to try again.")

    # ═══════════════════════════════ LICENSES TAB ═══════════════════════════════

    def _licenses_tab(self) -> None:
        t = self.tab_lic
        t.columnconfigure(0, weight=1)
        t.rowconfigure(2, weight=1)

        top = ttk.Frame(t)
        top.grid(row=0, column=0, sticky="ew", padx=14, pady=12)
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)
        ttk.Label(top, text="ISSUE A LICENSE", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(top, text="Product").grid(row=1, column=0, sticky="w", pady=3)
        self.v_lic_product = tk.StringVar()
        self.cb_product = ttk.Combobox(top, textvariable=self.v_lic_product, state="readonly")
        self.cb_product.grid(row=1, column=1, sticky="ew", pady=3)
        self.cb_product.bind("<<ComboboxSelected>>", lambda _e: self._load_ledger())
        ttk.Button(top, text="Refresh", style="Ghost.TButton", command=self._refresh_products).grid(row=1, column=2, padx=6)
        self.lbl_fp = ttk.Label(top, text="", style="Muted.TLabel")
        self.lbl_fp.grid(row=1, column=3, sticky="w")

        self.v_cust = tk.StringVar()
        self.v_email = tk.StringVar()
        self.v_months = tk.StringVar(value="6")
        self.v_expires = tk.StringVar()
        self.v_mid = tk.StringVar()
        self.v_feat = tk.StringVar()
        self.v_note = tk.StringVar()
        ttk.Label(top, text="Customer").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(top, textvariable=self.v_cust).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(top, text="Email").grid(row=2, column=2, sticky="w", padx=(12, 6))
        ttk.Entry(top, textvariable=self.v_email).grid(row=2, column=3, sticky="ew", pady=3)

        ttk.Label(top, text="Duration (months)").grid(row=3, column=0, sticky="w", pady=3)
        row = ttk.Frame(top)
        row.grid(row=3, column=1, sticky="w")
        ttk.Spinbox(row, from_=1, to=120, textvariable=self.v_months, width=6).pack(side="left")
        for m in (1, 3, 6, 12):
            ttk.Button(row, text=f"{m}m", style="Ghost.TButton", command=lambda m=m: self._set_months(m)).pack(side="left", padx=2)
        ttk.Label(top, text="or expires on").grid(row=3, column=2, sticky="w", padx=(12, 6))
        ttk.Entry(top, textvariable=self.v_expires).grid(row=3, column=3, sticky="ew", pady=3)
        ttk.Label(top, text="YYYY-MM-DD (overrides months)", style="Muted.TLabel").grid(row=4, column=3, sticky="w")

        ttk.Label(top, text="Machine ID (optional)").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Entry(top, textvariable=self.v_mid).grid(row=5, column=1, sticky="ew", pady=3)
        ttk.Label(top, text="Features").grid(row=5, column=2, sticky="w", padx=(12, 6))
        ttk.Entry(top, textvariable=self.v_feat).grid(row=5, column=3, sticky="ew", pady=3)
        ttk.Label(top, text="Note").grid(row=6, column=0, sticky="w", pady=3)
        ttk.Entry(top, textvariable=self.v_note).grid(row=6, column=1, columnspan=3, sticky="ew", pady=3)

        actions = ttk.Frame(top)
        actions.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="GENERATE LICENSE", style="Accent.TButton", command=self._issue).pack(side="left")
        ttk.Button(actions, text="Copy key", command=self._copy_key).pack(side="left", padx=6)
        ttk.Button(actions, text="Save .lic…", command=self._save_key).pack(side="left")
        ttk.Button(actions, text="Backup product key…", style="Ghost.TButton", command=self._backup_key).pack(side="right")
        ttk.Button(actions, text="Import product key…", style="Ghost.TButton", command=self._import_key).pack(side="right", padx=6)

        keyf = ttk.Frame(t)
        keyf.grid(row=1, column=0, sticky="ew", padx=14)
        keyf.columnconfigure(0, weight=1)
        self.txt_key = theme.text_widget(keyf, height=4, wrap="char")
        self.txt_key.grid(row=0, column=0, sticky="ew")

        ledf = ttk.Frame(t)
        ledf.grid(row=2, column=0, sticky="nsew", padx=14, pady=12)
        ledf.columnconfigure(0, weight=1)
        ledf.rowconfigure(1, weight=1)
        hdr = ttk.Frame(ledf)
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(hdr, text="ISSUED LICENSES", style="Section.TLabel").pack(side="left")
        ttk.Button(hdr, text="Export CSV…", style="Ghost.TButton", command=self._export_csv).pack(side="right")
        ttk.Button(hdr, text="Copy selected key", style="Ghost.TButton", command=self._copy_selected).pack(side="right", padx=6)
        cols = ("lid", "cust", "email", "issued", "expires", "left", "mid", "feat")
        self.tree = ttk.Treeview(ledf, columns=cols, show="headings", selectmode="browse")
        for c, label, w in (("lid", "ID", 100), ("cust", "Customer", 180), ("email", "Email", 180),
                            ("issued", "Issued", 90), ("expires", "Expires", 90), ("left", "Days left", 70),
                            ("mid", "Machine", 150), ("feat", "Features", 120)):
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(ledf, command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.tag_configure("expired", foreground=theme.MUTED)
        self.tree.tag_configure("soon", foreground=theme.WARN)

    def _set_months(self, m: int) -> None:
        self.v_months.set(str(m))
        self.v_expires.set("")

    def _refresh_products(self) -> None:
        prods = vendor.list_products()
        ids = [p["product_id"] for p in prods]
        self.cb_product.configure(values=ids)
        if ids and self.v_lic_product.get() not in ids:
            self.v_lic_product.set(ids[0])
        self._load_ledger()

    def _load_ledger(self) -> None:
        pid = self.v_lic_product.get()
        for i in self.tree.get_children():
            self.tree.delete(i)
        if not pid:
            self.lbl_fp.configure(text="No products yet — build one first.")
            return
        k = vendor.load_key(pid)
        self.lbl_fp.configure(text=f"key {rsa_lite.fingerprint(k['n'], k['e'])}" if k else "")
        now = time.time()
        for r in reversed(vendor.ledger(pid)):
            left = int((r["exp"] - now) // 86400)
            tag = "expired" if left < 0 else ("soon" if left <= 14 else "")
            self.tree.insert("", "end", iid=r["lid"] + str(r["iat"]), tags=(tag,), values=(
                r["lid"], r.get("cust", ""), r.get("email", ""), licensing.fmt_date(r["iat"]),
                licensing.fmt_date(r["exp"]), left if left >= 0 else "expired", r.get("mid") or "—",
                ", ".join(r.get("feat") or [])))

    def _issue(self) -> None:
        pid = self.v_lic_product.get()
        if not pid:
            messagebox.showwarning(APP_NAME, "Build a product first — its signing key is created during the build.")
            return
        cust = self.v_cust.get().strip()
        if not cust:
            messagebox.showwarning(APP_NAME, "Enter the customer's name (it is embedded in the key).")
            return
        expires = None
        if self.v_expires.get().strip():
            try:
                d = dt.datetime.strptime(self.v_expires.get().strip(), "%Y-%m-%d")
                expires = d.replace(hour=23, minute=59, second=59).timestamp()
            except ValueError:
                messagebox.showerror(APP_NAME, "Expiry must be YYYY-MM-DD.")
                return
        try:
            months = float(self.v_months.get() or 6)
        except ValueError:
            months = 6
        text, payload = vendor.issue_license(
            pid, customer=cust, email=self.v_email.get(), months=months, expires=expires,
            machine_id=self.v_mid.get() or None, features=self._csv(self.v_feat), note=self.v_note.get())
        self.txt_key.delete("1.0", "end")
        self.txt_key.insert("1.0", text)
        self._load_ledger()
        self.log(f"License {payload['lid']} issued to {cust} for {pid}, valid until {licensing.fmt_date(payload['exp'])}")

    def _copy_key(self) -> None:
        key = self.txt_key.get("1.0", "end").strip()
        if key:
            self.clipboard_clear()
            self.clipboard_append(key)

    def _copy_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        lid = self.tree.item(sel[0], "values")[0]
        for r in vendor.ledger(self.v_lic_product.get()):
            if r["lid"] == lid:
                self.clipboard_clear()
                self.clipboard_append(r["key"])
                self.txt_key.delete("1.0", "end")
                self.txt_key.insert("1.0", r["key"])
                return

    def _save_key(self) -> None:
        key = self.txt_key.get("1.0", "end").strip()
        if not key:
            return
        f = filedialog.asksaveasfilename(title="Save license", defaultextension=".lic",
                                         initialfile="license.lic", filetypes=[("License", "*.lic")])
        if f:
            Path(f).write_text(key + "\n", encoding="utf-8")

    def _export_csv(self) -> None:
        pid = self.v_lic_product.get()
        if not pid:
            return
        f = filedialog.asksaveasfilename(title="Export ledger", defaultextension=".csv",
                                         initialfile=f"{pid}-licenses.csv", filetypes=[("CSV", "*.csv")])
        if f:
            n = vendor.export_ledger_csv(pid, Path(f))
            messagebox.showinfo(APP_NAME, f"Exported {n} license(s).")

    def _backup_key(self) -> None:
        pid = self.v_lic_product.get()
        if not pid:
            return
        f = filedialog.asksaveasfilename(title="Backup product signing key (keep it private!)",
                                         defaultextension=".json", initialfile=f"{pid}.decint-key.json")
        if f:
            vendor.export_key(pid, Path(f))
            messagebox.showinfo(APP_NAME, "Saved. Anyone with this file can issue licenses for this product — "
                                          "store it somewhere safe and never ship it.")

    def _import_key(self) -> None:
        f = filedialog.askopenfilename(title="Import product key", filetypes=[("JSON", "*.json")])
        if f:
            try:
                pid = vendor.import_key(Path(f))
            except (ValueError, OSError) as ex:
                messagebox.showerror(APP_NAME, str(ex))
                return
            self._refresh_products()
            self.v_lic_product.set(pid)
            self._load_ledger()

    # ═══════════════════════════════ VERIFY TAB ═══════════════════════════════

    def _verify_tab(self) -> None:
        t = self.tab_verify
        t.columnconfigure(0, weight=1)
        t.rowconfigure(3, weight=1)
        ttk.Label(t, text="INSPECT A LICENSE KEY", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))
        ttk.Label(t, text="Paste a key a customer sent you (or one from the ledger) to see who it is for and whether it is still valid.",
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", padx=14)
        self.txt_verify_in = theme.text_widget(t, height=5, wrap="char")
        self.txt_verify_in.grid(row=2, column=0, sticky="ew", padx=14, pady=8)
        out = ttk.Frame(t)
        out.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 12))
        out.columnconfigure(0, weight=1)
        out.rowconfigure(1, weight=1)
        row = ttk.Frame(out)
        row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(row, text="CHECK", style="Accent.TButton", command=self._verify).pack(side="left")
        ttk.Button(row, text="This machine's ID", style="Ghost.TButton",
                   command=lambda: self._verify_out(f"Machine ID of this computer: {licensing.machine_id()}")).pack(side="left", padx=8)
        self.txt_verify_out = theme.text_widget(out, state="disabled")
        self.txt_verify_out.grid(row=1, column=0, sticky="nsew")

    def _verify_out(self, text: str) -> None:
        self.txt_verify_out.configure(state="normal")
        self.txt_verify_out.delete("1.0", "end")
        self.txt_verify_out.insert("1.0", text)
        self.txt_verify_out.configure(state="disabled")

    def _verify(self) -> None:
        text = self.txt_verify_in.get("1.0", "end")
        try:
            r = vendor.inspect_license(text)
        except licensing.LicenseError as ex:
            self._verify_out(f"✗ {ex}")
            return
        p = r["payload"]
        lines = [
            f"Product      : {p.get('pid')}",
            f"License ID   : {p.get('lid')}",
            f"Customer     : {p.get('cust')}  <{p.get('email') or '—'}>",
            f"Issued       : {licensing.fmt_date(p.get('iat', 0))}",
            f"Expires      : {licensing.fmt_date(p['exp'])}  ({licensing.days_left(p)} days left)",
            f"Machine      : {p.get('mid') or 'any'}",
            f"Features     : {', '.join(p.get('feat') or []) or 'standard'}",
            f"Note         : {p.get('note') or '—'}",
            "",
        ]
        if r["verified"] is True:
            lines.append("✓ Signature valid and license current.")
        elif r["verified"] is False:
            lines.append(f"✗ {r['reason']}")
        else:
            lines.append(f"? {r['reason']}")
        self._verify_out("\n".join(lines))

    # ═══════════════════════════════ SETTINGS TAB ═══════════════════════════════

    def _settings_tab(self) -> None:
        t = self.tab_settings
        t.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(t, text="VENICE AI", style="Section.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", padx=14, pady=(12, 4)); r += 1
        ttk.Label(t, text="API key").grid(row=r, column=0, sticky="w", padx=14, pady=3)
        self.v_api = tk.StringVar(value=self.cfg.get("venice_api_key", ""))
        self.ent_api = ttk.Entry(t, textvariable=self.v_api, show="•")
        self.ent_api.grid(row=r, column=1, sticky="ew", pady=3)
        row = ttk.Frame(t)
        row.grid(row=r, column=2, padx=(6, 14)); r += 1
        ttk.Button(row, text="Show", style="Ghost.TButton", command=self._toggle_key).pack(side="left")
        ttk.Button(row, text="Test", command=self._test_venice).pack(side="left", padx=4)
        env_note = "VENICE_API_KEY is set in the environment and takes precedence." if os.environ.get("VENICE_API_KEY") else \
            f"Stored in {settings.settings_path()} (mode 600). Never written into projects or builds."
        ttk.Label(t, text=env_note, style="Muted.TLabel").grid(row=r, column=1, sticky="w"); r += 1
        ttk.Label(t, text="Model").grid(row=r, column=0, sticky="w", padx=14, pady=3)
        self.v_model = tk.StringVar(value=self.cfg.get("venice_model", ""))
        self.cb_model = ttk.Combobox(t, textvariable=self.v_model, values=[""] + list(FALLBACK_MODELS))
        self.cb_model.grid(row=r, column=1, sticky="ew", pady=3)
        ttk.Button(t, text="Refresh models", command=self._refresh_models).grid(row=r, column=2, padx=(6, 14)); r += 1
        ttk.Label(t, text="Empty = Venice's default code model. Code-optimised models are listed first.", style="Muted.TLabel").grid(row=r, column=1, sticky="w"); r += 1

        ttk.Label(t, text="BUILD TOOLCHAIN", style="Section.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", padx=14, pady=(18, 4)); r += 1
        ttk.Label(t, text="Python to build with").grid(row=r, column=0, sticky="w", padx=14, pady=3)
        self.v_python = tk.StringVar(value=self.cfg.get("build_python", ""))
        ttk.Entry(t, textvariable=self.v_python).grid(row=r, column=1, sticky="ew", pady=3)
        row = ttk.Frame(t)
        row.grid(row=r, column=2, padx=(6, 14)); r += 1
        ttk.Button(row, text="Browse…", style="Ghost.TButton", command=self._pick_python).pack(side="left")
        ttk.Button(row, text="Detect", command=lambda: self._detect_python(False)).pack(side="left", padx=4)
        ttk.Button(row, text="Install PyInstaller", command=self._install_pyinstaller).pack(side="left")
        self.lbl_python = ttk.Label(t, text="", style="Muted.TLabel")
        self.lbl_python.grid(row=r, column=1, sticky="w"); r += 1
        ttk.Label(t, text="The build interpreter must have the target app's packages installed; "
                          "'Install missing packages' on the Build tab does that automatically. "
                          "Build on Windows to get a .exe — PyInstaller does not cross-compile.",
                  style="Muted.TLabel", wraplength=760).grid(row=r, column=1, columnspan=2, sticky="w"); r += 1

        ttk.Label(t, text="BRANDING", style="Section.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", padx=14, pady=(18, 4)); r += 1
        self.v_company = tk.StringVar(value=self.cfg.get("company", "DECINT"))
        self.v_copy = tk.StringVar(value=self.cfg.get("copyright_holder", "DECINT"))
        for label, var in (("Company name", self.v_company), ("Copyright holder", self.v_copy)):
            ttk.Label(t, text=label).grid(row=r, column=0, sticky="w", padx=14, pady=3)
            ttk.Entry(t, textvariable=var).grid(row=r, column=1, sticky="ew", pady=3); r += 1
        ttk.Label(t, text="Shown in the EXE's file properties (CompanyName / LegalCopyright).", style="Muted.TLabel").grid(row=r, column=1, sticky="w"); r += 1

        ttk.Button(t, text="SAVE SETTINGS", style="Accent.TButton", command=self._save_settings).grid(row=r, column=1, sticky="w", pady=18); r += 1
        ttk.Label(t, text=f"Config folder: {settings.config_dir()}   ·   product keys: {settings.keys_dir()}",
                  style="Muted.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", padx=14)

    def _toggle_key(self) -> None:
        self.ent_api.configure(show="" if self.ent_api.cget("show") else "•")

    def _save_settings(self, quiet: bool = False) -> None:
        self.cfg.update(venice_api_key=self.v_api.get().strip(), venice_model=self.v_model.get().strip(),
                        build_python=self.v_python.get().strip(), company=self.v_company.get().strip() or "DECINT",
                        copyright_holder=self.v_copy.get().strip() or "DECINT",
                        vendor_contact=self.v_contact.get().strip(), output_dir=self.v_out.get().strip())
        settings.save(self.cfg)
        if settings.venice_key(self.cfg):
            self.ai_status.configure(text="AI: Venice ready", style="Good.TLabel")
        else:
            self.ai_status.configure(text="AI: not configured", style="Muted.TLabel")
        if not quiet:
            self.log("Settings saved.")

    def _test_venice(self) -> None:
        self._save_settings(quiet=True)
        self.ai_status.configure(text="AI: testing…", style="Muted.TLabel")

        def work():
            v = self._venice()
            model = v.pick_model()
            reply = v.chat([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=20)
            return model, reply.strip()

        def done(res):
            model, reply = res
            self.ai_status.configure(text=f"AI: Venice ready ({model})", style="Good.TLabel")
            messagebox.showinfo(APP_NAME, f"Venice responded using {model}:\n{reply[:80]}")

        def err(ex):
            self.ai_status.configure(text="AI: error", style="Bad.TLabel")
            messagebox.showerror(APP_NAME, str(ex))

        self._bg(work, done, err)

    def _refresh_models(self) -> None:
        self._save_settings(quiet=True)

        def done(models):
            self.cb_model.configure(values=[""] + [m["id"] for m in models])
            self.log(f"Venice: {len(models)} text models; default code model: "
                     f"{next((m['id'] for m in models if 'default_code' in m['traits']), '?')}")

        self._bg(lambda: self._venice().models(), done, lambda ex: messagebox.showerror(APP_NAME, str(ex)))

    def _pick_python(self) -> None:
        f = filedialog.askopenfilename(title="Python interpreter",
                                       filetypes=[("Python", "python*.exe python3* python*"), ("All", "*.*")])
        if f:
            self.v_python.set(f)
            self._detect_python(False)

    def _detect_python(self, quiet: bool) -> None:
        def work():
            py = self.v_python.get().strip() or self.cfg.get("build_python", "")
            found = toolchain.find_python(py)
            return found, (toolchain.python_info(found) if found else None)

        def done(res):
            found, info = res
            if not found:
                self.lbl_python.configure(text="No Python interpreter found — install Python 3.9+ and click Detect.", style="Bad.TLabel")
                return
            self.v_python.set(found)
            self.cfg["build_python"] = found
            pi = info["pyinstaller"] or "not installed"
            self.lbl_python.configure(text=f"Python {info['version']} · PyInstaller {pi}",
                                      style="Good.TLabel" if info["pyinstaller"] else "Warn.TLabel")
            if not quiet:
                self.log(f"Build interpreter: {found} (Python {info['version']}, PyInstaller {pi})")

        self._bg(work, done)

    def _install_pyinstaller(self) -> None:
        py = self.v_python.get().strip()
        if not py:
            messagebox.showinfo(APP_NAME, "Detect or choose a Python interpreter first.")
            return
        self.nb.select(self.tab_build)
        self._bg(lambda: toolchain.ensure_pyinstaller(py, self.log) or toolchain.pip_install(py, ["pyinstaller", "pillow"], self.log),
                 lambda ok: (self.log("PyInstaller ready." if ok else "ERROR: PyInstaller install failed"),
                             self._detect_python(True)))


def main() -> int:
    app = App()
    app.mainloop()
    return 0
