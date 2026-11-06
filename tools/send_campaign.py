#!/usr/bin/env python3
"""Send one wave of a campaign through Microsoft High Volume Email.

    python3 tools/send_campaign.py <wave.csv> --verify
    python3 tools/send_campaign.py <wave.csv>                 # renders, sends nothing
    python3 tools/send_campaign.py <wave.csv> --seed you@example.com
    python3 tools/send_campaign.py <wave.csv> --send

NOTHING IS SENT WITHOUT --send. The default is a dry run that renders every
message, checks every address against the suppression list and prints what it
would do, because the first thing anybody wants from a tool pointed at nine
hundred strangers is to watch it not fire.

── What HVE is, and what this file has to make up for ──────────────────────

High Volume Email is an authenticated SMTP relay with a raised sending limit.
That is the whole product. It is not a mailing platform, and a mailing
platform's four jobs land here instead:

  a suppression list that actually suppresses   campaign.py, checked below
  list-unsubscribe headers                      built per message, below
  bounce handling                               the envelope sender, below
  per-wave numbers                              campaign_send, /admin/campaigns

Microsoft's own guidance points at Azure Communication Services or a
third-party provider for external bulk mail, and mail-templates/HVE.md sets
out that comparison and what was decided. This file is the honest version of
the decision that was taken: if the relay will not do these four things, the
club does them, and does them before the first message rather than after the
first complaint.

── What it will refuse to do ───────────────────────────────────────────────

  * Send with SECRET_KEY unset. The unsubscribe links would be signed with a
    key that dies with this process, so every one of them would be rejected
    by the server that has to honour them. An unsubscribe link that does not
    work is worse than no letter at all.
  * Send without a suppression list it can actually read.
  * Send a message with an unfilled {{ merge field }} in it.
  * Send to the same address twice in one campaign.

The wave CSV is whatever tools/plan_invitations.py wrote. It carries names and
addresses: it lives outside this repository and nothing from it is committed.
"""

from __future__ import annotations

import argparse
import csv
import email.policy
import os
import re
import smtplib
import sys
import time
from dataclasses import dataclass
from email.headerregistry import HeaderRegistry, UnstructuredHeader
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "api"))

from app import campaign                                      # noqa: E402
from app.config import settings                               # noqa: E402
from app.db import Database                                   # noqa: E402

TEMPLATES = REPO / "mail-templates"
DEFAULT_LETTER = "new-kid-on-the-block"
DEFAULT_SUBJECT = "The New Kid on the Block"

# Microsoft's submission endpoint for HVE accounts. Deliberately NOT
# smtp.office365.com: that is ordinary mailbox submission, and an HVE
# account is not a mailbox — it will be refused there, with a message about
# the credentials rather than about the host.
HVE_HOST = "smtp-hve.office365.com"
HVE_PORT = 587

LEFTOVER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
RULE = "-" * 72


class _Unfolded(UnstructuredHeader):
    """A header emitted on one line, exactly as it was set.

    Here for one header and one reason. List-Unsubscribe carries a signed URL
    with no spaces in it, which is longer than a header line; Python's default
    policy cannot fold a line with no fold points, so it falls back to RFC
    2047 encoded words and emits

        List-Unsubscribe: =?utf-8?q?=3Chttps=3A//warmspring…?=

    That is not a List-Unsubscribe header. Encoded words are not allowed in a
    structured header, no receiver parses it, and the one-click unsubscribe
    that Gmail and Yahoo require would be silently absent from every message
    in the wave — while the send itself looked perfectly healthy. Folding is
    turned off for this header instead: a long single-line List-Unsubscribe is
    what every real sender emits.
    """

    def fold(self, *, policy) -> str:
        return f"{self.name}: {self}{policy.linesep}"


_REGISTRY = HeaderRegistry()
_REGISTRY.map_to_type("list-unsubscribe", _Unfolded)
POLICY = email.policy.SMTP.clone(header_factory=_REGISTRY)


# ── the letter ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Letter:
    subject: str
    html: str
    text: str

    @classmethod
    def load(cls, name: str, subject: str) -> "Letter":
        html = (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")
        text = (TEMPLATES / f"{name}.txt").read_text(encoding="utf-8")
        # The plain-text file opens with a note to whoever edits it. The
        # reader must never see it, so everything up to the first full rule
        # comes off — and the rule has to be there, or the note would ship.
        before, found, after = text.partition(RULE)
        if not found:
            raise SystemExit(f"{name}.txt has no '{RULE[:12]}…' rule to cut the "
                             "editor's note from the letter")
        return cls(subject=subject, html=html, text=after.lstrip("\n"))

    def render(self, fields: dict[str, str]) -> tuple[str, str]:
        html, text = self.html, self.text
        for key, value in fields.items():
            html = html.replace("{{" + key + "}}", value)
            text = text.replace("{{" + key + "}}", value)
        # Whatever is still in braces was never going to be filled by anybody.
        # A letter that greets somebody as "{{first_name}}" is the kind of
        # mistake a list remembers, so it is a refusal and not a warning.
        missing = sorted(set(LEFTOVER.findall(html)) | set(LEFTOVER.findall(text)))
        if missing:
            raise SystemExit("unfilled merge fields: " + ", ".join(missing))
        return html, text


# ── the relay ────────────────────────────────────────────────────────────
class Relay:
    """One connection, held open across the wave.

    A connection per message is three round trips of handshake for every
    letter and looks, from the far side, like three hundred separate clients
    rather than one sender working through a list. It reconnects when the
    relay drops it, which it will on a wave this size.
    """

    def __init__(self, host: str, port: int, user: str, password: str,
                 timeout: float = 30.0):
        self._host, self._port = host, port
        self._user, self._password = user, password
        self._timeout = timeout
        self._server: smtplib.SMTP | None = None

    def _connect(self) -> smtplib.SMTP:
        server = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        server.ehlo()
        server.starttls()
        server.ehlo()
        # HVE accounts authenticate with a username and a password. They are
        # not mailboxes and have no OAuth identity, which is why this is the
        # one place in the club that still does basic authentication.
        server.login(self._user, self._password)
        self._server = server
        return server

    def send(self, msg: EmailMessage, envelope_from: str) -> None:
        for attempt in (1, 2):
            try:
                server = self._server or self._connect()
                server.send_message(msg, from_addr=envelope_from)
                return
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, OSError):
                # Dropped between messages: reconnect once and try again. A
                # second failure is the relay's answer and belongs on the page.
                self.close()
                if attempt == 2:
                    raise

    def verify(self) -> None:
        self._connect()
        print(f"Connected to {self._host}:{self._port}, STARTTLS accepted, "
              f"authenticated as {self._user}.")

    def close(self) -> None:
        if self._server is not None:
            try:
                self._server.quit()
            except Exception:                                  # noqa: BLE001
                pass
            self._server = None


# ── the list ─────────────────────────────────────────────────────────────
def read_wave(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    out, seen = [], set()
    for row in rows:
        email = campaign.tidy(row.get("email", ""))
        if not email or "@" not in email:
            continue
        if email in seen:
            continue                     # the planner dedupes; a hand-edited file may not
        seen.add(email)
        out.append({"email": email,
                    "first": (row.get("first") or "").strip(),
                    "link": (row.get("link") or "").strip()})
    return out


def read_suppressions(path: Path) -> set[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {campaign.tidy(row["email"])
                for row in csv.DictReader(handle) if row.get("email")}


def campaign_name(path: Path, given: str) -> str:
    """The name the send is recorded under, and it has to be stable.

    Stable because it is the key that makes a second run skip what the first
    run sent. Derived from the file name, so running the same wave again
    resumes it and running a different wave does not.
    """
    return given or path.stem


# ── the send ─────────────────────────────────────────────────────────────
def build(letter: Letter, person: dict[str, str], *, sender: str,
          reply_to: str, unsubscribe: str, postal: str,
          domain: str) -> EmailMessage:
    html, text = letter.render({
        # An empty name is likely in an export this old, and "Dear ," is
        # worse than the greeting the draft itself opened with.
        "first_name": person["first"] or "Wine Lover",
        "email": person["email"],
        "link": person["link"],
        "unsubscribe_url": unsubscribe,
        "postal_address": postal,
    })

    msg = EmailMessage(policy=POLICY)
    msg["From"] = sender
    msg["To"] = person["email"]
    msg["Subject"] = letter.subject
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["Reply-To"] = reply_to
    # The pair Gmail and Yahoo check for on bulk mail. The https URI must
    # accept a POST that acts immediately — /unsubscribe does — and the
    # mailto is the fallback for a client that will not POST.
    msg["List-Unsubscribe"] = (f"<{unsubscribe}>, "
                               f"<mailto:{reply_to}?subject=unsubscribe>")
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send one wave of a campaign through Microsoft HVE.",
        epilog="Nothing is sent without --send.")
    parser.add_argument("wave", type=Path, help="a CSV from tools/plan_invitations.py")
    parser.add_argument("--send", action="store_true",
                        help="actually send; without it this is a dry run")
    parser.add_argument("--verify", action="store_true",
                        help="connect and authenticate, send nothing, stop")
    parser.add_argument("--seed", metavar="ADDRESS",
                        help="send ONE copy here and stop, to read the headers")
    parser.add_argument("--campaign", default="",
                        help="the name this send is recorded under "
                             "(default: the CSV's file name)")
    parser.add_argument("--letter", default=DEFAULT_LETTER,
                        help=f"template pair in mail-templates (default: {DEFAULT_LETTER})")
    parser.add_argument("--subject", default=DEFAULT_SUBJECT)
    parser.add_argument("--suppressions", type=Path,
                        help="suppressions.csv from /admin/campaigns, when this "
                             "machine cannot see the club database")
    parser.add_argument("--per-minute", type=int, default=30,
                        help="messages a minute (default: 30)")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many messages")
    parser.add_argument("--postal", default="",
                        help="the winery's postal address for the footer; "
                             "required, CAN-SPAM")
    args = parser.parse_args()

    letter = Letter.load(args.letter, args.subject)
    name = campaign_name(args.wave, args.campaign)

    # ── the four refusals ────────────────────────────────────────────────
    if not settings.secret_from_env:
        raise SystemExit(
            "SECRET_KEY is unset, so every unsubscribe link this would mint is "
            "signed with a key that dies with this process and the server would "
            "reject all of them. Set it in .env — the same value the service "
            "runs with — before sending anything.")

    user = os.environ.get("HVE_USER", "").strip()
    password = os.environ.get("HVE_PASSWORD", "").strip()
    sender = os.environ.get("HVE_FROM", "").strip() or settings.mail_from
    # Where asynchronous bounces go. HVE reports a rejection in the SMTP
    # conversation when it can, but a mailbox that accepts and then gives up
    # replies hours later to the envelope sender, and if that is nobody's
    # mailbox the club never learns which addresses are dead.
    bounces = os.environ.get("HVE_BOUNCE_TO", "").strip() or sender
    host = os.environ.get("HVE_HOST", "").strip() or HVE_HOST
    port = int(os.environ.get("HVE_PORT", "").strip() or HVE_PORT)
    postal = args.postal or os.environ.get("CAMPAIGN_POSTAL_ADDRESS", "").strip()

    if not postal:
        raise SystemExit(
            "No postal address. CAN-SPAM requires a real physical address in a "
            "commercial message, and the footer has a {{postal_address}} waiting "
            "for it. Pass --postal or set CAMPAIGN_POSTAL_ADDRESS.")

    if args.suppressions:
        suppressed = read_suppressions(args.suppressions)
        where = f"{args.suppressions} ({len(suppressed)})"
        done: set[str] = set()
        db = None
    else:
        # Opening a Database creates the file if it is missing, and an empty
        # suppression list reads exactly like "nobody has ever unsubscribed".
        # That is the one failure here that writes to a person who asked us
        # not to, so a missing file is a refusal.
        if not settings.db_path.exists():
            raise SystemExit(
                f"No club database at {settings.db_path}, and no --suppressions "
                "file. One of the two has to be readable: sending with neither "
                "means sending to people who have asked us to stop. Download "
                "/admin/campaigns/suppressions.csv and pass it.")
        db = Database(settings.db_path)
        with db.connect() as con:
            suppressed = campaign.addresses(con)
            done = campaign.sent_already(con, name)
        where = f"{settings.db_path} ({len(suppressed)})"

    relay = Relay(host, port, user, password)
    if args.verify:
        if not user or not password:
            raise SystemExit("HVE_USER and HVE_PASSWORD are unset: nothing to "
                             "authenticate with.")
        relay.verify()
        relay.close()
        return

    # ── who is actually written to ───────────────────────────────────────
    people = read_wave(args.wave)
    queue, skipped_off, skipped_done = [], 0, 0
    for person in people:
        if person["email"] in suppressed:
            skipped_off += 1
        elif person["email"] in done:
            skipped_done += 1
        else:
            queue.append(person)
    if args.limit:
        queue = queue[:args.limit]

    def unsubscribe_for(person: dict[str, str]) -> str:
        return campaign.link(settings.public_base_url, person["email"], name,
                             settings.secret_key)

    print(f"campaign      {name}")
    print(f"letter        {args.letter} — \"{letter.subject}\"")
    print(f"from          {sender}   (bounces to {bounces})")
    print(f"relay         {host}:{port}" + (f" as {user}" if user else " — no HVE_USER"))
    print(f"suppressions  {where}")
    print(f"wave          {len(people)} rows → {len(queue)} to write to"
          f"  ({skipped_off} on the suppression list, "
          f"{skipped_done} already sent in this campaign)")

    if args.seed:
        person = {"email": campaign.tidy(args.seed), "first": "Wine Lover",
                  "link": f"{settings.public_base_url}/?src={name}-seed#join"}
        msg = build(letter, person, sender=sender, reply_to=settings.mail_reply_to,
                    unsubscribe=unsubscribe_for(person), postal=postal,
                    domain=sender.rpartition("@")[2])
        if not args.send:
            print("\n--- the seed message, not sent (add --send) ---")
            print(msg)
            return
        relay.send(msg, bounces)
        relay.close()
        print(f"\nSent one copy to {person['email']}. Read its headers: "
              "SPF, DKIM and DMARC all pass, and the mail client offers its own "
              "unsubscribe button. Only then send a wave.")
        return

    if not queue:
        print("\nNothing to send.")
        return

    if not args.send:
        # The dry run renders every message, because a merge field that is
        # only missing on row 212 is exactly the one a sample of three misses.
        for person in queue:
            build(letter, person, sender=sender, reply_to=settings.mail_reply_to,
                  unsubscribe=unsubscribe_for(person), postal=postal,
                  domain=sender.rpartition("@")[2])
        print(f"\nDry run: {len(queue)} messages render with every field filled, "
              "and nothing was sent.")
        print("First three addresses: "
              + ", ".join(p["email"] for p in queue[:3]))
        print("\nAdd --send to send them. Send a --seed to yourself first.")
        return

    if not user or not password:
        raise SystemExit("HVE_USER and HVE_PASSWORD are unset: nothing to "
                         "authenticate with.")
    if db is None:
        raise SystemExit(
            "Sending with --suppressions and no database means nothing records "
            "what went out, so a second run would write to everybody again. Run "
            "this where the club database is readable.")

    gap = 60.0 / max(args.per_minute, 1)
    sent = failed = 0
    print(f"\nSending {len(queue)} at {args.per_minute} a minute "
          f"(about {len(queue) * gap / 60:.0f} minutes). Ctrl-C stops it, and "
          "running it again resumes.\n")
    try:
        for index, person in enumerate(queue, start=1):
            msg = build(letter, person, sender=sender,
                        reply_to=settings.mail_reply_to,
                        unsubscribe=unsubscribe_for(person), postal=postal,
                        domain=sender.rpartition("@")[2])
            try:
                relay.send(msg, bounces)
            except Exception as exc:                           # noqa: BLE001
                failed += 1
                status, detail = "failed", f"{type(exc).__name__}: {exc}"
                print(f"  {index:4d}/{len(queue)}  {person['email']}  ✕ {detail}")
            else:
                sent += 1
                status, detail = "sent", ""
                print(f"  {index:4d}/{len(queue)}  {person['email']}")
            # Recorded one at a time, before the pause: the record exists to
            # survive this loop dying, and a batch written at the end is lost
            # in precisely that case.
            with db.transaction() as con:
                campaign.record(con, name, person["email"], status, detail)
            if index < len(queue):
                time.sleep(gap)
    except KeyboardInterrupt:
        print("\nStopped. Run the same command again to carry on where it left off.")
    finally:
        relay.close()

    print(f"\n{sent} accepted, {failed} refused. The numbers are at "
          "/admin/campaigns.")
    if failed:
        print("A refused message is not lost: run the same wave again and only "
              "the refusals are tried.")


if __name__ == "__main__":
    main()
