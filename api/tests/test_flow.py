"""The commercial flow, end to end and at its edges.

Everything runs against the fake payment provider and the file outbox, which is
what the service itself runs on until credentials exist — so these exercise the
real code paths, not stand-ins for them.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ADMIN = ("cellar", "test-password")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A fresh service with its own database, keys and outbox."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-not-random")
    monkeypatch.setenv("ADMIN_USER", ADMIN[0])
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    for name in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[name]
    main = importlib.import_module("app.main")
    importlib.reload(main)

    with TestClient(main.app) as test_client:
        test_client.main = main
        test_client.tmp = tmp_path
        yield test_client


APPLICATION = {
    "first_name": "Matías",
    "last_name": "Valdivia",
    "email": "matias@example.com",
    "phone": "555 0100",
    "city": "San Francisco",
    "state": "California",
    "tier_interest": "founders-reserve",
    "referral": "Friend or Family",
    "note": "We met at the harvest dinner.",
}


def apply_(client, **overrides) -> int:
    body = {**APPLICATION, **overrides}
    res = client.post("/api/applications", json=body)
    assert res.status_code == 201, res.text
    return res.json()["application_id"]


def approve(client, app_id: int | None = None,
            tiers=("founders-reserve", "full-case")) -> str:
    if app_id is None:
        app_id = int(client.main.db.one(
            "SELECT id FROM application WHERE status = 'pending' ORDER BY id DESC")["id"])
    res = client.post(f"/admin/applications/{app_id}/approve",
                      data={"tiers": list(tiers)}, auth=ADMIN, follow_redirects=False)
    assert res.status_code == 303, res.text
    offer = client.main.db.one(
        "SELECT * FROM offer WHERE application_id = ? ORDER BY id DESC", (app_id,))
    return client.main.flow.offer_token(int(offer["id"]))


def pay(client, token: str, tier_key: str) -> None:
    """Walk the member through checkout, including the provider's callback."""
    res = client.post(f"/club/offer/{token}/checkout",
                      data={"tier_key": tier_key}, follow_redirects=False)
    assert res.status_code == 303, res.text
    sandbox = res.headers["location"]
    assert "/club/sandbox/checkout" in sandbox

    page = client.get(sandbox)
    assert page.status_code == 200
    confirm = sandbox.replace("/checkout?", "/confirm?")
    done = client.get(confirm, follow_redirects=False)
    assert done.status_code == 303, done.text


# ── the whole thing ──────────────────────────────────────────────────────
def test_request_to_shipment(client):
    app_id = apply_(client)

    row = client.main.db.one("SELECT * FROM application WHERE id = ?", (app_id,))
    assert row["status"] == "pending"
    assert row["email"] == "matias@example.com"      # normalised to lower case

    token = approve(client)
    assert client.main.db.one(
        "SELECT status FROM application WHERE id = ?", (app_id,))["status"] == "approved"

    # the invitation reached the outbox, and carries the link
    outbox = sorted((client.tmp / "outbox").glob("*.eml"))
    assert len(outbox) == 1
    assert "/club/offer/" in outbox[0].read_text()

    offer_page = client.get(f"/club/offer/{token}")
    assert offer_page.status_code == 200
    assert "Founder" in offer_page.text and "The Full Case" in offer_page.text
    # The invite-only tier is never ticked by default, so it is not on offer.
    assert "The Inner Circle" not in offer_page.text

    pay(client, token, "founders-reserve")

    membership = client.main.db.one("SELECT * FROM membership WHERE application_id = ?", (app_id,))
    assert membership["status"] == "active"
    assert membership["tier_key"] == "founders-reserve"
    assert membership["provider_subscription_id"].startswith("sub_fake_")

    order = client.main.db.one('SELECT * FROM "order" WHERE membership_id = ?', (membership["id"],))
    assert order["status"] == "paid"
    assert order["total_cents"] == 34000              # Founder's Reserve includes shipping
    assert order["reference"].startswith("WSC-")

    invoice = client.main.db.one("SELECT * FROM invoice WHERE order_id = ?", (order["id"],))
    assert invoice["status"] == "issued"
    assert invoice["total_cents"] == order["total_cents"]

    shipment = client.main.db.one("SELECT * FROM shipment WHERE order_id = ?", (order["id"],))
    assert shipment["status"] == "ready"

    # the receipt went out too
    assert len(list((client.tmp / "outbox").glob("*.eml"))) == 2

    # pack it, ship it, deliver it
    sid = int(shipment["id"])
    client.post(f"/admin/shipments/{sid}/advance", data={"target": "picked"},
                auth=ADMIN, follow_redirects=False)
    client.post(f"/admin/shipments/{sid}/advance",
                data={"target": "in_transit", "carrier": "GSO", "tracking": "1Z999"},
                auth=ADMIN, follow_redirects=False)
    client.post(f"/admin/shipments/{sid}/advance", data={"target": "delivered"},
                auth=ADMIN, follow_redirects=False)

    shipment = client.main.db.one("SELECT * FROM shipment WHERE id = ?", (sid,))
    assert shipment["status"] == "delivered"
    assert shipment["tracking"] == "1Z999"
    # delivering closes the order out
    assert client.main.db.one('SELECT status FROM "order" WHERE id = ?',
                              (order["id"],))["status"] == "fulfilled"

    pdf = client.get(f"/admin/invoices/{invoice['id']}.pdf", auth=ADMIN)
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"


# ── the form's own rules ─────────────────────────────────────────────────
def test_form_refuses_a_state_we_cannot_ship_to(client):
    res = client.post("/api/applications", json={**APPLICATION, "state": "Utah"})
    assert res.status_code == 400
    assert "not licensed" in res.json()["error"]


def test_form_requires_a_plausible_email(client):
    res = client.post("/api/applications", json={**APPLICATION, "email": "matias@localhost"})
    assert res.status_code == 400


def test_form_requires_the_basics(client):
    res = client.post("/api/applications", json={"email": "a@b.com"})
    assert res.status_code == 400
    assert "first_name" in res.json()["error"]


def test_a_second_request_while_one_is_open_is_the_same_request(client):
    first = apply_(client)
    second = apply_(client)
    assert first == second
    assert client.main.db.one("SELECT COUNT(*) c FROM application")["c"] == 1


def test_the_form_is_rate_limited(client):
    for i in range(8):
        client.post("/api/applications", json={**APPLICATION, "email": f"a{i}@example.com"})
    res = client.post("/api/applications", json={**APPLICATION, "email": "last@example.com"})
    assert res.status_code == 429


# ── offers ───────────────────────────────────────────────────────────────
def test_a_tampered_link_is_refused(client):
    app_id = apply_(client)
    token = approve(client)
    res = client.get(f"/club/offer/{token[:-4]}xxxx")
    assert res.status_code == 410
    assert "not valid" in res.text


def test_an_expired_offer_is_refused(client):
    app_id = apply_(client)
    token = approve(client)
    with client.main.db.transaction() as con:
        con.execute("UPDATE offer SET expires_at = '2020-01-01T00:00:00+00:00'"
                    " WHERE application_id = ?", (app_id,))
    res = client.get(f"/club/offer/{token}")
    assert res.status_code == 410
    assert "expired" in res.text


def test_a_tier_that_was_not_offered_is_refused(client):
    app_id = apply_(client)
    token = approve(client, app_id, tiers=("founders-reserve",))
    res = client.post(f"/club/offer/{token}/checkout",
                      data={"tier_key": "full-case"}, follow_redirects=False)
    assert res.status_code == 303
    assert "error=" in res.headers["location"]
    assert client.main.db.one("SELECT COUNT(*) c FROM membership")["c"] == 0


def test_an_offer_cannot_be_used_twice(client):
    app_id = apply_(client)
    token = approve(client)
    pay(client, token, "founders-reserve")
    assert client.main.db.one("SELECT COUNT(*) c FROM membership")["c"] == 1

    res = client.get(f"/club/offer/{token}")
    assert res.status_code == 410
    assert "already been accepted" in res.text


def test_a_tier_that_charges_for_carriage_bills_it_as_its_own_line(client):
    """No tier charges for shipping today, and the code still has to.

    The entry tier that did was withdrawn in October 2026, so this exercises
    `_create_order` directly rather than through the catalogue. Carriage stays
    its own line because wine and shipping book to different accounts, and a
    tier that charges for it again must not fold it into the wine.
    """
    from app.catalogue import Tier

    apply_(client)
    pay(client, approve(client), "founders-reserve")
    membership_id = int(client.main.db.one("SELECT id FROM membership")["id"])

    carriage = Tier(key="carriage", name="Carriage", label="Test", bottles=4,
                    discount_pct=15, price_cents=18000, shipping_cents=2500,
                    invite_only=False, featured=False, summary="", benefits=())
    with client.main.db.transaction() as con:
        order_id = client.main.flow._create_order(con, membership_id, carriage, None)

    order = client.main.db.one('SELECT * FROM "order" WHERE id = ?', (order_id,))
    assert order["subtotal_cents"] == 18000
    assert order["shipping_cents"] == 2500
    assert order["total_cents"] == 20500
    lines = client.main.db.all("SELECT * FROM order_line WHERE order_id = ? ORDER BY id",
                               (order_id,))
    assert len(lines) == 2
    assert "shipping" in lines[1]["description"].lower()


# ── the webhook ──────────────────────────────────────────────────────────
def test_a_replayed_webhook_does_not_create_a_second_membership(client):
    apply_(client)
    token = approve(client)

    res = client.post(f"/club/offer/{token}/checkout",
                      data={"tier_key": "founders-reserve"}, follow_redirects=False)
    sandbox = res.headers["location"]
    confirm = sandbox.replace("/checkout?", "/confirm?")

    client.get(confirm, follow_redirects=False)
    # the provider retries the identical delivery, as they all do
    client.get(confirm, follow_redirects=False)

    assert client.main.db.one("SELECT COUNT(*) c FROM membership")["c"] == 1
    assert client.main.db.one('SELECT COUNT(*) c FROM "order"')["c"] == 1
    assert client.main.db.one("SELECT COUNT(*) c FROM invoice")["c"] == 1


def test_an_unsigned_webhook_is_refused(client):
    res = client.post("/api/payments/webhook", content=b'{"type":"checkout.completed"}')
    assert res.status_code == 400
    assert client.main.db.one("SELECT COUNT(*) c FROM membership")["c"] == 0


def test_a_wrongly_signed_webhook_is_refused(client):
    res = client.post("/api/payments/webhook", content=b'{"type":"checkout.completed"}',
                      headers={"x-signature": "deadbeef"})
    assert res.status_code == 400


# ── admin ────────────────────────────────────────────────────────────────
def test_the_desk_is_shut_without_credentials(client):
    assert client.get("/admin").status_code == 401
    assert client.get("/admin/orders").status_code == 401
    assert client.get("/admin", auth=("cellar", "wrong")).status_code == 401
    assert client.get("/admin", auth=ADMIN).status_code == 200


def test_declining_tells_the_applicant(client):
    app_id = apply_(client)
    res = client.post(f"/admin/applications/{app_id}/decline",
                      data={"reason": "Full for the season"}, auth=ADMIN,
                      follow_redirects=False)
    assert res.status_code == 303
    row = client.main.db.one("SELECT * FROM application WHERE id = ?", (app_id,))
    assert row["status"] == "declined"
    assert row["decline_reason"] == "Full for the season"
    assert len(list((client.tmp / "outbox").glob("*.eml"))) == 1


def test_an_application_is_decided_once(client):
    app_id = apply_(client)
    approve(client, app_id)
    res = client.post(f"/admin/applications/{app_id}/decline", data={"reason": ""},
                      auth=ADMIN, follow_redirects=False)
    assert res.status_code == 400


# ── releases ─────────────────────────────────────────────────────────────
def test_a_release_raises_one_order_per_active_member_and_only_once(client):
    for i in range(3):
        app_id = apply_(client, email=f"member{i}@example.com")
        token = approve(client, app_id)
        pay(client, token, "founders-reserve")

    # one of them steps out for the season
    member = client.main.db.one("SELECT * FROM membership ORDER BY id LIMIT 1")
    client.post(f"/admin/memberships/{member['id']}/status", data={"target": "paused"},
                auth=ADMIN, follow_redirects=False)

    client.post("/admin/releases", data={"name": "Spring 2026", "ships_on": "2026-04-15"},
                auth=ADMIN, follow_redirects=False)
    release = client.main.db.one('SELECT * FROM "release" ORDER BY id DESC')

    client.post(f"/admin/releases/{release['id']}/generate", auth=ADMIN, follow_redirects=False)
    first = client.main.db.one(
        'SELECT COUNT(*) c FROM "order" WHERE release_id = ?', (release["id"],))["c"]
    assert first == 2

    # running it again must not bill anybody twice
    client.post(f"/admin/releases/{release['id']}/generate", auth=ADMIN, follow_redirects=False)
    second = client.main.db.one(
        'SELECT COUNT(*) c FROM "order" WHERE release_id = ?', (release["id"],))["c"]
    assert second == 2


# ── the state machine itself ─────────────────────────────────────────────
def test_a_shipment_cannot_skip_ahead(client):
    apply_(client)
    token = approve(client)
    pay(client, token, "founders-reserve")
    shipment = client.main.db.one("SELECT * FROM shipment ORDER BY id DESC")

    res = client.post(f"/admin/shipments/{shipment['id']}/advance",
                      data={"target": "delivered"}, auth=ADMIN, follow_redirects=False)
    assert res.status_code == 400
    assert client.main.db.one("SELECT status FROM shipment WHERE id = ?",
                              (shipment["id"],))["status"] == "ready"


def test_a_shipment_does_not_travel_without_a_tracking_number(client):
    apply_(client)
    token = approve(client)
    pay(client, token, "founders-reserve")
    shipment = client.main.db.one("SELECT * FROM shipment ORDER BY id DESC")
    client.post(f"/admin/shipments/{shipment['id']}/advance", data={"target": "picked"},
                auth=ADMIN, follow_redirects=False)

    res = client.post(f"/admin/shipments/{shipment['id']}/advance",
                      data={"target": "in_transit", "carrier": "GSO"},
                      auth=ADMIN, follow_redirects=False)
    assert res.status_code == 400


def test_wine_can_be_held_out_of_a_heat_wave_and_released_again(client):
    apply_(client)
    token = approve(client)
    pay(client, token, "founders-reserve")
    shipment = client.main.db.one("SELECT * FROM shipment ORDER BY id DESC")
    sid = int(shipment["id"])

    client.post(f"/admin/shipments/{sid}/advance",
                data={"target": "held", "reason": "104F in Phoenix all week"},
                auth=ADMIN, follow_redirects=False)
    row = client.main.db.one("SELECT * FROM shipment WHERE id = ?", (sid,))
    assert row["status"] == "held"
    assert "Phoenix" in row["held_reason"]

    client.post(f"/admin/shipments/{sid}/advance", data={"target": "ready"},
                auth=ADMIN, follow_redirects=False)
    row = client.main.db.one("SELECT * FROM shipment WHERE id = ?", (sid,))
    assert row["status"] == "ready"
    assert row["held_reason"] is None


# ── numbering ────────────────────────────────────────────────────────────
def test_invoice_numbers_run_without_gaps(client):
    numbers = []
    for i in range(3):
        app_id = apply_(client, email=f"seq{i}@example.com")
        token = approve(client, app_id)
        pay(client, token, "founders-reserve")
        numbers.append(client.main.db.one(
            "SELECT number FROM invoice ORDER BY id DESC")["number"])

    tails = [int(n.rsplit("-", 1)[1]) for n in numbers]
    assert tails == [1, 2, 3]
    assert len(set(numbers)) == 3


# ── the public surfaces ──────────────────────────────────────────────────
def test_health_says_what_is_still_a_stub(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["payments"] == {"provider": "fake", "live": False}
    assert any("STRIPE_SECRET_KEY" in w for w in body["not_configured"])


def test_the_catalogue_is_served_for_the_landing_page(client):
    body = client.get("/api/catalogue").json()
    keys = [t["key"] for t in body["tiers"]]
    assert keys == ["founders-reserve", "full-case", "inner-circle"]
    assert body["tiers"][-1]["invite_only"] is True


def test_states_are_the_ones_we_can_ship_to(client):
    states = client.get("/api/states").json()["states"]
    assert "California" in states
    assert "Utah" not in states
