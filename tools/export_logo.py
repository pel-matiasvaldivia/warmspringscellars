#!/usr/bin/env python3
"""Export the winery's full lockup as a flat image, from the label artwork.

The lockup is the gable, the barrel-and-lantern medallion, EST 1982, the
arched doors, the ground line and the WARM SPRINGS / Cellar wordmark. All of
it is drawn in the label files — the wordmark is outlined, not live type — so
nothing here depends on having the brand's fonts installed.

Why this is a raster and `tools/extract_doormark.py` is a vector: that one
lifts the door mark alone, where the white line work always sits on top of a
burgundy mass and can be cut out of it with a mask. Across the whole lockup
that rule breaks. The wordmark and the medallion's interior are white on the
bare ground, with no mass behind them, so masking them out leaves a hole where
the brand's name should be. The page only ever needs the mark, so the mask
stays there and the lockup is rendered instead.

Two versions, because a logo is asked for on both kinds of paper:

  …-dark.jpg   exactly as printed — light ink on the label's near-black
  ….jpg        wine on cream, for a white page

The light one is not a hand recolour. Each pixel's distance from the label's
own ground becomes how much ink it carries, and that drives a blend from the
cream to the wine. Full-strength white line work lands on full wine; the
burgundy masses, which sit closer to the ground, land on a pale wine tint. The
drawing keeps its own relative weights instead of being flattened to one.

Run from the repo root:  python3 tools/export_logo.py
Requires PyMuPDF, Pillow and numpy.
"""

import numpy as np
import pymupdf
from PIL import Image

import brandmark

SRC = "labels/21CS_WSC_final_label.ai"
OUT_LIGHT = "images/logo-warm-springs-cellar.jpg"
OUT_DARK = "images/logo-warm-springs-cellar-dark.jpg"
# The same drawing on transparency, for anything that is not a flat cream or
# a flat near-black: one in the wine for light backgrounds, one in the label's
# own near-white for dark ones.
OUT_PNG_WINE = "images/logo-warm-springs-cellar.png"
OUT_PNG_LIGHT_INK = "images/logo-warm-springs-cellar-light.png"

# The same drawing again, small, for the mail signatures: one on transparency
# and one on solid cream, because Outlook will not always honour the first.
OUT_EMAIL = "images/logo-email.png"
OUT_EMAIL_CREAM = "images/logo-email-cream.png"
EMAIL_WIDTH = 240          # a 120px slot on a screen that doubles everything

# The lockup's box on the front panel, in PDF points: the roof's apex down
# through the tail of the script.
BOX = pymupdf.Rect(18, 8, 252, 296)
DPI = 600

WINE = brandmark.INK
CREAM = (250, 248, 245)
LABEL_INK = (247, 241, 232)
LABEL_GROUND = (36, 31, 32)

# Air around the drawing, as a fraction of its longest side.
PAD = 0.09
# Below this much ink a pixel is ground, not artwork, when trimming.
TRIM_FLOOR = 0.06


def artwork() -> np.ndarray:
    """The lockup's ink coverage, 0 to 1, trimmed to the drawing."""
    doc = pymupdf.open(SRC)
    ocgs = doc.get_ocgs()
    comp = [x for x, info in ocgs.items() if info["name"] == "Color comp"]
    if not comp:
        raise SystemExit(f"{SRC}: no 'Color comp' layer")
    doc.set_layer(-1, on=comp, off=[x for x in ocgs if x not in comp])

    pix = doc[0].get_pixmap(dpi=DPI, clip=BOX, alpha=False)
    rgb = np.asarray(
        Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    ).astype(np.float32)

    # The ground is read from a corner rather than assumed: the label's black
    # is not #000, and guessing it would leave a faint rectangle behind.
    ground = rgb[5:40, 5:40].reshape(-1, 3).mean(axis=0)
    distance = np.linalg.norm(rgb - ground, axis=2)
    # The 99.9th percentile, not the maximum: one stray antialiased pixel
    # should not decide what counts as full ink.
    ink = np.clip(distance / np.percentile(distance, 99.9), 0, 1)

    # The name lost its s when the domain did; here that is one stroke's worth
    # of coverage taken back out. See tools/brandmark.py.
    ink *= 1 - brandmark.s_mask(ink > 0.5)

    rows = np.where(ink.max(axis=1) > TRIM_FLOOR)[0]
    cols = np.where(ink.max(axis=0) > TRIM_FLOOR)[0]
    return ink[rows.min():rows.max() + 1, cols.min():cols.max() + 1]


def padded(ink: np.ndarray) -> np.ndarray:
    h, w = ink.shape
    pad = int(round(max(h, w) * PAD))
    canvas = np.zeros((h + 2 * pad, w + 2 * pad), np.float32)
    canvas[pad:pad + h, pad:pad + w] = ink
    return canvas


def render(ink: np.ndarray, fg, bg) -> Image.Image:
    coverage = padded(ink)[..., None]
    out = np.array(bg, np.float32) * (1 - coverage) + np.array(fg, np.float32) * coverage
    return Image.fromarray(out.round().astype(np.uint8))


def render_transparent(ink: np.ndarray, fg) -> Image.Image:
    """The same coverage as alpha, so it composites onto any background."""
    coverage = padded(ink)
    rgba = np.zeros((*coverage.shape, 4), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = fg
    rgba[..., 3] = (coverage * 255).round().astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def main() -> None:
    ink = artwork()
    print(f"lockup {ink.shape[1]}x{ink.shape[0]}px at {DPI}dpi")
    for path, fg, bg in ((OUT_LIGHT, WINE, CREAM), (OUT_DARK, LABEL_INK, LABEL_GROUND)):
        img = render(ink, fg, bg)
        img.save(path, quality=95, subsampling=0, optimize=True)
        print(f"{img.width}x{img.height}  -> {path}")

    for path, fg in ((OUT_PNG_WINE, WINE), (OUT_PNG_LIGHT_INK, LABEL_INK)):
        img = render_transparent(ink, fg)
        img.save(path, optimize=True)
        print(f"{img.width}x{img.height}  -> {path}  (transparent)")

    # The signatures ask for the same drawing at the size they display it,
    # rather than a 600dpi file the mail client has to shrink itself.
    for path, img in ((OUT_EMAIL, render_transparent(ink, WINE)),
                      (OUT_EMAIL_CREAM, render(ink, WINE, CREAM))):
        small = img.resize((EMAIL_WIDTH,
                            max(round(img.height * EMAIL_WIDTH / img.width), 1)),
                           Image.LANCZOS)
        small.save(path, optimize=True)
        print(f"{small.width}x{small.height}  -> {path}")


if __name__ == "__main__":
    main()
