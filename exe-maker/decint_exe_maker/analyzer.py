"""Static analysis of a Python project: entry point, GUI/console, imports, data.

This is the deterministic half of "analyze". Its report is what the Venice
model is shown (plus file excerpts), and what the GUI falls back to when there
is no API key. Everything here is heuristics on the AST — no code is executed.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", ".env", "node_modules",
             "build", "dist", ".idea", ".vscode", ".mypy_cache", ".pytest_cache", ".ruff_cache",
             ".tox", ".eggs", "site-packages", ".decint-build"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".spec", ".log", ".pid", ".swp", ".exe", ".zip", ".7z", ".rar", ".iso"}
MAX_DATA_FILE = 64 * 1024 * 1024

GUI_MODULES = {
    "tkinter": "tk", "customtkinter": "tk", "ttkbootstrap": "tk", "tkinterdnd2": "tk",
    "PyQt5": "qt", "PyQt6": "qt", "PySide2": "qt", "PySide6": "qt",
    "wx": "wx", "kivy": "kivy", "pygame": "pygame", "arcade": "arcade", "pyglet": "pyglet",
    "dearpygui": "dpg", "flet": "flet", "PySimpleGUI": "psg", "FreeSimpleGUI": "psg",
    "gi": "gtk", "webview": "webview", "eel": "eel", "toga": "toga", "pywebview": "webview",
}
# Web servers: they run as console apps with a browser UI.
SERVER_MODULES = {"flask", "fastapi", "django", "uvicorn", "gunicorn", "streamlit", "gradio",
                  "bottle", "tornado", "aiohttp", "sanic", "nicegui", "dash"}

# import name -> pip distribution name where they differ.
PIP_NAMES = {
    "PIL": "pillow", "cv2": "opencv-python", "yaml": "pyyaml", "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4", "Crypto": "pycryptodome", "Cryptodome": "pycryptodomex",
    "dotenv": "python-dotenv", "serial": "pyserial", "usb": "pyusb", "win32api": "pywin32",
    "win32con": "pywin32", "win32gui": "pywin32", "win32com": "pywin32", "pywintypes": "pywin32",
    "win32process": "pywin32", "win32clipboard": "pywin32", "dateutil": "python-dateutil",
    "jwt": "pyjwt", "docx": "python-docx", "pptx": "python-pptx", "fitz": "pymupdf",
    "Levenshtein": "python-Levenshtein", "OpenSSL": "pyopenssl", "attr": "attrs", "wx": "wxPython",
    "gi": "PyGObject", "telegram": "python-telegram-bot", "discord": "discord.py",
    "google.protobuf": "protobuf", "googleapiclient": "google-api-python-client",
    "magic": "python-magic", "psycopg2": "psycopg2-binary", "MySQLdb": "mysqlclient",
    "skimage": "scikit-image", "Xlib": "python-xlib", "pynput": "pynput", "webview": "pywebview",
    "zmq": "pyzmq", "nacl": "pynacl", "lxml": "lxml", "gtts": "gTTS", "speech_recognition": "SpeechRecognition",
    "pyttsx3": "pyttsx3", "websocket": "websocket-client", "socks": "PySocks", "ttk": "pyttk",
    "markdown": "Markdown", "mplfinance": "mplfinance", "sqlalchemy": "SQLAlchemy",
    "flask_cors": "Flask-Cors", "flask_sqlalchemy": "Flask-SQLAlchemy", "wtforms": "WTForms",
    "playsound": "playsound", "pyautogui": "PyAutoGUI", "pyperclip": "pyperclip",
    "Xlib": "python-xlib", "cairo": "pycairo", "ldap": "python-ldap", "snap7": "python-snap7",
}

# Packages PyInstaller regularly mis-bundles without help.
COLLECT_ALL = {"customtkinter", "ttkbootstrap", "tkinterdnd2", "sv_ttk", "pyttsx3", "gradio",
               "streamlit", "nicegui", "flet", "dearpygui", "tkcalendar", "babel", "pygame_gui",
               "fake_useragent", "cloudscraper", "tls_client", "curl_cffi", "playwright",
               "shapely", "rasterio", "ultralytics", "torchvision", "transformers"}
HIDDEN_IMPORTS = {
    "PIL": ["PIL._tkinter_finder"], "tkcalendar": ["babel.numbers"], "sklearn": ["sklearn.utils._typedefs"],
    "pkg_resources": ["pkg_resources.py2_warn"], "uvicorn": ["uvicorn.logging", "uvicorn.loops.auto",
                                                              "uvicorn.protocols.http.auto",
                                                              "uvicorn.lifespan.on"],
    "pynput": ["pynput.keyboard._win32", "pynput.mouse._win32"], "sqlalchemy": ["sqlalchemy.sql.default_comparator"],
    "pyttsx3": ["pyttsx3.drivers", "pyttsx3.drivers.sapi5"], "engineio": ["engineio.async_drivers.threading"],
    "win32com": ["win32timezone"], "pywinauto": ["comtypes.stream"],
}

STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {"__future__", "_tkinter"}


@dataclass
class FileInfo:
    path: Path                       # absolute
    rel: str                         # posix relative path
    imports: set[str] = field(default_factory=set)
    has_main_guard: bool = False
    top_level_calls: int = 0          # statements that *do* something at import time
    uses_input: bool = False
    argparse: bool = False
    defines_main: bool = False
    lines: int = 0


@dataclass
class Report:
    project_dir: Path
    files: list[FileInfo]
    entry_candidates: list[str]       # relative paths, best first
    entry: str | None
    app_type: str                     # "gui" | "console"
    gui_toolkit: str | None
    local_modules: set[str]
    third_party: list[str]            # import names
    pip_requirements: list[str]
    hidden_imports: list[str]
    collect_all: list[str]
    data_files: list[str]             # relative paths
    requirements_txt: list[str]
    suggested_name: str
    suggested_version: str
    notes: list[str]

    def as_dict(self) -> dict:
        return {
            "project_dir": str(self.project_dir),
            "python_files": [f.rel for f in self.files],
            "entry_candidates": self.entry_candidates, "entry": self.entry,
            "app_type": self.app_type, "gui_toolkit": self.gui_toolkit,
            "local_modules": sorted(self.local_modules), "third_party": self.third_party,
            "pip_requirements": self.pip_requirements, "hidden_imports": self.hidden_imports,
            "collect_all": self.collect_all, "data_files": self.data_files,
            "requirements_txt": self.requirements_txt, "suggested_name": self.suggested_name,
            "suggested_version": self.suggested_version, "notes": self.notes,
        }


# ─────────────────────────────── walking ───────────────────────────────

def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        for fn in sorted(filenames):
            if fn in SKIP_FILES or Path(fn).suffix.lower() in SKIP_SUFFIXES:
                continue
            yield Path(dirpath) / fn


def _scan_py(path: Path, root: Path) -> FileInfo:
    info = FileInfo(path=path, rel=path.relative_to(root).as_posix())
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        return info
    info.lines = src.count("\n") + 1
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                info.imports.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                info.imports.add(node.module)
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "input":
                info.uses_input = True
            if isinstance(fn, ast.Attribute) and fn.attr == "ArgumentParser":
                info.argparse = True
    for node in tree.body:
        if isinstance(node, ast.If) and _is_main_guard(node.test):
            info.has_main_guard = True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main":
            info.defines_main = True
        elif isinstance(node, (ast.Expr, ast.For, ast.While, ast.With, ast.Try)):
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):
                info.top_level_calls += 1
    return info


def _is_main_guard(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare) or len(test.comparators) != 1:
        return False
    left, right = test.left, test.comparators[0]
    names = {getattr(left, "id", None), getattr(right, "id", None)}
    consts = {getattr(left, "value", None), getattr(right, "value", None)}
    return "__name__" in names and "__main__" in consts


def _parse_requirements(root: Path) -> list[str]:
    out = []
    for name in ("requirements.txt", "requirements-prod.txt", "requirements/base.txt"):
        p = root / name
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.split("#", 1)[0].strip()
                if line and not line.startswith(("-", "git+")):
                    out.append(line)
    return out


def pip_name(import_name: str) -> str:
    top = import_name.split(".")[0]
    return PIP_NAMES.get(import_name, PIP_NAMES.get(top, top))


def entry_module_name(project_dir: Path, entry_rel: str) -> tuple[str, list[str]]:
    """``(dotted module name, extra sys.path dirs)`` for the launcher to run.

    ``src/pkg/main.py`` inside a package tree becomes ``pkg.main`` with ``src``
    on the path; a bare script in a non-package folder becomes ``main`` with
    its own folder on the path.
    """
    entry = (project_dir / entry_rel).resolve()
    parts = [entry.stem]
    d = entry.parent
    while d != project_dir.resolve() and (d / "__init__.py").is_file():
        parts.insert(0, d.name)
        d = d.parent
    extra = [str(d)] if d != project_dir.resolve() else []
    return ".".join(parts), extra


# ─────────────────────────────── analysis ───────────────────────────────

def analyze(project_dir: str | os.PathLike) -> Report:
    root = Path(project_dir).resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    notes: list[str] = []
    py: list[FileInfo] = []
    data: list[str] = []
    for p in iter_files(root):
        if p.suffix == ".py":
            py.append(_scan_py(p, root))
        else:
            try:
                if p.stat().st_size <= MAX_DATA_FILE:
                    data.append(p.relative_to(root).as_posix())
            except OSError:
                pass

    # Local module names: every top-level .py stem and every package dir.
    local: set[str] = set()
    for f in py:
        parts = Path(f.rel).parts
        local.add(Path(parts[0]).stem if len(parts) == 1 else parts[0])
        if len(parts) > 1:
            local.add(Path(f.rel).stem)   # bare stem in case its folder is on sys.path
    src_dir = root / "src"
    if src_dir.is_dir():
        for child in src_dir.iterdir():
            local.add(child.stem)

    all_imports: set[str] = set().union(*(f.imports for f in py)) if py else set()
    tops = {i.split(".")[0] for i in all_imports}
    third = sorted(t for t in tops if t not in STDLIB and t not in local and not t.startswith("_"))

    # GUI vs console.
    toolkit = None
    for mod, kind in GUI_MODULES.items():
        if mod in tops:
            toolkit = kind
            break
    app_type = "gui" if toolkit else "console"
    if toolkit == "pygame" or toolkit == "arcade":
        notes.append(f"{toolkit} app — windowed build, but it still prints to a console if run from one.")
    if tops & SERVER_MODULES:
        app_type = "console"
        notes.append("Web-server framework detected: built as a console app that serves a browser UI.")
    uses_input = any(f.uses_input for f in py)
    if app_type == "gui" and uses_input:
        notes.append("Uses input() — a windowed build would hang on it. Check the app type.")

    # Entry point ranking.
    entry_candidates = _rank_entries(py, root)
    entry = entry_candidates[0] if entry_candidates else None
    if not entry:
        notes.append("No obvious entry script — pick one manually.")

    # PyInstaller helpers.
    hidden = sorted({h for t in tops for h in HIDDEN_IMPORTS.get(t, [])}
                    | {h for i in all_imports for h in HIDDEN_IMPORTS.get(i, [])})
    if "PIL" in tops and toolkit != "tk":
        hidden = [h for h in hidden if h != "PIL._tkinter_finder"]
    collect = sorted(t for t in tops if t in COLLECT_ALL)

    reqs_txt = _parse_requirements(root)
    pip_reqs = sorted({pip_name(t) for t in third})

    # Data files: keep obviously-runtime assets, drop docs/tests/etc.
    keep = []
    for rel in data:
        low = rel.lower()
        if low.startswith(("test", "tests/", "docs/", "doc/", ".github/")):
            continue
        if Path(low).name in {"readme.md", "readme.txt", "license", "license.txt", "license.md",
                              "requirements.txt", "setup.py", "setup.cfg", "pyproject.toml",
                              "pipfile", "pipfile.lock", "poetry.lock", ".gitignore", ".gitattributes",
                              "makefile", "dockerfile", "docker-compose.yml"}:
            continue
        keep.append(rel)

    name = _suggest_name(root, entry)
    version = _suggest_version(root, py)

    return Report(project_dir=root, files=py, entry_candidates=entry_candidates, entry=entry,
                  app_type=app_type, gui_toolkit=toolkit, local_modules=local, third_party=third,
                  pip_requirements=pip_reqs, hidden_imports=hidden, collect_all=collect,
                  data_files=keep, requirements_txt=reqs_txt, suggested_name=name,
                  suggested_version=version, notes=notes)


def _rank_entries(py: list[FileInfo], root: Path) -> list[str]:
    imported_stems: set[str] = set()
    for f in py:
        for i in f.imports:
            imported_stems.add(i.split(".")[-1])
            imported_stems.add(i.split(".")[0])
    scored = []
    for f in py:
        stem = Path(f.rel).stem
        depth = f.rel.count("/")
        s = 0
        if f.has_main_guard:
            s += 50
        if stem == "__main__":
            s += 45
        if stem in ("main", "app", "run", "start", "launch", "gui", "cli", "__main__"):
            s += 25
        if stem == root.name or stem == root.name.replace("-", "_"):
            s += 20
        if f.defines_main:
            s += 10
        if f.top_level_calls and not f.has_main_guard:
            s += 8       # a script that just runs at import time
        if stem not in imported_stems:
            s += 10      # nothing imports it → likely the top of the tree
        if f.argparse:
            s += 5
        s -= depth * 6
        if stem.startswith("test") or "/tests/" in f"/{f.rel}" or stem in ("setup", "conftest", "__init__"):
            s -= 100
        if f.lines == 0:
            s -= 100
        scored.append((s, f.rel))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [rel for s, rel in scored if s > 0][:8]


def _suggest_name(root: Path, entry: str | None) -> str:
    base = root.name
    if entry and Path(entry).stem not in ("main", "app", "run", "start", "__main__", "gui", "cli"):
        base = Path(entry).stem
    words = re.sub(r"[_\-]+", " ", base).strip()
    return " ".join(w.capitalize() if w.islower() else w for w in words.split()) or "My App"


def _suggest_version(root: Path, py: list[FileInfo]) -> str:
    for cand in ("pyproject.toml", "setup.cfg", "setup.py", "VERSION", "version.txt"):
        p = root / cand
        if p.is_file():
            m = re.search(r"version\s*[=:]\s*['\"]?(\d+(?:\.\d+){0,3})", p.read_text(errors="ignore"), re.I)
            if m:
                return m.group(1)
            if cand.lower().startswith("version"):
                v = p.read_text(errors="ignore").strip()
                if re.fullmatch(r"\d+(\.\d+){0,3}", v):
                    return v
    for f in py[:50]:
        try:
            m = re.search(r"__version__\s*=\s*['\"](\d+(?:\.\d+){0,3})", f.path.read_text(errors="ignore"))
            if m:
                return m.group(1)
        except OSError:
            pass
    return "1.0.0"


def excerpts(report: Report, max_files: int = 4, max_chars: int = 3500) -> dict[str, str]:
    """Head of the most likely entry files (+ requirements) to show the model."""
    out = {}
    for rel in report.entry_candidates[:max_files]:
        try:
            out[rel] = (report.project_dir / rel).read_text(encoding="utf-8", errors="replace")[:max_chars]
        except OSError:
            continue
    for name in ("requirements.txt", "pyproject.toml", "README.md"):
        p = report.project_dir / name
        if p.is_file():
            out[name] = p.read_text(encoding="utf-8", errors="replace")[:1500]
    return out
