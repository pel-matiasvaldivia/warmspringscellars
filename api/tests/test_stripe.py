"""The Stripe adapter, as far as it can be checked without reaching Stripe.

This container has no egress to api.stripe.com, so the request builder is
checked by shape and the signature verification against vectors computed here.
What is NOT covered: that Stripe accepts the session payload, and that a real
delivery decodes. Run a test-mode checkout before trusting it.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.payments.base import WebhookError                     # noqa: E402
from app.payments.stripe_provider import StripeProvider, _form, sign_payload   # noqa: E402

SECRET = "whsec_test_secret"


@pytest.fixture()
def provider() -> StripeProvider:
    return StripeProvider("sk_test_x", SECRET)


# ── form encoding ────────────────────────────────────────────────────────
def test_nested_payloads_use_stripe_bracket_encoding():
    encoded = dict(_form({
        "mode": "subscription",
        "line_items": [{"quantity": 1, "price_data": {"unit_amount": 18000}}],
        "metadata": {"offer_id": "7"},
        "shipping_address_collection": {"allowed_countries": ["US"]},
    }))
    assert encoded["mode"] == "subscription"
    assert encoded["line_items[0][quantity]"] == "1"
    assert encoded["line_items[0][price_data][unit_amount]"] == "18000"
    assert encoded["metadata[offer_id]"] == "7"
    assert encoded["shipping_address_collection[allowed_countries][0]"] == "US"


def test_booleans_and_nulls_are_handled_the_way_stripe_wants():
    encoded = dict(_form({"a": True, "b": False, "c": None, "d": 0}))
    assert encoded["a"] == "true"
    assert encoded["b"] == "false"
    assert "c" not in encoded          # omitted, not sent as the string "None"
    assert encoded["d"] == "0"


# ── signatures ───────────────────────────────────────────────────────────
def test_a_correct_signature_verifies(provider):
    payload = json.dumps({"id": "evt_1", "type": "ping"}).encode()
    event = provider.verify(payload, sign_payload(SECRET, payload))
    assert event["id"] == "evt_1"


def test_a_payload_edited_after_signing_is_refused(provider):
    payload = json.dumps({"id": "evt_1", "amount": 100}).encode()
    header = sign_payload(SECRET, payload)
    tampered = json.dumps({"id": "evt_1", "amount": 1}).encode()
    with pytest.raises(WebhookError, match="does not match"):
        provider.verify(tampered, header)


def test_a_signature_from_another_secret_is_refused(provider):
    payload = b'{"id":"evt_1"}'
    with pytest.raises(WebhookError, match="does not match"):
        provider.verify(payload, sign_payload("whsec_someone_else", payload))


def test_an_old_delivery_is_refused_so_it_cannot_be_replayed(provider):
    payload = b'{"id":"evt_1"}'
    stale = sign_payload(SECRET, payload, timestamp=int(time.time()) - 3600)
    with pytest.raises(WebhookError, match="outside tolerance"):
        provider.verify(payload, stale)


def test_a_missing_or_malformed_header_is_refused(provider):
    payload = b'{"id":"evt_1"}'
    with pytest.raises(WebhookError, match="missing"):
        provider.verify(payload, None)
    with pytest.raises(WebhookError, match="malformed"):
        provider.verify(payload, "nonsense")


def test_without_a_webhook_secret_everything_is_refused():
    payload = b'{"id":"evt_1"}'
    bare = StripeProvider("sk_test_x", "")
    with pytest.raises(WebhookError, match="no webhook secret"):
        bare.verify(payload, sign_payload(SECRET, payload))


def test_several_signatures_in_one_header_are_all_tried(provider):
    """Stripe sends every valid secret's signature during a key rotation."""
    payload = b'{"id":"evt_1","type":"ping"}'
    good = sign_payload(SECRET, payload)
    ts = good.split(",")[0].split("=")[1]
    v1 = good.split("v1=")[1]
    header = f"t={ts},v1=0000000000000000000000000000000000000000000000000000000000000000,v1={v1}"
    assert provider.verify(payload, header)["id"] == "evt_1"


# ── event decoding ───────────────────────────────────────────────────────
def completed_session(**overrides) -> bytes:
    body = {
        "id": "evt_test",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_123",
            "payment_status": "paid",
            "customer": "cus_123",
            "subscription": "sub_123",
            "amount_total": 34000,
            "currency": "usd",
            "metadata": {"offer_id": "7", "tier_key": "founders-reserve"},
            **overrides,
        }},
    }
    return json.dumps(body).encode()


def test_a_completed_checkout_becomes_a_paid_event(provider):
    payload = completed_session()
    event = provider.parse_webhook(payload, sign_payload(SECRET, payload))
    assert event is not None
    assert event.session_id == "cs_test_123"
    assert event.subscription_id == "sub_123"
    assert event.amount_cents == 34000
    assert event.metadata["tier_key"] == "founders-reserve"


def test_an_unpaid_checkout_is_ignored(provider):
    payload = completed_session(payment_status="unpaid")
    assert provider.parse_webhook(payload, sign_payload(SECRET, payload)) is None


def test_events_we_do_not_act_on_are_ignored(provider):
    payload = json.dumps({"id": "evt_x", "type": "invoice.paid", "data": {"object": {}}}).encode()
    assert provider.parse_webhook(payload, sign_payload(SECRET, payload)) is None
