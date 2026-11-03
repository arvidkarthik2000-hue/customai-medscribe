"""Generated artwork for the fictional brands: abstract shapes plus the brand name.

No photos, no people, no product images: fine for a patient to glimpse on screen.
Returned as an SVG string; the browser shows it through a data: URL.
"""
from __future__ import annotations

from html import escape


def _tint(hex_color: str, amount: float) -> str:
    """Mix a colour with white (amount 0..1)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r = round(r + (255 - r) * amount)
    g = round(g + (255 - g) * amount)
    b = round(b + (255 - b) * amount)
    return f"#{r:02x}{g:02x}{b:02x}"


def _motif(kind: str, c: str) -> str:
    light, mid = _tint(c, 0.78), _tint(c, 0.45)
    if kind == "rings":
        return "".join(
            f'<circle cx="262" cy="60" r="{r}" fill="none" stroke="{col}" stroke-width="7"/>'
            for r, col in ((44, light), (30, mid), (16, c))
        )
    if kind == "waves":
        return "".join(
            f'<path d="M200 {y} q20 -16 40 0 t40 0 t40 0" fill="none" stroke="{col}" stroke-width="7" stroke-linecap="round"/>'
            for y, col in ((40, light), (62, mid), (84, c))
        )
    if kind == "arcs":
        return "".join(
            f'<path d="M{222 - i * 14} 100 a{40 + i * 14} {40 + i * 14} 0 0 1 {80 + i * 28} 0" fill="none" stroke="{col}" stroke-width="7" stroke-linecap="round"/>'
            for i, col in enumerate((c, mid, light))
        )
    if kind == "petals":
        return "".join(
            f'<ellipse cx="262" cy="60" rx="14" ry="38" fill="{col}" transform="rotate({a} 262 60)" opacity="0.9"/>'
            for a, col in ((0, light), (60, mid), (120, c))
        )
    if kind == "dots":
        out = []
        for i in range(4):
            for j in range(3):
                col = (light, mid, c)[(i + j) % 3]
                out.append(f'<circle cx="{214 + i * 30}" cy="{30 + j * 30}" r="9" fill="{col}"/>')
        return "".join(out)
    if kind == "pulse":
        return (
            f'<path d="M196 64 h26 l10 -26 l14 52 l12 -38 l8 12 h46" fill="none" stroke="{c}" stroke-width="7" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="300" cy="64" r="8" fill="{mid}"/>'
        )
    if kind == "lattice":
        lines = []
        for i in range(5):
            lines.append(f'<line x1="{206 + i * 22}" y1="18" x2="{206 + i * 22}" y2="102" stroke="{light}" stroke-width="5"/>')
            lines.append(f'<line x1="200" y1="{24 + i * 18}" x2="306" y2="{24 + i * 18}" stroke="{mid}" stroke-width="3"/>')
        return "".join(lines) + f'<rect x="240" y="44" width="30" height="30" rx="6" fill="{c}"/>'
    if kind == "steps":
        return "".join(
            f'<rect x="{208 + i * 24}" y="{88 - i * 18}" width="20" height="{14 + i * 18}" rx="4" fill="{col}"/>'
            for i, col in enumerate((light, mid, c, c))
        )
    if kind == "shield":
        return (
            f'<path d="M262 18 l38 14 v26 c0 26 -18 40 -38 48 c-20 -8 -38 -22 -38 -48 v-26z" fill="{light}"/>'
            f'<path d="M262 34 l24 9 v17 c0 16 -11 25 -24 30 c-13 -5 -24 -14 -24 -30 v-17z" fill="{c}"/>'
        )
    # bars
    return "".join(
        f'<rect x="{210 + i * 20}" y="{30 + (i % 3) * 10}" width="12" height="{60 - (i % 3) * 10}" rx="4" fill="{col}"/>'
        for i, col in enumerate((light, mid, c, mid, light))
    )


def brand_art(name: str, generic: str, color: str, motif: str) -> str:
    """A 320x120 card illustration with the brand wordmark on the left."""
    bg = _tint(color, 0.92)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 120" width="320" height="120" role="img" '
        f'aria-label="{escape(name)} sample artwork">'
        f'<rect width="320" height="120" rx="12" fill="{bg}"/>'
        f"{_motif(motif, color)}"
        f'<text x="22" y="58" font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif" font-size="28" font-weight="700" fill="{color}">{escape(name)}</text>'
        f'<text x="22" y="82" font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif" font-size="13" fill="#47525e">({escape(generic)})</text>'
        '<text x="22" y="104" font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif" font-size="10" fill="#7a4d00">Sample ad, not a real product</text>'
        "</svg>"
    )
