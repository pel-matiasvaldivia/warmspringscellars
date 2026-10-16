#!/usr/bin/env python3
"""Fetch the site's two typefaces from Google Fonts into fonts/.

The page self-hosts rather than linking the CDN, which is the choice Caveat
was already handling: one fewer third party between a visitor and the page,
no request to another company's server on every load, and the fonts cannot
change under the design without this file changing first.

Only the latin subset is taken. The site is in English and the one accent it
sets — the é in Rosé — is inside it; latin-ext would double the files for
languages the page does not have.

Run from the repo root:  python3 tools/fetch_fonts.py
Needs network access. Writes fonts/*.woff2 and prints the sizes, which is
what to check: a face that grew by an order of magnitude is a wrong subset.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

OUT = Path("fonts")
API = "https://fonts.googleapis.com/css2"
# Asking as a current browser is what gets woff2 back; the default urllib
# agent is served the ttf fallback, which is roughly twice the size.
AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
         "Chrome/120.0.0.0 Safari/537.36")

# The latin subset's unicode-range, which is how it is told apart from the
# others in the stylesheet. Google lists it last for every family.
LATIN = "U+0000-00FF"

# Each face the page actually sets. Nothing is fetched speculatively: an
# unused weight is bytes every visitor pays for and nobody sees.
# Playfair Display is a variable font, so one file carries every weight and
# asking for 500 as well returns the same bytes twice; the stylesheet gives
# that one face a weight range instead. Lato is static, so each weight is
# its own file.
FACES = {
    "Playfair Display": [("normal", 400), ("italic", 400)],
    "Lato": [("normal", 400), ("italic", 400), ("normal", 700)],
}


def stylesheet(family: str, faces: list[tuple[str, int]]) -> str:
    axes = ";".join(f"{1 if style == 'italic' else 0},{weight}"
                    for style, weight in sorted(faces, key=lambda f: (f[0] != "normal", f[1])))
    url = f"{API}?family={family.replace(' ', '+')}:ital,wght@{axes}&display=swap"
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode()


def latin_sources(css: str) -> dict[tuple[str, int], str]:
    """The woff2 URL of the latin subset, per style and weight."""
    found = {}
    for block in css.split("@font-face")[1:]:
        if LATIN not in block:
            continue
        style = re.search(r"font-style:\s*(\w+)", block).group(1)
        weight = int(re.search(r"font-weight:\s*(\d+)", block).group(1))
        found[(style, weight)] = re.search(r"url\((https://[^)]+\.woff2)\)", block).group(1)
    return found


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for family, faces in FACES.items():
        sources = latin_sources(stylesheet(family, faces))
        for style, weight in faces:
            url = sources.get((style, weight))
            if not url:
                raise SystemExit(f"{family} {weight} {style}: no latin subset came back")
            slug = family.lower().replace(" ", "-")
            name = f"{slug}-{weight}{'-italic' if style == 'italic' else ''}.woff2"
            request = urllib.request.Request(url, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            (OUT / name).write_bytes(data)
            print(f"{name:34s} {len(data) / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
