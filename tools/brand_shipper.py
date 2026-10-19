#!/usr/bin/env python3
"""Make the shipper photograph the winery's own.

The supplied frame is a studio shot of a packed case — lid up, three bottles
standing in a moulded pulp tray — and every printed thing in it belongs to
another winery: the mark and name on the lid, the labels on the bottles, a
line of copy down the open flap, and the carrier label on the front. All of
it comes off and ours goes on. Nothing else in the frame is touched: the
carton, the pulp, the glass, the capsules, the barrels behind and the light
stay exactly as photographed.

Four jobs, in the order they are done:

  1. The kraft is cleared where it carries print. Corrugated board is striped
     vertically and a diffusion fill would smear those stripes into a bruise,
     so each column is rebuilt from the clean board directly above and below
     it — which is the same board, at the same brightness, with the same
     stripe.
  2. The lid takes the winery's lockup, the one on the bottles, as a single
     ink multiplied onto the board. Multiplied rather than painted, because
     ink printed on kraft does not hide the corrugation — it darkens it.
  3. The bottles are relabelled.
  4. The carrier label gets our address.

Each bottle is a cylinder, so a label on it is not a rectangle. The artwork is mapped through the cylinder the same way
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
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import brandmark

SOURCE = "images/shipper-source.jpg"
OUT = "images/shipment-box.webp"
DEBUG_OUT = "images/shipment-box-debug.png"
LABELS = "images/labels/front-{}.png"

# A 750ml bottle across the body. Each label's own trim is on its Bottle.
BOTTLE_DIAMETER_IN = 3.0

def wrap_of(label_w_in: float) -> float:
    """How far the paper reaches around the glass, from its own width against
    the bottle's circumference. Not a taste decision."""
    return math.radians(min(label_w_in / (math.pi * BOTTLE_DIAMETER_IN) * 180.0, 78.0))

# What the stock does to the light that falls on it. The ground of these
# labels is matte near-black and sits below the glass it is stuck to; the
# white line work is what lifts off it. Solved from those two ends — ground
# at three quarters of the glass, full ink at two and a half times — rather
# than dialled, because the artwork's own ground is not black but 0.14.
PAPER_FLOOR = 0.43
INK_GAIN = 2.26
# A film label is one ink straight on the glass, so what it does is multiply.
INK_ON_GLASS = 1.35
# How much of the glass's own specular streak a label carries over.
SPECULAR = 0.45
# And how much dimmer the bottom of a label is than its top, in this light.
FALLOFF = 0.30
# The light is read at the shoulder, which is the brightest part of a bottle
# in this frame. A label hangs on the body, which is not.
SHOULDER_BIAS = 0.80

# ── the carton ───────────────────────────────────────────────────────────────
# Boxes of board to clear, each (x0, y0, x1, y1): the other winery's lockup on
# the lid, and the line of copy down the open left flap.
KRAFT_CLEAR = [
    (470, 12, 870, 198),           # lid: mark, name and strapline
]
# The flap's line of copy runs diagonally and the flap itself is a
# parallelogram, so no rectangle both covers the type and stays on the board.
# There the ink is found instead of bounded, and each run of it is closed from
# the clean board either side on its own row — which works because that face
# is smooth where the lid is striped.
FLAP_REGION = (150, 196, 320, 520)
FLAP_INK = 0.80                    # darker than this much of the local board
CLEAN_BAND = 26                    # rows of good board read above and below

# Where our lockup goes on the lid. The lid is very nearly square to the
# camera — its edges run vertical and its fold runs level — so a rectangle
# carries it and a trapezoid would only invent a perspective that is not there.
LID_BOX = (675, 16, 182)           # centre x, top y, height; width follows the art
# What print does to kraft: it darkens the board rather than covering it, so
# the corrugation still reads through the ink. Measured off the frame's own
# print against the board beside it.
PRINT_DARKEN = 0.34

# ── the carrier label ────────────────────────────────────────────────────────
# The white label on the front, and the lines on it that name somebody else.
# Each line is (x, y, size, bold, text) in frame pixels; the y is the cap line.
CARRIER_CLEAR = [(368, 696, 525, 722), (368, 739, 525, 777)]
CARRIER_LINES = [
    # x, cap line, cap height, bold, text
    (371, 698, 7.5, False, "Warm Springs Cellar,"),
    (371, 710, 7.5, False, "Glen Ellen, California"),
    (379, 740, 8.0, True, "WINE CLUB MEMBER"),
    (379, 753, 7.5, False, "Adult signature required"),
    (379, 765, 7.5, False, "on delivery"),
]
CARRIER_PAPER = (516, 548)         # blank label between the type and the QR
CARRIER_FONTS = {
    False: "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    True: "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
}

# The top edge of the carton's front wall, which is what hides the bottom of
# every bottle. Anything painted below this line would be painted onto the
# box, so nothing is.
CARTON_EDGE = ((300, 648), (1040, 643))


@dataclass
class Bottle:
    """One bottle in the frame.

    The axis runs from `base` to `shoulder` in the photograph.

    Distances along the axis are in inches from the base, so the label keeps the
    proportions of the artwork rather than being stretched to fit.
    """
    wine: str
    base: tuple[float, float]        # centre of the bottle's base, in frame
    shoulder: tuple[float, float]    # centre of the axis where the body ends
    half_base: float                 # half the bottle's visible width there
    half_shoulder: float
    label_from_in: float             # where the new label's bottom edge sits
    label_w_in: float                # the trim of this wine's own label
    label_h_in: float
    light_from_in: float             # a band of glass with no print on it, which
    light_to_in: float               # is what the new label is lit by


BOTTLES = [
    # Measured off the photograph: the silhouette gives the width, which gives
    # the scale, because a 750ml bottle is 3 inches across. The base of each
    # one is behind the carton's front wall, so `base` is a point on the axis
    # at that wall and everything is measured up from there; what falls below
    # it is clipped rather than painted.
    #
    # The left bottle is a Burgundy and wears the Pinot's shorter label; the
    # right one is flint glass and wears the Sauvignon Blanc's film.
    #
    # The labels sit a touch higher than they would on a bottle standing on a
    # table, because the carton's front wall crosses these ones at the chest.
    # Hung where the glass alone would put them, the only part above the wall
    # is the gable — the winery's name and the wine's are both under the
    # cardboard, and a shipper photograph whose job is to show whose wine it
    # is would show neither.
    Bottle("pinot", base=(470, 648), shoulder=(466, 395),
           half_base=60.0, half_shoulder=58.0,
           label_from_in=0.67, label_w_in=3.75, label_h_in=4.50,
           light_from_in=5.4, light_to_in=6.2),
    Bottle("cabernet", base=(679, 645), shoulder=(673, 410),
           half_base=66.0, half_shoulder=64.0,
           label_from_in=-1.12, label_w_in=3.73, label_h_in=6.23,
           light_from_in=5.3, light_to_in=6.1),
    Bottle("sauvignon-blanc", base=(888, 646), shoulder=(880, 400),
           half_base=62.0, half_shoulder=60.0,
           label_from_in=-0.94, label_w_in=3.75, label_h_in=6.25,
           light_from_in=5.5, light_to_in=6.3),
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



def span_corners(b: Bottle, from_in: float, to_in: float):
    """Four corners of the band between two distances from the base.

    The bottle tapers towards the shoulder, so the half-width is interpolated
    rather than assumed constant, and the band is narrowed to what the paper
    actually covers — 95% of the silhouette, not all of it.
    """
    (bx, by), (ux, uy), (nx, ny), ppi, length = geometry(b)
    shrink = math.sin(wrap_of(b.label_w_in))

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


def light_profile(photo: np.ndarray, quad, gw: int, clear: bool = False,
                  samples: int = 64) -> np.ndarray:
    """The light across a band of glass that carries no print.

    The original tool read the light from under the label it was replacing,
    taking a low percentile so the old white type did not count. That works
    where the old label is ink on glass. It does not work here: the labels
    being replaced are large dark panels covering the whole band, so the
    percentile reads the panel and every new label comes out black. So the
    light is read where there is nothing but glass — a strip above the old
    label — and carried down, indexed across the bottle rather than in pixels,
    which is why it still fits a band of another width.
    """
    to_frame = homography([(0, 0), (1, 0), (1, 1), (0, 1)], quad)
    ss, vv = np.meshgrid(np.linspace(0, 1, gw), np.linspace(0, 1, samples))
    pts = np.stack([ss, vv, np.ones_like(ss)])
    frame = np.tensordot(to_frame, pts, axes=(1, 0))
    band = sample(photo, frame[0] / frame[2], frame[1] / frame[2])
    across = np.median(band, axis=0)
    across = np.stack([smooth(across[:, c], 7) for c in range(3)], axis=1)
    # Flattened towards its own mean. Glass mirrors the light; paper laid on
    # it does not, and carrying the specular streak over at full strength is
    # what turns a label into a chrome band.
    mean = across.mean(axis=0, keepdims=True)
    return (mean + (across - mean) * (1.0 if clear else SPECULAR)) * SHOULDER_BIAS


def paint(photo: np.ndarray, quad, wine: str | None, wrap: float = 0.0,
          light: np.ndarray | None = None, supersample: int = 2):
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
    if light is None:
        across = np.percentile(patch, 20, axis=0)
        across = np.stack([smooth(across[:, c], 9) for c in range(3)], axis=1)
        along = np.percentile(patch, 20, axis=1)
        along = np.stack([smooth(along[:, c], 25) for c in range(3)], axis=1)
        along = along / np.maximum(along.mean(axis=0, keepdims=True), 1e-6)
        illum = across[None, :, :] * along[:, None, :]
    else:
        idx = np.linspace(0, light.shape[0] - 1, gw)
        across = np.stack([np.interp(idx, np.arange(light.shape[0]), light[:, c])
                           for c in range(3)], axis=1)
        # The light was read at the shoulder and the label hangs below it, so
        # it carries the same falloff down the bottle the rest of the frame has.
        down = np.linspace(1.0, 1.0 - FALLOFF, gh)[:, None, None]
        illum = across[None, :, :] * down
    new = illum.copy()
    mask = np.ones((gh, gw))

    if wine is not None:
        # Resized before sampling: the artwork is 1119x1869 going into a patch a
        # tenth that size, and point-sampling it there is how fine type turns to
        # mush. Wider than the patch because the wrap compresses the edges.
        src = Image.open(LABELS.format(wine))
        film = src.mode == "RGBA"
        if film:
            src = src.getchannel("A").convert("L")
        else:
            src = src.convert("RGB")
        label = np.asarray(src.resize((gw * 2, gh), Image.LANCZOS)).astype(float)
        if label.ndim == 2:
            label = label[..., None]
        lh, lw = label.shape[:2]

        # A column at screen offset s sits at angle asin(s), so the artwork
        # compresses towards the silhouette exactly as paper on glass does.
        offset = np.clip(2 * ss - 1, -1, 1)
        theta = np.arcsin(np.clip(offset * math.sin(wrap), -1, 1))
        u = (theta / wrap + 1) / 2
        art = sample(label, u * (lw - 1), vv * (lh - 1)) / 255.0

        if film:
            # A clear-film label prints onto the glass: the artwork's ground
            # carries no ink, so between the strokes the bottle is still the
            # bottle. Only the coverage lands, in the brand's one colour.
            cover = art[..., :1]
            ink = np.array(brandmark.INK, float) / 255.0
            new = illum * (1 - cover) + illum * ink * INK_ON_GLASS * cover
        else:
            new = illum * (PAPER_FLOOR + INK_GAIN * art)

        # The cut edge of the stock catches the light the glass does not. A
        # film has no cut edge — it is printed, not applied — so it gets none.
        edge = 0 if film else max(int(gw * 0.025), 1)
        lip = np.zeros((gh, gw))
        if edge:
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



# ── the carton ───────────────────────────────────────────────────────────────

def clear_kraft(img: np.ndarray, box, band: int = CLEAN_BAND) -> None:
    """Take the print off a panel of board, in place.

    Corrugated board is striped vertically, and the stripe is the thing that
    makes it read as board. Diffusing the hole closed — which is what works on
    cloth, see tools/retouch_patron.py before it was retired — averages those
    stripes into a bruise. So each column is rebuilt from the same column: the
    clean board a little above and a little below, ramped between the two, so
    the stripe keeps its own brightness and only the ink is gone.
    """
    x0, y0, x1, y1 = box
    # Whichever side has room; near the top of the frame the lid has only a
    # few rows of clean board above its print.
    band = max(min(band, y0, img.shape[0] - y1), 3)
    above = img[y0 - band:y0, x0:x1].mean(axis=0)
    below = img[y1:y1 + band, x0:x1].mean(axis=0)
    ramp = np.linspace(0, 1, y1 - y0)[:, None, None]
    fill = above[None] * (1 - ramp) + below[None] * ramp

    # The board's own fine grain, borrowed from the clean rows so the repaint
    # is not glassy next to them.
    rough = img[y0 - band:y0, x0:x1] - above[None]
    rng = np.random.default_rng(x0 * 131 + y0)
    grain = rough[rng.integers(0, rough.shape[0], y1 - y0)]

    patch = fill + grain
    # Feathered at every edge; a panel with four straight sides is a panel.
    soft = np.ones((y1 - y0, x1 - x0))
    fy = max((y1 - y0) // 14, 3)
    fx = max((x1 - x0) // 14, 3)
    soft[:fy] *= np.linspace(0, 1, fy)[:, None]
    soft[-fy:] *= np.linspace(1, 0, fy)[:, None]
    soft[:, :fx] *= np.linspace(0, 1, fx)
    soft[:, -fx:] *= np.linspace(1, 0, fx)
    img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - soft[..., None]) + patch * soft[..., None]


def clear_ink(img: np.ndarray, region, threshold: float = FLAP_INK) -> int:
    """Close every run of print in a region from the board on either side.

    Finds the ink rather than being told where it is: anything a good bit
    darker than the local board is print, and each horizontal run of it is
    filled from the first clean pixel to its left and its right. Rows are the
    right direction here — this face of the carton is smooth, and the type
    runs diagonally across it, so a run is short and its ends are near.
    """
    x0, y0, x1, y1 = region
    patch = img[y0:y1, x0:x1]
    lum = patch.mean(axis=2)
    board = np.asarray(Image.fromarray(lum.astype(np.uint8), "L")
                       .filter(ImageFilter.GaussianBlur(14))).astype(float)
    ink = lum < board * threshold
    # Grown a little: the edge of a letter is half ink and would ring the fill.
    ink = np.asarray(Image.fromarray((ink * 255).astype(np.uint8), "L")
                     .filter(ImageFilter.MaxFilter(5))) > 127

    out = patch.copy()
    for row in range(ink.shape[0]):
        marked = ink[row]
        if not marked.any():
            continue
        edges = np.flatnonzero(np.diff(np.r_[0, marked.view(np.int8), 0]))
        for a, b in zip(edges[::2], edges[1::2]):
            left = patch[row, a - 1] if a > 0 else patch[row, b]
            right = patch[row, b] if b < ink.shape[1] else patch[row, a - 1]
            ramp = np.linspace(0, 1, b - a)[:, None]
            out[row, a:b] = left * (1 - ramp) + right * ramp

    # Softened back in, so the repaired runs do not sit crisper than the board.
    blur = np.asarray(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
                      .filter(ImageFilter.GaussianBlur(0.6))).astype(float)
    weight = np.asarray(Image.fromarray((ink * 255).astype(np.uint8), "L")
                        .filter(ImageFilter.GaussianBlur(1.6))).astype(float)[..., None] / 255.0
    img[y0:y1, x0:x1] = patch * (1 - weight) + blur * weight
    return int(ink.sum())


def lockup() -> np.ndarray:
    """The winery's lockup as ink coverage: the drawing above the wine's name.

    Taken from the same label the bottles wear, so the box and the bottle in
    it carry one mark and not two drawings of one.
    """
    art = np.asarray(Image.open(LABELS.format("cabernet")).convert("RGB")).astype(float)
    grey = art.mean(axis=2)
    ink = grey > (grey.min() + grey.max()) / 2
    _, _, _, bottom = brandmark.script_box(ink)
    cover, _ = brandmark.as_print(art[:bottom + 1])
    rows = np.where(cover.max(axis=1) > 0.08)[0]
    cols = np.where(cover.max(axis=0) > 0.08)[0]
    return cover[rows.min():rows.max() + 1, cols.min():cols.max() + 1]


def print_lid(img: np.ndarray) -> None:
    """Print the lockup on the lid, in place.

    Ink on kraft darkens the board; it does not cover it. So this multiplies
    rather than paints, and the corrugation goes on showing through the mark
    exactly as it does through the frame's own print.
    """
    cover = lockup()
    cx, top, height = LID_BOX
    width = int(round(height * cover.shape[1] / cover.shape[0]))
    small = np.asarray(Image.fromarray((cover * 255).astype(np.uint8), "L")
                       .resize((width, height), Image.LANCZOS)).astype(float) / 255.0
    x0, y0 = int(round(cx - width / 2)), top
    panel = img[y0:y0 + height, x0:x0 + width]
    img[y0:y0 + height, x0:x0 + width] = panel * (1 - small[..., None] * (1 - PRINT_DARKEN))


def write_carrier(img: np.ndarray) -> None:
    """Our own details on the carrier label, in place.

    The supplied one names another winery and an address that is not an
    address — the frame was generated, and generated type falls apart when
    read. Both blocks are wiped to the label's own white and set again. No
    street is invented: a mockup carrying a plausible address is a mockup
    somebody will one day post something to.
    """
    # Each row is filled from blank label beside it, not from the rows above
    # and below: those are more type, and reading them is how the first go
    # printed a ghost of "SHIPPED FROM" through the address. The strip
    # between the last column of type and the first QR code is paper and
    # nothing else, and it carries the label's own shading down the block.
    for x0, y0, x1, y1 in CARRIER_CLEAR:
        paper = np.median(img[y0:y1, CARRIER_PAPER[0]:CARRIER_PAPER[1]], axis=1)
        soft = np.ones((y1 - y0, x1 - x0))
        fx = max((x1 - x0) // 20, 2)
        soft[:, :fx] *= np.linspace(0, 1, fx)
        soft[:, -fx:] *= np.linspace(1, 0, fx)
        img[y0:y1, x0:x1] = (img[y0:y1, x0:x1] * (1 - soft[..., None])
                             + paper[:, None, :] * soft[..., None])

    sheet = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(sheet)
    for x, cap, height, bold, text in CARRIER_LINES:
        probe = ImageFont.truetype(CARRIER_FONTS[bold], 60)
        tall = probe.getbbox("H")[3] - probe.getbbox("H")[1]
        font = ImageFont.truetype(CARRIER_FONTS[bold], max(round(60 * height / tall), 5))
        # Anchored on the cap line, which is the line the printed label used.
        draw.text((x, cap - font.getbbox("H")[1]), text, font=font, fill=(32, 30, 30))
    img[:] = np.asarray(sheet).astype(float)


def below_carton(shape) -> np.ndarray:
    """A mask that is 1 above the carton's front wall and 0 below it."""
    (ax, ay), (bx, by) = CARTON_EDGE
    xs = np.arange(shape[1])
    edge = ay + (by - ay) * (xs - ax) / (bx - ax)
    ys = np.arange(shape[0])[:, None]
    return np.clip((edge[None, :] - ys) / 3.0 + 0.5, 0, 1)


def debug_overlay(photo: np.ndarray) -> Image.Image:
    img = Image.fromarray(photo.astype(np.uint8))
    d = ImageDraw.Draw(img)
    for box in KRAFT_CLEAR:
        d.rectangle(box, outline=(255, 90, 90), width=2)
    d.rectangle(FLAP_REGION, outline=(255, 160, 60), width=2)
    for box in CARRIER_CLEAR:
        d.rectangle(box, outline=(90, 200, 255), width=1)
    cx, top, height = LID_BOX
    cover = lockup()
    w = int(round(height * cover.shape[1] / cover.shape[0]))
    d.rectangle((cx - w // 2, top, cx + w // 2, top + height), outline=(90, 255, 120), width=2)
    d.line(CARTON_EDGE, fill=(255, 220, 0), width=2)
    for b in BOTTLES:
        d.polygon(span_corners(b, b.label_from_in, b.label_from_in + b.label_h_in),
                  outline=(0, 255, 255))
        d.polygon(span_corners(b, b.light_from_in, b.light_to_in), outline=(140, 255, 140))
        d.line([b.base, b.shoulder], fill=(255, 0, 0))
        d.text((b.base[0] - 20, b.base[1] + 6), b.wine, fill=(255, 255, 0))
    return img


def main() -> None:
    photo = np.asarray(Image.open(SOURCE).convert("RGB")).astype(float)

    if "--debug" in sys.argv:
        out = debug_overlay(photo)
        out.save(DEBUG_OUT)
        print(f"{out.width}x{out.height}  -> {DEBUG_OUT}")
        return

    # ── the carton first, so the bottles are relabelled against finished board
    board = photo.copy()
    for box in KRAFT_CLEAR:
        clear_kraft(board, box)
    wiped = clear_ink(board, FLAP_REGION)
    print(f"  flap: {wiped} pixels of print closed")
    print_lid(board)
    write_carrier(board)

    keep = below_carton(photo.shape)[..., None]

    def blend(target, source, quad, wine, wrap, blur, light=None):
        new, alpha = paint(source, quad, wine, wrap, light)
        a = np.asarray(Image.fromarray((alpha * 255).astype(np.uint8))
                       .filter(ImageFilter.GaussianBlur(blur))).astype(float)[..., None] / 255.0
        # Nothing is painted onto the carton standing in front of the bottles.
        a = a * keep
        return target * (1 - a) + np.clip(new, 0, 255) * a

    # No wipe pass. The other winery's labels are short ones sitting high, and
    # every one of them falls inside the footprint of the label going over it,
    # so there is nothing to erase that is not about to be covered — and a
    # wipe that reaches past its label has to invent glass, which is how the
    # band of nothing appeared under the Pinot the first time round.
    result = board.copy()
    for bottle in BOTTLES:
        quad = span_corners(bottle, bottle.label_from_in,
                            bottle.label_from_in + bottle.label_h_in)
        glass = span_corners(bottle, bottle.light_from_in, bottle.light_to_in)
        film = Image.open(LABELS.format(bottle.wine)).mode == "RGBA"
        across = light_profile(board, glass,
                               max(int(math.dist(quad[0], quad[1]) * 2), 8), clear=film)
        result = blend(result, board, quad, bottle.wine,
                       wrap_of(bottle.label_w_in), 0.5, across)
        print(f"  {bottle.wine:16s} relabelled")

    out = Image.fromarray(np.clip(result, 0, 255).astype(np.uint8))
    out.save(OUT, "WEBP", quality=88, method=6)
    print(f"{OUT}  {out.size[0]}x{out.size[1]}")


if __name__ == "__main__":
    main()
