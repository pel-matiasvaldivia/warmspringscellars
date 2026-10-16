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

CREATE TABLE IF NOT EXISTS offer (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES application(id),
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'sent',
    opened_at      TEXT,
    accepted_at    TEXT,
    allowed_tiers  TEXT NOT NULL          -- JSON array of tier keys
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
    provider_subscription_id TEXT,
    paused_until    TEXT,
    cancelled_at    TEXT
);
CREATE INDEX IF NOT EXISTS membership_status ON membership(status);
CREATE UNIQUE INDEX IF NOT EXISTS membership_subscription
    ON membership(provider_subscription_id) WHERE provider_subscription_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS "release" (
    id         INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    name       TEXT NOT NULL UNIQUE,      -- 'Spring 2026'
    ships_on   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'planned'
);

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


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)

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
