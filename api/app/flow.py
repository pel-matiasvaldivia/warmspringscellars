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
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .accounting.queue import enqueue
from .catalogue import Tier, catalogue
from .config import SHIPPABLE_STATES, US_STATES, Settings
from .db import Database, log, next_in_sequence, now
from .payments import CardSaved, PaymentProvider, as_metadata
from .shipping import ICE_PACK_MONTHS
from .shipping import billed as carriage_billed
from .stock import Line, Member, Plan, Wine
from .stock import plan_release as compute_plan


class FlowError(RuntimeError):
    """A refused transition, or input the flow will not accept."""


def _in_words(when: datetime) -> str:
    """How long ago, for a refusal somebody reads once and acts on.

    A timestamp makes the reader do the subtraction, and they are reading
    because something they expected to work did not.
    """
    minutes = int((datetime.now(timezone.utc) - when).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


# A carrier will not leave wine on a doorstep: somebody over 21 has to be there
# to sign for it, which a box at the post office cannot do, so the address has
# to be one a person is at. Caught here rather than by the carrier three days
# later, when the wine is already in a truck.
PO_BOX = re.compile(r"\b(?:p\.?\s*o\.?|post\s+office)[\s.]*box\b", re.I)
POSTCODE = re.compile(r"^\d{5}(?:-\d{4})?$")

# How long a "put in another card" link keeps working. Longer than an
# invitation, because a declined card is the club's problem to chase and
# the member may be travelling.
CARD_LINK_DAYS = 30


@dataclass(frozen=True)
class Address:
    """Where a shipment goes. Everything but line2 is required.

    The club holds this itself instead of reading it back from the payment
    provider: it is needed before anyone pays — to say whether the wine can be
    sent there at all — and a club that keeps its own delivery list does not
    lose it the day it changes processor.
    """
    line1: str
    line2: str
    city: str
    state: str
    postcode: str

    @classmethod
    def parse(cls, data: dict) -> "Address":
        def field(name: str) -> str:
            return " ".join(str(data.get(name) or "").split())

        line1, line2 = field("ship_line1"), field("ship_line2")
        city, state = field("ship_city"), field("ship_state")
        postcode = field("ship_postcode").upper()

        if not line1 or not city or not state or not postcode:
            raise FlowError("We need the street, the city, the state and the ZIP code.")
        if PO_BOX.search(line1) or PO_BOX.search(line2):
            raise FlowError(
                "Wine has to be signed for by someone over 21, so a carrier "
                "cannot deliver it to a PO box. Please give us a street address "
                "— home or work, whichever suits you."
            )
        if state not in SHIPPABLE_STATES:
            raise FlowError(f"We are not licensed to ship wine to {state} yet.")
        if not POSTCODE.match(postcode):
            raise FlowError("That ZIP code does not look right — five digits, or ZIP+4.")
        return cls(line1, line2, city, state, postcode)

    def as_row(self) -> tuple[str, str | None, str, str, str]:
        return (self.line1, self.line2 or None, self.city, self.state, self.postcode)


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

    # ── card links ───────────────────────────────────────────────────────
    # A member whose card was declined has to be able to put in another one.
    # Signed like an offer and for the same reason: no account, no password,
    # and a link that stops working on its own.
    @property
    def _card_signer(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.settings.secret_key, salt="wsc-card")

    def card_token(self, membership_id: int) -> str:
        return self._card_signer.dumps({"membership": membership_id})

    def read_card_token(self, token: str) -> int:
        try:
            data = self._card_signer.loads(token, max_age=CARD_LINK_DAYS * 86400)
        except SignatureExpired as exc:
            raise FlowError("this link has expired — write to us and we will send "
                            "another") from exc
        except BadSignature as exc:
            raise FlowError("this link is not valid") from exc
        return int(data["membership"])

    def card_url(self, membership_id: int) -> str:
        return f"{self.settings.public_base_url}/club/card/{self.card_token(membership_id)}"

    def start_card_replacement(self, token: str) -> str:
        membership_id = self.read_card_token(token)
        member = self.db.one("SELECT * FROM membership WHERE id = ?", (membership_id,))
        if member is None:
            raise FlowError("this link is not valid")
        if member["status"] == "cancelled":
            raise FlowError("this membership is closed")

        session = self.payments.create_setup(
            reference=f"card-{membership_id}-{now()}",
            description="Warm Springs Cellar — card on file",
            customer_email=member["email"],
            success_url=f"{self.settings.public_base_url}/club/card/{token}/done",
            cancel_url=f"{self.settings.public_base_url}/club/card/{token}",
            metadata=as_metadata(membership_id=membership_id),
        )
        with self.db.transaction() as con:
            log(con, "membership", membership_id, "card_replacement_started",
                session=session.id)
        return session.url

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

    # ── the waiting list ─────────────────────────────────────────────────
    def join_waiting_list(self, data: dict) -> int:
        """Somebody in a state the winery cannot ship to, who asked to be told.

        The mirror image of create_application: that one refuses an unlicensed
        state, and this one refuses a licensed one — there is nothing to wait
        for, and the right answer is the form.
        """
        required = ("first_name", "last_name", "email", "state")
        missing = [f for f in required if not str(data.get(f, "")).strip()]
        if missing:
            raise FlowError("missing required fields: " + ", ".join(missing))

        email = str(data["email"]).strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise FlowError("that email address does not look right")

        state = str(data["state"]).strip()
        if state in SHIPPABLE_STATES:
            raise FlowError(
                f"We can already ship to {state} — ask for an allocation instead."
            )
        if state not in US_STATES:
            raise FlowError(f"{state} is not a state we recognise.")

        with self.db.transaction() as con:
            open_row = con.execute(
                "SELECT id FROM waiting WHERE email = ? AND state = ? AND status = 'waiting'",
                (email, state),
            ).fetchone()
            if open_row:
                log(con, "waiting", int(open_row["id"]), "asked_again")
                return int(open_row["id"])

            cur = con.execute(
                "INSERT INTO waiting (created_at, status, first_name, last_name, email,"
                " state, city, referral, note) VALUES (?, 'waiting', ?, ?, ?, ?, ?, ?, ?)",
                (now(), str(data["first_name"]).strip(), str(data["last_name"]).strip(),
                 email, state, (data.get("city") or "").strip() or None,
                 (data.get("referral") or "").strip() or None,
                 (data.get("note") or "").strip() or None),
            )
            waiting_id = int(cur.lastrowid)
            log(con, "waiting", waiting_id, "joined", email=email, state=state)
            return waiting_id

    def invite_from_waiting(self, waiting_id: int, actor: str) -> int:
        """The state opened. Turn the row into an ordinary application.

        It does not approve anything and it does not send anything: it puts
        the person in the same queue as everybody else, where somebody decides.
        That is the whole point of waiting rather than being declined.
        """
        row = self.db.one("SELECT * FROM waiting WHERE id = ?", (waiting_id,))
        if row is None:
            raise FlowError("no such row on the waiting list")
        if row["status"] != "waiting":
            raise FlowError(f"that row is already {row['status']}")
        if row["state"] not in SHIPPABLE_STATES:
            raise FlowError(
                f"we still cannot ship to {row['state']} — take it out of UNLICENSED_STATES first"
            )

        app_id = self.create_application({
            "first_name": row["first_name"], "last_name": row["last_name"],
            "email": row["email"], "state": row["state"], "city": row["city"],
            "referral": " · ".join(x for x in (row["referral"], "waiting list") if x),
            "note": row["note"],
        })
        with self.db.transaction() as con:
            con.execute(
                "UPDATE waiting SET status = 'invited', invited_at = ?, application_id = ?"
                " WHERE id = ?", (now(), app_id, waiting_id),
            )
            log(con, "waiting", waiting_id, "invited", actor=actor, application=app_id)
            log(con, "application", app_id, "from_waiting_list", actor=actor,
                waited_since=row["created_at"][:10])
        return app_id

    def drop_from_waiting(self, waiting_id: int, actor: str) -> None:
        with self.db.transaction() as con:
            row = _require(con, "waiting", waiting_id)
            if row["status"] != "waiting":
                raise FlowError(f"that row is already {row['status']}")
            con.execute("UPDATE waiting SET status = 'dropped' WHERE id = ?", (waiting_id,))
            log(con, "waiting", waiting_id, "dropped", actor=actor)

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

    def start_card_setup(self, token: str, tier_key: str, address: Address) -> str:
        offer, app, tiers = self.open_offer(token)
        tier = next((t for t in tiers if t.key == tier_key), None)
        if tier is None:
            raise FlowError("that allocation was not offered to you")

        # Written before the provider is called, so an address given by
        # somebody who then abandons checkout is still here when they come
        # back, and the desk can see where the wine was going either way.
        with self.db.transaction() as con:
            con.execute(
                "UPDATE offer SET ship_line1 = ?, ship_line2 = ?, ship_city = ?,"
                " ship_state = ?, ship_postcode = ? WHERE id = ?",
                (*address.as_row(), int(offer["id"])),
            )
            log(con, "offer", int(offer["id"]), "address_given",
                state=address.state, postcode=address.postcode)

        reference = f"offer-{offer['id']}-{tier.key}"
        session = self.payments.create_setup(
            reference=reference,
            description=f"Warm Springs Cellar — {tier.name}",
            customer_email=app["email"],
            success_url=f"{self.settings.public_base_url}/club/offer/{token}/welcome",
            cancel_url=f"{self.settings.public_base_url}/club/offer/{token}?checkout=cancelled",
            metadata=as_metadata(offer_id=offer["id"], application_id=app["id"],
                                 tier_key=tier.key),
        )
        with self.db.transaction() as con:
            log(con, "offer", int(offer["id"]), "card_setup_started",
                tier=tier.key, session=session.id, provider=self.payments.name)
        return session.url

    # ── the webhook: where a saved card becomes a membership ─────────────
    def accept_card(self, event: CardSaved) -> int | None:
        """Turn a saved card into a membership. No money has moved.

        It raises no order and issues no invoice, because at this moment
        nobody knows whether there is wine: the club charges when a release
        goes out, and a release is only raised against stock. What the member
        has done is agree to be charged, and the card is now held where it can
        be.

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

            # The address the member gave on the way to checkout. An offer
            # from before addresses were asked for has none, and falls back to
            # the state on the application — enough to tell the desk where it
            # is going, not enough to pack it, which the shipment screen says.
            cur = con.execute(
                "INSERT INTO membership (application_id, created_at, status, tier_key, email,"
                " first_name, last_name, ship_line1, ship_line2, ship_city, ship_state,"
                " ship_postcode, provider, provider_customer_id, provider_payment_method)"
                " VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (app["id"], now(), tier.key, app["email"], app["first_name"], app["last_name"],
                 offer["ship_line1"], offer["ship_line2"], offer["ship_city"],
                 offer["ship_state"] or app["state"], offer["ship_postcode"],
                 self.payments.name, event.customer_id or None,
                 event.payment_method_id or None),
            )
            membership_id = int(cur.lastrowid)
            log(con, "offer", offer_id, "accepted", membership_id=membership_id)
            log(con, "membership", membership_id, "created",
                tier=tier.key, provider=self.payments.name)
            log(con, "membership", membership_id, "card_saved", session=event.session_id)

        return membership_id

    def replace_card(self, event: CardSaved) -> int | None:
        """A member who came back and put in a different card.

        The same webhook as joining; what tells them apart is the metadata on
        the session, which carries a membership rather than an offer.
        """
        membership_id = int(event.metadata.get("membership_id", 0) or 0)
        if not membership_id:
            raise FlowError("the saved card named no membership")

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
            _require(con, "membership", membership_id)
            con.execute(
                "UPDATE membership SET provider_customer_id = ?, provider_payment_method = ?"
                " WHERE id = ?",
                (event.customer_id or None, event.payment_method_id or None, membership_id),
            )
            log(con, "membership", membership_id, "card_replaced", session=event.session_id)
        return membership_id

    # ── charging: when a release goes out ────────────────────────────────
    def charge_order(self, order_id: int, actor: str) -> dict:
        """Charge one order against the card its member left with us.

        Returns what happened rather than raising it. A release is charged one
        order at a time and a declined card is an answer, not a fault — the
        other members in that release are still owed their wine.
        """
        row = self.db.one(
            'SELECT o.*, m.id AS membership_id, m.first_name, m.email, m.status AS member_status,'
            ' m.provider_customer_id, m.provider_payment_method'
            ' FROM "order" o JOIN membership m ON m.id = o.membership_id WHERE o.id = ?',
            (order_id,),
        )
        if row is None:
            raise FlowError("no such order")
        if row["status"] not in {"pending_payment", "failed"}:
            raise FlowError(f"that order is already {row['status']}")
        if row["member_status"] != "active":
            return self._charge_failed(order_id, actor,
                                       f"the membership is {row['member_status']}", False)
        if not row["provider_customer_id"] or not row["provider_payment_method"]:
            return self._charge_failed(order_id, actor, "no card on file", True)

        tier = catalogue().get(row["tier_key"])
        charge = self.payments.charge(
            reference=row["reference"],
            amount_cents=int(row["total_cents"]),
            currency=row["currency"],
            customer_id=row["provider_customer_id"],
            payment_method_id=row["provider_payment_method"],
            description=f"Warm Springs Cellar — {tier.name if tier else row['tier_key']}"
                        f" — {row['reference']}",
            metadata=as_metadata(order_id=order_id, membership_id=row["membership_id"],
                                 reference=row["reference"]),
        )

        if not charge.paid:
            return self._charge_failed(order_id, actor, charge.error, charge.needs_member)

        with self.db.transaction() as con:
            if row["status"] == "failed":
                # Back through the state machine rather than around it: a
                # failed order returns to pending before it can be paid.
                con.execute("UPDATE \"order\" SET status = 'pending_payment' WHERE id = ?",
                            (order_id,))
                log(con, "order", order_id, "pending_payment", actor=actor, detail="retried")
            con.execute("UPDATE \"order\" SET charge_error = NULL WHERE id = ?", (order_id,))
            self._mark_paid(con, order_id, charge.id)
            invoice_id = self._issue_invoice(con, order_id)
            self._create_shipment(con, order_id)
        return {"order_id": order_id, "reference": row["reference"], "paid": True,
                "invoice_id": invoice_id, "membership_id": int(row["membership_id"])}

    def _charge_failed(self, order_id: int, actor: str, reason: str,
                       needs_member: bool) -> dict:
        with self.db.transaction() as con:
            order = _require(con, "order", order_id)
            if order["status"] == "pending_payment":
                guard("order", order["status"], "failed")
                con.execute("UPDATE \"order\" SET status = 'failed' WHERE id = ?", (order_id,))
            con.execute("UPDATE \"order\" SET charge_error = ? WHERE id = ?", (reason, order_id))
            log(con, "order", order_id, "charge_failed", actor=actor, reason=reason,
                needs_member=needs_member)
        return {"order_id": order_id, "reference": order["reference"], "paid": False,
                "error": reason, "needs_member": needs_member,
                "membership_id": int(order["membership_id"])}

    def charge_release(self, release_id: int, actor: str) -> list[dict]:
        """Charge everything this release raised that has not been paid yet.

        Safe to run again: an order already paid is skipped, and the provider
        is given the order's own reference as its idempotency key, so a second
        attempt at one that timed out settles the first payment rather than
        taking a second.
        """
        with self.db.transaction() as con:
            _require(con, "release", release_id)
        self._require_notice_has_settled(release_id)
        rows = self.db.all(
            'SELECT id FROM "order" WHERE release_id = ?'
            " AND status IN ('pending_payment', 'failed') ORDER BY id",
            (release_id,),
        )
        results = [self.charge_order(int(row["id"]), actor) for row in rows]
        with self.db.transaction() as con:
            log(con, "release", release_id, "charged", actor=actor,
                paid=sum(1 for r in results if r["paid"]),
                failed=sum(1 for r in results if not r["paid"]))
        return results

    def _require_notice_has_settled(self, release_id: int) -> None:
        """Refuse to charge within hours of warning people it was coming.

        Only when a notice actually went out. A release charged without one
        sends a single message per member and warns nobody, which is a
        different decision and not this one to make.

        Refusing the whole release still leaves one order chargeable on its
        own, which is the escape hatch for the member who rings up and asks
        to be taken now.
        """
        gap = self.settings.release_notice_gap_hours
        if gap <= 0:
            return
        row = self.db.one(
            "SELECT MAX(e.created_at) AS last FROM event e"
            ' JOIN "order" o ON o.id = e.subject_id'
            " WHERE e.subject = 'order' AND e.action = 'release_notice_sent'"
            "   AND o.release_id = ?",
            (release_id,),
        )
        if row is None or not row["last"]:
            return

        sent = datetime.fromisoformat(row["last"])
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=timezone.utc)
        ready = sent + timedelta(hours=gap)
        if datetime.now(timezone.utc) >= ready:
            return

        raise FlowError(
            f"the last release notice went out {_in_words(sent)} and the "
            f"charges wait {gap}h behind it, so this release can be charged "
            f"from {ready.strftime('%d %b %H:%M')} UTC. The gap gives a member "
            f"time to move a card or say no, and keeps the notice and the "
            f"receipt off the same day's sending allowance. One order can "
            f"still be charged on its own from Orders.")

    # ── orders, invoices, shipments ──────────────────────────────────────
    def _create_order(self, con: sqlite3.Connection, membership_id: int, tier: Tier,
                      release_id: int | None) -> int:
        year = date.today().year
        number = next_in_sequence(con, f"order-{year}")
        reference = f"WSC-{year}-{number:04d}"

        subtotal = tier.price_cents
        # Carriage is priced per destination and then the tier's credit comes
        # off it, so two members on the same tier pay different shipping and
        # the winery gives away the same amount to each.
        member = _require(con, "membership", membership_id)
        shipping = carriage_billed(
            member["ship_state"] or "", tier.bottles, tier.shipping_credit_cents)
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

        # The books are told inside this transaction, but only told that they
        # are owed a document — the push itself happens later, from the queue.
        # An invoice that committed always has its paperwork queued, and one
        # that rolled back never does.
        enqueue(con, "invoice", invoice_id)
        if order["status"] == "paid":
            enqueue(con, "payment", invoice_id)
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

    def change_address(self, membership_id: int, address: Address, actor: str) -> None:
        """Members move. The address on the membership is the one that is packed.

        Orders already despatched keep the address they went to, because that
        is where the wine is; only what has not left the cellar follows the
        member to the new house.
        """
        with self.db.transaction() as con:
            row = _require(con, "membership", membership_id)
            con.execute(
                "UPDATE membership SET ship_line1 = ?, ship_line2 = ?, ship_city = ?,"
                " ship_state = ?, ship_postcode = ? WHERE id = ?",
                (*address.as_row(), membership_id),
            )
            log(con, "membership", membership_id, "address_changed", actor=actor,
                was=" ".join(x for x in (row["ship_line1"], row["ship_city"],
                                         row["ship_state"], row["ship_postcode"]) if x),
                now=f"{address.line1} {address.city} {address.state} {address.postcode}")

    # ── releases ─────────────────────────────────────────────────────────
    def create_release(self, name: str, ships_on: str) -> int:
        """Plan a release. Ice packs start on if it ships in a warm month."""
        try:
            month = int(str(ships_on)[5:7])
        except ValueError:
            month = 0
        ice = month in ICE_PACK_MONTHS
        with self.db.transaction() as con:
            cur = con.execute(
                "INSERT INTO \"release\" (created_at, name, ships_on, status, ice_packs)"
                " VALUES (?, ?, ?, 'planned', ?)", (now(), name, ships_on, int(ice)),
            )
            release_id = int(cur.lastrowid)
            log(con, "release", release_id, "created", name=name, ships_on=ships_on,
                ice_packs=ice)
            return release_id

    def set_release_ice(self, release_id: int, on: bool, actor: str) -> None:
        """Say whether this release goes out with ice packs.

        Nothing about the member's bill changes: the winery carries the cost
        of keeping wine cool either way. What changes is what the desk is
        looking at when it decides whether the release can be afforded.
        """
        with self.db.transaction() as con:
            release = _require(con, "release", release_id)
            if release["status"] not in {"planned", "open"}:
                raise FlowError(f"that release is {release['status']}")
            con.execute('UPDATE "release" SET ice_packs = ? WHERE id = ?',
                        (int(on), release_id))
            log(con, "release", release_id, "ice_packs", actor=actor, on=on)

    def generate_release_orders(self, release_id: int, actor: str) -> list[int]:
        """Raise the orders this release can actually fill. Safe to run twice.

        A composed release raises orders only for the memberships the plan
        covers, and commits the bottles in the same transaction: orders and
        allocation agree because they are one commit, so the club cannot end
        up having charged for wine the count says is not there.

        A release with no composition keeps the old behaviour — one order per
        active member, stock unchecked. That is not a safe default and the
        screen says so in red; it is kept because a club that has not written
        its inventory down yet still has to be able to ship.

        The (membership, release) pair is unique in the schema, so a second run
        skips anyone already covered rather than billing them again — and
        allocates only for the orders it actually raised.
        """
        created: list[int] = []
        raised: dict[str, int] = {}
        with self.db.transaction() as con:
            _require(con, "release", release_id)
            plan = self._plan(con, release_id)
            eligible: set[int] | None = set(plan.covered) if plan.composed else None

            members = con.execute(
                "SELECT * FROM membership WHERE status = 'active' ORDER BY id"
            ).fetchall()
            for member in members:
                member_id = int(member["id"])
                if eligible is not None and member_id not in eligible:
                    continue
                exists = con.execute(
                    "SELECT 1 FROM \"order\" WHERE membership_id = ? AND release_id = ?",
                    (member_id, release_id),
                ).fetchone()
                if exists:
                    continue
                tier = catalogue().get(member["tier_key"])
                if tier is None:
                    log(con, "membership", member_id, "skipped_unknown_tier",
                        tier=member["tier_key"])
                    continue
                created.append(self._create_order(con, member_id, tier, release_id))
                raised[tier.key] = raised.get(tier.key, 0) + 1

            if eligible is not None and raised:
                self._commit_bottles(con, release_id, raised, actor)
            con.execute("UPDATE \"release\" SET status = 'open' WHERE id = ?", (release_id,))
            log(con, "release", release_id, "orders_generated", actor=actor,
                count=len(created), stock_checked=plan.composed,
                unfilled=len(plan.unfilled) if plan.composed else 0)
        return created

    def _commit_bottles(self, con: sqlite3.Connection, release_id: int,
                        raised: dict[str, int], actor: str) -> None:
        """Take the bottles for the orders just raised out of stock.

        One move per wine rather than one per order: a move is tied to the
        release and the tier, not to an order, so a row per order would add
        volume without adding anything anyone could read. Running generation
        again allocates only for what that run raised, which is why the count
        comes in as an argument instead of being read back from the orders.
        """
        shipments = ", ".join(f"{count} × {tier}" for tier, count in sorted(raised.items()))
        per_wine: dict[int, int] = {}
        for tier_key, count in raised.items():
            for row in con.execute(
                "SELECT wine_id, bottles FROM release_item"
                " WHERE release_id = ? AND tier_key = ?", (release_id, tier_key),
            ):
                wine_id = int(row["wine_id"])
                per_wine[wine_id] = per_wine.get(wine_id, 0) + int(row["bottles"]) * count
        for wine_id, bottles in sorted(per_wine.items()):
            con.execute(
                "INSERT INTO stock_move (created_at, wine_id, bottles, kind, release_id,"
                " note, actor) VALUES (?, ?, ?, 'allocated', ?, ?, ?)",
                (now(), wine_id, -bottles, release_id, shipments, actor),
            )

    # ── the cellar: wines, bottles, and what a release is made of ────────
    def add_wine(self, sku: str, name: str, vintage: str | int | None,
                 actor: str) -> int:
        sku = " ".join(str(sku or "").split()).upper()
        name = " ".join(str(name or "").split())
        if not sku or not name:
            raise FlowError("A wine needs a code and a name.")
        year: int | None = None
        if str(vintage or "").strip():
            try:
                year = int(str(vintage).strip())
            except ValueError:
                raise FlowError("The vintage should be a year, or left empty.") from None
        with self.db.transaction() as con:
            clash = con.execute("SELECT id FROM wine WHERE sku = ?", (sku,)).fetchone()
            if clash:
                raise FlowError(f"{sku} is already in the cellar.")
            cur = con.execute(
                "INSERT INTO wine (created_at, sku, name, vintage, status)"
                " VALUES (?, ?, ?, ?, 'active')", (now(), sku, name, year),
            )
            wine_id = int(cur.lastrowid)
            log(con, "wine", wine_id, "added", actor=actor, sku=sku, name=name)
            return wine_id

    def set_wine_status(self, wine_id: int, target: str, actor: str) -> None:
        if target not in {"active", "retired"}:
            raise FlowError("A wine is either active or retired.")
        with self.db.transaction() as con:
            _require(con, "wine", wine_id)
            con.execute("UPDATE wine SET status = ? WHERE id = ?", (target, wine_id))
            log(con, "wine", wine_id, target, actor=actor)

    def receive_bottles(self, wine_id: int, bottles: int, note: str, actor: str) -> None:
        """Bottles the club now has that it did not have before."""
        bottles = _whole(bottles, "The number of bottles")
        if bottles == 0:
            raise FlowError("That would change nothing.")
        with self.db.transaction() as con:
            _require(con, "wine", wine_id)
            con.execute(
                "INSERT INTO stock_move (created_at, wine_id, bottles, kind, note, actor)"
                " VALUES (?, ?, ?, 'received', ?, ?)",
                (now(), wine_id, bottles, " ".join(str(note or "").split()) or None, actor),
            )
            log(con, "wine", wine_id, "received", actor=actor, bottles=bottles)

    def count_bottles(self, wine_id: int, counted: int, actor: str) -> int:
        """Record a physical count. Returns the correction it had to make.

        The count does not overwrite a balance, because there is no balance to
        overwrite: it writes the difference between what was counted and what
        the moves added up to. Both numbers go in the log, so a count that was
        itself wrong can be found later instead of having quietly replaced the
        truth.
        """
        counted = _whole(counted, "A count")
        if counted < 0:
            raise FlowError("A count cannot be negative.")
        with self.db.transaction() as con:
            _require(con, "wine", wine_id)
            before = self._on_hand(con).get(wine_id, 0)
            delta = counted - before
            if delta:
                con.execute(
                    "INSERT INTO stock_move (created_at, wine_id, bottles, kind, note, actor)"
                    " VALUES (?, ?, ?, 'count', ?, ?)",
                    (now(), wine_id, delta, f"counted {counted}, books said {before}", actor),
                )
            log(con, "wine", wine_id, "counted", actor=actor,
                counted=counted, was=before, correction=delta)
            return delta

    def set_release_item(self, release_id: int, tier_key: str, wine_id: int,
                         bottles: int, actor: str) -> None:
        """How many bottles of one wine go in one shipment of one tier.

        Zero removes the wine from that tier's shipment, which is how a
        composition is corrected — there is no separate delete to get wrong.
        """
        bottles = _whole(bottles, "The number of bottles")
        if bottles < 0:
            raise FlowError("A shipment cannot hold a negative number of bottles.")
        if catalogue().get(tier_key) is None:
            raise FlowError(f"{tier_key} is not a tier in the catalogue.")
        with self.db.transaction() as con:
            release = _require(con, "release", release_id)
            _require(con, "wine", wine_id)
            if release["status"] not in {"planned", "open"}:
                raise FlowError(f"that release is {release['status']}")
            if bottles == 0:
                con.execute(
                    "DELETE FROM release_item WHERE release_id = ? AND tier_key = ?"
                    " AND wine_id = ?", (release_id, tier_key, wine_id),
                )
            else:
                con.execute(
                    "INSERT INTO release_item (release_id, tier_key, wine_id, bottles)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT (release_id, tier_key, wine_id)"
                    " DO UPDATE SET bottles = excluded.bottles",
                    (release_id, tier_key, wine_id, bottles),
                )
            log(con, "release", release_id, "composed", actor=actor,
                tier=tier_key, wine_id=wine_id, bottles=bottles)

    def plan_release(self, release_id: int) -> Plan:
        """What this release can ship, and who it cannot fill. Reads only."""
        with self.db.connect() as con:
            _require(con, "release", release_id)
            return self._plan(con, release_id)

    def _plan(self, con: sqlite3.Connection, release_id: int) -> Plan:
        members = [
            Member(
                id=int(row["id"]),
                tier_key=row["tier_key"],
                name=f"{row['first_name']} {row['last_name']}".strip(),
                state=row["ship_state"] or "",
                has_card=bool(row["provider_customer_id"]
                              and row["provider_payment_method"]),
            )
            for row in con.execute(
                "SELECT * FROM membership WHERE status = 'active' ORDER BY id")
        ]
        lines = [
            Line(tier_key=row["tier_key"], wine_id=int(row["wine_id"]),
                 bottles=int(row["bottles"]))
            for row in con.execute(
                "SELECT * FROM release_item WHERE release_id = ?", (release_id,))
        ]
        wines = {
            int(row["id"]): Wine(int(row["id"]), row["sku"], row["name"])
            for row in con.execute("SELECT * FROM wine")
        }
        return compute_plan(
            members=members,
            lines=lines,
            on_hand=self._on_hand(con),
            wines=wines,
            tiers={t.key: (t.name, t.bottles) for t in catalogue().tiers},
            shippable_states=SHIPPABLE_STATES,
        )

    def _on_hand(self, con: sqlite3.Connection) -> dict[int, int]:
        return {
            int(row["wine_id"]): int(row["bottles"])
            for row in con.execute(
                "SELECT wine_id, COALESCE(SUM(bottles), 0) AS bottles"
                " FROM stock_move GROUP BY wine_id")
        }

    def cellar(self) -> list[dict]:
        """Every wine with what it has on hand and what is committed."""
        rows = self.db.all(
            "SELECT w.*,"
            " (SELECT COALESCE(SUM(bottles), 0) FROM stock_move s WHERE s.wine_id = w.id)"
            "   AS on_hand,"
            " (SELECT COALESCE(SUM(-bottles), 0) FROM stock_move s WHERE s.wine_id = w.id"
            "   AND s.kind = 'allocated') AS committed"
            " FROM wine w ORDER BY w.status, w.name"
        )
        return [dict(row) for row in rows]


def _whole(value: object, what: str) -> int:
    """A number of bottles, or a sentence a person at a desk can act on."""
    text = str(value if value is not None else "").strip()
    try:
        return int(text)
    except ValueError:
        raise FlowError(f"{what} has to be a whole number.") from None


def _require(con: sqlite3.Connection, table: str, row_id: int) -> sqlite3.Row:
    quoted = f'"{table}"'
    row = con.execute(f"SELECT * FROM {quoted} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise FlowError(f"no {table} with id {row_id}")
    return row
