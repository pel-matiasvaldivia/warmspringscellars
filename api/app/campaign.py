"""The winery's own do-not-contact list.

The campaign itself is sent from a mailing platform — see
`mail-templates/CAMPAIGN.md` — and that platform keeps its own suppression
list, offers its own unsubscribe link and reports its own numbers. None of
that is reimplemented here, and an earlier attempt to do so has been removed.

What stays is the one piece a platform cannot hold for the winery: a record
of who must not be written to, that belongs to the winery rather than to
whichever account is paying a subscription this year.

Three reasons it has to live here and not only there:

  * **Opt-outs outlive platforms.** Somebody who unsubscribes today is still
    unsubscribed after the winery moves from one provider to the next, and
    an export that nobody kept is an opt-out that quietly stops being
    honoured. Honouring it is not optional.
  * **Deerfield's opt-outs came with the list.** People who had already told
    Deerfield to stop writing are on the export like everybody else, and
    they must be filtered out before a single address is uploaded anywhere.
    This is where that filter is kept.
  * **Some people ask off by replying, or by telephone.** Those never reach
    the platform at all unless somebody types them in.

The flow is deliberately dull: addresses go in here, the list comes out as
CSV at /admin/campaigns, and that CSV is what is removed from a wave before
it is uploaded. Nothing in this module sends anything.
"""

from __future__ import annotations

import re
import sqlite3

from .db import now

# How an address came to be on the list. Not equal to each other: the first
# is somebody's decision, the second is a dead mailbox that a new address
# could replace, and the third is the most permanent of the three.
SOURCES = ("unsubscribe", "bounce", "complaint", "hand")


def tidy(email: str) -> str:
    """The form an address is stored and compared in.

    One address, one row. The export arrives with 'Anita@Example.COM ' and
    'anita@example.com' in different rows, and if those two are different
    keys then suppressing is a coin toss.
    """
    return email.strip().lower()


def suppress(con: sqlite3.Connection, email: str, source: str,
             campaign: str = "", detail: str = "") -> bool:
    """Add an address. Returns whether this call was the one that added it.

    The first reason wins. A bounce after an unsubscribe does not overwrite
    the unsubscribe, because the unsubscribe is the fact somebody would be
    asked to defend: it is the one that says a person chose this.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown suppression source: {source!r}")
    cursor = con.execute(
        "INSERT INTO email_suppression (email, created_at, source, campaign, detail) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (email) DO NOTHING",
        (tidy(email), now(), source, campaign or None, detail or None),
    )
    return cursor.rowcount > 0


def suppress_many(con: sqlite3.Connection, emails: list[str], source: str,
                  campaign: str = "", detail: str = "") -> tuple[int, int]:
    """Take in a platform's unsubscribe export. Returns (added, already there).

    This is how the list is fed now that nothing here does the sending: after
    each wave, the provider's unsubscribes and bounces are exported and
    brought across, so the winery's record stays the complete one even though
    the sending happened somewhere else.

    Rubbish in the file is skipped rather than stored. An export pasted from
    a spreadsheet picks up headers, blank rows and the occasional name, and
    none of those are addresses that anybody needs protected.
    """
    added = seen = 0
    for raw in emails:
        address = tidy(raw)
        if "@" not in address or " " in address:
            continue
        if suppress(con, address, source, campaign, detail):
            added += 1
        else:
            seen += 1
    return added, seen


def is_suppressed(con: sqlite3.Connection, email: str) -> bool:
    return con.execute("SELECT 1 FROM email_suppression WHERE email = ?",
                       (tidy(email),)).fetchone() is not None


def suppressed(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT email, created_at, source, campaign, detail FROM email_suppression "
        "ORDER BY created_at DESC").fetchall()


def addresses(con: sqlite3.Connection) -> set[str]:
    return {row["email"] for row in
            con.execute("SELECT email FROM email_suppression")}


def by_source(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """How many of each reason, for the screen."""
    return con.execute(
        "SELECT source, COUNT(*) AS how_many FROM email_suppression "
        "GROUP BY source ORDER BY how_many DESC").fetchall()


# ── what each wave actually brought in ───────────────────────────────────
# The platform counts who opened a letter. Only the club knows who joined
# because of it, and it knows because every wave's link carries its own
# `src`, which the form stores on the application's referral.

# The referral holds either the campaign tag alone, or the answer somebody
# chose from the dropdown and then the tag: "Deerfield Ranch Winery ·
# wave-4-six-bottle-a". The tag is always last, because that is where the
# form appends it.
_WAVE = re.compile(r"(?:^|·\s*)(wave-[a-z0-9-]+)\s*$")


def wave_of(referral: str | None) -> str:
    match = _WAVE.search((referral or "").strip())
    return match.group(1) if match else ""


def what_each_wave_brought(con: sqlite3.Connection) -> list[dict]:
    """Every applicant, grouped by the wave whose link they arrived through.

    One row per person rather than a count, because "how many" is the
    question the winery asks first and "who, and where did they stop" is the
    one it asks immediately afterwards — and the second one is what somebody
    can act on. A person with an offer they never opened is a telephone call
    worth making; a bare number is not.

    People who came without a campaign link are kept, under a heading of
    their own. They are not noise: they are the word of mouth.
    """
    rows = con.execute(
        "SELECT a.id, a.created_at, a.first_name, a.last_name, a.email,"
        " a.city, a.state, a.referral, a.status AS application_status,"
        " o.status AS offer_status, o.expires_at,"
        " m.status AS membership_status,"
        # What they settled on if they joined, and what they asked for if
        # they did not — the second is the more useful of the two when the
        # point of the row is to telephone somebody.
        " COALESCE(m.tier_key, a.tier_interest) AS tier_key"
        " FROM application a"
        " LEFT JOIN offer o ON o.application_id = a.id"
        " LEFT JOIN membership m ON m.application_id = a.id"
        " ORDER BY a.created_at"
    ).fetchall()

    groups: dict[str, dict] = {}
    for row in rows:
        key = wave_of(row["referral"])
        group = groups.setdefault(key, {"source": key, "people": [],
                                        "members": 0, "waiting": 0})
        group["people"].append(row)
        if row["membership_status"] == "active":
            group["members"] += 1
        elif row["offer_status"] in ("sent", "opened"):
            # Still reachable: an offer that is open is somebody the winery
            # can still close, and that is the whole point of listing them.
            group["waiting"] += 1

    # Waves in their own order, and the people who came by another road last.
    def order(group: dict) -> tuple:
        return (group["source"] == "", group["source"])

    return sorted(groups.values(), key=order)
