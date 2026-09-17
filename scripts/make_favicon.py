#!/usr/bin/env python3
"""Generate the DECINT favicon/icon set — the violet shield mark.

Shape notes (matched to the uploaded original):
  * The top edge is TWO CURVES that sweep up and meet at a centre peak — not a
    flat heraldic top. That dome is the distinguishing feature of the mark.
  * Straight flanks, then a quadratic sweep in to a point at bottom centre.
  * Hollow (EMPTY) shield — nothing inside it, ever. The silhouette is the mark.
  * Flat near-black plate (#10121C, sampled from the reference lockup) with a
    violet shield stroke. The old violet->cyan gradient plate is retired: it
    glowed against the dark UI, which is the opposite of sleek.

    python scripts/make_favicon.py [out_dir]

Writes into frontend/public (default): favicon.ico, favicon-16x16.png,
favicon-32x32.png, apple-touch-icon.png, icon-192.png, icon-512.png and
mark-512.png (transparent mark). Also prints the matching SVG path data so the
in-app <Shield> component can use the exact same geometry.

Needs Pillow.
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

# ── palette ──────────────────────────────────────────────────────────────────
# Plate: flat near-black, sampled from the reference lockup (#10121C is 89% of
# that image's pixels). The previous violet -> cyan gradient plate is retired.
# Shield: the brand violet, unchanged. One hue, no second stop, no easing.
PLATE = (16, 18, 28)        # #10121C  near-black plate, sampled from the lockup
ACCENT = (141, 91, 246)     # #8D5BF6  brand violet — the shield stroke
OUTLINE = ACCENT            # the shield IS the accent now; no white rim
STROKE_FRAC = 0.042         # thinner than before: sleeker line, same silhouette

# ── geometry ─────────────────────────────────────────────────────────────────
TOP_RISE = 0.18   # how far the centre peak sits above the shoulders (of height)
SHOULDER = 0.52   # where the straight flanks end and the bottom sweep begins
BELLY = 0.86      # bottom sweep control point; higher = fuller heraldic waist


def shield_points(w, h, top_rise=TOP_RISE, shoulder=SHOULDER, belly=BELLY, steps=44):
    """Closed polygon outline of the DECINT shield.

    Two quadratic arcs form the domed top, meeting at (cx, y0) with a
    horizontal tangent so the apex is smooth rather than cusped; straight
    flanks drop to `shoulder`; a quadratic sweep closes to a point at bottom
    centre.
    """
    x0, x1, y0, y1 = 0.0, w, 0.0, h
    cx = (x0 + x1) / 2.0
    ty = y0 + (y1 - y0) * top_rise      # shoulder height (flanks start here)
    sy = y0 + (y1 - y0) * shoulder      # bottom sweep starts here
    ctrl_y = y0 + (y1 - y0) * belly

    def top_arc(x_end):
        """Apex (cx, y0) → shoulder (x_end, ty). Control shares the apex's y,
        which is what makes the two arcs meet smoothly at the top."""
        ctrl_x = cx + (x_end - cx) * 0.48
        out = []
        for i in range(steps + 1):
            t = i / steps
            u = 1.0 - t
            out.append((
                u * u * cx + 2 * u * t * ctrl_x + t * t * x_end,
                u * u * y0 + 2 * u * t * y0 + t * t * ty,
            ))
        return out

    def sweep(x_start):
        """Flank (x_start, sy) → point (cx, y1)."""
        out = []
        for i in range(steps + 1):
            t = i / steps
            u = 1.0 - t
            out.append((
                u * u * x_start + 2 * u * t * x_start + t * t * cx,
                u * u * sy + 2 * u * t * ctrl_y + t * t * y1,
            ))
        return out

    return (top_arc(x1)                      # apex → right shoulder
            + [(x1, sy)]                     # right flank
            + sweep(x1)                      # right sweep → bottom point
            + list(reversed(sweep(x0)))      # bottom point → left flank
            + [(x0, ty)]                     # left flank
            + list(reversed(top_arc(x0))))   # left shoulder → apex


def shield_svg_path(w, h, ox=0.0, oy=0.0,
                    top_rise=TOP_RISE, shoulder=SHOULDER, belly=BELLY):
    """Same geometry as compact SVG quadratic curves, offset by (ox, oy)."""
    x0, x1, y0, y1 = ox, ox + w, oy, oy + h
    cx = ox + w / 2.0
    ty = oy + h * top_rise
    sy = oy + h * shoulder
    cy = oy + h * belly
    cr = cx + (x1 - cx) * 0.48
    cl = cx + (x0 - cx) * 0.48
    f = lambda v: f"{v:.1f}".rstrip("0").rstrip(".")
    return (
        f"M{f(cx)},{f(y0)}"
        f"Q{f(cr)},{f(y0)} {f(x1)},{f(ty)}"
        f"L{f(x1)},{f(sy)}"
        f"Q{f(x1)},{f(cy)} {f(cx)},{f(y1)}"
        f"Q{f(x0)},{f(cy)} {f(x0)},{f(sy)}"
        f"L{f(x0)},{f(ty)}"
        f"Q{f(cl)},{f(y0)} {f(cx)},{f(y0)}Z"
    )


def shield_svg_hollow(size=256, mx=34.0, my=10.0, stroke=21.0):
    """Outer + inner subpaths — render with fillRule="evenodd" for a hollow
    shield that matches the icon set."""
    w, h = size - 2 * mx, size - 2 * my
    outer = shield_svg_path(w, h, mx, my)
    inner = shield_svg_path(w - 2 * stroke, h - 2 * stroke, mx + stroke, my + stroke)
    return outer + inner


# ── rendering ────────────────────────────────────────────────────────────────

def _plate(size):
    """Flat near-black plate, RGBA. Deliberately flat — a gradient here made the
    icon glow in a dark tab strip."""
    return Image.new("RGBA", (size, size), PLATE + (255,))


def _at(w, h, ox, oy):
    return [(x + ox, y + oy) for x, y in shield_points(w, h)]


def draw_icon(size, ss=4, plate=True):
    """Render the mark at `size` px, supersampled for clean edges.

    Below 24 px a hollow shield closes into a blob, so the mark fills instead —
    but it keeps the black rim, which is what preserves its silhouette at
    taskbar/tab size.
    """
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ground = _plate(S) if plate else None

    if plate:
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, S - 1, S - 1], radius=round(S * 0.22), fill=255
        )
        img.paste(ground, (0, 0), mask)
        # Narrower than tall, like the original mark.
        mx, my = S * 0.26, S * 0.17
    else:
        mx, my = S * 0.10, S * 0.04

    sw, sh = S - 2 * mx, S - 2 * my
    d = ImageDraw.Draw(img)

    rim = max(1, round(size * STROKE_FRAC)) * ss   # violet stroke weight

    # 1. violet shield silhouette — this becomes the outline
    d.polygon(_at(sw, sh, mx, my), fill=OUTLINE + (255,))

    # 2. punch the interior back to the plate gradient, so the shield is filled
    #    with exactly the plate behind it and reads as a violet outline only.
    #    Kept hollow at every shipped size — a 1px outline still resolves at
    #    16px, and falling back to a solid silhouette there made the tab icon
    #    look like a different (inverted) mark from the 32/48px versions.
    if size >= 8:
        iw, ih = sw - 2 * rim, sh - 2 * rim
        if iw > 0 and ih > 0:
            hole = Image.new("L", (S, S), 0)
            ImageDraw.Draw(hole).polygon(_at(iw, ih, mx + rim, my + rim), fill=255)
            if plate:
                img.paste(ground, (0, 0), hole)
            else:
                # No plate behind it, so the interior must go fully TRANSPARENT.
                # (Filling it with ACCENT used to work only because the stroke
                # was white; now that stroke and fill are both violet, filling
                # would silently produce a solid shield instead of an empty one.)
                img.paste((0, 0, 0, 0), (0, 0), hole)

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "frontend", "public")
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)

    def save(img, name):
        img.save(os.path.join(out, name))
        print("wrote", os.path.join(out, name))

    save(draw_icon(16), "favicon-16x16.png")
    save(draw_icon(32), "favicon-32x32.png")
    save(draw_icon(180), "apple-touch-icon.png")
    save(draw_icon(192), "icon-192.png")
    save(draw_icon(512), "icon-512.png")
    save(draw_icon(512, plate=False), "mark-512.png")

    # Each size is composed at its own proportions (so the small ones keep a
    # heavier relative stroke), then embedded. The LARGEST must be the base:
    # Pillow only writes ICO entries up to the base image's size, so saving
    # from the 16px render would silently produce a 16-only icon.
    ico_sizes = (16, 32, 48)
    imgs = [draw_icon(s) for s in ico_sizes]
    ico_path = os.path.join(out, "favicon.ico")
    base, rest = imgs[-1], imgs[:-1]
    base.save(ico_path, format="ICO", sizes=[(s, s) for s in ico_sizes],
              append_images=rest)
    with Image.open(ico_path) as ico:
        embedded = sorted(ico.info.get("sizes", []))
    print("wrote", ico_path, "sizes:", embedded)

    print("\nSVG path (256 viewBox, fillRule=evenodd) for components/icons.tsx <Shield>:")
    print(shield_svg_hollow(256))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
