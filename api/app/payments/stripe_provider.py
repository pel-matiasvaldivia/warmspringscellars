"""Stripe Checkout, in subscription mode.

Written against Stripe's documented HTTP API rather than the SDK: the service
needs two calls and a signature check, and a direct httpx call is easier to
read and to pin than a dependency that moves.

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

import httpx

from .base import CheckoutSession, PaidEvent, PaymentError, WebhookError

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


class StripeProvider:
    name = "stripe"
    live = True

    def __init__(self, secret_key: str, webhook_secret: str, timeout: float = 20.0):
        if not secret_key:
            raise ValueError("StripeProvider needs a secret key")
        self._key = secret_key
        self._webhook_secret = webhook_secret
        self._timeout = timeout

    # ── checkout ─────────────────────────────────────────────────────────
    def create_checkout(
        self,
        *,
        reference: str,
        description: str,
        amount_cents: int,
        currency: str,
        interval_months: int,
        customer_email: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
    ) -> CheckoutSession:
        payload = {
            "mode": "subscription",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "customer_email": customer_email,
            "client_reference_id": reference,
            "line_items": [
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": currency,
                        "unit_amount": amount_cents,
                        # Twice a year is six months, which Stripe expresses as
                        # a month interval counted six at a time.
                        "recurring": {"interval": "month", "interval_count": interval_months},
                        "product_data": {"name": description},
                    },
                }
            ],
            # Shipping is collected by Stripe rather than by us: it is the one
            # place the member is already filling in an address.
            "shipping_address_collection": {"allowed_countries": ["US"]},
            "metadata": metadata,
            "subscription_data": {"metadata": metadata},
        }

        try:
            res = httpx.post(
                f"{API}/checkout/sessions",
                data=_form(payload),
                auth=(self._key, ""),
                timeout=self._timeout,
                headers={"Idempotency-Key": f"checkout-{reference}"},
            )
        except httpx.HTTPError as exc:
            raise PaymentError(f"could not reach Stripe: {exc}") from exc

        if res.status_code >= 400:
            detail = res.json().get("error", {}).get("message", res.text)
            raise PaymentError(f"Stripe refused the checkout session: {detail}")

        body = res.json()
        if not body.get("url"):
            raise PaymentError("Stripe returned a session with no URL")
        return CheckoutSession(id=body["id"], url=body["url"])

    # ── webhooks ─────────────────────────────────────────────────────────
    def parse_webhook(self, payload: bytes, signature: str | None) -> PaidEvent | None:
        event = self.verify(payload, signature)

        if event.get("type") != "checkout.session.completed":
            return None

        session = event["data"]["object"]
        if session.get("payment_status") not in {"paid", "no_payment_required"}:
            return None

        return PaidEvent(
            event_id=event["id"],
            session_id=session["id"],
            customer_id=session.get("customer") or "",
            subscription_id=session.get("subscription") or "",
            amount_cents=int(session.get("amount_total") or 0),
            currency=(session.get("currency") or "usd"),
            metadata=dict(session.get("metadata") or {}),
        )

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
