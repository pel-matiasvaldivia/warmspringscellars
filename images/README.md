# Site photography

Photographs live here with these exact filenames. The page references them
directly and degrades gracefully (placeholder art) if a file is missing.

| Filename                 | Photo                                                   | Used in                                  |
| ------------------------ | ------------------------------------------------------- | ---------------------------------------- |
| `cellar-exterior.jpeg`   | The winery building with the arched wooden doors         | Hero — faded behind the headline          |
| `robert_rex.jpg`         | Robert Rex in the vineyard, straw hat, holding a bottle  | Our Winemaker — the consultant band       |
| `cecilia-valdivia.jpeg`  | Cecilia Valdivia leaning on a barrel, glass in hand      | Our Winemaker — the card                  |
| `richard-deane.jpg`      | Richard Deane with Cecilia and Robert, at the estate     | Our Winemaker — the Richard Deane band    |
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
| `shipment-box.webp`          | Generated: the packed case, branded                 | What Arrives — beside the four steps      |
| `shipper-source.jpg`         | The studio shot it is made from, as supplied        | Nothing — the input, kept for reruns      |

Recommended: JPEG, sRGB, ~2000px on the long edge, under 400 KB each. The
founder photos are cropped to a 4:5 portrait, so keep faces near the upper
third. The hero image is cropped wide and full-bleed.

**`richard-deane.jpg` arrives finished and is placed like the rest**, which
is worth a note only because for a while it did not.

The first frame of the three of them was a phone shot taken in the dark at
the end of an evening: underexposed, noisy, orange, with another winery's
name embroidered on two of the jackets, and with Richard's hand swept into a
smear by the exposure. It was retouched here — the embroidery diffused out,
the exposure lifted, the blurred hand let down into shadow because no
sharpening recovers fingers that were never recorded. The winery then
supplied a cleaned-up frame of the same moment, already exposed, already
square to 4:5, with no embroidery visible and the hand sharp, so the retouch
and its tool came out: the right amount of machinery for a photograph that
no longer needs any is none.

The file is used exactly as supplied, not re-encoded. It is already the 4:5
the patron frames want, so nothing crops it, and a second pass through a
JPEG encoder would only cost a little of what it came with.

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

`shipment-box.webp` is a studio shot of a packed case — lid up, moulded pulp
tray, three bottles standing in it — made the winery's own. The supplied
frame came with every printed thing on it wrong: first another winery's, then
ours misspelled, because the frame was generated and generated type falls
apart when you read it. "WADM SORINGS" on the Zinfandel, "2054 ZINPANOEL",
"Low Chemical Vineyard" for Los Chamizal, "Colllon" for Cellar on the lid.
All of it comes off and ours goes on. Nothing else is touched — the carton,
the pulp, the glass, the capsules, the barrels behind and the light stay
exactly as photographed.

```bash
python3 tools/brand_shipper.py            # the real work
python3 tools/brand_shipper.py --debug    # draw every region it will touch
```

**The lid.** Corrugated board is striped vertically, and the stripe is what
makes it read as board; diffusing a hole closed averages those stripes into a
bruise. So each column is rebuilt from the same column — the clean board a
little above and a little below, ramped between the two. Then the lockup goes
on, taken from the same label the bottles wear so the case and the bottle in
it carry one mark and not two drawings of one. It is multiplied onto the
board rather than painted over it, because ink on kraft darkens the
corrugation instead of hiding it.

**The bottles.** A bottle is a cylinder, so a label on it is not a rectangle.
Each is mapped through the cylinder the way `tools/label_bottles.py` does it
— a column at screen offset *s* sits at angle asin(*s*), so the artwork
compresses towards the silhouette — then through a homography onto the four
corners the bottle occupies in frame, which carries the camera's perspective.
The scale comes from the silhouette: a 750ml bottle is three inches across,
so its width in pixels is the ruler, and each label keeps the proportions of
its own trim rather than being stretched to fit.

They are **printed, not pasted**, the same as the bottle shots: the drawing's
ground carries no ink, so between the strokes the bottle goes on being the
bottle, and there is no panel, no cut edge and no shadow of one.

Two things this frame taught the tool. The light for a label is read on a
strip of glass *above* it, not under it — under it is the artwork being
replaced, and the low percentile that reads glass through ink-on-glass reads
that artwork instead and turns every new label black. And that profile is
then **levelled** to the glass actually under the label, because the shoulder
is the brightest part of a bottle and a profile carried down from it unscaled
made the Sauvignon Blanc a khaki card on a straw bottle. Which percentile
counts as bare glass depends on the bottle: high on the flint one, where the
glass is bright and the print dark; low on the two reds, where it is the
other way round. Reading the same one for both turned the reds metallic.

There is no wipe pass. Every label being replaced sits inside the footprint
of the label going over it, so there is nothing to erase that is not about to
be covered — and a wipe reaching past its label has to invent glass.

**The carrier label** gets our details. Each block is filled from the strip of
blank label between the last column of type and the first QR code — not from
the rows above and below, which are more type, and reading those printed a
ghost of "SHIPPED FROM" through the address. No street is invented: a mockup
carrying a plausible address is a mockup somebody will one day post something
to.

`--debug` draws it all: the board it clears, the lockup's box, the label and
light bands on each bottle, and the line of the carton's front wall, below
which nothing is painted because below it is the box and not the bottle.

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
