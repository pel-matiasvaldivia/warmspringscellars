#!/usr/bin/env python3
"""Cut the bottle renders out of their studio backdrop onto transparency.

The supplied renders (images/WarmSpringsCellars_*.png) sit on a light grey
sweep with a cast shadow and a floor reflection. The page places the bottles
directly on its own warm background, so all of that has to go — and a plain
"delete light pixels" pass is not enough, because the reflection of a black
bottle is dark and the studio floor is mid-grey.

Three passes, in order:

  1. Flood the light neutral backdrop inwards from the frame. This gets the
     silhouette right without eating the bright specular highlights inside the
     glass, which are not reachable from the border.
  2. Clip each row to that row's own glass extent, so the cast shadow smearing
     sideways at the base cannot survive.
  3. Find the bottom of the glass — the lowest row that is both wide enough to
     be the bottle and mostly dark — and drop everything under it. That is what
     separates the bottle from its reflection and from the lit contact line.

Run from the repo root:  python3 tools/cutout_bottles.py
Requires Pillow and numpy. Rerun whenever the source renders change.
"""

from collections import deque

import numpy as np
from PIL import Image, ImageFilter

# Output height in pixels. Both bottles are normalised to this so they share a
# baseline in the layout; their real heights differ by only a few millimetres.
OUTPUT_HEIGHT = 980

SOURCES = [
    ("images/WarmSpringsCellars_Cabernet.png", "images/cabernet-bottle.png"),
    ("images/WarmSpringsCellars_Pinot.png", "images/pinot-bottle.png"),
]


def flood_backdrop(a):
    """Mask of the studio backdrop, flooded inwards from the image border."""
    h, w, _ = a.shape
    mx, mn = a.max(axis=2), a.min(axis=2)
    light_neutral = (mx > 196) & ((mx - mn) < 20)

    seen = np.zeros((h, w), bool)
    queue = deque()

    def push(y, x):
        if light_neutral[y, x] and not seen[y, x]:
            seen[y, x] = True
            queue.append((y, x))

    for x in range(w):
        push(0, x)
        push(h - 1, x)
    for y in range(h):
        push(y, 0)
        push(y, w - 1)

    while queue:
        y, x = queue.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w:
                push(ny, nx)

    return seen


def cutout(src, dst):
    im = Image.open(src).convert("RGB")
    a = np.asarray(im).astype(np.int16)
    h, w, _ = a.shape
    mx, mn = a.max(axis=2), a.min(axis=2)
    lum = a.mean(axis=2)

    keep = ~flood_backdrop(a)

    # The glass: dark, or strongly coloured (the purple capsule). The floor and
    # the shadow are mid-grey and match neither test.
    glass = (mx < 165) | ((mx - mn) > 40)

    # Widest point of the bottle, measured clear of the capsule and the base.
    waist = glass[int(h * 0.35):int(h * 0.72)]
    body = np.where(waist.sum(axis=0) > waist.shape[0] * 0.5)[0]
    body_width = body.max() - body.min()

    # Scan up for the bottom of the glass. The reflection below it is narrower,
    # and the contact line where the bottle meets the floor is lit, so require
    # a row to be both wide and mostly dark before accepting it as the base.
    base = h - 1
    for y in range(h - 1, int(h * 0.5), -1):
        xs = np.where(glass[y])[0]
        if xs.size == 0:
            continue
        lo, hi = xs.min(), xs.max()
        dark = (lum[y, lo:hi + 1] < 130).mean()
        if (hi - lo) > body_width * 0.78 and dark >= 0.75:
            base = y
            break

    silhouette = np.zeros((h, w), bool)
    for y in range(base + 1):
        xs = np.where(glass[y])[0]
        if xs.size == 0:
            continue
        lo, hi = xs.min(), xs.max()
        if hi - lo < w * 0.06:      # a stray speck, not the bottle
            continue
        silhouette[y, lo:hi + 1] = True
    keep &= silhouette

    alpha = Image.fromarray(np.where(keep, 255, 0).astype(np.uint8))
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.7))   # soften the cut edge

    out = im.convert("RGBA")
    out.putalpha(alpha)
    out = out.crop(out.getbbox())
    out = out.resize(
        (round(out.width * OUTPUT_HEIGHT / out.height), OUTPUT_HEIGHT),
        Image.LANCZOS,
    )
    out.save(dst, optimize=True)
    print(f"{dst}  {out.size[0]}x{out.size[1]}  base row {base}/{h}")


if __name__ == "__main__":
    for src, dst in SOURCES:
        cutout(src, dst)
