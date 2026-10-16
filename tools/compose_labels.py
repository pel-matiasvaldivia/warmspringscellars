#!/usr/bin/env python3
"""Draw the front panels for the two wines that have no .ai file yet.

The Sauvignon Blanc and the Rosé are bottled in flint glass, and the mockups
the winery supplied put their labels on clear film: one ink, printed straight
onto the bottle, with the wine itself showing through everywhere the ink is
not. That is the opposite of the five reds, where the artwork is near white
on a near black panel. No file was supplied for either, and composing one by
hand would mean redrawing the gable, the medallion and the doors.

So the drawing is borrowed instead. The Cabernet's front is taken apart into
the three inks it was drawn with, and what comes out is ink coverage rather
than a picture: the doors' mass carries ink, the bare ground carries none.
The line work is the part no rule settles. On the open panel it is the
drawing — the wordmark, EST 1982, the ground line — and has to print. Inside
the doors it is the hatching, which on film has to print as nothing at all,
or the doors fill in solid.

The vintage, the varietal and the vineyard are then set again underneath, in
the faces the supplied labels use and on the lines they use, measured off the
Zinfandel because it carries a one-word varietal like these two do.

Run from the repo root:  python3 tools/compose_labels.py
Requires Pillow and numpy. Runs after tools/extract_labels.py, whose output
it reads, and before tools/label_bottles.py, which puts these on glass.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import brandmark

TEMPLATE = "images/labels/front-cabernet.png"
OUT_DIR = "images/labels"

# The two faces the supplied labels are set in, as close as this machine has
# them: a grotesque for the vintage and the varietal, a slab-ish serif for
# the vineyard. Both are matched to the artwork by cap height below, not by
# point size, so a substitution changes the shapes and not the layout.
SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"

# Measured on images/labels/front-zinfandel.png, 1116 x 1869: the top of each
# line, its cap height, and how wide the words come out once tracked. All as
# fractions of the panel, so they survive a label of another size.
PANEL = (1116, 1869)
CENTRE = 556 / 1116
LINES = {
    "vintage":  dict(top=1321 / 1869, cap=42 / 1869, font=SANS,  track=0.055),
    "varietal": dict(top=1407 / 1869, cap=41 / 1869, font=SANS,  track=0.055),
    "estate":   dict(top=1678 / 1869, cap=36 / 1869, font=SERIF, track=0.0),
    "region":   dict(top=1741 / 1869, cap=32 / 1869, font=SERIF, track=0.0),
}

WINES = {
    "sauvignon-blanc": dict(vintage="2026", varietal="SAUVIGNON BLANC",
                            estate="Peterson Vineyard", region="Sonoma Valley"),
    "rose": dict(vintage="2026", varietal="ROSÉ SYRAH",
                 estate="Deerfield Vineyard", region="Sonoma Valley"),
}


def inside_doors(rgb: np.ndarray) -> np.ndarray:
    """True where a pixel's surroundings are doors rather than bare panel.

    The hatching inside the doors is the same white as the wordmark, and the
    two have to go opposite ways when the panel turns pale. Nothing about the
    pixels themselves separates them, and flooding the panel from its edge
    does not either — the roof and the ground line are both drawn in the
    doors' ink and both run the full width, so they wall the wordmark off
    from every corner.

    What does separate them is what each sits on. Blurring the two inks far
    enough to swallow a stroke asks of every pixel which of them its
    neighbourhood is made of: plum under the hatching, bare ground under the
    name. The radius only has to beat the width of a stroke.
    """
    weights = brandmark.weights(rgb, doors=brandmark.INK)
    radius = max(round(min(rgb.shape[:2]) * 0.012), 2)

    def spread(plane):
        grey = (np.clip(plane, 0, 1) * 255).round().astype(np.uint8)
        return np.asarray(Image.fromarray(grey, "L")
                          .filter(ImageFilter.GaussianBlur(radius))).astype(int)

    return spread(weights[..., 1]) > spread(weights[..., 0])


def coverage(rgb: np.ndarray) -> np.ndarray:
    """How much ink each pixel of the film carries, 0 to 1.

    The doors print solid. The line work prints where it is the drawing and
    prints nothing where it is the hatching, which leaves the hatching as
    bare glass — the same holes the reds show in white.
    """
    w = brandmark.weights(rgb, doors=brandmark.INK)
    enclosed = inside_doors(rgb)
    return np.clip(w[..., 1] + w[..., 2] * ~enclosed, 0, 1)


def set_line(panel: Image.Image, text: str, spec: dict, ink) -> None:
    """One line of type, centred and sized by its cap height, not its points."""
    w, h = panel.size
    cap = spec["cap"] * h

    # Point size is solved for rather than guessed: ask the face how tall its
    # capitals come out, then scale. One pass is enough — it is linear.
    probe = ImageFont.truetype(spec["font"], 100)
    probe_cap = probe.getbbox("H")[3] - probe.getbbox("H")[1]
    font = ImageFont.truetype(spec["font"], max(round(100 * cap / probe_cap), 1))

    track = spec["track"] * font.size
    draw = ImageDraw.Draw(panel)
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + track * (len(text) - 1)

    x = CENTRE * w - total / 2
    # Anchored on the cap line, so every line sits where the artwork's did
    # whatever the face's own ascent happens to be.
    y = spec["top"] * h - font.getbbox("H")[1]
    for ch, advance in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=ink)
        x += advance + track


def compose(name: str, words: dict) -> None:
    rgb = np.asarray(Image.open(TEMPLATE).convert("RGB")).astype(float)
    ink = Image.fromarray((coverage(rgb) * 255).round().astype(np.uint8), "L")

    # Everything under the script belongs to this wine; the template's goes.
    grey = rgb.mean(axis=2)
    _, _, _, script_bottom = brandmark.script_box(grey > (grey.min() + grey.max()) / 2)
    ImageDraw.Draw(ink).rectangle(
        [0, script_bottom + int(ink.height * 0.012), ink.width, ink.height], fill=0)

    for key, spec in LINES.items():
        set_line(ink, words[key], spec, 255)

    # Written as the ink itself on transparency, so the file is the label and
    # not a picture of it on a background it will never be printed on.
    panel = Image.new("RGBA", ink.size, (*brandmark.INK, 0))
    panel.putalpha(ink)

    out = f"{OUT_DIR}/front-{name}.png"
    panel.save(out, optimize=True)
    print(f"{name:16s} {panel.width}x{panel.height}px  -> {out}")


def main() -> None:
    for name, words in WINES.items():
        compose(name, words)


if __name__ == "__main__":
    main()
