#!/usr/bin/env python3
"""Turn a letter in mail-templates/ into HTML ready to paste into a platform.

    python3 tools/render_for_platform.py new-kid-on-the-block brevo \\
        --src wave-1-inner-circle -o ~/WSC-wave-1.html

The letters are written once, with neutral `{{field}}` placeholders, because
Brevo, Mailchimp and the next one all spell their merge tags differently and a
letter rewritten per platform is a letter that drifts. This does the
translation, and it does three things by hand that are easy to forget:

  * It strips the notes to whoever edits the template. They are HTML comments,
    so they would not render — but they would travel, inside the source of
    every message, where anyone can read them. One of them explains which
    sentences the winery has not signed off yet.
  * It fills in the postal address from config.POSTAL_ADDRESS rather than
    leaving it to be typed. CAN-SPAM requires a real one in the message.
  * It refuses to write a file with an unfilled `{{field}}` left in it.

`--src` is the one thing that changes between waves: it rides on the link, is
written into the referral column when somebody joins, and is the only way to
tell afterwards which wave produced which member. One campaign per wave, one
--src per campaign.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "mail-templates"
sys.path.insert(0, str(REPO / "api"))

from app.config import POSTAL_ADDRESS                         # noqa: E402

SITE = "https://warmspringscellar.com"

# How each platform spells the fields the letters use. `link` and
# `postal_address` are built below, not looked up here.
#
# Brevo's filter syntax is `{{ contact.FIRSTNAME|default:"..." }}` and its
# unsubscribe tag is `{{ unsubscribe }}` — both from Brevo's own help centre.
# The attribute may not be called FIRSTNAME in an account created in another
# language (NOMBRE in Spanish, for instance): check Contacts → Settings →
# Contact attributes before the first send.
DIALECTS = {
    "brevo": {
        "first_name": '{{ contact.FIRSTNAME|default:"Wine Lover" }}',
        "email": "{{ contact.EMAIL }}",
        "unsubscribe_url": "{{ unsubscribe }}",
    },
    "mailchimp": {
        "first_name": "*|FNAME|*",
        "email": "*|EMAIL|*",
        "unsubscribe_url": "*|UNSUB|*",
    },
    # Sequenzy reads attributes off the subscriber and has one tag for the
    # unsubscribe URL.
    #
    # SPELL `firstName` RIGHT. Sequenzy's own documentation warns about this
    # and it is the nastiest failure here: `{{ subscriber.frstName | default:
    # "there" }}` greets EVERY recipient as "there", including the 983 whose
    # first name is stored, and the rendered email looks perfectly
    # personalized. Nothing about the preview gives it away. The only honest
    # test is the render endpoint's `unresolvedMergeTags` coming back empty —
    # a tag with a fallback is still reported there when its name is
    # unrecognized. Check that, not the greeting.
    "sequenzy": {
        "first_name": '{{ subscriber.firstName | default: "Wine Lover" }}',
        "email": "{{subscriber.email}}",
        "unsubscribe_url": "{{unsubscribeUrl}}",
    },
}

FIELD = re.compile(r"\{\{([a-z_]+)\}\}")


def render(name: str, platform: str, src: str) -> str:
    source = TEMPLATES / f"{name}.html"
    if not source.exists():
        raise SystemExit(f"no such letter: {source}")
    html = source.read_text(encoding="utf-8")

    # The editor's notes never leave this repository.
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S).strip()

    fields = dict(DIALECTS[platform])
    fields["postal_address"] = POSTAL_ADDRESS
    # Only the wave name travels. No name, no address, no identifier — the
    # platform's merge tags put the reader into the page, and the export this
    # list came from has an address that may be years old.
    fields["link"] = f"{SITE}/?src={src}#join"

    # Checked BEFORE substituting, not after. A platform's own tags look like
    # placeholders — Brevo's unsubscribe link is literally `{{ unsubscribe }}`
    # — so a check run on the output flags what this function just inserted.
    # Reading the source instead also catches the likelier mistake: a field
    # misspelt in the template, which would otherwise ship as braces.
    wanted = {m.group(1) for m in FIELD.finditer(html)}
    unknown = sorted(wanted - set(fields))
    if unknown:
        raise SystemExit(
            "the letter asks for fields this does not know how to fill: "
            + ", ".join(unknown))

    for key, value in fields.items():
        html = html.replace("{{" + key + "}}", value)
    return html


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a letter for a mailing platform.")
    parser.add_argument("letter", help="a name in mail-templates, without .html")
    parser.add_argument("platform", choices=sorted(DIALECTS))
    parser.add_argument("--src", required=True,
                        help="the wave name, e.g. wave-1-inner-circle")
    parser.add_argument("-o", "--out", type=Path,
                        help="write here instead of standard output")
    args = parser.parse_args()

    html = render(args.letter, args.platform, args.src)
    if args.out:
        args.out.write_text(html, encoding="utf-8")
        print(f"{args.out}  ({len(html):,} bytes)")
        print(f"  platform  {args.platform}")
        print(f"  src       {args.src}")
        print(f"  address   {POSTAL_ADDRESS}")
        print("\nPaste it into a 'paste your own code' campaign. Check the "
              "preview for two unsubscribe footers:\nthe platform adds its own, "
              "and if both appear, delete the one in this HTML — the\nplatform's "
              "is the one wired to its suppression list.")
    else:
        sys.stdout.write(html)


if __name__ == "__main__":
    main()
