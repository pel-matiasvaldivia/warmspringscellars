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

The export and everything written from it are personal data — names, home
addresses, dates of birth, telephone numbers. They do not belong in this
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
    """The form, with what we already know filled in and the wave recorded."""
    parts = {"src": source, "f": row["first"], "l": row["last"], "e": row["email"]}
    if row["state"] in SHIPPABLE_STATES:
        parts["c"], parts["st"] = row["city"], row["state"]
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
            person["held"] = f"we are not licensed to ship to {person['state']}"
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

FIELDS = ["email", "first", "last", "state", "city", "postcode", "club", "phone", "link"]


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


def write(rows: list[dict], path: Path, source: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for person in rows:
            writer.writerow({**{k: person.get(k, "") for k in FIELDS},
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
    held = [p for p in people if p["held"]]

    print(f"{len(people)} rows · {len(ready)} to write to · {len(held)} set aside\n")
    for reason, count in Counter(p["held"] for p in held).most_common():
        print(f"  {count:4d}  {reason}")

    print("\nsends, in order — one a day, and only if the one before it behaved")
    for day, (name, why, group) in enumerate(sends(waves(people)), start=1):
        write(group, out / f"wave-{name}.csv", f"deerfield-{name}")
        states = len({p["state"] for p in group})
        print(f"  day {day}: {len(group):4d}  wave-{name}.csv  ({states} states)\n"
              f"          {why}")

    # The held rows get the reason instead of a link: there is nothing to
    # click until a person has decided what to do about each one.
    with (out / "held-back.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS[:-1] + ["held_because"])
        writer.writeheader()
        for person in held:
            writer.writerow({**{k: person.get(k, "") for k in FIELDS[:-1]},
                             "held_because": person["held"]})
    print(f"\n  {len(held):4d}  held-back.csv — nobody writes to these without reading the "
          "reason first")
    print(f"\nWritten to {out}. Personal data: keep it out of the repository.")


if __name__ == "__main__":
    main()
