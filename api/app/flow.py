"""The commercial flow: every state change the club can make.

Routes do no business logic. They authenticate, parse, call one function here
and render the result, which is what keeps the rules in one readable place and
makes them testable without HTTP.

Transitions that are not in ALLOWED are refused. A flow that silently accepts
"deliver a shipment that was never picked" will eventually be asked to explain
an order nobody can reconstruct.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .catalogue import Tier, catalogue
from .config import SHIPPABLE_STATES, Settings
from .db import Database, log, next_in_sequence, now
from .payments import PaidEvent, PaymentProvider, as_metadata


class FlowError(RuntimeError):
    """A refused transition, or input the flow will not accept."""


ALLOWED: dict[str, dict[str, set[str]]] = {
    "application": {
        "pending": {"approved", "declined", "withdrawn"},
        "approved": set(),
        "declined": set(),
        "withdrawn": set(),
    },
    "offer": {
        "sent": {"opened", "accepted", "expired", "revoked"},
        "opened": {"accepted", "expired", "revoked"},
        "accepted": set(),
        "expired": {"revoked"},
        "revoked": set(),
    },
    "membership": {
        "active": {"paused", "cancelled"},
        "paused": {"active", "cancelled"},
        "cancelled": set(),
    },
    "order": {
        "pending_payment": {"paid", "failed", "cancelled"},
        "paid": {"fulfilled", "refunded"},
        "fulfilled": {"refunded"},
        "failed": {"pending_payment", "cancelled"},
        "refunded": set(),
        "cancelled": set(),
    },
    "shipment": {
        "ready": {"picked", "held"},
        "picked": {"in_transit", "held"},
        "in_transit": {"delivered", "returned"},
        "held": {"ready"},
        "delivered": set(),
        "returned": {"ready"},
    },
}


def guard(entity: str, current: str, target: str) -> None:
    allowed = ALLOWED.get(entity, {}).get(current)
    if allowed is None:
        raise FlowError(f"{entity} has no state {current!r}")
    if target not in allowed:
        raise FlowError(f"a {entity} cannot go from {current} to {target}")


@dataclass
class Flow:
    db: Database
    settings: Settings
    payments: PaymentProvider

    # ── offer links ──────────────────────────────────────────────────────
    @property
    def _signer(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.settings.secret_key, salt="wsc-offer")

    def offer_token(self, offer_id: int) -> str:
        return self._signer.dumps({"offer": offer_id})

    def read_offer_token(self, token: str) -> int:
        """The offer id inside a link, or FlowError if it will not verify.

        Two clocks: the signature carries its own age, and the offer row
        carries an expiry the winery can shorten. Both have to pass.
        """
        max_age = self.settings.offer_days * 86400
        try:
            data = self._signer.loads(token, max_age=max_age)
        except SignatureExpired as exc:
            raise FlowError("this invitation has expired") from exc
        except BadSignature as exc:
            raise FlowError("this invitation link is not valid") from exc
        return int(data["offer"])

    def offer_url(self, offer_id: int) -> str:
        return f"{self.settings.public_base_url}/club/offer/{self.offer_token(offer_id)}"

    # ── applications ─────────────────────────────────────────────────────
    def create_application(self, data: dict) -> int:
        required = ("first_name", "last_name", "email", "state")
        missing = [f for f in required if not str(data.get(f, "")).strip()]
        if missing:
            raise FlowError("missing required fields: " + ", ".join(missing))

        email = str(data["email"]).strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise FlowError("that email address does not look right")

        state = str(data["state"]).strip()
        if state not in SHIPPABLE_STATES:
            raise FlowError(
                f"we are not licensed to ship wine to {state} yet. "
                "Write to us and we will tell you the moment that changes."
            )

        tier_key = (data.get("tier_interest") or "").strip() or None
        if tier_key and catalogue().get(tier_key) is None:
            raise FlowError("that allocation does not exist")

        with self.db.transaction() as con:
            # A second request from the same address while one is open is the
            # same request, not a new one.
            open_row = con.execute(
                "SELECT id FROM application WHERE email = ? AND status = 'pending'", (email,)
            ).fetchone()
            if open_row:
                log(con, "application", int(open_row["id"]), "duplicate_request")
                return int(open_row["id"])

            cur = con.execute(
                "INSERT INTO application (created_at, status, first_name, last_name, email,"
                " phone, city, state, tier_interest, referral, note)"
                " VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now(), str(data["first_name"]).strip(), str(data["last_name"]).strip(), email,
                 (data.get("phone") or "").strip() or None,
                 (data.get("city") or "").strip() or None, state, tier_key,
                 (data.get("referral") or "").strip() or None,
                 (data.get("note") or "").strip() or None),
            )
            app_id = int(cur.lastrowid)
            log(con, "application", app_id, "received", email=email, state=state)
            return app_id

    def approve_application(self, app_id: int, actor: str,
                            allowed_tiers: list[str] | None = None) -> tuple[int, str]:
        """Approve, and open an offer. Returns (offer_id, url)."""
        allowed_tiers = allowed_tiers or [t.key for t in catalogue().selectable]
        unknown = [k for k in allowed_tiers if catalogue().get(k) is None]
        if unknown:
            raise FlowError("unknown tiers: " + ", ".join(unknown))
        if not allowed_tiers:
            raise FlowError("an offer with no tiers is not an offer")

        with self.db.transaction() as con:
            app = _require(con, "application", app_id)
            guard("application", app["status"], "approved")

            expires = datetime.now(timezone.utc) + timedelta(days=self.settings.offer_days)
            con.execute(
                "UPDATE application SET status = 'approved', decided_at = ?, decided_by = ?"
                " WHERE id = ?", (now(), actor, app_id),
            )
            cur = con.execute(
                "INSERT INTO offer (application_id, created_at, expires_at, status, allowed_tiers)"
                " VALUES (?, ?, ?, 'sent', ?)",
                (app_id, now(), expires.isoformat(timespec="seconds"), json.dumps(allowed_tiers)),
            )
            offer_id = int(cur.lastrowid)
            log(con, "application", app_id, "approved", actor=actor, offer_id=offer_id)
            log(con, "offer", offer_id, "created", actor=actor, tiers=allowed_tiers)

        return offer_id, self.offer_url(offer_id)

    def decline_application(self, app_id: int, actor: str, reason: str = "") -> None:
        with self.db.transaction() as con:
            app = _require(con, "application", app_id)
            guard("application", app["status"], "declined")
            con.execute(
                "UPDATE application SET status = 'declined', decided_at = ?, decided_by = ?,"
                " decline_reason = ? WHERE id = ?", (now(), actor, reason or None, app_id),
            )
            log(con, "application", app_id, "declined", actor=actor, reason=reason)

    # ── offers ───────────────────────────────────────────────────────────
    def open_offer(self, token: str) -> tuple[sqlite3.Row, sqlite3.Row, list[Tier]]:
        offer_id = self.read_offer_token(token)
        with self.db.transaction() as con:
            offer = _require(con, "offer", offer_id)
            app = _require(con, "application", int(offer["application_id"]))

            if offer["status"] in {"revoked", "expired"}:
                raise FlowError("this invitation is no longer open")
            if offer["status"] == "accepted":
                raise FlowError("this invitation has already been accepted")
            if datetime.fromisoformat(offer["expires_at"]) < datetime.now(timezone.utc):
                con.execute("UPDATE offer SET status = 'expired' WHERE id = ?", (offer_id,))
                log(con, "offer", offer_id, "expired")
                raise FlowError("this invitation has expired")

            if offer["status"] == "sent":
                con.execute(
                    "UPDATE offer SET status = 'opened', opened_at = ? WHERE id = ?",
                    (now(), offer_id),
                )
                log(con, "offer", offer_id, "opened")
                offer = _require(con, "offer", offer_id)

        keys = json.loads(offer["allowed_tiers"])
        tiers = [t for t in (catalogue().get(k) for k in keys) if t is not None]
        return offer, app, tiers

    def start_checkout(self, token: str, tier_key: str) -> str:
        offer, app, tiers = self.open_offer(token)
        tier = next((t for t in tiers if t.key == tier_key), None)
        if tier is None:
            raise FlowError("that allocation was not offered to you")

        reference = f"offer-{offer['id']}-{tier.key}"
        session = self.payments.create_checkout(
            reference=reference,
            description=f"Warm Springs Cellars — {tier.name}",
            amount_cents=tier.total_cents,
            currency=catalogue().currency,
            interval_months=12 // max(catalogue().shipments_per_year, 1),
            customer_email=app["email"],
            success_url=f"{self.settings.public_base_url}/club/offer/{token}/welcome",
            cancel_url=f"{self.settings.public_base_url}/club/offer/{token}?checkout=cancelled",
            metadata=as_metadata(offer_id=offer["id"], application_id=app["id"],
                                 tier_key=tier.key),
        )
        with self.db.transaction() as con:
            log(con, "offer", int(offer["id"]), "checkout_started",
                tier=tier.key, session=session.id, provider=self.payments.name)
        return session.url

    # ── the webhook: where money becomes a membership ────────────────────
    def accept_payment(self, event: PaidEvent) -> int | None:
        """Turn a confirmed payment into a membership, order, invoice and shipment.

        Idempotent on the provider's event id: a provider that retries a
        delivery, and they all do, must not create a second membership.
        """
        offer_id = int(event.metadata.get("offer_id", 0) or 0)
        tier_key = event.metadata.get("tier_key", "")
        tier = catalogue().get(tier_key)
        if not offer_id or tier is None:
            raise FlowError("payment carried no offer or no known tier")

        with self.db.transaction() as con:
            seen = con.execute(
                "SELECT 1 FROM webhook_seen WHERE provider = ? AND event_id = ?",
                (self.payments.name, event.event_id),
            ).fetchone()
            if seen:
                return None
            con.execute(
                "INSERT INTO webhook_seen (provider, event_id, received_at) VALUES (?, ?, ?)",
                (self.payments.name, event.event_id, now()),
            )

            offer = _require(con, "offer", offer_id)
            app = _require(con, "application", int(offer["application_id"]))
            if offer["status"] == "accepted":
                return None
            guard("offer", offer["status"], "accepted")

            con.execute(
                "UPDATE offer SET status = 'accepted', accepted_at = ? WHERE id = ?",
                (now(), offer_id),
            )

            cur = con.execute(
                "INSERT INTO membership (application_id, created_at, status, tier_key, email,"
                " first_name, last_name, ship_state, provider, provider_customer_id,"
                " provider_subscription_id)"
                " VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)",
                (app["id"], now(), tier.key, app["email"], app["first_name"], app["last_name"],
                 app["state"], self.payments.name, event.customer_id or None,
                 event.subscription_id or None),
            )
            membership_id = int(cur.lastrowid)
            log(con, "offer", offer_id, "accepted", membership_id=membership_id)
            log(con, "membership", membership_id, "created",
                tier=tier.key, provider=self.payments.name)

            order_id = self._create_order(con, membership_id, tier, release_id=None)
            self._mark_paid(con, order_id, event.session_id)
            self._issue_invoice(con, order_id)
            self._create_shipment(con, order_id)

        return membership_id

    # ── orders, invoices, shipments ──────────────────────────────────────
    def _create_order(self, con: sqlite3.Connection, membership_id: int, tier: Tier,
                      release_id: int | None) -> int:
        year = date.today().year
        number = next_in_sequence(con, f"order-{year}")
        reference = f"WSC-{year}-{number:04d}"

        subtotal = tier.price_cents
        shipping = tier.shipping_cents
        # Tax is not computed. DTC wine tax depends on the destination and is
        # not something to guess at — see FLOW.md.
        tax = 0
        total = subtotal + shipping + tax

        cur = con.execute(
            "INSERT INTO \"order\" (created_at, reference, membership_id, release_id, status,"
            " tier_key, subtotal_cents, discount_cents, shipping_cents, tax_cents, total_cents,"
            " currency) VALUES (?, ?, ?, ?, 'pending_payment', ?, ?, 0, ?, ?, ?, ?)",
            (now(), reference, membership_id, release_id, tier.key, subtotal, shipping, tax,
             total, catalogue().currency),
        )
        order_id = int(cur.lastrowid)
        con.execute(
            "INSERT INTO order_line (order_id, description, quantity, unit_cents, total_cents)"
            " VALUES (?, ?, ?, ?, ?)",
            (order_id, f"{tier.name} — {tier.bottles} bottles", 1, subtotal, subtotal),
        )
        if shipping:
            con.execute(
                "INSERT INTO order_line (order_id, description, quantity, unit_cents, total_cents)"
                " VALUES (?, 'Temperature-controlled shipping', 1, ?, ?)",
                (order_id, shipping, shipping),
            )
        log(con, "order", order_id, "created", reference=reference, total_cents=total)
        return order_id

    def _mark_paid(self, con: sqlite3.Connection, order_id: int, payment_id: str) -> None:
        order = _require(con, "order", order_id)
        guard("order", order["status"], "paid")
        con.execute(
            "UPDATE \"order\" SET status = 'paid', paid_at = ?, provider_payment_id = ?"
            " WHERE id = ?", (now(), payment_id or None, order_id),
        )
        log(con, "order", order_id, "paid", payment=payment_id)

    def _issue_invoice(self, con: sqlite3.Connection, order_id: int) -> int:
        order = _require(con, "order", order_id)
        year = date.today().year
        number = next_in_sequence(con, f"invoice-{year}")
        reference = f"WSC-INV-{year}-{number:04d}"
        cur = con.execute(
            "INSERT INTO invoice (order_id, number, issued_at, status, total_cents, currency)"
            " VALUES (?, ?, ?, 'issued', ?, ?)",
            (order_id, reference, now(), order["total_cents"], order["currency"]),
        )
        invoice_id = int(cur.lastrowid)
        log(con, "invoice", invoice_id, "issued", number=reference, order_id=order_id)
        return invoice_id

    def _create_shipment(self, con: sqlite3.Connection, order_id: int) -> int:
        cur = con.execute(
            "INSERT INTO shipment (order_id, created_at, status) VALUES (?, ?, 'ready')",
            (order_id, now()),
        )
        shipment_id = int(cur.lastrowid)
        log(con, "shipment", shipment_id, "ready", order_id=order_id)
        return shipment_id

    def advance_shipment(self, shipment_id: int, target: str, actor: str,
                         carrier: str = "", tracking: str = "", reason: str = "") -> None:
        with self.db.transaction() as con:
            shipment = _require(con, "shipment", shipment_id)
            guard("shipment", shipment["status"], target)

            fields = {"status": target}
            if target == "in_transit":
                if not tracking:
                    raise FlowError("a shipment does not go in transit without a tracking number")
                fields.update(carrier=carrier or "unknown", tracking=tracking, shipped_at=now())
            if target == "delivered":
                fields["delivered_at"] = now()
            if target == "held":
                fields["held_reason"] = reason or "held"
            if target == "ready":
                fields["held_reason"] = None

            sets = ", ".join(f"{k} = ?" for k in fields)
            con.execute(f"UPDATE shipment SET {sets} WHERE id = ?",
                        (*fields.values(), shipment_id))
            log(con, "shipment", shipment_id, target, actor=actor,
                carrier=carrier or None, tracking=tracking or None, reason=reason or None)

            if target == "delivered":
                order = _require(con, "order", int(shipment["order_id"]))
                if order["status"] == "paid":
                    con.execute("UPDATE \"order\" SET status = 'fulfilled' WHERE id = ?",
                                (order["id"],))
                    log(con, "order", int(order["id"]), "fulfilled")

    # ── memberships ──────────────────────────────────────────────────────
    def set_membership_status(self, membership_id: int, target: str, actor: str) -> None:
        with self.db.transaction() as con:
            row = _require(con, "membership", membership_id)
            guard("membership", row["status"], target)
            extra = ", cancelled_at = ?" if target == "cancelled" else ""
            args: tuple = (target, membership_id)
            if extra:
                args = (target, now(), membership_id)
            con.execute(f"UPDATE membership SET status = ?{extra} WHERE id = ?", args)
            log(con, "membership", membership_id, target, actor=actor)

    # ── releases ─────────────────────────────────────────────────────────
    def create_release(self, name: str, ships_on: str) -> int:
        with self.db.transaction() as con:
            cur = con.execute(
                "INSERT INTO \"release\" (created_at, name, ships_on, status)"
                " VALUES (?, ?, ?, 'planned')", (now(), name, ships_on),
            )
            release_id = int(cur.lastrowid)
            log(con, "release", release_id, "created", name=name, ships_on=ships_on)
            return release_id

    def generate_release_orders(self, release_id: int, actor: str) -> list[int]:
        """One order per active membership. Safe to run twice.

        The (membership, release) pair is unique in the schema, so a second run
        skips anyone already covered rather than billing them again.
        """
        created: list[int] = []
        with self.db.transaction() as con:
            _require(con, "release", release_id)
            members = con.execute(
                "SELECT * FROM membership WHERE status = 'active' ORDER BY id"
            ).fetchall()
            for member in members:
                exists = con.execute(
                    "SELECT 1 FROM \"order\" WHERE membership_id = ? AND release_id = ?",
                    (member["id"], release_id),
                ).fetchone()
                if exists:
                    continue
                tier = catalogue().get(member["tier_key"])
                if tier is None:
                    log(con, "membership", int(member["id"]), "skipped_unknown_tier",
                        tier=member["tier_key"])
                    continue
                created.append(self._create_order(con, int(member["id"]), tier, release_id))
            con.execute("UPDATE \"release\" SET status = 'open' WHERE id = ?", (release_id,))
            log(con, "release", release_id, "orders_generated", actor=actor, count=len(created))
        return created


def _require(con: sqlite3.Connection, table: str, row_id: int) -> sqlite3.Row:
    quoted = f'"{table}"'
    row = con.execute(f"SELECT * FROM {quoted} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise FlowError(f"no {table} with id {row_id}")
    return row
