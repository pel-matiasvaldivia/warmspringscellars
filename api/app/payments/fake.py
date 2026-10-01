"""A payment provider that moves no money.

Not a mock in the testing sense — it is what the service runs on until Stripe
credentials exist, so the whole flow from application to shipment can be walked
through and demonstrated. It hands back an internal URL that stands in for the
hosted checkout page, and the same webhook path handles its confirmation, so
the code under test is the real code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets

from .base import CheckoutSession, PaidEvent, WebhookError


class FakeProvider:
    name = "fake"
    live = False

    def __init__(self, secret: str, base_url: str):
        self._secret = secret
        self._base = base_url.rstrip("/")

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
        session_id = "cs_fake_" + secrets.token_urlsafe(12)
        payload = json.dumps(
            {
                "session_id": session_id,
                "amount_cents": amount_cents,
                "currency": currency,
                "metadata": metadata,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "description": description,
            },
            sort_keys=True,
        )
        token = _sign(self._secret, payload)
        return CheckoutSession(
            id=session_id,
            url=f"{self._base}/club/sandbox/checkout?d={_b64(payload)}&s={token}",
        )

    def parse_webhook(self, payload: bytes, signature: str | None) -> PaidEvent | None:
        if not signature or not hmac.compare_digest(_sign(self._secret, payload.decode()), signature):
            raise WebhookError("sandbox signature does not match")
        body = json.loads(payload)
        if body.get("type") != "checkout.completed":
            return None
        data = body["data"]
        return PaidEvent(
            event_id=body["id"],
            session_id=data["session_id"],
            customer_id="cus_fake_" + data["session_id"][-8:],
            subscription_id="sub_fake_" + data["session_id"][-8:],
            amount_cents=int(data["amount_cents"]),
            currency=data["currency"],
            metadata=dict(data.get("metadata") or {}),
        )

    # Used by the sandbox route to post a delivery back to the real webhook.
    def sandbox_delivery(self, session_id: str, amount_cents: int, currency: str,
                         metadata: dict[str, str]) -> tuple[bytes, str]:
        body = json.dumps(
            {
                "id": "evt_fake_" + secrets.token_urlsafe(10),
                "type": "checkout.completed",
                "data": {
                    "session_id": session_id,
                    "amount_cents": amount_cents,
                    "currency": currency,
                    "metadata": metadata,
                },
            },
            sort_keys=True,
        ).encode()
        return body, _sign(self._secret, body.decode())


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _b64(text: str) -> str:
    import base64
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def unb64(text: str) -> str:
    import base64
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad).decode()
