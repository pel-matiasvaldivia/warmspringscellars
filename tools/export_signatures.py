#!/usr/bin/env python3
"""Lift the two winemakers' signatures out of the label, onto transparency.

They are already printed on every bottle, so these are the real signatures and
not a font pretending to be one. The two are stored quite differently inside
the artwork, which decides how each is taken:

  Robert Rex       drawn as vector paths, so it comes out at any resolution.
  Cecilia Valdivia placed as a 150×122 bitmap with a soft mask. The mask is
                   the signature; the image under it is two flat near-whites.
                   That bitmap is all the detail that exists, so her file is
                   exported at the largest size that is still honest about it.

Both are cut from the artwork rather than from a render of the page, because
the typeset names sit across both signatures on the label and a rectangular
crop would drag them in.

Run from the repo root:  python3 tools/export_signatures.py
Requires PyMuPDF, Pillow and numpy. Needs no browser: the vector is rasterised
by PyMuPDF itself.
"""

import io

import numpy as np
import pymupdf
from PIL import Image

SRC = "labels/21CS_WSC_final_label.ai"

# Where each signature lives in the label, in PDF points.
ROBERT_BOX = pymupdf.Rect(329.0, 247.0, 398.5, 275.5)
# Cecilia's is the one embedded image on the page that carries a soft mask.
CECILIA_IMAGE_RECT = pymupdf.Rect(437.3, 243.3, 473.3, 272.6)

INK = (114, 47, 58)          # the brand's wine
ROBERT_WIDTH = 900           # vector: chosen, not limited
CECILIA_SCALE = 3            # 150px of real detail, no more
PAD = 0.04                   # air around the strokes, as a fraction of the long side

OUT_ROBERT = "images/signature-robert-rex.png"
OUT_CECILIA = "images/signature-cecilia-valdivia.png"


def open_colour_comp():
    doc = pymupdf.open(SRC)
    ocgs = doc.get_ocgs()
    comp = [x for x, info in ocgs.items() if info["name"] == "Color comp"]
    if not comp:
        raise SystemExit(f"{SRC}: no 'Color comp' layer")
    doc.set_layer(-1, on=comp, off=[x for x in ocgs if x not in comp])
    return doc


def robert_coverage(doc) -> np.ndarray:
    """Robert's signature as ink coverage, rasterised from its own paths.

    The paths are copied onto a blank page of their own. Rendering the label
    instead would bring the typeset "Robert Rex" with them: it is printed
    across the signature's tail, and no rectangle separates the two.
    """
    page = doc[0]
    strokes = [d for d in page.get_drawings()
               if ROBERT_BOX.contains(d["rect"]) and not d["rect"].is_empty]
    if not strokes:
        raise SystemExit("no signature paths found in Robert's box")

    out = pymupdf.open()
    sheet = out.new_page(width=ROBERT_BOX.width, height=ROBERT_BOX.height)
    shift = pymupdf.Point(-ROBERT_BOX.x0, -ROBERT_BOX.y0)
    shape = sheet.new_shape()
    for drawing in strokes:
        for item in drawing["items"]:
            if item[0] == "l":
                shape.draw_line(item[1] + shift, item[2] + shift)
            elif item[0] == "c":
                shape.draw_bezier(item[1] + shift, item[2] + shift,
                                  item[3] + shift, item[4] + shift)
            elif item[0] == "re":
                shape.draw_rect(item[1] + pymupdf.Rect(shift, shift))
            elif item[0] == "qu":
                shape.draw_quad(item[1] + pymupdf.Rect(shift, shift))
        shape.finish(fill=(0, 0, 0), color=None, closePath=True)
    shape.commit()

    zoom = ROBERT_WIDTH / ROBERT_BOX.width
    pix = sheet.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    grey = np.asarray(Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                      .convert("L")).astype(np.float32)
    # Drawn black on white: ink is where the page went dark.
    return np.clip((255.0 - grey) / 255.0, 0, 1)


def cecilia_coverage(doc) -> np.ndarray:
    """Cecilia's signature, taken from the soft mask of her placed bitmap."""
    page = doc[0]
    for xref, *_ in page.get_images(full=True):
        rects = page.get_image_rects(xref)
        if not any(r.intersects(CECILIA_IMAGE_RECT) for r in rects):
            continue
        info = doc.extract_image(xref)
        if not info.get("smask"):
            continue
        mask = Image.open(io.BytesIO(doc.extract_image(info["smask"])["image"])).convert("L")
        big = mask.resize((mask.width * CECILIA_SCALE, mask.height * CECILIA_SCALE),
                          Image.LANCZOS)
        return np.asarray(big).astype(np.float32) / 255.0
    raise SystemExit("could not find Cecilia's masked image on the page")


def write(coverage: np.ndarray, path: str) -> None:
    rows = np.where(coverage.max(axis=1) > 0.04)[0]
    cols = np.where(coverage.max(axis=0) > 0.04)[0]
    coverage = coverage[rows.min():rows.max() + 1, cols.min():cols.max() + 1]

    h, w = coverage.shape
    pad = max(int(round(max(h, w) * PAD)), 4)
    canvas = np.zeros((h + 2 * pad, w + 2 * pad), np.float32)
    canvas[pad:pad + h, pad:pad + w] = coverage

    rgba = np.zeros((*canvas.shape, 4), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = INK
    rgba[..., 3] = (canvas * 255).round().astype(np.uint8)
    img = Image.fromarray(rgba, "RGBA")
    img.save(path, optimize=True)
    print(f"{img.width}x{img.height}  -> {path}")


def main() -> None:
    doc = open_colour_comp()
    write(robert_coverage(doc), OUT_ROBERT)
    write(cecilia_coverage(doc), OUT_CECILIA)


if __name__ == "__main__":
    main()
