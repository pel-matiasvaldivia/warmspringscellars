#!/usr/bin/env python3
"""Prepare the photograph of Richard, Robert and Cecilia for the page.

The only picture of the three of them together is a phone shot taken in the
dark at the end of an evening: underexposed, noisy, and lit by something
tungsten enough to turn everything orange. It also has another winery's name
embroidered on two of the jackets, which is why it sat out of the page for so
long — the alternative was cropping above the chest, and that loses the arm
around the shoulder, which is the reason the photograph is worth having.

So the embroidery comes off instead. Both marks sit on flat, near-black
fleece, so what replaces them is not a guess: the hole is filled by letting
the fabric around it diffuse inwards until it closes, which on a surface with
no detail is what was behind the thread anyway, and the sensor's own grain is
measured next to the patch and put back on top so the fill does not read as a
smooth patch in a noisy frame.

Then the frame itself:

  * white balance, taken off Robert's white shirt and applied at three
    quarters' strength — a night shot corrected the whole way stops looking
    like night. It divides rather than multiplies, so nothing that was not
    already clipped becomes clipped.
  * exposure, as a curve that lifts the people and leaves the sky alone.
    A plain gamma lifts the darkest part of the frame hardest, which here is
    four square feet of grainy black sky; this one fades out below the level
    the subjects occupy.
  * noise. Colour noise is the ugly half and it is also the half that can be
    blurred without losing anything, so chroma is smoothed hard and luma
    barely — enough to take the speckle out of the shadows, not enough to
    cost the frame its detail. It was costing it: at a quarter of the frame's
    width the hand on Richard's shoulder had lost its knuckles.
  * a 4:5 crop with the three faces in the upper third, which is the shape
    and the placement the page's patron frames expect.

Run from the repo root:  python3 tools/retouch_patron.py
Requires Pillow and numpy. Reads images/richard-deane-source.jpg and writes
images/richard-deane.jpg; pass --boxes to write a copy with the inpaint
rectangles drawn on it, for checking them after a different crop or a
different photograph.
"""

from __future__ import annotations

import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SRC = "images/richard-deane-source.jpg"
OUT = "images/richard-deane.jpg"
BOXES_OUT = "images/richard-deane-boxes.png"

# The two pieces of embroidery, in source pixels, each with a little margin
# so the diffusion starts on clean fabric rather than on a stitched edge.
LOGOS = [
    (512, 870, 611, 931),      # Robert's chest
    (799, 1082, 876, 1138),    # Richard's jacket
]

# Robert's shirt, which is the one white thing in the frame.
NEUTRAL = (455, 860, 495, 890)
WHITE_BALANCE = 0.65           # how much of the measured cast to take out

# The curve. GAMMA lifts; the window is where it fades in, so that the sky —
# which is darker than anything on a person — keeps its own level.
GAMMA = 0.58
LIFT_FROM, LIFT_TO = 0.014, 0.055

CHROMA_BLUR = 3.2
LUMA_BLUR = 0.7
LUMA_KEEP = 0.88               # how much of the original luma survives it

# The right edge is where it is for Robert's hand on Richard's shoulder: a
# tighter frame cut the little finger and the wedding ring off at the fingers,
# and a hand that ends at the frame edge reads as a smudge rather than a hand.
CROP = (112, 505, 960, 1565)    # 848 x 1060, which is 4:5
SHARPEN = dict(radius=1.2, percent=70, threshold=3)
QUALITY = 88


FEATHER = 3                    # pixels the fill fades into the fabric over


def diffuse(img: np.ndarray, box: tuple[int, int, int, int], rounds: int = 700) -> None:
    """Close a rectangle by letting its edges seep inwards, in place.

    Jacobi relaxation: every interior pixel becomes the average of its four
    neighbours, over and over, with the ring around the hole held fixed. What
    it converges on is the smoothest surface that meets those edges, which is
    the right answer for fabric with nothing printed on it and the wrong one
    for anything with structure — hence two small rectangles and not a mask
    drawn around a face.

    Two things keep the result from reading as a patch. The grain is measured
    on the fabric immediately outside the hole and put back at that strength,
    so the fill is as noisy as its surroundings and no noisier; and the whole
    thing is blended in over a few pixels, because a rectangle of anything,
    however well matched, has four straight edges and the eye finds them.

    This runs after the tone curve, not before: grain measured on a frame
    that is about to be lifted two stops comes back two stops too strong.
    """
    x0, y0, x1, y1 = box
    pad = FEATHER + 3
    patch = img[y0 - pad:y1 + pad, x0 - pad:x1 + pad].astype(np.float64)
    before = patch.copy()

    hole = np.zeros(patch.shape[:2], bool)
    hole[pad:-pad, pad:-pad] = True

    # Grain is what is left of the fabric once its folds are taken out, not
    # how much the fabric varies: a patch sitting across a shadow has a wide
    # spread and almost no noise, and matching the spread would fill it with
    # confetti.
    smooth = np.asarray(Image.fromarray(np.clip(patch, 0, 255).astype(np.uint8))
                        .filter(ImageFilter.GaussianBlur(2.0))).astype(np.float64)
    grain = float((patch - smooth)[~hole].std())

    work = patch.copy()
    for _ in range(rounds):
        nb = np.zeros_like(work)
        nb[1:-1, 1:-1] = (work[:-2, 1:-1] + work[2:, 1:-1] +
                          work[1:-1, :-2] + work[1:-1, 2:]) / 4.0
        work[hole] = nb[hole]

    # One value for all three channels. Independent noise per channel is
    # colour noise, and the denoise pass has just taken the colour noise out
    # of everything around it.
    rng = np.random.default_rng(x0 * 7919 + y0)
    speck = rng.normal(0.0, grain, size=(hole.sum(), 1))
    work[hole] += speck

    soft = np.asarray(Image.fromarray((hole * 255).astype(np.uint8), "L")
                      .filter(ImageFilter.GaussianBlur(FEATHER)))[..., None] / 255.0
    img[y0 - pad:y1 + pad, x0 - pad:x1 + pad] = before * (1 - soft) + work * soft


def balanced(img: np.ndarray) -> np.ndarray:
    """The cast taken off, by dividing the channels rather than lifting them."""
    x0, y0, x1, y1 = NEUTRAL
    white = img[y0:y1, x0:x1].reshape(-1, 3).mean(axis=0)
    gain = white.max() / np.maximum(white, 1e-6)
    gain = gain / gain.max()                      # the brightest channel is left alone
    gain = 1.0 + (gain - 1.0) * WHITE_BALANCE
    return img * gain


def exposed(img: np.ndarray) -> np.ndarray:
    """Lifted where there are people, left alone where there is only sky."""
    t = img / 255.0
    lum = t.mean(axis=2, keepdims=True)
    lifted = t ** GAMMA
    w = np.clip((lum - LIFT_FROM) / (LIFT_TO - LIFT_FROM), 0, 1)
    w = w * w * (3 - 2 * w)                       # smoothstep, so there is no edge
    return (t * (1 - w) + lifted * w) * 255.0


def denoised(img: np.ndarray) -> np.ndarray:
    """Chroma smoothed hard, luma barely: the speckle goes, the skin stays."""
    ycbcr = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).convert("YCbCr")
    y, cb, cr = ycbcr.split()
    cb = cb.filter(ImageFilter.GaussianBlur(CHROMA_BLUR))
    cr = cr.filter(ImageFilter.GaussianBlur(CHROMA_BLUR))
    soft = y.filter(ImageFilter.GaussianBlur(LUMA_BLUR))
    y = Image.blend(soft, y, LUMA_KEEP)
    return np.asarray(Image.merge("YCbCr", (y, cb, cr)).convert("RGB")).astype(float)


def main() -> None:
    src = Image.open(SRC).convert("RGB")
    img = np.asarray(src).astype(float).copy()

    if "--boxes" in sys.argv:
        marked = src.copy()
        draw = ImageDraw.Draw(marked)
        for box in LOGOS:
            draw.rectangle(box, outline=(255, 64, 64), width=3)
        draw.rectangle(NEUTRAL, outline=(64, 255, 64), width=3)
        draw.rectangle(CROP, outline=(64, 160, 255), width=4)
        marked.save(BOXES_OUT)
        print(f"{marked.width}x{marked.height}  -> {BOXES_OUT}")
        return

    img = denoised(exposed(balanced(img)))
    for box in LOGOS:
        diffuse(img, box)

    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).crop(CROP)
    out = out.filter(ImageFilter.UnsharpMask(**SHARPEN))
    out.save(OUT, quality=QUALITY, subsampling=0, optimize=True, progressive=True)
    print(f"{out.width}x{out.height}  -> {OUT}")


if __name__ == "__main__":
    main()
