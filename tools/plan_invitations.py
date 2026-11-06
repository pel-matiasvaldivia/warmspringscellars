#!/usr/bin/env python3
"""Turn a club member export into cleaned, ordered mailing waves.

The list this was written for is Deerfield's: ~985 people who already belong
to a wine club, exported from WithWine. Warm Springs Cellar's domain, on the
other hand, has barely sent a message. Those two facts together are the whole
reason this file exists.

A new domain that sends nine hundred messages in an afternoon, more than half
of them to Gmail and Yahoo, looks exactly like a domain that has been bought
to spam from. Gmail and Yahoo have both required, since February 2024, that a
bulk sender authenticate with SPF, DKIM and DMARC, offer one-click
unsubscribe, and keep spam complaints under 0.3%; a cold list from a cold
domain is how a sender finds out what the other side of that threshold looks
like. And the address that gets burned is the one the invitations, the
invoices and the shipping notices go out from — the club cannot afford it.

So the list is cut into waves, smallest and warmest first, each one a test of
the next. What this script does NOT do is send anything: it writes files, and
a person decides what happens to them.

    python3 tools/plan_invitations.py <export.csv> <output-directory>

A wave file is uploaded to a mailing platform, which makes every column in
it a decision about what a third party gets to hold permanently. So a wave
file carries three: the address, the first name and the link. The export it
came from carries dates of birth, home addresses and telephone numbers —
953, 904 and 929 of them in the October list — and none of those reach a
platform from here. They survive in held-back.csv alone, which nobody
uploads and which exists so a person can ring somebody and recover a
missing state.

The export and everything written from it are personal data, and consumer
records collected in California at that. They do not belong in this
repository, and the script refuses to write inside it.
"""

from __future__ import annotations

import csv
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parent.parent
SITE = "https://warmspringscellar.com"

# The states the winery is licensed for, read from the service itself rather
# than copied, so this file cannot disagree with what the form will accept.
sys.path.insert(0, str(REPO / "api"))
from app.config import SHIPPABLE_STATES                       # noqa: E402

ABBREVIATIONS = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}
SPELLED_OUT = {"washington dc": "District of Columbia", "d.c.": "District of Columbia"}

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
ROLE = {"info", "sales", "admin", "office", "support", "contact", "billing",
        "noreply", "no-reply", "postmaster", "webmaster"}


def state_of(raw: str) -> str:
    """The export mixes 'CA', 'California' and 'Washington DC' in one column."""
    value = raw.strip()
    if not value or value in SHIPPABLE_STATES:
        return value
    if value.upper() in ABBREVIATIONS:
        return ABBREVIATIONS[value.upper()]
    if value.lower() in SPELLED_OUT:
        return SPELLED_OUT[value.lower()]
    titled = value.title()
    return titled if titled in SHIPPABLE_STATES else value


def bottles_in(club: str) -> int | None:
    found = re.match(r"\s*(\d+)", club)
    return int(found.group(1)) if found else None


def tidy_name(raw: str) -> str:
    """'Stephen & Amy' is two people in one field; the letter greets the first."""
    name = unicodedata.normalize("NFC", raw.strip())
    name = re.split(r"\s*(?:&|,| and )\s*", name)[0].strip()
    # SHOUTED or lowercase entries are common in an export this old.
    if name.isupper() or name.islower():
        name = name.title()
    return name


def link_for(row: dict, source: str) -> str:
    """The form, with the name filled in and the wave recorded.

    The name and the address, and deliberately NOT the city or the state.

    Two reasons, and the first is the one that matters. **The address in the
    export may be years old, and the wine is going to a door.** Prefilling a
    city somebody moved out of in 2019 does not save them typing; it invites
    them to press the button without reading the field, and the box goes to
    the old address. The one thing in this form that must be current is the
    one thing we should not guess at.

    The second is that this link now travels through a mailing platform,
    which keeps whatever the URL contains. Name and email are the ordinary
    content of a marketing list. Where somebody lives does not have to be.

    The form still says straight away whether we can ship to a state — it
    says it when they choose one, which is also when they have told us the
    truth rather than confirmed something stale.
    """
    parts = {"src": source, "f": row["first"], "l": row["last"], "e": row["email"]}
    query = "&".join(f"{k}={quote(v)}" for k, v in parts.items() if v)
    return f"{SITE}/?{query}#join"


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        raw = list(csv.DictReader(handle))

    people, seen = [], set()
    for row in raw:
        email = row.get("Email", "").strip().lower()
        first, last = row.get("FirstName", ""), row.get("LastName", "")
        club = row.get("ClubName", "").strip()
        person = {
            "email": email,
            "first": tidy_name(first),
            "last": tidy_name(last),
            "club": club,
            "bottles": bottles_in(club),
            "pickup": "pick up" in club.lower(),
            "city": row.get("City", "").strip(),
            "state": state_of(row.get("State", "")),
            "postcode": row.get("Postcode", "").strip(),
            "phone": row.get("Phone", "").strip(),
        }
        # Why a row is set aside, in the order the reasons disqualify it.
        if "test" in (first + last + email).lower():
            person["held"] = "a test row in the export"
        elif not EMAIL.match(email):
            person["held"] = "no usable email address"
        elif email in seen:
            person["held"] = "the same address appears earlier in the export"
        elif email.split("@")[0] in ROLE:
            person["held"] = "a role address, not a person"
        elif not person["state"]:
            person["held"] = "no state in the export, so we cannot say if we may ship"
        elif person["state"] not in SHIPPABLE_STATES:
            # Not a problem with the row — a problem with the licence. These
            # people are written to, with a different letter, so they come out
            # in their own file rather than the pile nobody reads.
            person["held"] = f"we are not licensed to ship to {person['state']}"
            person["unlicensed"] = True
        else:
            person["held"] = ""
        seen.add(email)
        people.append(person)
    return people


def waves(people: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """Smallest and warmest first, each wave a test of the one after it.

    The order is by how close the person already is to the winery, which is
    also the order of how likely they are to open rather than complain — and
    complaints early on are what decide whether the later waves arrive at all.
    """
    ready = [p for p in people if not p["held"]]

    def take(test) -> list[dict]:
        found = [p for p in ready if test(p)]
        for person in found:
            ready.remove(person)
        return found

    plan = [
        ("1-inner-circle", "The Inner Circle: the closest, and the test of everything else",
         take(lambda p: p["club"] == "Inner Circle")),
        ("2-pickup", "Pick-up members: they came to the winery, so the letter is different",
         take(lambda p: p["pickup"])),
        ("3-twelve-bottle", "Twelve-bottle members — the natural home for The Full Case",
         take(lambda p: p["bottles"] == 12)),
        ("4-six-bottle", "Six-bottle members — The Founder's Reserve, bottle for bottle",
         take(lambda p: p["bottles"] == 6)),
        ("5-three-bottle", "Three-bottle members: the step up has to be worth saying out loud",
         take(lambda p: p["bottles"] == 3)),
    ]
    if ready:
        plan.append(("6-everyone-else", "Everything the earlier waves did not describe", ready))
    return [(name, why, group) for name, why, group in plan if group]


# How many messages one send may carry, as the domain earns its reputation.
# A rung is taken per send and never given back, so the ramp only ever grows;
# a wave larger than its rung is split into parts, one part per sending day.
# These are deliberately modest: the cost of going slowly is a fortnight, and
# the cost of going quickly is the address the invoices go out from.
LADDER = [50, 100, 200, 300, 400, 500]

# What goes in a wave file, and nothing else. A wave file is now uploaded to
# a mailing platform (mail-templates/CAMPAIGN.md), which makes every column a
# decision about what a third party gets to hold.
#
# The platform needs an address to send to, a name to greet and the link. It
# has no use for a date of birth, a home address, a postcode or a telephone
# number, and the October export carries 953, 904 and 929 of the last three.
# None of them makes the letter better; all of them sit in somebody else's
# system afterwards, including after an account is closed or a vendor folds.
# These are consumer records collected in California, so this is a CCPA
# question and not only a tidiness one.
WAVE_FIELDS = ["email", "first", "link"]

# The unlicensed letter is about the state, so it keeps the state.
UNLICENSED_FIELDS = ["email", "first", "state", "link"]

# Held-back rows are the one place a telephone number earns its keep: 81 of
# them are held only because the export has no state, and INVITATIONS.md says
# the way to recover it is the old club system, an invoice, or a telephone
# call. Nothing in this file is uploaded anywhere — a person reads the reason
# and acts on it — so the detail stays where the work is.
HELD_FIELDS = ["email", "first", "last", "state", "city", "postcode", "club",
               "phone", "held_because"]


def sends(plan: list[tuple[str, str, list[dict]]]) -> list[tuple[str, str, list[dict]]]:
    """Cut the waves into sends small enough for a domain with no history."""
    out, rung = [], 0

    def cap() -> int:
        return LADDER[min(rung, len(LADDER) - 1)]

    for name, why, group in plan:
        if len(group) <= cap():
            out.append((name, why, group))
            rung += 1
            continue
        # Each part takes the next rung, so a large wave climbs the ramp
        # itself rather than waiting for the waves behind it.
        parts, rest = [], group
        while rest:
            parts.append(rest[:cap()])
            rest = rest[cap():]
            rung += 1
        for index, part in enumerate(parts):
            out.append((f"{name}-{chr(ord('a') + index)}",
                        f"{why} (part {index + 1} of {len(parts)})", part))
    return out


def write(rows: list[dict], path: Path, source: str,
          fields: list[str] = WAVE_FIELDS) -> None:
    """One file, ready to upload, carrying only `fields`.

    `link` is built rather than copied, and it is worth knowing what it
    carries, because a URL reaches the platform whatever the columns say:
    the person's first and last name, their email, and the wave. Nothing
    else. That is the ordinary content of a marketing list. A date of birth,
    a telephone number and a home address are not, and none of the three
    gets past this function — see link_for for why the address in particular
    is left out even though we have it.
    """
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for person in rows:
            writer.writerow({**{k: person.get(k, "") for k in fields},
                             "link": link_for(person, source)})


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    source_file, out = Path(sys.argv[1]).expanduser(), Path(sys.argv[2]).expanduser()
    if REPO in out.resolve().parents or out.resolve() == REPO:
        raise SystemExit(
            f"refusing to write inside the repository ({REPO}): these files carry "
            "names, home addresses and dates of birth. Choose a directory outside it."
        )
    out.mkdir(parents=True, exist_ok=True)

    people = read(source_file)
    ready = [p for p in people if not p["held"]]
    unlicensed = [p for p in people if p.get("unlicensed")]
    held = [p for p in people if p["held"] and not p.get("unlicensed")]

    print(f"{len(people)} rows · {len(ready)} to write to · "
          f"{len(unlicensed)} the other letter · {len(held)} set aside\n")
    for reason, count in Counter(p["held"] for p in held).most_common():
        print(f"  {count:4d}  {reason}")

    print("\nsends, in order — one a day, and only if the one before it behaved")
    for day, (name, why, group) in enumerate(sends(waves(people)), start=1):
        write(group, out / f"wave-{name}.csv", f"deerfield-{name}")
        states = len({p["state"] for p in group})
        print(f"  day {day}: {len(group):4d}  wave-{name}.csv  ({states} states)\n"
              f"          {why}")

    # Their own file, and their own letter: invitation-unlicensed.html, which
    # says we cannot ship there instead of asking them to join something they
    # cannot have. The link still works for them, and asking them for an
    # address is no longer the special case it once was: no link prefills one
    # now. For these 23 it is the whole question — an address somewhere we can
    # reach is what turns this into an ordinary application today — which is
    # why the state stays as a COLUMN here, so the file can be read by state.
    if unlicensed:
        write(unlicensed, out / "letter-unlicensed-states.csv", "deerfield-unlicensed",
              UNLICENSED_FIELDS)
        where = Counter(p["state"] for p in unlicensed).most_common()
        print(f"\n  {len(unlicensed):4d}  letter-unlicensed-states.csv — a different letter: "
              + ", ".join(f"{state} {count}" for state, count in where))

    # The held rows get the reason instead of a link: there is nothing to
    # click until a person has decided what to do about each one. This is the
    # one file that keeps the telephone number, because ringing somebody is
    # how the 81 rows with no state get a state.
    with (out / "held-back.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HELD_FIELDS)
        writer.writeheader()
        for person in held:
            writer.writerow({**{k: person.get(k, "") for k in HELD_FIELDS},
                             "held_because": person["held"]})
    print(f"\n  {len(held):4d}  held-back.csv — nobody writes to these without reading the "
          "reason first. The only file here with a telephone number in it, and "
          "the only one that is never uploaded anywhere.")
    print(f"\nWritten to {out}. Personal data: keep it out of the repository.")
    print("The wave files carry three columns — address, first name, link — "
          "because every column in them becomes something a mailing platform "
          "holds. Subtract /admin/campaigns/suppressions.csv before uploading.")


if __name__ == "__main__":
    main()
