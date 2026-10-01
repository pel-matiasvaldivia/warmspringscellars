#!/usr/bin/env python3
"""Put the Warm Springs labels on the bottles in the shipment photograph.

The winery supplied a real photograph of a packed shipper — a cardboard case
with a moulded pulp insert and three bottles lying in it — but the bottles in it
are somebody else's. This replaces each label without touching anything else in
the frame: the box, the pulp, the glass, the capsules and the light all stay
exactly as photographed.

Each bottle is a cylinder lying on its side, so a label on it is not a
rectangle. The artwork is mapped through the cylinder the same way
tools/label_bottles.py does it — a column at screen offset s sits at angle
asin(s), so the artwork compresses towards the silhouette — and then through a
homography onto the four corners the bottle occupies in frame, which carries the
camera's perspective.

Lighting is not invented. For each bottle the glass's own brightness is measured
under where the label will go, taking a low percentile along the bottle's length
so the existing white print is excluded and only the glass is left. That profile
— the specular streak, the fall-off towards the edges — is what the new label is
lit by, which is why it sits in the same light as the capsule above it.

Run from the repo root:  python3 tools/relabel_box_photo.py [--debug]
Requires Pillow and numpy.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SOURCE = "images/shipment-box-source.jpg"
OUT = "images/shipment-box.webp"
LABELS = "images/labels/front-{}.png"

# A 750ml Bordeaux bottle, and the trim of the labels in labels/*.ai.
BOTTLE_DIAMETER_IN = 3.0
LABEL_W_IN = 3.73
LABEL_H_IN = 6.23

# How far the paper reaches around the glass, from its own width against the
# bottle's circumference. Not a taste decision.
WRAP = math.radians(min(LABEL_W_IN / (math.pi * BOTTLE_DIAMETER_IN) * 180.0, 78.0))

# Matte black stock on dark glass reads a touch darker than the glass itself;
# the white ink is what lifts off it.
PAPER_FLOOR = 0.80
INK_GAIN = 3.9


@dataclass
class Bottle:
    """One bottle in the frame.

    The axis runs from `base` to `shoulder` in the photograph. `clean` is how far
    along it the existing print has to be wiped — further than the new label
    reaches, because the other winery printed onto the shoulder and a competitor's
    mark peeking past ours is the one outcome that is not acceptable.

    Distances along the axis are in inches from the base, so the label keeps the
    proportions of the artwork rather than being stretched to fit.
    """
    wine: str
    base: tuple[float, float]        # centre of the bottle's base, in frame
    shoulder: tuple[float, float]    # centre of the axis where the body ends
    half_base: float                 # half the bottle's visible width there
    half_shoulder: float
    clean_from_in: float             # wipe the glass between these two, measured
    clean_to_in: float               # in inches from the base
    label_from_in: float             # where the new label's bottom edge sits


BOTTLES = [
    # Measured off the photograph: the silhouette gives the width, which gives
    # the scale, because a 750ml Bordeaux is 3 inches across.
    Bottle("cabernet", base=(670, 224.0), shoulder=(470, 219.0),
           half_base=42.0, half_shoulder=43.0,
           clean_from_in=0.0, clean_to_in=7.5, label_from_in=0.40),
    Bottle("zinfandel", base=(204, 309.0), shoulder=(414, 304.0),
           half_base=43.0, half_shoulder=40.5,
           clean_from_in=0.0, clean_to_in=7.6, label_from_in=0.40),
    Bottle("sangiovese", base=(660, 419.5), shoulder=(450, 412.5),
           half_base=42.5, half_shoulder=44.0,
           clean_from_in=0.0, clean_to_in=7.6, label_from_in=0.40),
]

GRID = 520          # resolution of the unwrapped patch, along the label's height


def geometry(b: Bottle):
    """Scale, axis direction and normal for one bottle."""
    bx, by = b.base
    sx, sy = b.shoulder
    dx, dy = sx - bx, sy - by
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length           # along the axis, base -> shoulder
    nx, ny = uy, -ux                            # across it, label-left side
    px_per_in = (b.half_base + b.half_shoulder) / BOTTLE_DIAMETER_IN
    return (bx, by), (ux, uy), (nx, ny), px_per_in, length


WIPE_GROW = 1.06


def span_corners(b: Bottle, from_in: float, to_in: float, visible: bool = True):
    """Four corners of the band between two distances from the base.

    The bottle tapers towards the shoulder, so the half-width is interpolated
    rather than assumed constant. `visible` narrows the band to what the paper
    actually covers, which is 95% of the silhouette and not all of it.
    """
    (bx, by), (ux, uy), (nx, ny), ppi, length = geometry(b)
    shrink = math.sin(WRAP) if visible else WIPE_GROW

    def at(d_in: float):
        d = d_in * ppi
        t = min(max(d / length, 0.0), 1.4)
        half = (b.half_base + (b.half_shoulder - b.half_base) * t) * shrink
        return (bx + ux * d, by + uy * d), half

    (tx, ty), half_top = at(to_in)
    (ex, ey), half_bot = at(from_in)
    return [
        (tx + nx * half_top, ty + ny * half_top),
        (tx - nx * half_top, ty - ny * half_top),
        (ex - nx * half_bot, ey - ny * half_bot),
        (ex + nx * half_bot, ey + ny * half_bot),
    ]


def homography(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> np.ndarray:
    a, b = [], []
    for (sx, sy), (dx, dy) in zip(src, dst):
        a.append([sx, sy, 1, 0, 0, 0, -dx * sx, -dx * sy]); b.append(dx)
        a.append([0, 0, 0, sx, sy, 1, -dy * sx, -dy * sy]); b.append(dy)
    h = np.linalg.solve(np.array(a, float), np.array(b, float))
    return np.append(h, 1).reshape(3, 3)


def sample(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    return ((img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx) * (1 - fy) +
            (img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx) * fy)


def smooth(v: np.ndarray, k: int) -> np.ndarray:
    kern = np.ones(k) / k
    return np.convolve(np.pad(v, (k, k), mode="edge"), kern, "same")[k:-k]


def paint(photo: np.ndarray, quad, wine: str | None, supersample: int = 2):
    """Repaint one band of a bottle.

    With a wine, the band gets that label wrapped round the cylinder. Without
    one, it gets nothing but the light that was already there — which is how the
    other winery's print is wiped without inventing any glass.
    """
    xs = [p[0] for p in quad]; ys = [p[1] for p in quad]
    along = math.dist(quad[0], quad[3])
    across = math.dist(quad[0], quad[1])
    gh = max(int(round(along * supersample)), 16)
    gw = max(int(round(across * supersample)), 8)
    to_frame = homography([(0, 0), (1, 0), (1, 1), (0, 1)], quad)

    ss, vv = np.meshgrid(np.linspace(0, 1, gw), np.linspace(0, 1, gh))
    pts = np.stack([ss, vv, np.ones_like(ss)])
    frame = np.tensordot(to_frame, pts, axes=(1, 0))
    fx, fy = frame[0] / frame[2], frame[1] / frame[2]

    # ── the light already on the glass ──
    # A low percentile along the bottle: the existing white print is a minority
    # of any column, so this reads the glass beneath it rather than the ink.
    patch = sample(photo, fx, fy)
    across = np.percentile(patch, 20, axis=0)
    across = np.stack([smooth(across[:, c], 9) for c in range(3)], axis=1)

    along = np.percentile(patch, 20, axis=1)
    along = np.stack([smooth(along[:, c], 25) for c in range(3)], axis=1)
    along = along / np.maximum(along.mean(axis=0, keepdims=True), 1e-6)

    illum = across[None, :, :] * along[:, None, :]
    new = illum.copy()
    mask = np.ones((gh, gw))

    if wine is not None:
        # Resized before sampling: the artwork is 1119x1869 going into a patch a
        # tenth that size, and point-sampling it there is how fine type turns to
        # mush. Wider than the patch because the wrap compresses the edges.
        src = Image.open(LABELS.format(wine)).convert("RGB")
        label = np.asarray(src.resize((gw * 2, gh), Image.LANCZOS)).astype(float)
        lh, lw = label.shape[:2]

        # A column at screen offset s sits at angle asin(s), so the artwork
        # compresses towards the silhouette exactly as paper on glass does.
        offset = np.clip(2 * ss - 1, -1, 1)
        theta = np.arcsin(np.clip(offset * math.sin(WRAP), -1, 1))
        u = (theta / WRAP + 1) / 2
        art = sample(label, u * (lw - 1), vv * (lh - 1)) / 255.0

        new = illum * (PAPER_FLOOR + INK_GAIN * art)

        # The cut edge of the stock catches the light the glass does not.
        edge = max(int(gw * 0.025), 1)
        lip = np.zeros((gh, gw))
        lip[:, :edge] = 1.0
        lip[:, -edge:] = 1.0
        hem = max(int(gh * 0.006), 1)
        lip[:hem, :] = 1.0
        lip[-hem:, :] = 1.0
        new = new + lip[..., None] * illum * 0.30

        # Feather only where the paper meets the glass along the sides; the top
        # and bottom edges of a label are cut, not faded.
        soft = max(int(gw * 0.012), 2)
        mask[:, :soft] *= np.linspace(0, 1, soft)
        mask[:, -soft:] *= np.linspace(1, 0, soft)
    else:
        # A wipe has to disappear into what surrounds it, so it feathers all round.
        fx_ = max(int(gw * 0.035), 2)
        fy_ = max(int(gh * 0.03), 2)
        mask[:, :fx_] *= np.linspace(0, 1, fx_)
        mask[:, -fx_:] *= np.linspace(1, 0, fx_)
        mask[:fy_] *= np.linspace(0, 1, fy_)[:, None]
        mask[-fy_:] *= np.linspace(1, 0, fy_)[:, None]

    # ── back into the frame ──
    to_unit = np.linalg.inv(to_frame)
    x0, x1 = int(math.floor(min(xs))) - 2, int(math.ceil(max(xs))) + 2
    y0, y1 = int(math.floor(min(ys))) - 2, int(math.ceil(max(ys))) + 2
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, photo.shape[1]), min(y1, photo.shape[0])

    yy, xx = np.mgrid[y0:y1, x0:x1]
    p = np.stack([xx + 0.5, yy + 0.5, np.ones_like(xx, dtype=float)])
    q = np.tensordot(to_unit, p, axes=(1, 0))
    s_of, v_of = q[0] / q[2], q[1] / q[2]
    inside = (s_of >= 0) & (s_of <= 1) & (v_of >= 0) & (v_of <= 1) & (q[2] > 0)

    colour = sample(new, np.clip(s_of, 0, 1) * (gw - 1), np.clip(v_of, 0, 1) * (gh - 1))
    weight = sample(mask[..., None], np.clip(s_of, 0, 1) * (gw - 1),
                    np.clip(v_of, 0, 1) * (gh - 1))[..., 0]

    out = np.zeros_like(photo)
    alpha = np.zeros(photo.shape[:2])
    out[y0:y1, x0:x1] = np.where(inside[..., None], colour, 0)
    alpha[y0:y1, x0:x1] = np.where(inside, weight, 0)
    return out, alpha


def debug_overlay(photo: np.ndarray) -> Image.Image:
    img = Image.fromarray(photo.astype(np.uint8))
    d = ImageDraw.Draw(img)
    for b in BOTTLES:
        d.polygon(span_corners(b, b.clean_from_in, b.clean_to_in, visible=False),
                  outline=(255, 140, 0))
        d.polygon(span_corners(b, b.label_from_in, b.label_from_in + LABEL_H_IN),
                  outline=(0, 255, 255))
        d.line([b.base, b.shoulder], fill=(255, 0, 0))
        d.text((b.base[0] - 20, b.base[1] - 48), b.wine, fill=(255, 255, 0))
    return img


def main() -> None:
    photo = np.asarray(Image.open(SOURCE).convert("RGB")).astype(float)

    if "--debug" in sys.argv:
        debug_overlay(photo).resize((photo.shape[1] * 2, photo.shape[0] * 2),
                                    Image.LANCZOS).save("/tmp/relabel-debug.png")
        print("wrote /tmp/relabel-debug.png")
        return

    result = photo.copy()

    def blend(target, quad, wine, blur):
        new, alpha = paint(photo, quad, wine)
        a = np.asarray(Image.fromarray((alpha * 255).astype(np.uint8))
                       .filter(ImageFilter.GaussianBlur(blur))).astype(float)[..., None] / 255.0
        return target * (1 - a) + np.clip(new, 0, 255) * a

    # Wipe every bottle first, then label. Doing it in two passes means a wipe
    # can overlap its neighbour's label area without eating it.
    for bottle in BOTTLES:
        result = blend(result, span_corners(bottle, bottle.clean_from_in,
                                            bottle.clean_to_in, visible=False), None, 1.4)
    for bottle in BOTTLES:
        quad = span_corners(bottle, bottle.label_from_in, bottle.label_from_in + LABEL_H_IN)
        result = blend(result, quad, bottle.wine, 0.5)
        print(f"  {bottle.wine:11s} wiped and relabelled")

    out = Image.fromarray(np.clip(result, 0, 255).astype(np.uint8))
    out.save(OUT, "WEBP", quality=88, method=6)
    print(f"{OUT}  {out.size[0]}x{out.size[1]}")


if __name__ == "__main__":
    main()
