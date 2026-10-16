# Email signatures

Two signature blocks for Microsoft 365, one per winemaker.

```bash
python3 email-signatures/build.py
```

| File | What it is |
| --- | --- |
| `cecilia-valdivia.html` | The block to paste. Logo by URL. |
| `robert-rex.html` | The same, his details. |
| `*.preview.html` | Open in a browser to see it. Not for pasting — it points at the logo on disk. |

The town is **Glen Ellen**, which is what the bottles say: "PRODUCED AND
BOTTLED BY WARM SPRINGS CELLARS, GLEN ELLEN, CALIFORNIA". The site used to
say Kenwood and was changed to match, so the signature, the site and the
label now agree.

## Putting one in Outlook

**For one person**, in Outlook on the web: Settings → Mail → Compose and
reply → Email signature. Open the `.preview.html` in a browser, select the
whole block, copy, and paste into the signature box — pasting carries the
formatting and the image, and the image then travels with each message, so
it shows even if the recipient blocks remote content.

**For the whole organisation**, through an Exchange transport rule, the image
cannot be an attachment: it has to answer from a public URL. The HTML already
points at `https://warmspringscellar.com/images/logo-email.png`, which will
serve once that file is published — today it is excluded from the container
image along with the rest of the off-site artwork, so publishing it is a line
in `.dockerignore`. Anything on a public URL can be downloaded by anyone; that
is normally fine for a logo and worth knowing anyway.

## Why it is written the way it is

Outlook on Windows renders mail through Word, which ignores flexbox, grid,
`<style>` blocks, classes and margins on block elements. So this is tables and
inline styles and nothing else, which is also what Outlook on the web, Apple
Mail and Gmail all accept. The fonts are Georgia and Arial rather than the
site's Didot: a mail client has only what is installed on the reader's
machine, and a face nobody has falls back to one nobody chose.

The winery's name appears twice — drawn in the logo and set in type beneath
the role. That is deliberate. Outlook blocks remote images by default until
the recipient allows them, and a signature whose entire branding is one image
arrives as an empty box. The type carries it when the image does not load.

`images/logo-email.png` is 240px wide for a 120px slot, so it stays sharp on a
high-density screen. `images/logo-email-cream.png` is the same on solid cream,
for a client that mishandles transparency — in Outlook's dark mode a
transparent wine logo can lose contrast against the dark background.
