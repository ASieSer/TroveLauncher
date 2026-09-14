"""Generates `web/img/trove-accounts-hub.svg`: isometric mark plus wordmark.

The wordmark is traced to curves from the Comfortaa that already ships with the
app (instanced at wght=700), so the SVG depends on no font when painted and can
be used as a CSS mask to take the accent colour.

Only needed to REGENERATE the logo; the application neither uses this script nor
needs its dependencies:

    pip install fonttools brotli
    python tools/make_logo.py     # from the repository root
"""
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.svgPathPen import SVGPathPen

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "web/fonts/1PtCg8LJRfWJmhDAuUsSQamb1W0lwk4S4WjMXL830efAesmwYSFoxBEP_I0.woff2"

base = TTFont(FONT)
upem = base["head"].unitsPerEm
cap_h = base["OS/2"].sCapHeight
cmap = base.getBestCmap()

weights = {}
for w in (600, 700):
    f = instantiateVariableFont(TTFont(FONT), {"wght": w})
    weights[w] = (f.getGlyphSet(), f["hmtx"])


def line(text, cap, track, weight):
    """One line of text already traced to curves, plus its width."""
    glyphs, hmtx = weights[weight]
    scale = cap / cap_h
    parts, x = [], 0.0
    for ch in text:
        name = cmap[ord(ch)]
        if ch != " ":
            pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.1f}")
            glyphs[name].draw(pen)
            d = pen.getCommands()
            if d:
                # y flipped: the font grows upwards, the SVG downwards.
                parts.append(f'<path transform="translate({x:.2f} 0) '
                             f'scale({scale:.5f} {-scale:.5f})" d="{d}"/>')
        x += (hmtx[name][0] + track * upem) * scale
    return parts, x - track * upem * scale


# --- wordmark: the short name on top, the tail below in small caps ---------
#
# Both lines are justified to the same width: the lower one, which is the long
# one, is measured and the upper one's tracking is opened until it matches. A
# block with straight sides reads as one piece, not as two lines of text.
CAP_TOP, CAP_SUB = 13.2, 6.6
sub, sub_w = line("ACCOUNTS HUB", CAP_SUB, 0.155, 600)
_, bare_w = line("TROVE", CAP_TOP, 0.0, 700)
track_top = (sub_w - bare_w) / (upem * (CAP_TOP / cap_h) * (len("TROVE") - 1))
top, top_w = line("TROVE", CAP_TOP, track_top, 700)
LEAD = 6.4                                   # gap between the two lines
word_w = max(top_w, sub_w)
word_h = CAP_TOP + LEAD + CAP_SUB

# --- mark: an isometric three-faced cube with seams -----------------------
W, H, S = 10.5, 6.1, 11.5             # half-width, half-height of the rhombus, side height
faces = [
    [(0, 0), (W, H), (0, 2 * H), (-W, H)],                        # top
    [(-W, H), (0, 2 * H), (0, 2 * H + S), (-W, H + S)],           # left
    [(W, H), (0, 2 * H), (0, 2 * H + S), (W, H + S)],             # right
]
INSET = 1.45                           # gap between faces + corner radius
mark = []
for face in faces:
    cx = sum(p[0] for p in face) / len(face)
    cy = sum(p[1] for p in face) / len(face)
    # Shrink towards the centre and give the thickness back with a rounded
    # stroke: that way the corners come out blunt without hand-computing arcs.
    pts = []
    for px, py in face:
        dx, dy = px - cx, py - cy
        L = (dx * dx + dy * dy) ** .5
        k = max(0.0, (L - INSET) / L)
        pts.append(f"{cx + dx * k:.2f},{cy + dy * k:.2f}")
    mark.append(f'<polygon points="{" ".join(pts)}" stroke-width="{INSET * 0.66:.2f}"/>')

mark_w, mark_h = 2 * W, 2 * H + S
GAP = 8.5
total_w = mark_w + GAP + word_w
total_h = max(mark_h, word_h)
mark_y = (total_h - mark_h) / 2
word_y = (total_h - word_h) / 2

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w:.2f} {total_h:.2f}" fill="currentColor">
<title>Trove Accounts Hub</title>
<g transform="translate({W:.2f} {mark_y:.2f})" stroke="currentColor" stroke-linejoin="round">
{chr(10).join(mark)}
</g>
<g transform="translate({mark_w + GAP:.2f} {word_y + CAP_TOP:.2f})">
{chr(10).join(top)}
</g>
<g transform="translate({mark_w + GAP:.2f} {word_y + CAP_TOP + LEAD + CAP_SUB:.2f})">
{chr(10).join(sub)}
</g>
</svg>
'''
out = ROOT / "web/img/trove-accounts-hub.svg"
out.write_text(svg, encoding="utf-8")
print(f"{out.relative_to(ROOT)}: viewBox {total_w:.2f} x {total_h:.2f}  (ratio {total_w / total_h:.3f})")
print(f"marca {mark_w:.1f}x{mark_h:.1f}   TROVE {top_w:.1f}   ACCOUNTS HUB {sub_w:.1f}")
