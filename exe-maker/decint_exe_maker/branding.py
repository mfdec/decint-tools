"""DECINT branding baked into every build: icon, splash, Windows version info.

The shield geometry is the same as ``scripts/make_favicon.py`` in the DECINT
console repo (flat #10121C plate, #8D5BF6 violet stroke, hollow — nothing is
ever drawn inside the shield). Pillow is optional: without it the pre-rendered
``assets/decint.ico`` and ``assets/splash.png`` are used as they are.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

COMPANY = "DECINT"
PLATE = (16, 18, 28)        # #10121C
ACCENT = (141, 91, 246)     # #8D5BF6
INK = (230, 230, 240)
MUTED = (138, 143, 168)
STROKE_FRAC = 0.042
TOP_RISE, SHOULDER, BELLY = 0.18, 0.52, 0.86


def resource_dir() -> Path:
    """Where ``assets/`` and ``runtime/`` live — also when frozen by PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "decint_exe_maker"
    return Path(__file__).resolve().parent


def default_icon() -> Path:
    return resource_dir() / "assets" / "decint.ico"


def default_splash() -> Path:
    return resource_dir() / "assets" / "splash.png"


# ────────────────────────────── geometry ──────────────────────────────

def shield_points(w, h, steps=44):
    x0, x1, y0, y1 = 0.0, w, 0.0, h
    cx = (x0 + x1) / 2.0
    ty = y0 + (y1 - y0) * TOP_RISE
    sy = y0 + (y1 - y0) * SHOULDER
    ctrl_y = y0 + (y1 - y0) * BELLY

    def top_arc(x_end):
        ctrl_x = cx + (x_end - cx) * 0.48
        return [((1 - t) ** 2 * cx + 2 * (1 - t) * t * ctrl_x + t * t * x_end,
                 (1 - t) ** 2 * y0 + 2 * (1 - t) * t * y0 + t * t * ty)
                for t in (i / steps for i in range(steps + 1))]

    def sweep(xs):
        return [((1 - t) ** 2 * xs + 2 * (1 - t) * t * xs + t * t * cx,
                 (1 - t) ** 2 * sy + 2 * (1 - t) * t * ctrl_y + t * t * y1)
                for t in (i / steps for i in range(steps + 1))]

    return (top_arc(x1) + [(x1, sy)] + sweep(x1) + list(reversed(sweep(x0)))
            + [(x0, ty)] + list(reversed(top_arc(x0))))


def _at(w, h, ox, oy):
    return [(x + ox, y + oy) for x, y in shield_points(w, h)]


def draw_icon(size: int, ss: int = 4, plate: bool = True):
    from PIL import Image, ImageDraw
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ground = Image.new("RGBA", (S, S), PLATE + (255,))
    if plate:
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=round(S * 0.22), fill=255)
        img.paste(ground, (0, 0), mask)
        mx, my = S * 0.26, S * 0.17
    else:
        mx, my = S * 0.10, S * 0.04
    sw, sh = S - 2 * mx, S - 2 * my
    d = ImageDraw.Draw(img)
    rim = max(1, round(size * STROKE_FRAC)) * ss
    d.polygon(_at(sw, sh, mx, my), fill=ACCENT + (255,))
    iw, ih = sw - 2 * rim, sh - 2 * rim
    if iw > 0 and ih > 0:
        hole = Image.new("L", (S, S), 0)
        ImageDraw.Draw(hole).polygon(_at(iw, ih, mx + rim, my + rim), fill=255)
        img.paste(ground if plate else (0, 0, 0, 0), (0, 0), hole)
    return img.resize((size, size), Image.LANCZOS)


def write_icon(dest: Path) -> Path:
    """Multi-size .ico (16→256) of the DECINT shield."""
    sizes = (16, 24, 32, 48, 64, 128, 256)
    imgs = [draw_icon(s) for s in sizes]
    base, rest = imgs[-1], imgs[:-1]
    dest = Path(dest)
    base.save(dest, format="ICO", sizes=[(s, s) for s in sizes], append_images=rest)
    return dest


def write_splash(dest: Path, product_name: str = "", width: int = 520, height: int = 300) -> Path:
    """Dark splash card: shield mark, product name, 'licensed by DECINT'."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (width, height), PLATE + (255,))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width - 1, height - 1], outline=(42, 46, 68), width=1)
    d.rectangle([0, 0, width - 1, 3], fill=ACCENT)
    mark = draw_icon(96, plate=False)
    img.alpha_composite(mark, (int(width / 2 - 48), 52))

    def font(size, bold=False):
        candidates = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
                      else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
        for c in candidates:
            for base in ("C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu",
                         "/System/Library/Fonts/Supplemental", ""):
                p = os.path.join(base, c) if base else c
                try:
                    return ImageFont.truetype(p, size)
                except OSError:
                    continue
        return ImageFont.load_default()

    title = product_name or COMPANY
    f1, f2 = font(26, True), font(13)
    tw = d.textlength(title, font=f1)
    d.text(((width - tw) / 2, 168), title, fill=INK, font=f1)
    sub = "licensed by DECINT  ·  loading…"
    sw = d.textlength(sub, font=f2)
    d.text(((width - sw) / 2, 212), sub, fill=MUTED, font=f2)
    dest = Path(dest)
    img.convert("RGB").save(dest, format="PNG")
    return dest


# ─────────────────────────── version resource ───────────────────────────

def parse_version(v: str) -> tuple[int, int, int, int]:
    nums = []
    for part in (v or "1.0.0").replace("-", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            nums.append(int(digits) & 0xFFFF)
        if len(nums) == 4:
            break
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]


def version_info_text(product_name: str, version: str, description: str = "",
                      company: str = COMPANY, copyright_holder: str = COMPANY,
                      exe_name: str = "") -> str:
    """Contents of a PyInstaller ``--version-file`` (VSVersionInfo)."""
    a, b, c, d = parse_version(version)
    year = time.strftime("%Y")
    esc = lambda s: (s or "").replace("\\", "\\\\").replace("'", "\\'")
    exe_name = exe_name or product_name
    return f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({a}, {b}, {c}, {d}),
    prodvers=({a}, {b}, {c}, {d}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', '{esc(company)}'),
        StringStruct('FileDescription', '{esc(description or product_name)}'),
        StringStruct('FileVersion', '{a}.{b}.{c}.{d}'),
        StringStruct('InternalName', '{esc(exe_name)}'),
        StringStruct('LegalCopyright', '© {year} {esc(copyright_holder)}. Licensed software — see license terms.'),
        StringStruct('OriginalFilename', '{esc(exe_name)}.exe'),
        StringStruct('ProductName', '{esc(product_name)}'),
        StringStruct('ProductVersion', '{a}.{b}.{c}.{d}'),
        StringStruct('Comments', 'Built and licensed with DECINT EXE Maker.')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
