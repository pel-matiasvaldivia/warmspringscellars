#!/usr/bin/env python3
"""Build the winemakers' email signatures for Microsoft 365.

Written as tables with inline styles and nothing else, because Outlook on
Windows renders mail through Word: flexbox, grid, `<style>` blocks, classes,
background images and margins on block elements are all unreliable or simply
ignored there. Everything below is the subset that survives Outlook, Outlook
on the web, Apple Mail and Gmail alike.

Two decisions worth stating:

  * The logo is referenced by URL, not embedded. A signature set from an
    Exchange transport rule cannot carry an attachment, so the image has to
    live somewhere public; LOGO_URL is where it will be once the site serves
    it. For a signature pasted into one person's Outlook the image travels
    with the message and any address works.
  * The addresses are the winery's own, confirmed: firstname.lastname@.

Run from the repo root:  python3 email-signatures/build.py
"""

import pathlib

OUT_DIR = pathlib.Path("email-signatures")

# Where the logo will answer from once the site is deployed. Until then the
# previews below use the local file.
LOGO_URL = "https://warmspringscellar.com/images/logo-email.png"
LOGO_W, LOGO_H = 120, 144          # half the file's pixels, so it stays sharp

WINE = "#5c0037"
TEXT = "#2c1e24"
MUTED = "#8a7a80"
RULE = "#e2d8d0"

SERIF = "Georgia, 'Times New Roman', Times, serif"
SANS = "Arial, Helvetica, sans-serif"

PHONE = "707.318-0167"
PHONE_HREF = "+17073180167"
SITE = "warmspringscellar.com"
PLACE = "Glen Ellen, Sonoma Valley, California"

PEOPLE = [
    {"slug": "cecilia-valdivia", "name": "Cecilia Valdivia",
     "role": "Owner &amp; Winemaker", "email": "cecilia.valdivia@warmspringscellar.com"},
    {"slug": "robert-rex", "name": "Robert Rex",
     "role": "Expert Consultant", "email": "robert.rex@warmspringscellar.com"},
]


def signature(person: dict, logo_src: str) -> str:
    """One signature block. No stylesheet, no classes — Word ignores both."""
    return f"""<table cellpadding="0" cellspacing="0" border="0" role="presentation"
       style="border-collapse:collapse;font-family:{SANS};">
  <tr>
    <td valign="top" style="padding:0 18px 0 0;">
      <img src="{logo_src}" width="{LOGO_W}" height="{LOGO_H}" alt="Warm Springs Cellar"
           style="display:block;border:0;outline:none;text-decoration:none;
                  width:{LOGO_W}px;height:{LOGO_H}px;">
    </td>
    <td valign="top" style="padding:0 0 0 18px;border-left:1px solid {RULE};">
      <table cellpadding="0" cellspacing="0" border="0" role="presentation"
             style="border-collapse:collapse;">
        <tr>
          <td style="font-family:{SERIF};font-size:19px;line-height:24px;
                     color:{TEXT};letter-spacing:0.3px;padding:0 0 2px;">
            {person['name']}
          </td>
        </tr>
        <tr>
          <td style="font-family:{SANS};font-size:11px;line-height:16px;
                     letter-spacing:1.6px;text-transform:uppercase;
                     color:{WINE};padding:0 0 10px;">
            {person['role']}
          </td>
        </tr>
        <tr>
          <td style="font-family:{SERIF};font-size:14px;line-height:19px;
                     color:{WINE};letter-spacing:1.4px;padding:0 0 9px;">
            WARM SPRINGS <span style="font-style:italic;letter-spacing:0.2px;">Cellar</span>
          </td>
        </tr>
        <tr>
          <td style="font-family:{SANS};font-size:12px;line-height:19px;color:{TEXT};">
            <a href="mailto:{person['email']}"
               style="color:{TEXT};text-decoration:none;">{person['email']}</a><br>
            <a href="tel:{PHONE_HREF}"
               style="color:{TEXT};text-decoration:none;">{PHONE}</a><br>
            <a href="https://{SITE}"
               style="color:{WINE};text-decoration:none;">{SITE}</a>
          </td>
        </tr>
        <tr>
          <td style="font-family:{SANS};font-size:11px;line-height:16px;
                     color:{MUTED};padding:9px 0 0;">
            {PLACE}<br>EST. 1982 &middot; 1,000 cases a year
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


def page(body: str, title: str) -> str:
    """A wrapper for looking at it in a browser. Paste the table, not this."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>
<body style="margin:0;padding:40px;background:#ffffff;">
{body}</body></html>
"""


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for person in PEOPLE:
        block = signature(person, LOGO_URL)
        (OUT_DIR / f"{person['slug']}.html").write_text(block)
        # A preview that works offline, pointing at the file on disk.
        preview = signature(person, "../images/logo-email.png")
        (OUT_DIR / f"{person['slug']}.preview.html").write_text(
            page(preview, f"{person['name']} — signature"))
        print(f"{person['slug']}.html  ({len(block)} bytes)")


if __name__ == "__main__":
    main()
