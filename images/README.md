# Site photography

Photographs live here with these exact filenames. The page references them
directly and degrades gracefully (placeholder art) if a file is missing.

| Filename                 | Photo                                                   | Used in                                  |
| ------------------------ | ------------------------------------------------------- | ---------------------------------------- |
| `cellar-exterior.jpeg`   | The winery building with the arched wooden doors         | Hero — faded behind the headline          |
| `robert_rex.jpg`         | Robert Rex in the vineyard, straw hat, holding a bottle  | Our Winemaker — the consultant band       |
| `cecilia-valdivia.jpeg`  | Cecilia Valdivia leaning on a barrel, glass in hand      | Our Winemaker — the card                  |
| `richard-deane.jpg`      | Generated: Richard with Cecilia and Robert, retouched    | Our Winemaker — the Richard Deane band    |
| `richard-deane-source.jpg` | The phone shot it is made from, untouched              | Nothing — the input, kept for reruns      |
| `door-mark.svg`          | Generated: the winery's mark, from the label artwork     | Inlined once in the page, used five times |
| `favicon-small.svg`      | Generated: the mark's solid cut                         | The tab icon                              |
| `favicon.svg`            | Generated: the full mark in wine, standalone            | Source for the touch icon; general use    |
| `favicon-16/32/180.png`  | Generated: rasterised from the two above                | Tab icon fallbacks, Apple touch icon      |
| `logo-warm-springs-cellar[-dark].jpg` | Generated: the full lockup, flat          | Nothing on the page — for use off-site    |
| `logo-warm-springs-cellar[-light].png` | Generated: the lockup on transparency    | Nothing on the page — for use off-site    |
| `signature-robert-rex.png`   | Generated: his signature, from the label            | Nothing on the page — email signatures    |
| `signature-cecilia-valdivia.png` | Generated: hers, from the label                 | Nothing on the page — her handwriting     |
| `logo-email[-cream].png`     | Generated: the lockup at 240px                      | The Outlook signatures in `email-signatures/` |
| `sauvignon-blanc-bottle.png` | Generated: the white, on transparency               | The Wines — sixth card                    |
| `rose-bottle.png`            | Generated: the rosé, on transparency                | The Wines — seventh card                  |

Recommended: JPEG, sRGB, ~2000px on the long edge, under 400 KB each. The
founder photos are cropped to a 4:5 portrait, so keep faces near the upper
third. The hero image is cropped wide and full-bleed.

**`richard-deane.jpg` is the one picture that is built rather than placed.**

```bash
python3 tools/retouch_patron.py           # -> images/richard-deane.jpg
python3 tools/retouch_patron.py --boxes   # the same, with its rectangles drawn
```

The only photograph of the three of them together is a phone shot taken in the
dark at the end of an evening: underexposed, noisy, orange, and with another
winery's name embroidered on two of the jackets. That embroidery is why the
band stood without a photo for so long — the way around it was a crop above
the chest, which loses the arm around the shoulder, and the arm around the
shoulder is the reason the photograph is worth having.

So the marks come off instead. Both sit on flat near-black fleece, so the
fill is not invention: the fabric around each one diffuses inwards until the
hole closes, which on a surface with no detail is what the thread was
covering, and the grain measured just outside goes back on top so the patch
is as noisy as its neighbours. Then white balance off Robert's shirt at two
thirds' strength, an exposure curve that lifts the people and leaves the sky
at its own level, chroma denoise hard and luma barely, and a 4:5 crop with
the faces high.

One thing is not fixed, because it cannot be: Richard's hand, the one holding
the glass, swept during the exposure. The fingers were never recorded, so
sharpening has nothing to work on and deconvolution — tried, with linear
motion kernels at several lengths and angles — comes back rimmed and ringing,
because the blur is a hand turning rather than a straight slide. What the
tool does instead is let it down into shadow. It was the brightest thing in
the lower half of a dark frame, which is why the eye found it second; at the
tone around it, it reads as a hand in shadow and the faces get the attention
back. The mask is the hand, not a shape drawn over it — in that corner skin
is the only thing both warm and bright. `--boxes` writes a copy with the rectangles and the crop
drawn on it, which is how to re-aim any of them.

The untouched original stays as `richard-deane-source.jpg` so the retouch can
be run again, and neither it nor the tool ships in the container.

The group photo of the team has no slot yet — the band that displayed it was
removed because the file never landed here and it 404'd on every page load.

## The winery's mark

`door-mark.svg` is the gable, the medallion and the arched doors from the
label, lifted as **vector** out of the artwork and not traced by hand:

```bash
python3 tools/extract_doormark.py        # labels/21CS_WSC_final_label.ai -> images/door-mark.svg
```

A brand mark redrawn by eye is a different mark, and nobody notices until a
printer puts the two side by side. The `.ai` files are PDF inside, so the
drawing is already vector; the tool pulls the paths inside the mark's box and
writes them out.

The label prints it in two inks — burgundy masses with white line work over
them — and flattening that to one colour loses every line inside the doors. So
the line work is **cut out of the mass with a mask** rather than painted in the
background's colour. Painting it would mean naming that colour, and the mark
sits over the hero photograph, over cream once the page scrolls, and over a
dark surface in the dark theme: three answers, one of them a photograph. A hole
is the same hole on all of them.

It is inlined into `index.html` rather than loaded through `<img>`, because an
SVG in an `<img>` can see neither `currentColor` nor a CSS variable, and the
mark takes the header's own colour.

Not taken: the "EST 1982" lettering, which is set as text. At header size it
would be illegible anyway.

The page inlines the mark **once**, as a `<symbol>`, and points at it with
`<use>` from the header, the age gate, the hero, the handwritten card and the
footer. Five inline copies would have meant five identical mask ids colliding
in one document, and 100 KB where 20 KB does.

All five of those places used to draw an invented house — a gable, a rounded
rectangle and a circle, nothing to do with the winery. They are the real mark
now.

### The logo, for use off the site

```bash
python3 tools/export_logo.py    # -> images/logo-warm-springs-cellar[-dark].jpg
```

The full lockup — gable, medallion, EST 1982, doors, ground line and the
WARM SPRINGS / Cellar wordmark — at 600dpi, for slides, letterheads and
anyone who asks for "the logo". The wordmark is outlined in the label, not
live type, so this needs none of the brand's fonts. Two versions: as printed
(light ink on the label's near-black) and wine on cream for a white page.

Why this one is a raster while the door mark is vector: the mask trick works
only where white line work sits on a burgundy mass and can be cut out of it.
The wordmark and the medallion's interior are white on the bare ground with
no mass behind them, so masking them leaves a hole where the brand's name
should be. The page only needs the mark, so the vector stays there and the
lockup is rendered instead. Neither file ships in the container; the page
does not request them.

### The winemakers' signatures

```bash
python3 tools/export_signatures.py   # -> images/signature-*.png
```

Both are already printed on every bottle, so these are the real signatures and
not a font pretending to be one. They are stored differently inside the
artwork, and that decides how each is taken and how large each can honestly
be:

| | In the label | Exported |
| --- | --- | --- |
| Robert Rex | vector paths | 945 × 414, as crisp as you like |
| Cecilia Valdivia | a 150 × 122 bitmap behind a soft mask | 444 × 389, and that is the ceiling |

Her signature is the mask, not the image: the bitmap under it is two flat
near-whites and reads as blank on its own. Three times its own size is as far
as it goes before the upscaling starts showing; at the size a mail client
displays a signature it is comfortable.

Neither is cut from a render of the page. The typeset "Robert Rex" is printed
across the tail of his signature, so a rectangular crop would drag the type in
with it; his strokes are copied onto a page of their own instead.

### The tab icon

```bash
python3 tools/extract_doormark.py     # writes favicon.svg and favicon-small.svg
node tools/render_favicons.mjs        # the three PNGs, via a headless browser
```

A browser draws a favicon at 16px, where the line work inside the doors is
thinner than a pixel and turns into a smudge — the detailed mark genuinely
does not survive it. So the tab gets `favicon-small.svg`: the masses alone,
roof and medallion and door, which is the same mark with its detail switched
off rather than a different one. The 180px Apple touch icon keeps the full
drawing, on cream, because Apple renders it large and wants it opaque.

## The shipment photograph

**This photograph is currently off the page.** It was removed in October 2026;
the slot, the caption and the styles are still in `index.html`, commented, so a
better photograph goes back in by dropping the file here and uncommenting ten
lines. Everything below still describes how the file in the repository was
made.

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
python3 tools/compose_labels.py    # the two with no file  -> images/labels/front-<wine>.png
python3 tools/label_bottles.py     # + the base renders    -> images/renders/<wine>.png
python3 tools/cutout_bottles.py    # strip the backdrop    -> images/<wine>-bottle.png
python3 tools/pale_bottles.py      # the white and the rosé -> images/<wine>-bottle.png
```

**`extract_labels.py`** rasterises the official label files. They are PDF
inside, one page carrying the front and back panels side by side, with layers
for the print separations and for guides — only "Color comp" is rendered. What
comes out is not quite what was printed: `tools/brandmark.py` takes the final s
off the script word and repaints the doors in the brand's ink on the way past,
because the name and the colour both moved after the print run and the .ai
files are the archive of it. Both changes are measured against the script
word's own bounding box, so they land the same on every label and on the
lockup at 600dpi.

**`compose_labels.py`** draws the two fronts that have no .ai file — the
Sauvignon Blanc and the Rosé. Their labels are clear film rather than paper,
one ink printed straight onto flint glass, so the tool takes the Cabernet's
drawing apart into the three inks it was made of and keeps only the coverage:
the doors print solid, the hatching inside them prints nothing and lets the
wine through, the wordmark and the rules print. Which white stroke is drawing
and which is hatching is settled by blurring the two inks far enough to
swallow a stroke and asking what each pixel's surroundings are made of. The
vintage, varietal and vineyard are then set again underneath, on the lines the
Zinfandel uses, sized by cap height so a substituted face changes the shapes
and not the layout.

**`label_bottles.py`** puts each label on a bottle. The winery supplied two
renders, `WarmSpringsCellars_Cabernet.png` (Bordeaux) and
`WarmSpringsCellars_Pinot.png` (Burgundy), both already wearing older artwork.
The tool learns the studio lighting from clean glass above and below the old
label, repaints the band with it to erase that artwork, then wraps the new
artwork around the cylinder — how far it reaches follows from the trim width
against the bottle's real circumference.

It is **printed, not pasted**, and that is the point of the picture. These
bottles are decorated directly: the ink is fired onto the glass, which costs
several times what a paper label costs and is what the wines are known for,
so the shot has to show it. In practice that means the drawing's ground is
nothing at all — there is no panel, no edge, no shadow cast onto the glass,
and the bottle goes on being a bottle between the strokes. Only the ink is
lit, as a matte film on a cylinder under the studio's key, with a floor high
enough that a stroke keeps reading white well into the turn where the glass
behind it has gone black. Then the bottle's own highlight is carried back
over the top of the artwork: a label stands off the glass and interrupts the
reflection, a fired print lies under the same gloss, and that unbroken
highlight is the thing the eye reads as the expensive one.

One correction the press would make and a downsampler will not: the artwork
is three times the resolution it ends up at, so a hairline arrives as a
half-covered pixel and the dark glass drinks it. A gamma on coverage gives
the fine type back the weight it had, which matters most on the Burgundy —
its label is a 4.5in trim, so every line on it lands smaller.

**`pale_bottles.py`** does for the Sauvignon Blanc and the Rosé what the two
above do for the reds — including the printed finish: no panel, no edge, no
cast shadow, and the bottle's highlight carried over the ink, which shows
further on flint glass than on black because the highlight is harder. It is a
separate tool because none of the reds' assumptions survive a pale bottle:
the mockups came already on transparency, already lit, and light enough that a
"find the dark bottle, flood away the backdrop" pass reads the glass as sky.
The silhouette comes from the alpha channel instead, and the work is done at twice the
output size and brought back down — which is what keeps enlarged glass from
looking soft beside a label drawn at 300dpi. The output is tied to the reds'
scale in pixels per inch, so the sets stand together; these two are taller
because the bottles are.

**`cutout_bottles.py`** lifts the five reds off the grey studio sweep. Separating
a black bottle from its own reflection is the hard part, since the reflection
is dark too; it is found by scanning up for the lowest row both wide enough and
dark enough to be the glass itself.

That edge is then walked back up through the **contact ramp**. The floor throws
light onto the last few rows of the heel — on these renders the glass runs dark
to row 1139 and the floor has taken over by 1148 — and keeping those rows left a
pale lip across the bottom of every bottle. On the page, under a drop-shadow,
that lip read as a shadow someone had sliced through. The cut now goes where
the climb to floor brightness begins (5% of the way up it), which lands all five
bottles on the same row, as it should: they share a floor.

The page carries no drop-shadow on the bottles any more. A CSS filter shadow
hangs off the flat bottom edge of a cut-out and announces it; the only grounding
is the soft ellipse on the stage, which is its own element and cannot be clipped
by the card.

Every bottle is scaled by one common factor rather than fitted individually, so
the Burgundy stays genuinely shorter than the Bordeaux when they stand side by
side. The page reads each render's pixel height from a `--h` custom property to
preserve that on screen; if `OUTPUT_HEIGHT` changes, `--bottle-scale` in
`index.html` must change with it.

Source material stays in the repo so the output is reproducible: `labels/*.ai`
and `images/WarmSpringsCellar*_*.png`. The intermediate renders in
`images/renders/` are git-ignored. None of it ships in the container image.
