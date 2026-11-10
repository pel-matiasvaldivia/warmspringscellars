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
from urllib.parse import parse_qsl
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
        "mode": "setup",
        # A list, which Stripe numbers. No payload sends one today; the
        # helper still has to, and this is the shape it owes them.
        "expand": ["customer"],
        "setup_intent_data": {"metadata": {"offer_id": "7"}},
        "metadata": {"offer_id": "7"},
    }))
    assert encoded["mode"] == "setup"
    assert encoded["expand[0]"] == "customer"
    assert encoded["setup_intent_data[metadata][offer_id]"] == "7"
    assert encoded["metadata[offer_id]"] == "7"


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
class Answer:
    """The two things the adapter reads off an httpx response."""

    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)
        self.content = self.text.encode()

    def json(self) -> dict:
        return self._body


def completed_session(**overrides) -> bytes:
    body = {
        "id": "evt_test",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_123",
            "mode": "setup",
            "status": "complete",
            "customer": "cus_123",
            "setup_intent": "seti_123",
            "metadata": {"offer_id": "7", "tier_key": "founders-reserve"},
            **overrides,
        }},
    }
    return json.dumps(body).encode()


@pytest.fixture()
def saved_card(monkeypatch):
    """Stripe answering the one GET the adapter makes to learn which card it is."""
    asked: list[str] = []

    def fake_get(url, **kwargs):
        asked.append(url)
        return Answer(200, {"id": "seti_123", "payment_method": "pm_123"})

    monkeypatch.setattr("app.payments.stripe_provider.httpx.get", fake_get)
    return asked


def test_a_saved_card_becomes_an_event_carrying_the_card(provider, saved_card):
    payload = completed_session()
    event = provider.parse_webhook(payload, sign_payload(SECRET, payload))
    assert event is not None
    assert event.session_id == "cs_test_123"
    assert event.customer_id == "cus_123"
    # the card is on the setup intent, not on the session, so it took a look
    assert event.payment_method_id == "pm_123"
    assert saved_card == ["https://api.stripe.com/v1/setup_intents/seti_123"]
    assert event.metadata["tier_key"] == "founders-reserve"


def test_a_session_that_is_not_a_card_setup_is_ignored(provider):
    payload = completed_session(mode="payment")
    assert provider.parse_webhook(payload, sign_payload(SECRET, payload)) is None


def test_an_unfinished_session_is_ignored(provider):
    payload = completed_session(status="open")
    assert provider.parse_webhook(payload, sign_payload(SECRET, payload)) is None


def test_events_we_do_not_act_on_are_ignored(provider):
    payload = json.dumps({"id": "evt_x", "type": "invoice.paid", "data": {"object": {}}}).encode()
    assert provider.parse_webhook(payload, sign_payload(SECRET, payload)) is None


# ── charging a saved card ────────────────────────────────────────────────
def charge_with(monkeypatch, answer: Answer) -> dict:
    """Charge against a stubbed Stripe, handing back what was sent to it."""
    sent: dict = {}

    def fake_post(url, content=None, **kwargs):
        sent["url"] = url
        # Read back out of the wire format rather than off the call, so these
        # assertions describe what Stripe receives and not what we meant.
        sent["body"] = content
        sent["data"] = dict(parse_qsl(content or "", keep_blank_values=True))
        sent["headers"] = kwargs.get("headers", {})
        return answer

    monkeypatch.setattr("app.payments.stripe_provider.httpx.post", fake_post)
    return sent


def test_a_charge_that_succeeds_is_paid(provider, monkeypatch):
    sent = charge_with(monkeypatch, Answer(200, {"id": "pi_1", "status": "succeeded"}))
    charge = provider.charge(
        reference="WSC-2026-0001", amount_cents=27500, currency="usd",
        customer_id="cus_123", payment_method_id="pm_123",
        description="Warm Springs Cellar", metadata={"order_id": "4"},
    )
    assert charge.paid and charge.id == "pi_1"
    assert sent["url"].endswith("/payment_intents")
    assert sent["data"]["amount"] == "27500"
    assert sent["data"]["customer"] == "cus_123"
    assert sent["data"]["payment_method"] == "pm_123"
    # nobody is at the keyboard, and the bank is told so
    assert sent["data"]["off_session"] == "true"
    assert sent["data"]["confirm"] == "true"
    # keyed on the order, so a retry settles the first payment, not a second
    assert sent["headers"]["Idempotency-Key"] == "charge-WSC-2026-0001"


def test_a_declined_card_comes_back_as_an_answer_not_an_exception(provider, monkeypatch):
    charge_with(monkeypatch, Answer(402, {"error": {
        "code": "card_declined", "message": "Your card was declined.",
        "payment_intent": {"id": "pi_2"}}}))
    charge = provider.charge(
        reference="WSC-2026-0002", amount_cents=27500, currency="usd",
        customer_id="cus_123", payment_method_id="pm_123",
        description="x", metadata={},
    )
    assert not charge.paid
    assert charge.id == "pi_2"
    assert "declined" in charge.error
    assert not charge.needs_member


def test_a_bank_that_wants_the_member_is_told_apart_from_a_decline(provider, monkeypatch):
    charge_with(monkeypatch, Answer(402, {"error": {
        "code": "authentication_required",
        "message": "The payment requires authentication.",
        "payment_intent": {"id": "pi_3"}}}))
    charge = provider.charge(
        reference="WSC-2026-0003", amount_cents=27500, currency="usd",
        customer_id="cus_123", payment_method_id="pm_123",
        description="x", metadata={},
    )
    assert not charge.paid
    # retrying this one in the night fails every time; only the member can settle it
    assert charge.needs_member


# ── the body on the wire ─────────────────────────────────────────────────
def test_what_we_hand_httpx_is_a_body_httpx_can_actually_send():
    """The bug this file could not see.

    Every other test here replaces httpx.post outright and reads the payload
    off the call, so the adapter's intent was checked and the wire never was.
    httpx form-encodes a mapping; given the list of pairs `_form` returns it
    treats the whole thing as a streaming body, and the failure arrives much
    later and much further away — inside h11, as "expected a bytes-like
    object, tuple found", naming neither Stripe nor this adapter.

    So this one builds a real httpx request and reads the bytes back.
    """
    import httpx                                              # noqa: PLC0415
    from app.payments.stripe_provider import FORM, _body      # noqa: PLC0415

    payload = {"mode": "setup", "metadata": {"offer": 7}, "off_session": True}
    request = httpx.Request("POST", "https://api.stripe.com/v1/checkout/sessions",
                            content=_body(payload), headers=FORM)

    # .content raises outright on a streaming body, which is what the old
    # call produced, so reaching these assertions is half the test.
    body = request.content.decode()
    assert "mode=setup" in body
    assert "metadata%5Boffer%5D=7" in body, "Stripe's bracket nesting, encoded"
    assert "off_session=true" in body, "a JSON bool would be rejected"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"


def test_both_ways_out_to_stripe_use_the_same_encoding():
    """Two call sites post to Stripe, and only one of them was reached today.

    The charge path runs three times a year, so the same fault there would
    have waited for a release to show itself.
    """
    import inspect                                            # noqa: PLC0415
    from app.payments import stripe_provider                  # noqa: PLC0415

    source = inspect.getsource(stripe_provider)
    assert source.count("httpx.post(") == 2, "a third would need this test read"
    assert "data=_form(" not in source, "the shape httpx silently mishandles"
    assert source.count("content=_body(payload)") == 2


# ── idempotency keys that mean what they say ─────────────────────────────
def _setup(provider, monkeypatch, *, success_url: str) -> str:
    """Start a card setup, handing back the idempotency key that was sent."""
    seen: dict = {}

    def fake_post(url, content=None, **kwargs):
        seen["key"] = kwargs.get("headers", {}).get("Idempotency-Key", "")
        return Answer(200, {"id": "cs_1", "url": "https://checkout.stripe.com/c/x"})

    monkeypatch.setattr("app.payments.stripe_provider.httpx.post", fake_post)
    provider.create_setup(
        reference="offer-7-tasting-case",
        description="Warm Springs Cellar — The Tasting Case",
        customer_email="member@example.com",
        success_url=success_url,
        cancel_url="https://warmspringscellar.com/club/offer/T/cancel",
        metadata={"offer_id": "7"},
    )
    return seen["key"]


def test_the_same_request_twice_keeps_the_same_key(provider, monkeypatch):
    """A double click must still replay one session rather than open two."""
    url = "https://warmspringscellar.com/club/offer/TOKEN-A/welcome"

    assert _setup(provider, monkeypatch, success_url=url) == \
           _setup(provider, monkeypatch, success_url=url)


def test_a_second_attempt_does_not_reuse_the_first_attempt_key(provider,
                                                               monkeypatch):
    """The failure a member could not get out of.

    The success URL carries the offer's signed token, and that token is
    minted fresh on every render because its signature embeds a timestamp.
    A member who came back to the page therefore sent the same key with
    different parameters, which Stripe refuses — and every retry differed
    again, so the offer stayed dead to them.
    """
    first = _setup(provider, monkeypatch,
                   success_url="https://warmspringscellar.com/club/offer/TOKEN-A/welcome")
    second = _setup(provider, monkeypatch,
                    success_url="https://warmspringscellar.com/club/offer/TOKEN-B/welcome")

    assert first != second, "a changed request needs a key of its own"
    assert first.startswith("setup-offer-7-tasting-case-")
    assert second.startswith("setup-offer-7-tasting-case-")


def test_a_charge_keeps_one_key_for_one_order(provider, monkeypatch):
    """The opposite rule, and the reason the two differ.

    A charge payload carries no URL and no token, so it is stable by
    construction — and that stable key is the whole protection against
    taking a member's money twice for one order.
    """
    sent = charge_with(monkeypatch, Answer(200, {"id": "pi_1", "status": "succeeded"}))
    provider.charge(reference="WSC-2026-0001", amount_cents=12000, currency="usd",
                    customer_id="cus_1", payment_method_id="pm_1",
                    description="x", metadata={"order_id": "4"})

    assert sent["headers"]["Idempotency-Key"] == "charge-WSC-2026-0001", \
        "no hash here: the order's own reference is the point"


def test_the_session_does_not_name_payment_methods(provider, monkeypatch):
    """Stripe refuses the whole request if it does.

    `payment_method_types` is no longer accepted when a Checkout Session is
    created — "Payment methods are now managed from your Dashboard settings"
    — and the refusal takes the session with it, so a member gets a 500
    rather than a card page. Which methods are offered is an account setting
    now, not a line in this payload.
    """
    seen: dict = {}

    def fake_post(url, content=None, **kwargs):
        seen["body"] = content or ""
        return Answer(200, {"id": "cs_1", "url": "https://checkout.stripe.com/c/x"})

    monkeypatch.setattr("app.payments.stripe_provider.httpx.post", fake_post)
    provider.create_setup(
        reference="offer-7-tasting-case", description="x",
        customer_email="member@example.com",
        success_url="https://warmspringscellar.com/w", cancel_url="https://x/c",
        metadata={"offer_id": "7"},
    )

    assert "payment_method_types" not in seen["body"]
    assert "mode=setup" in seen["body"], "still a setup session, taking no money"
