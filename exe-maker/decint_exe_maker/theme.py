"""DECINT look for tkinter/ttk: near-black plate, violet accent, terminal feel."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

PLATE = "#10121C"
PANEL = "#171A28"
PANEL2 = "#1E2233"
BORDER = "#2A2E44"
INK = "#E6E6F0"
MUTED = "#8A8FA8"
VIOLET = "#8D5BF6"
VIOLET_HI = "#A47CFF"
VIOLET_DIM = "#5B3FA6"
GOOD = "#3DDC97"
BAD = "#FF5C7A"
WARN = "#FFC857"

UI_FONT = ("Segoe UI", 10) if sys.platform == "win32" else ("DejaVu Sans", 10)
UI_BOLD = (UI_FONT[0], 10, "bold")
TITLE_FONT = (UI_FONT[0], 16, "bold")
SMALL = (UI_FONT[0], 9)
MONO = ("Consolas", 10) if sys.platform == "win32" else ("DejaVu Sans Mono", 9)


def apply(root: tk.Tk) -> ttk.Style:
    root.configure(bg=PLATE)
    root.option_add("*Font", UI_FONT)
    root.option_add("*TCombobox*Listbox.background", PANEL2)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", VIOLET)
    root.option_add("*TCombobox*Listbox.selectForeground", INK)

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=PLATE, foreground=INK, fieldbackground=PANEL, bordercolor=BORDER,
                lightcolor=PANEL, darkcolor=PANEL, troughcolor=PANEL, font=UI_FONT,
                selectbackground=VIOLET, selectforeground=INK, insertcolor=INK)
    s.configure("TFrame", background=PLATE)
    s.configure("Panel.TFrame", background=PANEL)
    s.configure("TLabel", background=PLATE, foreground=INK)
    s.configure("Muted.TLabel", foreground=MUTED, font=SMALL)
    s.configure("Title.TLabel", font=TITLE_FONT)
    s.configure("Section.TLabel", foreground=VIOLET_HI, font=UI_BOLD)
    s.configure("Good.TLabel", foreground=GOOD)
    s.configure("Bad.TLabel", foreground=BAD)
    s.configure("Warn.TLabel", foreground=WARN)
    s.configure("Panel.TLabel", background=PANEL)

    s.configure("TButton", background=PANEL2, foreground=INK, borderwidth=0, focusthickness=0,
                padding=(12, 6))
    s.map("TButton", background=[("active", BORDER), ("disabled", PANEL)],
          foreground=[("disabled", MUTED)])
    s.configure("Accent.TButton", background=VIOLET, foreground=INK, font=UI_BOLD, padding=(16, 8))
    s.map("Accent.TButton", background=[("active", VIOLET_HI), ("disabled", VIOLET_DIM)],
          foreground=[("disabled", MUTED)])
    s.configure("Ghost.TButton", background=PLATE, foreground=MUTED, padding=(8, 4))
    s.map("Ghost.TButton", background=[("active", PANEL)], foreground=[("active", INK)])

    s.configure("TEntry", fieldbackground=PANEL, foreground=INK, insertcolor=INK, bordercolor=BORDER,
                lightcolor=BORDER, darkcolor=BORDER, padding=5)
    s.map("TEntry", bordercolor=[("focus", VIOLET)], lightcolor=[("focus", VIOLET)],
          darkcolor=[("focus", VIOLET)])
    s.configure("TCombobox", fieldbackground=PANEL, background=PANEL2, foreground=INK,
                arrowcolor=MUTED, bordercolor=BORDER, padding=4)
    s.map("TCombobox", fieldbackground=[("readonly", PANEL)], bordercolor=[("focus", VIOLET)],
          foreground=[("readonly", INK)])
    s.configure("TSpinbox", fieldbackground=PANEL, background=PANEL2, foreground=INK,
                arrowcolor=MUTED, bordercolor=BORDER, padding=4)

    # clam draws the tick with indicatorforeground on an indicatorbackground box.
    for w in ("TCheckbutton", "TRadiobutton"):
        s.configure(w, background=PLATE, foreground=INK, indicatorbackground=PANEL2,
                    indicatorforeground=INK, indicatormargin=(2, 2, 6, 2), padding=2)
        s.map(w, indicatorbackground=[("selected", VIOLET), ("active", BORDER)],
              indicatorforeground=[("selected", INK)], background=[("active", PLATE)],
              foreground=[("disabled", MUTED)])

    s.configure("TNotebook", background=PLATE, borderwidth=0, tabmargins=(0, 4, 0, 0))
    s.configure("TNotebook.Tab", background=PLATE, foreground=MUTED, padding=(18, 8), borderwidth=0,
                font=UI_BOLD)
    s.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", INK)],
          expand=[("selected", (0, 0, 0, 0))])

    s.configure("TLabelframe", background=PLATE, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    s.configure("TLabelframe.Label", background=PLATE, foreground=VIOLET_HI, font=UI_BOLD)

    s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK, borderwidth=0,
                rowheight=24)
    s.configure("Treeview.Heading", background=PANEL2, foreground=MUTED, borderwidth=0, font=SMALL)
    s.map("Treeview", background=[("selected", VIOLET_DIM)], foreground=[("selected", INK)])
    s.map("Treeview.Heading", background=[("active", BORDER)])

    s.configure("Horizontal.TProgressbar", background=VIOLET, troughcolor=PANEL, borderwidth=0,
                lightcolor=VIOLET, darkcolor=VIOLET)
    s.configure("Vertical.TScrollbar", background=PANEL2, troughcolor=PLATE, borderwidth=0,
                arrowcolor=MUTED)
    s.map("Vertical.TScrollbar", background=[("active", BORDER)])
    s.configure("TSeparator", background=BORDER)
    s.configure("TPanedwindow", background=PLATE)
    s.configure("Sash", sashthickness=6, background=PLATE)
    return s


def text_widget(parent, **kw) -> tk.Text:
    """A tk.Text styled like the rest of the UI (ttk has no Text)."""
    opts = dict(bg=PANEL, fg=INK, insertbackground=INK, relief="flat", font=MONO, wrap="word",
                padx=8, pady=6, highlightthickness=1, highlightbackground=BORDER, highlightcolor=VIOLET,
                selectbackground=VIOLET, selectforeground=INK)
    opts.update(kw)
    return tk.Text(parent, **opts)


def listbox(parent, **kw) -> tk.Listbox:
    opts = dict(bg=PANEL, fg=INK, relief="flat", font=UI_FONT, selectbackground=VIOLET_DIM,
                selectforeground=INK, highlightthickness=1, highlightbackground=BORDER,
                highlightcolor=VIOLET, activestyle="none")
    opts.update(kw)
    return tk.Listbox(parent, **opts)
