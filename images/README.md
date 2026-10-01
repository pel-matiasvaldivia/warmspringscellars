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

## The shipment crate — slot still open

The "What Arrives" section has no packaging photograph yet. A procedural render
of a pine crate lived here and was cut: it was geometrically sound and nobody
believed it. Generating a convincing photograph of wood, wood wool and glass is
not something code gets to by itself, and a near-miss reads worse on a premium
page than no photograph at all. It is in the history (`tools/render_shipment.py`)
if it is ever wanted as a starting point.

What to put here instead, in order of preference:

1. A studio photograph of a real packed crate — open, three-quarter view, bottles
   standing in wood wool, shot against a plain sweep so it can be cut out.
2. A photograph staged at the winery, same framing, daylight.
3. A render from a 3D tool with real materials.

Drop it in as `shipment-crate.webp` (or `.jpg`) and add it back to the section.
A cut-out with a transparent background is worth the extra step: it sits on the
page's own ground in both light and dark themes, rather than as a pasted-in
rectangle of studio grey.

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
