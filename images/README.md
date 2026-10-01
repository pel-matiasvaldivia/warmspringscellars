# Site photography

Photographs live here with these exact filenames. The page references them
directly and degrades gracefully (placeholder art) if a file is missing.

| Filename                 | Photo                                                   | Used in                                  |
| ------------------------ | ------------------------------------------------------- | ---------------------------------------- |
| `cellar-exterior.jpeg`   | The winery building with the arched wooden doors         | Hero — faded behind the headline          |
| `robert_rex.jpg`         | Robert Rex in the vineyard, straw hat, holding a bottle  | Our Winemakers — left card                |
| `cecilia-valdivia.jpeg`  | Cecilia Valdivia leaning on a barrel, glass in hand      | Our Winemakers — right card               |

Recommended: JPEG, sRGB, ~2000px on the long edge, under 400 KB each. The
founder photos are cropped to a 4:5 portrait, so keep faces near the upper
third. The hero image is cropped wide and full-bleed.

The group photo of the team has no slot yet — the band that displayed it was
removed because the file never landed here and it 404'd on every page load.

## The shipment photograph

`shipment-box.webp` is the winery's own photograph of a packed case — corrugated
outer, moulded pulp cradle, three bottles — with the labels replaced. The
bottles in the original wore another winery's brand; everything else in the
frame is untouched: the box, the pulp, the capsules, the floor and the light.

```bash
python3 tools/relabel_box_photo.py            # the real work
python3 tools/relabel_box_photo.py --debug    # overlay the spans on the source
```

A bottle lying on its side is a cylinder, so a label on it is not a rectangle.
Each one is mapped through the cylinder the way `tools/label_bottles.py` does it
— a column at screen offset *s* sits at angle asin(*s*), so the artwork
compresses towards the silhouette — then through a homography onto the four
corners the bottle occupies in frame, which carries the camera's perspective.
The scale comes from the silhouette: a 750ml Bordeaux is three inches across, so
the bottle's width in pixels is the ruler for everything else, and the label
keeps the proportions of the artwork instead of being stretched to fit.

The lighting is not invented. For each bottle the glass's own brightness is
measured under where the label will go, taking a low percentile along the
bottle's length so the existing print is excluded and only the glass is left.
That profile — the specular streak, the fall-off at the edges — is what the new
label is lit by, which is why it sits in the same light as the capsule above it.

Each bottle is wiped before it is labelled, over a longer span than the label
covers, because the other winery printed onto the shoulder where no label of
ours reaches. The wipe paints that same measured profile and nothing else, so it
reconstructs clean glass rather than inventing any. Running `--debug` draws both
spans: orange is wiped, cyan is labelled.

**The source is only 1024×576**, which is tight for a full-width image on a
high-density screen. If a larger original exists, drop it in as
`shipment-box-source.jpg` and rerun — the measurements in `BOTTLES` are in
source pixels and would need scaling with it.

## Bottle shots

`<wine>-bottle.png` is generated, never hand-made. Three steps, each a tool in
`tools/`, run in this order from the repo root:

```bash
python3 tools/extract_labels.py    # labels/*.ai           -> images/labels/front-<wine>.png
python3 tools/label_bottles.py     # + the base renders    -> images/renders/<wine>.png
python3 tools/cutout_bottles.py    # strip the backdrop    -> images/<wine>-bottle.png
```

**`extract_labels.py`** rasterises the official label files. They are PDF
inside, one page carrying the front and back panels side by side, with layers
for the print separations and for guides — only "Color comp" is rendered.

**`label_bottles.py`** puts each label on a bottle. The winery supplied two
renders, `WarmSpringsCellars_Cabernet.png` (Bordeaux) and
`WarmSpringsCellars_Pinot.png` (Burgundy), both already wearing older artwork.
The tool learns the studio lighting from clean glass above and below the old
label, repaints the band with it to erase that artwork, then wraps the new
label around the cylinder — how far it reaches follows from the label's trim
width against the bottle's real circumference. Paper is lit as paper, not as
glass, which matters most on the Burgundy: that bottle is darkest dead centre,
so borrowing its own profile would drop a shadow through the wine's name.

**`cutout_bottles.py`** lifts the bottles off the grey studio sweep. Separating
a black bottle from its own reflection is the hard part, since the reflection
is dark too; it is found by scanning up for the lowest row both wide enough and
dark enough to be the glass itself.

Every bottle is scaled by one common factor rather than fitted individually, so
the Burgundy stays genuinely shorter than the Bordeaux when they stand side by
side. The page reads each render's pixel height from a `--h` custom property to
preserve that on screen; if `OUTPUT_HEIGHT` changes, `--bottle-scale` in
`index.html` must change with it.

Source material stays in the repo so the output is reproducible: `labels/*.ai`
and `images/WarmSpringsCellars_*.png`. The intermediate renders in
`images/renders/` are git-ignored. None of it ships in the container image.
