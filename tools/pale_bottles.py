#!/usr/bin/env python3
"""Put the new film labels onto the two pale bottles.

The five reds come off studio renders on a grey sweep, and tools/
label_bottles.py and tools/cutout_bottles.py are built around that: a dark
bottle to find by its own darkness, a backdrop to flood away, a cast shadow
to cut off. The Sauvignon Blanc and the Rosé arrived as something else —
already on transparency, already lit, and pale enough that every one of
those tests would read the glass as background.

So they get their own short pipeline, borrowing the parts of label_bottles
that are about cylinders rather than about dark glass. What is different:

  * the silhouette comes from the alpha channel, which is exact, instead of
    being traced out of the luminance;
  * the label is film, not paper, so it is multiplied onto the glass rather
    than lit as a separate diffuse surface, and it casts nothing;
  * the sources are small, so the work is done at twice the output size and
    brought back down at the end, which is also what keeps the upscaled
    glass from looking soft next to a label drawn at 300dpi.

The output scale is tied to the reds: a bottle here comes out at the same
pixels per inch as one from cutout_bottles.py, so the two sets stand
together. These bottles are taller because the bottles are taller.

Run from the repo root:  python3 tools/pale_bottles.py
Requires Pillow and numpy. Runs after tools/compose_labels.py.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

import brandmark
from label_bottles import (CAPSULE_CEILING, CAPSULE_CHROMA, CAPSULE_HUE,
                           CAPSULE_VALUE, FEATHER, capsule_rows,
                           lighting_profile, retint, sample, silhouette,
                           vertical_gain)

LABEL_DIR = "images/labels"

# What a finished bottle measures, in pixels to the inch. Taken from the red
# lineup: 92px/in in the renders, brought to 980px from a 1063px cut.
PIXELS_PER_INCH = 92.0 * 980 / 1063
# Twice that while the work is done, halved at the end.
OVERSAMPLE = 2

BOTTLES = {
    "sauvignon-blanc": {
        "file": "images/WarmSpringsCellar_SauvignonBlanc.png",
        "label_in": (3.75, 6.25),
        "width_in": 2.92,          # the body of an Alsace bottle, across
    },
    "rose": {
        "file": "images/WarmSpringsCellar_Rose.png",
        "label_in": (3.75, 6.25),
        "width_in": 2.92,
    },
}

# Where the artwork sits and where the glass is clean, as fractions of the
# bottle's own height. Measured on the supplied Sauvignon Blanc and shared,
# because the two mockups are the same bottle photographed the same way.
BAND = (0.374, 0.879)
CLEAN = [(0.259, 0.367), (0.883, 0.912)]
BOW = 11


def opened(spec):
    """The source at working scale, as float RGB plus its alpha."""
    img = Image.open(spec["file"]).convert("RGBA")
    body = np.asarray(img)[..., 3] > 128
    cols = np.where(body.any(axis=0))[0]
    across = cols.max() - cols.min()

    scale = PIXELS_PER_INCH * OVERSAMPLE / (across / spec["width_in"])
    big = img.resize((max(round(img.width * scale), 1),
                      max(round(img.height * scale), 1)), Image.LANCZOS)
    rgba = np.asarray(big).astype(float)
    return rgba[..., :3].copy(), rgba[..., 3].copy()


def rows_of(alpha, fractions):
    """Fractions of the bottle's height, as rows of this image."""
    ys = np.where((alpha > 128).any(axis=1))[0]
    top, height = ys.min(), ys.max() - ys.min()
    return tuple(int(round(top + f * height)) for f in fractions)


def build(name, spec):
    img, alpha = opened(spec)
    h, w, _ = img.shape

    rows = capsule_rows(img)
    if rows:
        retint(img, rows, CAPSULE_HUE, CAPSULE_CHROMA, CAPSULE_CEILING, CAPSULE_VALUE)

    # Outside the bottle is nothing at all, which would drag the repaint
    # towards black at the edges; the silhouette comes from alpha instead, so
    # every profile below is sampled across glass only.
    left, right = silhouette(255.0 - alpha, 128)
    y0, y1 = rows_of(alpha, BAND)
    clean_rows = [rows_of(alpha, pair) for pair in CLEAN]

    prof = lighting_profile(img, left, right, clean_rows)
    lum = img.mean(axis=2)
    all_y, gain, _ref = vertical_gain(lum, left, right, (y0, y1), clean_rows)
    gain_at = lambda y: np.interp(y, all_y, gain)

    film = np.asarray(Image.open(f"{LABEL_DIR}/front-{name}.png").convert("RGBA"))
    cover = film[..., 3].astype(float) / 255.0
    LH, LW = cover.shape

    out = img.copy()
    ys = np.arange(y0 - FEATHER - 2, y1 + FEATHER + 3)
    Y, X = np.meshgrid(ys, np.arange(w), indexing="ij")

    L, R = left[ys][:, None], right[ys][:, None]
    C, A = (L + R) / 2, (R - L) / 2
    s = (X - C) / np.maximum(A, 1e-6)
    on_bottle = np.abs(s) <= 1.0
    theta = np.arcsin(np.clip(s, -1, 1))

    # ── 1. Clean glass, to erase the mockup's own artwork ────────────────
    u = (s + 1) / 2 * (prof.shape[0] - 1)
    glass = np.stack([np.interp(u.ravel(), np.arange(prof.shape[0]), prof[:, c]).reshape(u.shape)
                      for c in range(3)], axis=-1)
    glass *= gain_at(Y)[..., None]
    # No grain is carried over, unlike the reds. These sources are smooth and
    # have been enlarged, so what a de-trending pass finds in them is not the
    # render's texture but the ringing of the enlargement — and tiling that
    # down the bottle prints a ghost of the old artwork's edges.

    d_top = (Y - ys[0]) / FEATHER
    d_bot = (ys[-1] - Y) / FEATHER
    blend = np.clip(np.minimum(d_top, d_bot), 0, 1) * on_bottle
    band = out[ys[0]:ys[-1] + 1]
    band[:] = band * (1 - blend[..., None]) + glass * blend[..., None]

    # ── 2. Wrap the film ─────────────────────────────────────────────────
    px_per_in = (y1 - y0) / spec["label_in"][1]
    radius_in = (right[(y0 + y1) // 2] - left[(y0 + y1) // 2]) / 2 / px_per_in
    theta_max = np.radians(min(spec["label_in"][0] / (2 * np.pi * radius_in) * 180.0, 78.0))

    bow = BOW * OVERSAMPLE * (1 - np.cos(theta))
    v = (Y + bow - y0) / (y1 - y0)
    uu = (theta / theta_max + 1) / 2
    on_label = on_bottle & (np.abs(theta) <= theta_max) & (v >= 0) & (v <= 1)

    ink_at = sample(cover[..., None], uu * (LW - 1), np.clip(v, 0, 1) * (LH - 1))[..., 0]
    ink_at = np.where(on_label, ink_at, 0.0)

    # Ink on clear film takes the light the glass under it is taking, so it
    # is shaded by that glass rather than lit as a surface of its own. Half
    # strength: a printed solid darkens in shadow far less than glass does.
    shade = np.clip(band.mean(axis=2) / max(float(np.median(glass.mean(axis=2))), 1e-6),
                    0.45, 1.6)
    shade = 1 + (shade - 1) * 0.5
    ink = np.array(brandmark.INK, float) * shade[..., None]

    band[:] = band * (1 - ink_at[..., None]) + ink * ink_at[..., None]
    out[ys[0]:ys[-1] + 1] = band

    rgba = np.dstack([np.clip(out, 0, 255), alpha]).astype(np.uint8)
    # Trimmed to the bottle, not to the bounding box: the mockups carry a few
    # stray lit pixels out in the margin, and getbbox would keep all of them.
    solid = alpha > 40
    keep_rows = np.where(solid.sum(axis=1) > 4)[0]
    keep_cols = np.where(solid.sum(axis=0) > 4)[0]
    full = Image.fromarray(rgba, "RGBA").crop(
        (int(keep_cols.min()), int(keep_rows.min()),
         int(keep_cols.max()) + 1, int(keep_rows.max()) + 1))
    small = full.resize((max(full.width // OVERSAMPLE, 1), max(full.height // OVERSAMPLE, 1)),
                        Image.LANCZOS)
    return small.filter(ImageFilter.UnsharpMask(radius=1.2, percent=55, threshold=3))


def main():
    for name, spec in BOTTLES.items():
        img = build(name, spec)
        out = f"images/{name}-bottle.png"
        img.save(out, optimize=True)
        print(f"{name:16s} {img.width}x{img.height}  -> {out}")


if __name__ == "__main__":
    main()
