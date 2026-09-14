"""Generates the application icon: the isometric cube from the brand mark.

The mark that `make_logo.py` draws has two parts, a cube and a wordmark. An
application icon is looked at at 16 pixels, where a wordmark is an unreadable
smudge, so only the cube is used here.

The geometry is the same one `web/img/trove-accounts-hub.svg` carries, copied as
literal coordinates: it is three quadrilaterals, each shrunk towards its own
centre and given the thickness back as a round-jointed stroke, which is what
makes the corners blunt and leaves the seams between faces.

It writes:

  * `web/img/app.ico`  - for the Windows executable (7 sizes in one file).
  * `web/img/app.png`  - 512px, for the Linux .desktop entry.

Only needed to REGENERATE the icon; the application does not use this script:

    pip install pillow
    python tools/make_icon.py     # from the repository root
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "web" / "img"

# The three faces, exactly as they appear in trove-accounts-hub.svg: top, left
# and right, already shrunk towards their centres.
FACES = [
    [(0.00, 1.45), (9.05, 6.10), (0.00, 10.75), (-9.05, 6.10)],
    [(-9.76, 7.35), (-1.29, 12.86), (-0.74, 22.45), (-9.21, 16.94)],
    [(9.76, 7.35), (1.29, 12.86), (0.74, 22.45), (9.21, 16.94)],
]
STROKE = 0.96              # stroke-width in the SVG

# The default accent. A club theme repaints the interface, but a file on disk
# cannot follow it, so the icon keeps the colour the app starts with.
COLOUR = (34, 197, 94, 255)        # #22c55e

# The cube fills this much of the canvas; the rest is breathing room, which is
# what keeps it from touching the edges of a taskbar button.
FILL = 0.86
SUPERSAMPLE = 8            # drawn this many times larger, then reduced


def bounds():
    """The mark's box, stroke included."""
    xs = [x for face in FACES for x, _y in face]
    ys = [y for face in FACES for _x, y in face]
    pad = STROKE / 2
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def render(size: int) -> Image.Image:
    """The cube, centred on a transparent square of `size` pixels."""
    big = size * SUPERSAMPLE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    x0, y0, x1, y1 = bounds()
    scale = big * FILL / max(x1 - x0, y1 - y0)
    # Centre the mark's box on the canvas rather than its coordinate origin.
    off_x = (big - (x1 - x0) * scale) / 2 - x0 * scale
    off_y = (big - (y1 - y0) * scale) / 2 - y0 * scale

    width = max(int(round(STROKE * scale)), 1)
    for face in FACES:
        pts = [(x * scale + off_x, y * scale + off_y) for x, y in face]
        draw.polygon(pts, fill=COLOUR)
        # `joint="curve"` is what reproduces the SVG's stroke-linejoin: round.
        draw.line(pts + [pts[0]], fill=COLOUR, width=width, joint="curve")

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Windows picks the size it needs out of the file; 16 and 32 are the ones
    # actually seen, so they are rendered at their own size rather than being
    # squeezed down from 256, which turns the seams to mush.
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [render(s) for s in sizes]
    ico = OUT_DIR / "app.ico"
    frames[-1].save(ico, format="ICO",
                    sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    print(f"{ico.relative_to(ROOT)}  {ico.stat().st_size} bytes  "
          f"({', '.join(str(s) for s in sizes)})")

    png = OUT_DIR / "app.png"
    render(512).save(png, format="PNG")
    print(f"{png.relative_to(ROOT)}  {png.stat().st_size} bytes  (512)")


if __name__ == "__main__":
    main()
