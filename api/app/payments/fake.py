"""A payment provider that moves no money.

Not a mock in the testing sense — it is what the service runs on until Stripe
credentials exist, so the whole flow from application to shipment can be walked
through and demonstrated. It hands back an internal URL that stands in for the
hosted page where a card is saved, and the same webhook path handles its
confirmation, so the code under test is the real code.

Its charges always succeed. A sandbox that declined at random would be a
sandbox nobody could demonstrate; the declined path is exercised by the tests,
which hand the flow a provider that refuses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets

from .base import CardSaved, Charge, SetupSession, WebhookError


class FakeProvider:
    name = "fake"
    live = False

    def __init__(self, secret: str, base_url: str):
        self._secret = secret
        self._base = base_url.rstrip("/")

    def ensure_customer(self, *, email: str, name: str) -> str:
        return "cus_fake_" + secrets.token_urlsafe(9)

    def create_setup(
        self,
        *,
        customer_id: str,
        reference: str,
        description: str,
        success_url: str,
        cancel_url: str,
        currency: str,
        metadata: dict[str, str],
    ) -> SetupSession:
        session_id = "cs_fake_" + secrets.token_urlsafe(12)
        payload = json.dumps(
            {
                "session_id": session_id,
                "metadata": metadata,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "description": description,
            },
            sort_keys=True,
        )
        token = _sign(self._secret, payload)
        return SetupSession(
            id=session_id,
            url=f"{self._base}/club/sandbox/card?d={_b64(payload)}&s={token}",
        )

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
        # Derived from the reference rather than random, so charging the same
        # order twice returns the same id — which is what the real provider's
        # idempotency key buys, and what the test for it relies on.
        digest = hashlib.sha256(reference.encode()).hexdigest()[:16]
        return Charge(id=f"pi_fake_{digest}", paid=True)

    def parse_webhook(self, payload: bytes, signature: str | None) -> CardSaved | None:
        if not signature or not hmac.compare_digest(_sign(self._secret, payload.decode()), signature):
            raise WebhookError("sandbox signature does not match")
        body = json.loads(payload)
        if body.get("type") != "card.saved":
            return None
        data = body["data"]
        tail = data["session_id"][-8:]
        return CardSaved(
            event_id=body["id"],
            session_id=data["session_id"],
            customer_id="cus_fake_" + tail,
            payment_method_id="pm_fake_" + tail,
            metadata=dict(data.get("metadata") or {}),
        )

    # Used by the sandbox route to post a delivery back to the real webhook.
    def sandbox_delivery(self, session_id: str,
                         metadata: dict[str, str]) -> tuple[bytes, str]:
        body = json.dumps(
            {
                "id": "evt_fake_" + secrets.token_urlsafe(10),
                "type": "card.saved",
                "data": {"session_id": session_id, "metadata": metadata},
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
