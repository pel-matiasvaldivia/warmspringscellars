"""Unsubscribing, and the list of people who have.

This module is here because of a decision about how the first mailing goes
out. The letter is being sent through Microsoft's High Volume Email, which is
an authenticated SMTP relay with a raised sending limit and nothing else: no
suppression list, no bounce processing, no list-unsubscribe header, no
numbers. A mailing platform supplies all four. HVE supplies none, so the club
has to, and this is where three of the four live.

Two things are not negotiable before a single message goes out, and neither is
decoration:

  CAN-SPAM wants a working opt-out and a real postal address in the message.
  Gmail and Yahoo, since February 2024, want ONE-CLICK unsubscribe on bulk
  mail — RFC 8058, which means a POST that acts immediately, with no
  confirmation page and no sign-in — and they junk a bulk sender who does not
  have it.

So the unsubscribe link has to work for a stranger, with no session, forever.
That shapes everything here:

  * The token is signed but NOT timed. An opt-out link that expires is an
    opt-out that stops working, which is the one failure mode that is both
    illegal and unforgivable. It is signed so that nobody can unsubscribe
    somebody else by editing a URL, and it carries the campaign so the desk
    can see which letter produced each opt-out.
  * The address is the key, not the person. Unsubscribing is from the winery,
    not from one letter: the next campaign must not ask again.
  * Pressing it twice is not an error. The relay retries, the reader
    double-clicks, Gmail prefetches — all of which hit this endpoint, and all
    of which have to mean the same thing as pressing it once.

The signing key is SECRET_KEY, the same one that signs offer links, which has
one consequence worth writing down: rotating it invalidates every unsubscribe
link already in somebody's inbox. .env.example says so.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from itsdangerous import BadSignature, URLSafeSerializer

from .db import now

# Distinct from the salt on offer links, so a token minted for one purpose
# cannot be presented for the other.
SALT = "unsubscribe"

# How an address came to be on the list. Not equal to each other: the first
# is somebody's decision, the second is a dead mailbox that a new address
# could replace, and the third is the most permanent of the three.
SOURCES = ("unsubscribe", "bounce", "complaint", "hand")


class BadToken(Exception):
    """The token was not minted by us, or not for this purpose."""


@dataclass(frozen=True)
class Recipient:
    """Who an unsubscribe token is about."""

    email: str
    campaign: str


def tidy(email: str) -> str:
    """The form an address is stored and compared in.

    One address, one row. The export arrives with 'Anita@Example.COM ' and
    'anita@example.com' in different rows, and if those two are different keys
    then unsubscribing is a coin toss.
    """
    return email.strip().lower()


def mint(email: str, campaign: str, secret: str) -> str:
    return URLSafeSerializer(secret, salt=SALT).dumps(
        {"e": tidy(email), "c": campaign})


def read(token: str, secret: str) -> Recipient:
    try:
        payload = URLSafeSerializer(secret, salt=SALT).loads(token)
    except BadSignature as exc:
        raise BadToken("that unsubscribe link is not one of ours") from exc
    if not isinstance(payload, dict) or not payload.get("e"):
        raise BadToken("that unsubscribe link carries no address")
    return Recipient(email=tidy(str(payload["e"])),
                     campaign=str(payload.get("c") or ""))


def link(base_url: str, email: str, campaign: str, secret: str) -> str:
    """The absolute URL that goes in the letter and in the header.

    `base_url` has no trailing slash — Settings strips it — so one is written
    here rather than hoped for.
    """
    return f"{base_url}/unsubscribe?t={mint(email, campaign, secret)}"


# ── the list ─────────────────────────────────────────────────────────────
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


def is_suppressed(con: sqlite3.Connection, email: str) -> bool:
    return con.execute("SELECT 1 FROM email_suppression WHERE email = ?",
                       (tidy(email),)).fetchone() is not None


def suppressed(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT email, created_at, source, campaign, detail FROM email_suppression "
        "ORDER BY created_at DESC").fetchall()


def addresses(con: sqlite3.Connection) -> set[str]:
    """The whole list, for a sender that has to check a thousand rows."""
    return {row["email"] for row in
            con.execute("SELECT email FROM email_suppression")}


# ── what went out ────────────────────────────────────────────────────────
def record(con: sqlite3.Connection, campaign: str, email: str, status: str,
           detail: str = "") -> None:
    """Note that a message was accepted by the relay, or was not.

    Written one message at a time rather than in a batch at the end: the
    whole point is to survive the send dying half way through, and a batch
    written at the end is lost in exactly the case it was written for.
    """
    if status not in ("sent", "failed"):
        raise ValueError(f"unknown send status: {status!r}")
    con.execute(
        "INSERT INTO campaign_send (campaign, email, created_at, status, detail) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (campaign, email) DO UPDATE SET "
        "  created_at = excluded.created_at, status = excluded.status, "
        "  detail = excluded.detail",
        (campaign, tidy(email), now(), status, detail or None),
    )


def sent_already(con: sqlite3.Connection, campaign: str) -> set[str]:
    """Addresses this campaign has already reached.

    Only the accepted ones. A row that failed is a row to try again — that is
    the difference between resuming a send and abandoning the people the relay
    happened to refuse the first time.
    """
    return {row["email"] for row in con.execute(
        "SELECT email FROM campaign_send WHERE campaign = ? AND status = 'sent'",
        (campaign,))}


def tally(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Per-campaign numbers, newest campaign first.

    The report HVE does not give. It says nothing about opens or clicks,
    which this club cannot measure and should not pretend to: it says how
    many the relay took, how many it refused, and how many people asked not
    to be written to again afterwards.
    """
    return con.execute(
        "SELECT s.campaign AS campaign,"
        "       SUM(s.status = 'sent')   AS sent,"
        "       SUM(s.status = 'failed') AS failed,"
        "       MIN(s.created_at)        AS started_at,"
        "       MAX(s.created_at)        AS finished_at,"
        "       (SELECT COUNT(*) FROM email_suppression u"
        "         WHERE u.campaign = s.campaign AND u.source = 'unsubscribe')"
        "                                AS unsubscribed"
        "  FROM campaign_send s GROUP BY s.campaign"
        " ORDER BY MAX(s.created_at) DESC").fetchall()
