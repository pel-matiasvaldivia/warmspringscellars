"""SQLite storage.

SQLite because the whole club is a few hundred members and a few thousand
bottles; a row never contends with another, and a single file is one less
service to run and back up. Money is stored in integer cents, never floats.
Every state change also writes to `event`, so an order can be reconstructed
from what happened rather than from what the record says now.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS application (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    email         TEXT NOT NULL,
    phone         TEXT,
    city          TEXT,
    state         TEXT NOT NULL,
    tier_interest TEXT,
    referral      TEXT,
    note          TEXT,
    decided_at    TEXT,
    decided_by    TEXT,
    decline_reason TEXT
);
CREATE INDEX IF NOT EXISTS application_status ON application(status, created_at DESC);
CREATE INDEX IF NOT EXISTS application_email ON application(email);

-- The address columns hold where the member asked for the wine to go, taken
-- on the way to checkout. They live here rather than on the membership
-- because the membership does not exist until the payment clears, and the
-- address is given before that; accept_payment copies them across.
CREATE TABLE IF NOT EXISTS offer (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES application(id),
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'sent',
    opened_at      TEXT,
    accepted_at    TEXT,
    allowed_tiers  TEXT NOT NULL,         -- JSON array of tier keys
    ship_line1     TEXT,
    ship_line2     TEXT,
    ship_city      TEXT,
    ship_state     TEXT,
    ship_postcode  TEXT
);
CREATE INDEX IF NOT EXISTS offer_application ON offer(application_id);

CREATE TABLE IF NOT EXISTS membership (
    id              INTEGER PRIMARY KEY,
    application_id  INTEGER NOT NULL REFERENCES application(id),
    created_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    tier_key        TEXT NOT NULL,
    email           TEXT NOT NULL,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    ship_line1      TEXT,
    ship_line2      TEXT,
    ship_city       TEXT,
    ship_state      TEXT NOT NULL,
    ship_postcode   TEXT,
    provider        TEXT,                 -- 'stripe' | 'fake'
    provider_customer_id     TEXT,
    -- The card itself stays with the provider; this is its handle, and what
    -- a release is charged against. A membership without one cannot be
    -- charged, which the despatch screens say rather than discovering it on
    -- the morning the wine goes out.
    provider_payment_method  TEXT,
    -- Unused since the club stopped running subscriptions, and kept because
    -- memberships created under one still carry theirs.
    provider_subscription_id TEXT,
    paused_until    TEXT,
    cancelled_at    TEXT
);
CREATE INDEX IF NOT EXISTS membership_status ON membership(status);
CREATE UNIQUE INDEX IF NOT EXISTS membership_subscription
    ON membership(provider_subscription_id) WHERE provider_subscription_id IS NOT NULL;

-- People the winery cannot ship to yet, who asked to be told when that
-- changes. Deliberately not an application: an application is a request for
-- an allocation and gets an answer, and this cannot be answered until a
-- licence arrives that nobody here controls. When the state opens, a row
-- becomes an ordinary application and goes through the ordinary queue.
CREATE TABLE IF NOT EXISTS waiting (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'waiting',  -- waiting | invited | dropped
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT NOT NULL,
    state       TEXT NOT NULL,
    city        TEXT,
    referral    TEXT,
    note        TEXT,
    invited_at  TEXT,
    application_id INTEGER REFERENCES application(id)
);
CREATE INDEX IF NOT EXISTS waiting_state ON waiting(status, state, created_at);
-- One row per person per state while they are still waiting: the same person
-- asking twice is the same request, as it is on the application form.
CREATE UNIQUE INDEX IF NOT EXISTS waiting_open
    ON waiting(email, state) WHERE status = 'waiting';

CREATE TABLE IF NOT EXISTS "release" (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    name       TEXT NOT NULL UNIQUE,      -- 'Spring 2026'
    ships_on   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'planned',
    -- Whether this one goes out with ice packs. Defaulted from the shipping
    -- month and then the desk's to change: whether a box needs one is a
    -- decision about the forecast at the far end, which no calendar knows
    -- months ahead. The member is never billed for it either way.
    ice_packs  INTEGER NOT NULL DEFAULT 0
);

-- The wines, as the club refers to them. Not the winery's whole production
-- record: the few bottlings a release can be composed from.
CREATE TABLE IF NOT EXISTS wine (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    sku        TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,             -- '2021 Estate Cabernet Sauvignon'
    vintage    INTEGER,
    status     TEXT NOT NULL DEFAULT 'active'   -- active | retired
);
CREATE INDEX IF NOT EXISTS wine_status ON wine(status, name);

-- Bottles in and bottles out, one signed row each, and on-hand is their sum.
-- One summing rule and no stored balance to disagree with it: a physical
-- count is not a figure that overwrites anything, it is an adjustment for the
-- difference, which is why a miscount leaves a trail instead of erasing one.
CREATE TABLE IF NOT EXISTS stock_move (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    wine_id    INTEGER NOT NULL REFERENCES wine(id),
    bottles    INTEGER NOT NULL,          -- + received or counted in, - committed
    kind       TEXT NOT NULL,             -- received | count | allocated | returned
    release_id INTEGER REFERENCES "release"(id),
    note       TEXT,
    actor      TEXT NOT NULL DEFAULT 'system'
);
CREATE INDEX IF NOT EXISTS stock_move_wine ON stock_move(wine_id, id);
CREATE INDEX IF NOT EXISTS stock_move_release ON stock_move(release_id)
    WHERE release_id IS NOT NULL;

-- What one shipment of one tier is made of, in this release. Per release
-- because spring's six bottles are not autumn's six, and per tier because
-- that is the promise the catalogue makes — twelve bottles means twelve, and
-- a composition that adds up to eleven is caught before the wine is packed.
CREATE TABLE IF NOT EXISTS release_item (
    id         INTEGER PRIMARY KEY,
    release_id INTEGER NOT NULL REFERENCES "release"(id) ON DELETE CASCADE,
    tier_key   TEXT NOT NULL,
    wine_id    INTEGER NOT NULL REFERENCES wine(id),
    bottles    INTEGER NOT NULL,
    UNIQUE (release_id, tier_key, wine_id)
);
CREATE INDEX IF NOT EXISTS release_item_release ON release_item(release_id, tier_key);

CREATE TABLE IF NOT EXISTS "order" (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT NOT NULL,
    reference      TEXT NOT NULL UNIQUE,  -- WSC-2026-0001
    membership_id  INTEGER NOT NULL REFERENCES membership(id),
    release_id     INTEGER REFERENCES "release"(id),
    status         TEXT NOT NULL DEFAULT 'pending_payment',
    tier_key       TEXT NOT NULL,
    subtotal_cents INTEGER NOT NULL,
    discount_cents INTEGER NOT NULL DEFAULT 0,
    shipping_cents INTEGER NOT NULL DEFAULT 0,
    tax_cents      INTEGER NOT NULL DEFAULT 0,
    total_cents    INTEGER NOT NULL,
    currency       TEXT NOT NULL,
    provider_payment_id TEXT,
    -- Why the last charge did not go through, in the provider's own words,
    -- so the desk can tell a dead card from a bank asking for the member.
    charge_error   TEXT,
    paid_at        TEXT,
    UNIQUE (membership_id, release_id)
);
CREATE INDEX IF NOT EXISTS order_status ON "order"(status, created_at DESC);

CREATE TABLE IF NOT EXISTS order_line (
    id          INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES "order"(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    quantity    INTEGER NOT NULL,
    unit_cents  INTEGER NOT NULL,
    total_cents INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS order_line_order ON order_line(order_id);

CREATE TABLE IF NOT EXISTS invoice (
    id          INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL UNIQUE REFERENCES "order"(id),
    number      TEXT NOT NULL UNIQUE,
    issued_at   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'issued',
    total_cents INTEGER NOT NULL,
    currency    TEXT NOT NULL,
    pdf_path    TEXT
);

CREATE TABLE IF NOT EXISTS shipment (
    id           INTEGER PRIMARY KEY,
    order_id     INTEGER NOT NULL UNIQUE REFERENCES "order"(id),
    created_at   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'ready',
    carrier      TEXT,
    tracking     TEXT,
    held_reason  TEXT,
    shipped_at   TEXT,
    delivered_at TEXT
);
CREATE INDEX IF NOT EXISTS shipment_status ON shipment(status);

-- Numbering that no code path can reuse: the row is updated inside the same
-- transaction that writes the invoice, so a crash cannot skip or repeat.
CREATE TABLE IF NOT EXISTS sequence (
    name TEXT PRIMARY KEY,
    next INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS event (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    subject    TEXT NOT NULL,            -- 'order', 'application', …
    subject_id INTEGER NOT NULL,
    action     TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT 'system',
    detail     TEXT                      -- JSON
);
CREATE INDEX IF NOT EXISTS event_subject ON event(subject, subject_id, id);

-- Webhook deliveries, so a provider retrying the same event cannot charge or
-- ship twice.
CREATE TABLE IF NOT EXISTS webhook_seen (
    provider    TEXT NOT NULL,
    event_id    TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (provider, event_id)
);

-- Documents waiting to reach the accounting system. Written in the same
-- transaction as the invoice they describe, so the books can never be asked
-- to record an order that was rolled back, and an order that committed always
-- has its paperwork queued even if QuickBooks was down at the time.
CREATE TABLE IF NOT EXISTS ledger_task (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,            -- 'invoice' | 'payment'
    subject_id  INTEGER NOT NULL,         -- invoice.id in both cases
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    external_id TEXT,                     -- the ledger's own id for the document
    provider    TEXT,
    synced_at   TEXT,
    UNIQUE (kind, subject_id)
);
CREATE INDEX IF NOT EXISTS ledger_task_status ON ledger_task(status, id);

-- Addresses that must not be written to again. One row per address, and the
-- address is the key: a person who unsubscribes has unsubscribed from the
-- winery, not from one letter, and the next campaign has no business asking
-- the question again.
--
-- This table exists because High Volume Email does not have it. A mailing
-- platform keeps a suppression list that actually suppresses, and refuses the
-- send itself; HVE is an SMTP relay that will deliver whatever it is handed,
-- as many times as it is handed it. So the list lives here and
-- tools/send_campaign.py checks it before every message.
--
-- `source` records how the address got here, because the reasons are not
-- equal: 'unsubscribe' is someone's decision and is permanent, 'bounce' is a
-- dead mailbox and could be revived if the person gives a new one, 'complaint'
-- is a spam report and is the most permanent of the three.
CREATE TABLE IF NOT EXISTS email_suppression (
    email       TEXT PRIMARY KEY,         -- lower-cased on the way in
    created_at  TEXT NOT NULL,
    source      TEXT NOT NULL,            -- unsubscribe | bounce | complaint | hand
    campaign    TEXT,                     -- the send it came from, when known
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS email_suppression_when
    ON email_suppression(created_at DESC);

-- One row per address per campaign, written the moment a message is accepted
-- by the relay. Two jobs, and the second is the one that matters at three in
-- the afternoon on day four:
--
--   * a send that dies half way through can be run again and will skip what
--     already went out, because this table is the record of what went out;
--   * the per-wave numbers nobody gets from HVE — how many were accepted, how
--     many were refused and what the relay said when it refused them.
--
-- status is 'sent' when the relay took it and 'failed' when it did not; a
-- failed row carries the server's own sentence, which is the only text that
-- ever explains a Microsoft rejection.
CREATE TABLE IF NOT EXISTS campaign_send (
    id         INTEGER PRIMARY KEY,
    campaign   TEXT NOT NULL,
    email      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status     TEXT NOT NULL,             -- sent | failed
    detail     TEXT,
    UNIQUE (campaign, email)
);
CREATE INDEX IF NOT EXISTS campaign_send_campaign
    ON campaign_send(campaign, status);

-- OAuth tokens for the accounting system. In the database rather than the
-- environment because Intuit rotates the refresh token on every refresh: a
-- value baked into .env would be stale an hour after the first sync.
CREATE TABLE IF NOT EXISTS oauth_token (
    provider            TEXT PRIMARY KEY,
    realm_id            TEXT,
    access_token        TEXT,
    refresh_token       TEXT,
    access_expires_at   TEXT,
    refresh_expires_at  TEXT,
    updated_at          TEXT NOT NULL
);
"""


# Columns added to a table after the first deployment. CREATE TABLE IF NOT
# EXISTS leaves a table that already exists untouched, so a database made
# before this release would never grow them, and SQLite has no ADD COLUMN IF
# NOT EXISTS — each one is asked for and added if it is missing. Adding a
# nullable column is the only migration that is safe to run on every start;
# anything that rewrites data belongs in a script that is run once, on purpose.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "offer": {
        "ship_line1": "TEXT", "ship_line2": "TEXT", "ship_city": "TEXT",
        "ship_state": "TEXT", "ship_postcode": "TEXT",
    },
    "membership": {"provider_payment_method": "TEXT"},
    "release": {"ice_packs": "INTEGER NOT NULL DEFAULT 0"},
    "order": {"charge_error": "TEXT"},
}


def migrate(con: sqlite3.Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        present = {row["name"] for row in con.execute(f'PRAGMA table_info("{table}")')}
        for name, declaration in columns.items():
            if name not in present:
                con.execute(f'ALTER TABLE "{table}" ADD COLUMN {name} {declaration}')


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)
            migrate(con)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One unit of work. Anything that writes more than one row uses this."""
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
            except Exception:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")

    # ── helpers ──────────────────────────────────────────────────────────
    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        with self.connect() as con:
            return con.execute(sql, args).fetchone()

    def all(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(sql, args).fetchall()


def log(con: sqlite3.Connection, subject: str, subject_id: int, action: str,
        actor: str = "system", **detail: Any) -> None:
    con.execute(
        "INSERT INTO event (created_at, subject, subject_id, action, actor, detail) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now(), subject, subject_id, action, actor,
         json.dumps(detail, default=str) if detail else None),
    )


def next_in_sequence(con: sqlite3.Connection, name: str, start: int = 1) -> int:
    """Take the next number in a sequence, inside the caller's transaction.

    Called from within a transaction that also writes the row using the number,
    so the two commit together: an accountant asking why 1043 is missing should
    never have to be told it was a crash.
    """
    row = con.execute("SELECT next FROM sequence WHERE name = ?", (name,)).fetchone()
    if row is None:
        con.execute("INSERT INTO sequence (name, next) VALUES (?, ?)", (name, start + 1))
        return start
    value = int(row["next"])
    con.execute("UPDATE sequence SET next = ? WHERE name = ?", (value + 1, name))
    return value
