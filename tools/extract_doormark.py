#!/usr/bin/env python3
"""Lift the door mark out of the official label as vector art.

The winery's mark — the gable, the barrel-and-lantern medallion and the arched
double doors — is drawn in the label files, which are PDF inside. It is vector
there, so it comes out as vector here: an SVG that stays crisp at 28px in a
header and at any size above it, themed by CSS rather than baked into a PNG.

Tracing it by eye would have been faster and wrong. A brand mark redrawn by
hand is a different mark, and nobody would notice until a printer put the two
side by side.

What this does NOT take: the "EST 1982" lettering under the medallion, which is
set as text and would need its outlines. The header sets that in the page's own
type instead, so the mark here is the drawing alone.

Run from the repo root:  python3 tools/extract_doormark.py
Requires PyMuPDF. Writes images/door-mark.svg.
"""

import pymupdf

SRC = "labels/21CS_WSC_final_label.ai"
OUT = "images/door-mark.svg"

# The mark's box on the front panel, in PDF points, measured off a 150dpi
# render of the panel: the apex of the roof down to the floor line under the
# doors, and out to where the eaves are cropped by the panel edge.
BOX = pymupdf.Rect(20, 8, 250, 232)

# The mark is two inks: burgundy masses (the roof band, the doors) and white
# line work drawn over them (panels, hinges, handles, the medallion's barrel).
# Flattening both to one colour loses every line inside the doors.
#
# So the line work is cut out of the mass with a mask rather than painted in
# the background's colour. Painting it would mean naming that colour, and the
# mark sits over the hero photograph, over cream once the page scrolls, and
# over whatever dark surface the theme chooses — three different answers, one
# of them a photograph. A hole is the same hole on all of them.
#
# The mass takes currentColor, so the mark inherits the header's own wine and
# changes with it. That is why this is inlined into the page: an SVG loaded
# through <img> can see neither currentColor nor a CSS variable.
MASS, LINE = "dm-mass", "dm-line"


def ink(drawing):
    """Which of the label's two inks this drawing is."""
    colour = drawing.get("fill") or drawing.get("color") or (0, 0, 0)
    return LINE if min(colour) > 0.8 else MASS


def fmt(value):
    return f"{value:.2f}".rstrip("0").rstrip(".")


def path_data(items, close):
    """One PDF drawing's items as SVG path data."""
    out = []
    here = None
    for item in items:
        kind = item[0]
        if kind == "l":
            start, end = item[1], item[2]
            if here is None or (abs(start.x - here.x) > 0.01 or abs(start.y - here.y) > 0.01):
                out.append(f"M{fmt(start.x)} {fmt(start.y)}")
            out.append(f"L{fmt(end.x)} {fmt(end.y)}")
            here = end
        elif kind == "c":
            start, c1, c2, end = item[1], item[2], item[3], item[4]
            if here is None or (abs(start.x - here.x) > 0.01 or abs(start.y - here.y) > 0.01):
                out.append(f"M{fmt(start.x)} {fmt(start.y)}")
            out.append(f"C{fmt(c1.x)} {fmt(c1.y)} {fmt(c2.x)} {fmt(c2.y)} "
                       f"{fmt(end.x)} {fmt(end.y)}")
            here = end
        elif kind == "re":
            r = item[1]
            out.append(f"M{fmt(r.x0)} {fmt(r.y0)}H{fmt(r.x1)}V{fmt(r.y1)}H{fmt(r.x0)}Z")
            here = None
        elif kind == "qu":
            q = item[1]
            out.append(f"M{fmt(q.ul.x)} {fmt(q.ul.y)}L{fmt(q.ur.x)} {fmt(q.ur.y)}"
                       f"L{fmt(q.lr.x)} {fmt(q.lr.y)}L{fmt(q.ll.x)} {fmt(q.ll.y)}Z")
            here = None
    if close and out:
        out.append("Z")
    return "".join(out)


def main():
    doc = pymupdf.open(SRC)
    ocgs = doc.get_ocgs()
    comp = [x for x, info in ocgs.items() if info["name"] == "Color comp"]
    doc.set_layer(-1, on=comp, off=[x for x in ocgs if x not in comp])
    page = doc[0]

    paths = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if not BOX.contains(rect) or rect.is_empty:
            continue
        # The panel's own background fill is in the box too, and it is the one
        # thing in there that is not the mark.
        if rect.width > BOX.width * 0.95 and rect.height > BOX.height * 0.95:
            continue
        data = path_data(drawing["items"], drawing.get("closePath"))
        if not data:
            continue
        filled = drawing["type"] in ("f", "fs")
        attrs = [f'class="{ink(drawing)}"', f'd="{data}"']
        if filled:
            if drawing.get("even_odd"):
                attrs.append('fill-rule="evenodd"')
        else:
            attrs.append(f'stroke-width="{fmt(drawing.get("width") or 0.5)}"')
        paths.append("  <path " + " ".join(attrs) + "/>")

    # In the mask, white keeps and black cuts: the masses first, then the line
    # work punched through them.
    paths.sort(key=lambda p: 0 if f'"{MASS}"' in p else 1)
    body = "\n".join(
        p.replace(f'class="{MASS}"', 'fill="#fff" stroke="#fff"')
         .replace(f'class="{LINE}"', 'fill="#000" stroke="#000"')
        for p in paths
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{fmt(BOX.x0)} {fmt(BOX.y0)} {fmt(BOX.width)} {fmt(BOX.height)}" '
        f'aria-hidden="true">\n'
        f'  <mask id="door-mark-ink" maskUnits="userSpaceOnUse" '
        f'x="{fmt(BOX.x0)}" y="{fmt(BOX.y0)}" '
        f'width="{fmt(BOX.width)}" height="{fmt(BOX.height)}">\n'
        f'{body}\n  </mask>\n'
        f'  <rect x="{fmt(BOX.x0)}" y="{fmt(BOX.y0)}" '
        f'width="{fmt(BOX.width)}" height="{fmt(BOX.height)}" '
        f'fill="currentColor" mask="url(#door-mark-ink)"/>\n</svg>\n'
    )
    with open(OUT, "w") as fh:
        fh.write(svg)
    print(f"{len(paths)} paths -> {OUT}  ({len(svg)} bytes)")


if __name__ == "__main__":
    main()
