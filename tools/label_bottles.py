#!/usr/bin/env python3
"""Put the official label artwork onto the winery's bottle renders.

The two supplied renders — a Bordeaux bottle and a Burgundy bottle — carry the
old artwork, so a new wine cannot just reuse them. This rebuilds the label area
from scratch:

  1. Trace the bottle's silhouette row by row to get the cylinder's axis and
     radius at every height.
  2. Learn the studio lighting from rows of clean glass above and below the old
     label — a profile across the bottle holding the key light on the left, the
     core shadow right of centre and the rim light on the right edge.
  3. Repaint the label band with that profile, which erases the old artwork and
     leaves a clean bottle.
  4. Wrap the new artwork around the cylinder: a column at screen offset s sits
     at angle asin(s), so it compresses towards the edges exactly as something
     printed on glass does. How far it reaches follows from the artwork's real
     width and the bottle's real circumference, not from taste.
  5. Print it rather than paste it. These bottles are decorated directly —
     the ink is fired onto the glass, which is the expensive way to do it and
     the thing the wines are known for — so the drawing's ground is nothing at
     all and the glass shows between the strokes. Only the ink is lit, as a
     matte film on a cylinder, and the bottle's own highlight is carried over
     the top of it, because a print lies under the same gloss as the glass
     while a paper label stands off it and breaks the reflection.

Run from the repo root:  python3 tools/label_bottles.py
Requires Pillow and numpy. Labels come from tools/extract_labels.py.
"""

import os

import numpy as np
from PIL import Image, ImageFilter

import brandmark

LABEL_DIR = "images/labels"
OUT_DIR = "images/renders"

# Each render, with the label band measured on it and the glass rows to learn
# the lighting from. The band is where the old artwork lives; it is repainted
# whole, so it must cover every trace of the old label.
BASES = {
    "bordeaux": {
        "file": "images/WarmSpringsCellars_Cabernet.png",
        "band": (535, 1095),
        "clean_rows": [(400, 530), (1100, 1140)],
        "bow": 10,
    },
    # The Burgundy render arrived wearing a label well under the trim size of
    # the file it is supposed to be printing — 77 pixels to the inch where the
    # bottle itself measures 92, so the artwork sat a sixth too small and the
    # wine's name came out unreadable beside the other four. The band is set
    # from the bottle instead: 4.50in of label at the render's own scale,
    # ending where the old one did, which is where the artwork really stops.
    "burgundy": {
        "file": "images/WarmSpringsCellars_Pinot.png",
        "band": (691, 1105),
        "clean_rows": [(596, 684), (1112, 1140)],
        "bow": 12,
    },
}

# Label trim sizes in inches, from the artboards in the supplied .ai files.
# The wrap angle is derived from these, so they have to be right.
WINES = {
    "cabernet":   {"base": "bordeaux", "label_in": (3.75, 6.25)},
    "malbec":     {"base": "bordeaux", "label_in": (3.74, 6.25)},
    "zinfandel":  {"base": "bordeaux", "label_in": (3.74, 6.25)},
    "sangiovese": {"base": "bordeaux", "label_in": (3.74, 6.25)},
    "pinot":      {"base": "burgundy", "label_in": (3.75, 4.50)},
}

# The capsule the renders arrived with is a mid violet, and against the new
# label it reads as a different brand. These are the numbers that take it to
# the wine the lineup actually wears: one hue for every bottle, the violet's
# weak chroma pushed up to foil strength, and the whole thing darkened, since
# a tinted foil is dark where a violet plastic sleeve was not.
CAPSULE_HUE = 332.0 / 360.0
CAPSULE_CHROMA = 3.0
CAPSULE_CEILING = 0.78
CAPSULE_VALUE = 0.55

FEATHER = 26            # rows the repainted band fades over at each end
PRINT_FLOOR = 0.58      # what the ink still returns where the key does not reach
GLOSS = 0.55            # how much of the bottle's highlight carries over the ink
INK_GAIN = 0.78         # gamma on coverage, to keep fine strokes from thinning
KEY_ANGLE = np.radians(-30.0)   # studio key, upper left of the bottle
RIM_ANGLE = np.radians(70.0)    # weak fill down the far edge


def capsule_rows(img):
    """First and last row of the foil at the neck.

    Found rather than measured: the supplied capsule is the one violet thing
    in the frame — red and blue both well clear of green — and it is the
    topmost run of rows where that holds across most of the neck.
    """
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    violet = ((r - g > 18) & (b - g > 18)).sum(axis=1)
    rows = np.where(violet > 20)[0]
    if rows.size == 0:
        return None
    start = rows[0]
    end = start
    for y in rows:
        if y - end > 30:        # a gap this long is the next thing down
            break
        end = y
    return start, end


def retint(img, rows, hue, chroma, ceiling, value):
    """Swing the foil's hue onto the brand's, in place.

    Hue is replaced outright and saturation is pushed up to a fixed ceiling,
    because the target is one flat ink; brightness is only scaled, so the
    highlight running down the capsule and the shadow under its skirt survive
    as the shape of the metal.
    """
    y0, y1 = rows
    patch = img[y0:y1 + 1] / 255.0
    mx = patch.max(axis=2)
    mn = patch.min(axis=2)
    delta = mx - mn

    # How much of the pixel is foil, feathered so the cut has no edge.
    r, g, b = patch[..., 0], patch[..., 1], patch[..., 2]
    weight = np.clip((np.minimum(r - g, b - g) * 255 - 4) / 14.0, 0, 1)

    sat = np.where(mx > 0, delta / np.maximum(mx, 1e-6), 0)
    val = mx
    sat = np.minimum(sat * chroma, ceiling)
    val = val * value

    i = np.floor(hue * 6.0) % 6
    f = hue * 6.0 - np.floor(hue * 6.0)
    p, q, t = val * (1 - sat), val * (1 - sat * f), val * (1 - sat * (1 - f))
    table = [(val, t, p), (q, val, p), (p, val, t),
             (p, q, val), (t, p, val), (val, p, q)]
    tinted = np.stack(table[int(i)], axis=-1) * 255.0

    img[y0:y1 + 1] = patch * 255.0 * (1 - weight[..., None]) + tinted * weight[..., None]


def silhouette(lum, backdrop):
    """Left and right edge of the bottle for every row, lightly smoothed."""
    h, w = lum.shape
    dark = lum < backdrop
    left = np.full(h, np.nan)
    right = np.full(h, np.nan)
    for y in range(h):
        xs = np.where(dark[y])[0]
        if xs.size > 40:
            left[y], right[y] = xs.min(), xs.max()
    ok = ~np.isnan(left)
    idx = np.arange(h)
    left = np.interp(idx, idx[ok], left[ok])
    right = np.interp(idx, idx[ok], right[ok])
    k = 15
    kern = np.ones(k) / k
    left = np.convolve(np.pad(left, (k, k), mode="edge"), kern, "same")[k:-k]
    right = np.convolve(np.pad(right, (k, k), mode="edge"), kern, "same")[k:-k]
    return left, right


def lighting_profile(img, left, right, clean_rows, samples=256):
    """Median colour across the bottle, over rows of glass with no artwork.

    Sampled in normalised width so the slow taper of the body does not smear
    the highlight, and taken as a median so a stray speck cannot bend it.
    """
    rows = []
    for y0, y1 in clean_rows:
        for y in range(y0, y1):
            L, R = left[y], right[y]
            if R - L < 60:
                continue
            xs = L + (R - L) * np.linspace(0, 1, samples)
            row = np.stack([np.interp(xs, np.arange(img.shape[1]), img[y, :, c])
                            for c in range(3)], axis=1)
            rows.append(row)
    return np.median(np.stack(rows), axis=0)       # (samples, 3)


def vertical_gain(lum, left, right, band, clean_rows):
    """How much the body dims from the top of the band to the bottom.

    Measured on clean glass either side of the band and interpolated across it,
    so the repainted stretch keeps the render's own falloff.
    """
    ys, vals = [], []
    for y0, y1 in clean_rows:
        for y in range(y0, y1, 4):
            L, R = int(left[y]), int(right[y])
            if R - L < 60:
                continue
            ys.append(y)
            vals.append(np.median(lum[y, L + 6:R - 5]))
    ys, vals = np.array(ys, float), np.array(vals, float)
    ref = np.median(vals)
    gain = vals / max(ref, 1e-6)
    all_y = np.arange(band[0] - 60, band[1] + 60)
    return all_y, np.interp(all_y, ys, gain), ref


def grain_field(img, left, right, rows, samples=256):
    """The render's surface texture, lifted off a stretch of clean glass.

    Sampled in normalised width like the lighting profile, then de-trended so
    only the high-frequency grain is left. Repainted glass without it reads as
    plastic next to the untouched parts of the bottle.
    """
    y0, y1 = rows
    patch = []
    for y in range(y0, y1):
        L, R = left[y], right[y]
        xs = L + (R - L) * np.linspace(0, 1, samples)
        patch.append(np.stack([np.interp(xs, np.arange(img.shape[1]), img[y, :, c])
                               for c in range(3)], axis=1))
    patch = np.stack(patch)
    blur = np.asarray(Image.fromarray(np.clip(patch, 0, 255).astype(np.uint8))
                      .filter(ImageFilter.GaussianBlur(2.2))).astype(float)
    return patch - blur


def tile_rows(field, n):
    """Repeat a patch to n rows, mirroring so the joins do not show."""
    h = field.shape[0]
    idx = np.arange(n) % (2 * h)
    idx = np.where(idx < h, idx, 2 * h - 1 - idx)
    return field[idx]


def sample(img, x, y):
    """Bilinear sample of an (H, W, C) image at float coordinates."""
    h, w = img.shape[:2]
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    return ((img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx) * (1 - fy) +
            (img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx) * fy)


def build(wine, spec):
    base = BASES[spec["base"]]
    img = np.asarray(Image.open(base["file"]).convert("RGB")).astype(float)
    h, w, _ = img.shape

    rows = capsule_rows(img)
    if rows:
        retint(img, rows, CAPSULE_HUE, CAPSULE_CHROMA, CAPSULE_CEILING, CAPSULE_VALUE)

    lum = img.mean(axis=2)

    backdrop = 150
    left, right = silhouette(lum, backdrop)

    y0, y1 = base["band"]
    prof = lighting_profile(img, left, right, base["clean_rows"])
    all_y, gain, ref = vertical_gain(lum, left, right, base["band"], base["clean_rows"])
    gain_at = lambda y: np.interp(y, all_y, gain)

    # ── Scale ─────────────────────────────────────────────────────────────
    label = np.asarray(Image.open(f"{LABEL_DIR}/front-{wine}.png").convert("RGB")).astype(float)
    coverage, pigment = brandmark.as_print(label)
    LH, LW = label.shape[:2]
    label_w_in, label_h_in = spec["label_in"]
    px_per_in = (y1 - y0) / label_h_in

    mid = (y0 + y1) // 2
    radius_px = (right[mid] - left[mid]) / 2
    radius_in = radius_px / px_per_in
    circumference_in = 2 * np.pi * radius_in
    # How far the artwork reaches around the glass, as a half-angle.
    theta_max = np.radians(min(label_w_in / circumference_in * 180.0, 78.0))

    grain = grain_field(img, left, right, base["clean_rows"][0])

    out = img.copy()
    ys = np.arange(y0 - FEATHER - 2, y1 + FEATHER + 3)
    Y, X = np.meshgrid(ys, np.arange(w), indexing="ij")

    L = left[ys][:, None]
    R = right[ys][:, None]
    C = (L + R) / 2
    A = (R - L) / 2

    s = (X - C) / np.maximum(A, 1e-6)
    on_bottle = np.abs(s) <= 1.0
    theta = np.arcsin(np.clip(s, -1, 1))

    # ── 1. Repaint the band with clean glass ─────────────────────────────
    u = (s + 1) / 2 * (prof.shape[0] - 1)
    glass = np.stack([np.interp(u.ravel(), np.arange(prof.shape[0]), prof[:, c]).reshape(u.shape)
                      for c in range(3)], axis=-1)
    glass *= gain_at(Y)[..., None]

    g = tile_rows(grain, len(ys))
    gi = np.clip(((s + 1) / 2 * (g.shape[1] - 1)), 0, g.shape[1] - 1)
    gi0 = np.floor(gi).astype(int)
    gf = (gi - gi0)[..., None]
    rows_i = np.arange(len(ys))[:, None]
    grain_px = (g[rows_i, gi0] * (1 - gf) +
                g[rows_i, np.minimum(gi0 + 1, g.shape[1] - 1)] * gf)
    glass += grain_px

    # Cross-fade into the untouched bottle at the top and bottom of the band,
    # so the repaint has no horizontal seam to give it away.
    d_top = (Y - ys[0]) / FEATHER
    d_bot = (ys[-1] - Y) / FEATHER
    blend = np.clip(np.minimum(d_top, d_bot), 0, 1) * on_bottle
    orig = out[ys[0]:ys[-1] + 1]
    out[ys[0]:ys[-1] + 1] = orig * (1 - blend[..., None]) + glass * blend[..., None]

    # ── 2. Print the artwork onto the glass ──────────────────────────────
    # The band lifts towards the sides, because the camera sits above the
    # artwork's centre and a line around a cylinder reads as an ellipse.
    bow = base["bow"] * (1 - np.cos(theta))
    v = (Y + bow - y0) / (y1 - y0)
    uu = (theta / theta_max + 1) / 2

    printed = on_bottle & (np.abs(theta) <= theta_max) & (v >= 0) & (v <= 1)
    xs_art, ys_art = uu * (LW - 1), np.clip(v, 0, 1) * (LH - 1)
    cover = sample(coverage[..., None], xs_art, ys_art)[..., 0] * printed
    # The artwork is three times the resolution it ends up at, so a hairline
    # arrives as a half-covered pixel and the glass drinks it. A press does
    # not thin a rule down for being fine; this gives the small type back the
    # weight the downsampling took, without touching anything already solid.
    cover = cover ** INK_GAIN
    ink = sample(pigment, xs_art, ys_art)

    # Ink laid on the bottle, not paper laid against it: the drawing's ground
    # carries no ink at all, so the glass goes on being glass between the
    # strokes and there is no panel to have an edge. What is left to light is
    # the ink itself, as a matte film on a cylinder under this studio's key.
    # Its floor is high, because a stroke of enamel keeps reading white deep
    # into the turn where the glass behind it has long gone black.
    key = np.clip(np.cos(theta - KEY_ANGLE), 0, 1) ** 1.1
    rim = np.clip(np.cos(theta - RIM_ANGLE), 0, 1) ** 6
    lit = (PRINT_FLOOR + (1 - PRINT_FLOOR) * key + 0.14 * rim) * gain_at(Y)

    band = out[ys[0]:ys[-1] + 1]
    band[:] = band * (1 - cover[..., None]) + ink * lit[..., None] * cover[..., None]

    # ── 3. Sell the print: the bottle's own gloss runs across it ─────────
    # This is the tell. A label stands off the glass and interrupts the
    # highlight; a fired-on print is under the same varnish as the rest of
    # the bottle, so the key light travels over the artwork without a seam.
    gloss = np.clip(glass.mean(axis=2) - np.median(glass.mean(axis=2)), 0, None)
    band[:] = np.minimum(band + GLOSS * gloss[..., None] * cover[..., None], 255)

    out[ys[0]:ys[-1] + 1] = band
    return np.clip(out, 0, 255).astype(np.uint8)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for wine, spec in WINES.items():
        arr = build(wine, spec)
        out = f"{OUT_DIR}/{wine}.png"
        Image.fromarray(arr).save(out, optimize=True)
        print(f"{wine:11s} {spec['base']:9s} -> {out}")


if __name__ == "__main__":
    main()
