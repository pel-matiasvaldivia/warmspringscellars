#!/usr/bin/env python3
"""Take the final s off the script word in the label artwork.

The winery could not have warmspringscellars.com, so the name is now Warm
Springs Cellar. The printed labels still carry the plural — they were printed
— but every drawing the site and the mail generate comes out of those same
files, and a logo that says Cellars beside a page that says Cellar reads as a
mistake. So the s is removed here, once, in the one place both pipelines pass
through: tools/extract_labels.py for the bottle shots and tools/export_logo.py
for the lockup.

The word is live outlines, not type, so this is geometry rather than text. It
is two shapes, both measured against the script word's own bounding box so
they survive the labels being different sizes and the lockup being rendered at
a different resolution:

  cut     the s itself, from the join after the r out to the end of its tail
  taper   the connector the r throws towards the s, faded to nothing so the
          stroke lifts off the page like a pen rather than stopping at a wall

What is left is the r closing its bowl and flicking up — which is how the
word would have been drawn had it always been singular.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

# ── The brand's ink ─────────────────────────────────────────────────────────
# Measured off the lineup the winery supplied as the reference, where the
# doors are flat on a cream label and nothing is tinting them. It is asked for
# as "PMS 299", which it is not — Pantone 299 is a mid cyan — so the reference
# wins and the nearest standard chip is noted for the printer.
INK = (92, 0, 55)               # #5c0037 — nearest standard chip: Pantone 229 C
INK_PANTONE = "229 C"

# What the supplied .ai files are drawn in: a near-black ground, the doors in
# a plum so dark it reads brown on screen, and white line work over both.
ART_GROUND = (35, 31, 32)       # #231f20
ART_DOORS = (59, 30, 47)        # #3b1e2f
ART_LINE = (255, 255, 255)

# Everything below is in units of the script word's bounding box: u across,
# v down, both 0 at the top left corner of the word and 1 at the far side.
# Measured on images/labels/front-cabernet.png at 300dpi, where the word sits
# at x 411–708, y 1083–1188.
CUT = [(0.852, 0.162), (1.125, 0.162), (1.125, 1.067),
       (0.879, 1.067), (0.872, 0.733), (0.859, 0.638)]
TAPER = (0.821, 0.619, 0.872, 0.810)      # u0, v0, u1, v1

# A row of ink this wide can only be the ground line under the doors. The
# bands below it are WARM SPRINGS and then the script, in that order.
GROUND_RULE = 0.30


def _bands(ink: np.ndarray) -> list[tuple[int, int]]:
    """First and last row of every horizontal run of ink."""
    rows = ink.any(axis=1)
    bands, start = [], None
    for y, filled in enumerate(rows):
        if filled and start is None:
            start = y
        elif not filled and start is not None:
            bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, len(rows) - 1))
    return bands


def script_box(ink: np.ndarray) -> tuple[int, int, int, int]:
    """The script word's bounding box, as (x0, y0, x1, y1) inclusive.

    Found by structure rather than by position: the doors end on the ground
    line, the only row wide enough to be one, and the word is two bands below
    it. Nothing here depends on which wine the label belongs to.
    """
    bands = _bands(ink)
    counts = ink.sum(axis=1)
    for i, (y0, y1) in enumerate(bands):
        if counts[y1] >= ink.shape[1] * GROUND_RULE:
            doors = i
            break
    else:
        raise ValueError("no ground line: this is not the label artwork")

    if doors + 2 >= len(bands):
        raise ValueError("nothing below WARM SPRINGS: the script is missing")

    y0, y1 = bands[doors + 2]
    cols = np.where(ink[y0:y1 + 1].any(axis=0))[0]
    return int(cols.min()), y0, int(cols.max()), y1


def s_mask(ink: np.ndarray) -> np.ndarray:
    """How much of each pixel the s claims, 0 to 1, over the whole artwork."""
    x0, y0, x1, y1 = script_box(ink)
    w, h = x1 - x0, y1 - y0

    def at(u, v):
        return (x0 + u * w, y0 + v * h)

    cut = Image.new("F", (ink.shape[1], ink.shape[0]), 0.0)
    ImageDraw.Draw(cut).polygon([at(u, v) for u, v in CUT], fill=1.0)
    mask = np.asarray(cut).copy()

    # The connector fades from untouched at its left to gone at the cut, so
    # the two meet without a step.
    tu0, tv0, tu1, tv1 = TAPER
    (tx0, ty0), (tx1, ty1) = at(tu0, tv0), at(tu1, tv1)
    xs = np.arange(ink.shape[1])
    ramp = np.clip((xs - tx0) / max(tx1 - tx0, 1e-6), 0, 1)
    rows = slice(int(round(ty0)), int(round(ty1)) + 1)
    mask[rows] = np.maximum(mask[rows], ramp[None, :])
    return mask


def singular(rgb: np.ndarray, ground: np.ndarray | None = None) -> np.ndarray:
    """The artwork with the s painted out in the label's own ground colour."""
    grey = rgb.mean(axis=2)
    ink = grey > (grey.min() + grey.max()) / 2
    if ground is None:
        ground = rgb[5:45, 5:45].reshape(-1, 3).mean(axis=0)
    mask = s_mask(ink)[..., None]
    return rgb * (1 - mask) + np.asarray(ground, float) * mask


def recoloured(rgb: np.ndarray, doors=INK) -> np.ndarray:
    """The artwork with the doors repainted, edges and all.

    A hand-rolled "replace this colour" pass would leave a fringe: every edge
    in the drawing is an antialiased mix of two inks, and those mixed pixels
    match neither. So each pixel is unmixed into how much ground, doors and
    line work it holds — three knowns, so the mix is solvable — and then put
    back together with the new colour in the doors' place. The line work and
    the ground come through untouched, and the edges land where they should
    because they are rebuilt from the same weights that described them.
    """
    inks = np.array([ART_GROUND, ART_DOORS, ART_LINE], float)
    # The fourth row makes the weights a mix rather than any three numbers
    # that happen to hit the colour: it asks them to add up to one.
    basis = np.vstack([inks.T, np.ones(3)])
    weights = (np.linalg.pinv(basis) @
               np.concatenate([rgb.reshape(-1, 3), np.ones((rgb[..., 0].size, 1))],
                              axis=1).T).T
    weights = np.clip(weights, 0, None)
    weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-6)

    out = np.array([ART_GROUND, doors, ART_LINE], float)
    return (weights @ out).reshape(rgb.shape)


def rebrand(rgb: np.ndarray, doors=INK) -> np.ndarray:
    """Both changes at once: the doors in the brand's ink, and no final s."""
    return singular(recoloured(rgb, doors), ground=np.array(ART_GROUND, float))
