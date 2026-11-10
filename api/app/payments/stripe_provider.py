"""Stripe Checkout in setup mode, and charges the club decides the timing of.

Written against Stripe's documented HTTP API rather than the SDK: the service
needs a handful of calls and a signature check, and a direct httpx call is
easier to read and to pin than a dependency that moves.

No subscription is created. Stripe can only bill on an even interval, and the
club ships in spring, autumn and at Christmas — four months apart on paper,
six and two and four in fact. More to the point, a subscription charges
whether or not there is wine: the club charges when a release goes out.

NOT EXERCISED AGAINST LIVE STRIPE from the machine this was written on — it has
no egress to api.stripe.com. The request shapes follow Stripe's documentation;
the signature verification is tested against vectors computed locally. Run a
test-mode checkout before trusting it with anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx

from .base import CardSaved, Charge, PaymentError, SetupSession, WebhookError

API = "https://api.stripe.com/v1"

# Stripe rejects a timestamp outside this window, which is what stops a captured
# delivery being replayed later.
TOLERANCE_SECONDS = 300


def _form(data: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Stripe takes form encoding with bracketed nesting, not JSON."""
    out: list[tuple[str, str]] = []
    for key, value in data.items():
        name = f"{prefix}[{key}]" if prefix else key
        if isinstance(value, dict):
            out.extend(_form(value, name))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    out.extend(_form(item, f"{name}[{i}]"))
                else:
                    out.append((f"{name}[{i}]", str(item)))
        elif isinstance(value, bool):
            out.append((name, "true" if value else "false"))
        elif value is not None:
            out.append((name, str(value)))
    return out


FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def _body(payload: dict) -> str:
    """The request body Stripe reads, encoded here rather than by the client.

    Stripe wants form encoding with bracketed keys, and `_form` returns the
    pairs in order. httpx only form-encodes a MAPPING, though: handed a list
    of pairs it takes the whole thing for a streaming body, and the failure
    surfaces far away inside the HTTP library as "expected a bytes-like
    object, tuple found" — which names neither Stripe nor this file.

    So both calls go through here. It owes nothing to which version of httpx
    is installed, and a third caller cannot get it wrong in a new way.
    """
    return urlencode(_form(payload))


class StripeProvider:
    name = "stripe"
    live = True

    def __init__(self, secret_key: str, webhook_secret: str, timeout: float = 20.0):
        if not secret_key:
            raise ValueError("StripeProvider needs a secret key")
        self._key = secret_key
        self._webhook_secret = webhook_secret
        self._timeout = timeout

    # ── the page where a card is saved ───────────────────────────────────
    def create_setup(
        self,
        *,
        reference: str,
        description: str,
        customer_email: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
    ) -> SetupSession:
        payload = {
            # Setup mode takes a card and charges nothing. Checkout creates the
            # Customer to attach it to, which is what `customer` on the
            # completed session carries back.
            "mode": "setup",
            "payment_method_types": ["card"],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "customer_email": customer_email,
            "client_reference_id": reference,
            # Stripe is deliberately NOT asked to collect a shipping address.
            # The club takes it before this page, because whether the wine may
            # be sent at all depends on the state. Asking twice would leave two
            # addresses that can disagree, and no answer to which one the box
            # is packed from.
            "metadata": metadata,
            "setup_intent_data": {"metadata": metadata, "description": description},
        }
        body = self._post("/checkout/sessions", payload, f"setup-{reference}")
        if not body.get("url"):
            raise PaymentError("Stripe returned a session with no URL")
        return SetupSession(id=body["id"], url=body["url"])

    # ── charging a card that is already on file ──────────────────────────
    def charge(
        self,
        *,
        reference: str,
        amount_cents: int,
        currency: str,
        customer_id: str,
        payment_method_id: str,
        description: str,
        metadata: dict[str, str],
    ) -> Charge:
        payload = {
            "amount": amount_cents,
            "currency": currency,
            "customer": customer_id,
            "payment_method": payment_method_id,
            # Nobody is at the keyboard: this runs on the morning a release is
            # charged. Telling Stripe so is what lets the bank apply the
            # exemption it agreed to when the card was saved.
            "off_session": True,
            "confirm": True,
            "description": description,
            "metadata": metadata,
        }
        # Keyed on the order's own reference, so charging a release twice —
        # by a double click, or a retry after a timeout — settles the same
        # payment rather than taking the money again.
        try:
            res = httpx.post(
                f"{API}/payment_intents",
                content=_body(payload),
                auth=(self._key, ""),
                timeout=self._timeout,
                headers={"Idempotency-Key": f"charge-{reference}", **FORM},
            )
        except httpx.HTTPError as exc:
            raise PaymentError(f"could not reach Stripe: {exc}") from exc

        body = res.json() if res.content else {}
        if res.status_code < 400:
            status = body.get("status")
            if status == "succeeded":
                return Charge(id=body.get("id", ""), paid=True)
            # requires_action and friends: taken, not settled.
            return Charge(id=body.get("id", ""), paid=False,
                          error=f"the payment is {status} rather than settled",
                          needs_member=status == "requires_action")

        error = body.get("error", {})
        intent = error.get("payment_intent") or {}
        code = error.get("code", "")
        return Charge(
            id=intent.get("id", ""),
            paid=False,
            error=error.get("message") or res.text,
            needs_member=code == "authentication_required",
        )

    # ── webhooks ─────────────────────────────────────────────────────────
    def parse_webhook(self, payload: bytes, signature: str | None) -> CardSaved | None:
        event = self.verify(payload, signature)

        if event.get("type") != "checkout.session.completed":
            return None

        session = event["data"]["object"]
        if session.get("mode") != "setup" or session.get("status") != "complete":
            return None

        customer = session.get("customer") or ""
        setup_intent = session.get("setup_intent") or ""
        if not customer or not setup_intent:
            raise PaymentError("a completed setup session carried no customer or intent")

        # The card itself is on the SetupIntent, not on the session, so it
        # takes one more call to learn which card was saved. Doing it here
        # keeps the rest of the service from knowing Stripe's shape.
        intent = self._get(f"/setup_intents/{setup_intent}")
        payment_method = intent.get("payment_method") or ""
        if not payment_method:
            raise PaymentError("the setup intent completed with no payment method")

        return CardSaved(
            event_id=event["id"],
            session_id=session["id"],
            customer_id=customer,
            payment_method_id=payment_method,
            metadata=dict(session.get("metadata") or {}),
        )

    # ── talking to Stripe ────────────────────────────────────────────────
    def _post(self, path: str, payload: dict, idempotency: str) -> dict:
        try:
            res = httpx.post(
                f"{API}{path}",
                content=_body(payload),
                auth=(self._key, ""),
                timeout=self._timeout,
                headers={"Idempotency-Key": idempotency, **FORM},
            )
        except httpx.HTTPError as exc:
            raise PaymentError(f"could not reach Stripe: {exc}") from exc
        if res.status_code >= 400:
            detail = res.json().get("error", {}).get("message", res.text)
            raise PaymentError(f"Stripe refused {path}: {detail}")
        return res.json()

    def _get(self, path: str) -> dict:
        try:
            res = httpx.get(f"{API}{path}", auth=(self._key, ""), timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise PaymentError(f"could not reach Stripe: {exc}") from exc
        if res.status_code >= 400:
            detail = res.json().get("error", {}).get("message", res.text)
            raise PaymentError(f"Stripe refused {path}: {detail}")
        return res.json()

    def verify(self, payload: bytes, signature: str | None) -> dict:
        """Check the Stripe-Signature header and return the decoded event.

        Refuses rather than warns: an unverified webhook is an instruction from
        a stranger to create a paid membership.
        """
        if not self._webhook_secret:
            raise WebhookError("no webhook secret configured, refusing the delivery")
        if not signature:
            raise WebhookError("missing Stripe-Signature header")

        parts = dict(
            piece.split("=", 1) for piece in signature.split(",") if "=" in piece
        )
        timestamp = parts.get("t")
        sent = [v for k, v in (p.split("=", 1) for p in signature.split(",") if "=" in p)
                if k == "v1"]
        if not timestamp or not sent:
            raise WebhookError("malformed Stripe-Signature header")

        try:
            age = abs(time.time() - int(timestamp))
        except ValueError as exc:
            raise WebhookError("Stripe-Signature carries a bad timestamp") from exc
        if age > TOLERANCE_SECONDS:
            raise WebhookError(f"webhook timestamp is {age:.0f}s old, outside tolerance")

        expected = hmac.new(
            self._webhook_secret.encode(),
            f"{timestamp}.".encode() + payload,
            hashlib.sha256,
        ).hexdigest()

        if not any(hmac.compare_digest(expected, candidate) for candidate in sent):
            raise WebhookError("Stripe-Signature does not match the payload")

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise WebhookError("webhook body is not JSON") from exc


def sign_payload(secret: str, payload: bytes, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header. Used by the tests, and by nothing else."""
    ts = timestamp if timestamp is not None else int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"
