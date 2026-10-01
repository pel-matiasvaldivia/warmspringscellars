#!/usr/bin/env python3
"""Pull the front panel out of each official label file.

The supplied .ai files are PDF inside, one page holding the front and back
panels side by side with the front on the left. Each carries optional-content
layers — the separation plates for the print run, plus a "Guides" layer whose
rules would otherwise render as stray lines across the artwork. Only "Color
comp" is wanted.

Run from the repo root:  python3 tools/extract_labels.py
Requires PyMuPDF and Pillow. Feeds tools/label_bottles.py.
"""

import glob
import os

import pymupdf
from PIL import Image

SRC_DIR = "labels"
OUT_DIR = "images/labels"
DPI = 300

# The wine each file belongs to, keyed by a fragment of its name. Note that SN
# is Sangiovese, not Sauvignon, and that the Malbec file is named for 2024
# while the artwork inside reads 2023.
WINES = {
    "_PN_": "pinot",
    "CS_": "cabernet",
    "_Malbec_": "malbec",
    "_SN_": "sangiovese",
    "_ZN_": "zinfandel",
}

# The artboard runs to the page edge, and the last row and column of pixels
# pick up the page itself. Shave them, or the bottle gets a bright hairline
# where the label ends.
BLEED = 3


def extract(path, name):
    doc = pymupdf.open(path)
    ocgs = doc.get_ocgs()
    comp = [x for x, info in ocgs.items() if info["name"] == "Color comp"]
    if not comp:
        raise SystemExit(f"{path}: no 'Color comp' layer")
    doc.set_layer(-1, on=comp, off=[x for x in ocgs if x not in comp])

    pix = doc[0].get_pixmap(dpi=DPI, alpha=False)
    page = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    # Front panel is the left half of the artboard.
    front = page.crop((BLEED, BLEED, page.width // 2 - BLEED, page.height - BLEED))
    out = f"{OUT_DIR}/front-{name}.png"
    front.save(out, optimize=True)
    print(f"{name:11s} {front.width}x{front.height}px  "
          f"{front.width/DPI:.2f} x {front.height/DPI:.2f} in  -> {out}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    files = sorted(glob.glob(f"{SRC_DIR}/*.ai"))
    if not files:
        raise SystemExit(f"no .ai files in {SRC_DIR}/")
    for path in files:
        base = os.path.basename(path)
        match = [v for k, v in WINES.items() if k in base]
        if not match:
            print(f"skipping {base}: unrecognised wine")
            continue
        extract(path, match[0])


if __name__ == "__main__":
    main()
