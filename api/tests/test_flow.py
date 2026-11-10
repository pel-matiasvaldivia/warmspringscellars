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
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ADMIN = ("cellar", "test-password")


def letters(client, containing: str = "") -> list[str]:
    """The outbox, oldest first, decoded; optionally only the ones that say something.

    Counting messages was how these tests used to check who was written to,
    and every new notification broke them all at once without any of them
    being wrong. What they each meant was "this person got this letter".

    Decoded, and with the line breaks flattened, because a .eml is
    quoted-printable: a sentence worth asserting on is exactly the length
    that gets split across a soft line break, and the failure looks like the
    sentence is missing.
    """
    import email
    import email.policy

    out = []
    for path in sorted((client.tmp / "outbox").glob("*.eml")):
        msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
        parts = [f"To: {msg['To']}", f"Subject: {msg['Subject']}"]
        for part in msg.walk():
            if part.get_content_maintype() == "text":
                parts.append(part.get_content())
        out.append(" ".join(" ".join(parts).split()))
    return [t for t in out if containing in t] if containing else out


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
            tiers=("tasting-case", "founders-reserve", "full-case")) -> str:
    if app_id is None:
        app_id = int(client.main.db.one(
            "SELECT id FROM application WHERE status = 'pending' ORDER BY id DESC")["id"])
    res = client.post(f"/admin/applications/{app_id}/approve",
                      data={"tiers": list(tiers)}, auth=ADMIN, follow_redirects=False)
    assert res.status_code == 303, res.text
    offer = client.main.db.one(
        "SELECT * FROM offer WHERE application_id = ? ORDER BY id DESC", (app_id,))
    return client.main.flow.offer_token(int(offer["id"]))


ADDRESS = {
    "ship_line1": "1204 Warm Springs Road",
    "ship_line2": "Apt 2",
    "ship_city": "Glen Ellen",
    "ship_state": "California",
    "ship_postcode": "95442",
}


def join(client, token: str, tier_key: str, **address) -> int:
    """Walk the member to the card page, including the provider's callback.

    No money moves here. They end with a membership and a card on file.
    """
    res = client.post(f"/club/offer/{token}/checkout",
                      data={"tier_key": tier_key, **ADDRESS, **address},
                      follow_redirects=False)
    assert res.status_code == 303, res.text
    sandbox = res.headers["location"]
    assert "/club/sandbox/card" in sandbox

    page = client.get(sandbox)
    assert page.status_code == 200
    done = client.get(sandbox.replace("/card?", "/card/confirm?"), follow_redirects=False)
    assert done.status_code == 303, done.text
    return int(client.main.db.one("SELECT id FROM membership ORDER BY id DESC")["id"])


def plan_release(client, name: str = "", ships_on: str = "2026-04-15") -> int:
    """A release, with its orders raised but nothing charged."""
    count = client.main.db.one('SELECT COUNT(*) c FROM "release"')["c"]
    client.post("/admin/releases",
                data={"name": name or f"Season {count + 1}", "ships_on": ships_on},
                auth=ADMIN, follow_redirects=False)
    release_id = int(client.main.db.one('SELECT id FROM "release" ORDER BY id DESC')["id"])
    client.post(f"/admin/releases/{release_id}/generate", auth=ADMIN, follow_redirects=False)
    return release_id


def pay(client, token: str, tier_key: str, **address) -> int:
    """Join, raise a release, and charge that one member's order.

    What used to happen in a single step at checkout now takes three: the card
    is saved when they join, and money moves only when a release goes out.
    Only their own order is charged, so a test with several members does not
    bill the earlier ones again.
    """
    membership_id = join(client, token, tier_key, **address)
    release_id = plan_release(client)
    order = client.main.db.one(
        'SELECT id FROM "order" WHERE membership_id = ? AND release_id = ?',
        (membership_id, release_id))
    res = client.post(f"/admin/orders/{order['id']}/charge", auth=ADMIN,
                      follow_redirects=False)
    assert res.status_code == 303, res.text
    return membership_id


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
    assert len(letters(client, "/club/offer/")) == 1

    offer_page = client.get(f"/club/offer/{token}")
    assert offer_page.status_code == 200
    assert "Founder" in offer_page.text and "The Full Case" in offer_page.text

    pay(client, token, "founders-reserve")

    membership = client.main.db.one("SELECT * FROM membership WHERE application_id = ?", (app_id,))
    assert membership["status"] == "active"
    assert membership["tier_key"] == "founders-reserve"
    assert membership["provider_payment_method"].startswith("pm_fake_")

    order = client.main.db.one('SELECT * FROM "order" WHERE membership_id = ?', (membership["id"],))
    assert order["status"] == "paid"
    # $275 of wine, and $46 of carriage to Glen Ellen less the tier's $20 credit
    assert order["total_cents"] == 30100
    assert order["reference"].startswith("WSC-")

    invoice = client.main.db.one("SELECT * FROM invoice WHERE order_id = ?", (order["id"],))
    assert invoice["status"] == "issued"
    assert invoice["total_cents"] == order["total_cents"]

    shipment = client.main.db.one("SELECT * FROM shipment WHERE order_id = ?", (order["id"],))
    assert shipment["status"] == "ready"

    # acknowledged on the form, invited on approval, welcomed when the card
    # was saved, receipted when it was charged
    assert len(letters(client, "We have your request")) == 1
    assert len(letters(client, "/club/offer/")) == 1
    assert len(letters(client, "Nothing has been charged")) == 1
    assert len(letters(client, "Invoice")) == 1

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


# ── money moves when a release goes out, not when somebody joins ─────────
def test_joining_saves_a_card_and_charges_nothing(client):
    apply_(client)
    membership_id = join(client, approve(client), "founders-reserve")

    member = client.main.db.one("SELECT * FROM membership WHERE id = ?", (membership_id,))
    assert member["status"] == "active"
    assert member["provider_customer_id"].startswith("cus_fake_")
    assert member["provider_payment_method"].startswith("pm_fake_")
    assert client.main.db.one('SELECT COUNT(*) c FROM "order"')["c"] == 0
    assert client.main.db.one("SELECT COUNT(*) c FROM invoice")["c"] == 0

    # and the letter they get says so, because a card page that charges
    # nothing is not what most people assume they just did
    welcome = sorted((client.tmp / "outbox").glob("*.eml"))[-1].read_text()
    assert "Nothing has been charged" in welcome


def test_a_release_raises_orders_and_only_charging_takes_the_money(client):
    apply_(client)
    join(client, approve(client), "founders-reserve")

    client.post("/admin/releases", data={"name": "Spring 2026", "ships_on": "2026-04-15"},
                auth=ADMIN, follow_redirects=False)
    release_id = int(client.main.db.one('SELECT id FROM "release" ORDER BY id DESC')["id"])
    client.post(f"/admin/releases/{release_id}/generate", auth=ADMIN, follow_redirects=False)

    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    assert order["status"] == "pending_payment"
    assert client.main.db.one("SELECT COUNT(*) c FROM invoice")["c"] == 0

    res = client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN,
                      follow_redirects=False)
    assert res.status_code == 303
    assert "1 charged" in unquote(res.headers["location"])

    order = client.main.db.one('SELECT * FROM "order" WHERE id = ?', (order["id"],))
    assert order["status"] == "paid"
    assert order["provider_payment_id"].startswith("pi_fake_")
    assert client.main.db.one("SELECT COUNT(*) c FROM invoice")["c"] == 1
    assert client.main.db.one("SELECT COUNT(*) c FROM shipment")["c"] == 1

    # charging the release again finds nothing left to charge
    again = client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN,
                        follow_redirects=False)
    assert "0 charged" in unquote(again.headers["location"])
    assert client.main.db.one("SELECT COUNT(*) c FROM invoice")["c"] == 1


def test_a_declined_card_holds_the_order_and_writes_to_the_member(client):
    apply_(client)
    join(client, approve(client), "founders-reserve")
    release_id = plan_release(client)

    # the provider refuses, as a bank sometimes does
    from app.payments.base import Charge
    client.main.payments.charge = lambda **kw: Charge(
        id="pi_x", paid=False, error="Your card was declined.", needs_member=False)

    before = len(list((client.tmp / "outbox").glob("*.eml")))
    client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN, follow_redirects=False)

    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    assert order["status"] == "failed"
    assert order["charge_error"] == "Your card was declined."
    # no invoice for money that never arrived
    assert client.main.db.one("SELECT COUNT(*) c FROM invoice")["c"] == 0

    # two letters now: the member, and the winery that would not otherwise know
    assert len(letters(client)) == before + 2
    told = letters(client, "/club/card/")[-1]
    assert "declined" in told                 # with a way to fix it themselves
    desk = letters(client, "refused")[-1]
    assert "1 card was refused" in desk


def test_a_member_can_put_in_another_card_and_the_order_then_charges(client):
    apply_(client)
    membership_id = join(client, approve(client), "founders-reserve")
    release_id = plan_release(client)

    from app.payments.base import Charge
    real_charge = client.main.payments.charge
    client.main.payments.charge = lambda **kw: Charge(id="pi_x", paid=False, error="Declined.")
    client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN, follow_redirects=False)

    # they open the link from the letter and leave a different card
    token = client.main.flow.card_token(membership_id)
    page = client.get(f"/club/card/{token}")
    assert page.status_code == 200 and "a new card" in page.text

    was = client.main.db.one(
        "SELECT provider_payment_method p FROM membership WHERE id = ?", (membership_id,))["p"]
    started = client.get(f"/club/card/{token}/start", follow_redirects=False)
    assert started.status_code == 303
    sandbox = started.headers["location"]
    client.get(sandbox.replace("/card?", "/card/confirm?"), follow_redirects=False)

    now_on_file = client.main.db.one(
        "SELECT provider_payment_method p FROM membership WHERE id = ?", (membership_id,))["p"]
    assert now_on_file and now_on_file != was
    assert "card_replaced" in [e["action"] for e in client.main.db.all(
        "SELECT * FROM event WHERE subject = 'membership' AND subject_id = ?", (membership_id,))]

    # and the desk charges the held order again, which now goes through
    client.main.payments.charge = real_charge
    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    res = client.post(f"/admin/orders/{order['id']}/charge", auth=ADMIN,
                      follow_redirects=False)
    assert res.status_code == 303
    order = client.main.db.one('SELECT * FROM "order" WHERE id = ?', (order["id"],))
    assert order["status"] == "paid"
    assert order["charge_error"] is None
    assert client.main.db.one("SELECT COUNT(*) c FROM invoice")["c"] == 1


def test_a_membership_with_no_card_is_not_charged_and_says_why(client):
    apply_(client)
    membership_id = join(client, approve(client), "founders-reserve")
    with client.main.db.transaction() as con:
        con.execute("UPDATE membership SET provider_payment_method = NULL WHERE id = ?",
                    (membership_id,))

    release_id = plan_release(client)
    client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN, follow_redirects=False)

    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    assert order["status"] == "failed"
    assert order["charge_error"] == "no card on file"


def test_a_paused_member_is_not_charged(client):
    apply_(client)
    membership_id = join(client, approve(client), "founders-reserve")
    release_id = plan_release(client)
    client.post(f"/admin/memberships/{membership_id}/status", data={"target": "paused"},
                auth=ADMIN, follow_redirects=False)

    client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN, follow_redirects=False)
    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    assert order["status"] == "failed"
    assert "paused" in order["charge_error"]


# ── waiting on a licence ─────────────────────────────────────────────────
WAITING = {"first_name": "Audra", "last_name": "Thomas", "email": "Audra@Example.com",
           "state": "Arkansas", "city": "Little Rock",
           "referral": "Replied to the Deerfield letter"}


def test_somebody_we_cannot_ship_to_can_ask_to_be_told(client):
    res = client.post("/api/waiting", json=WAITING)
    assert res.status_code == 201, res.text
    row = client.main.db.one("SELECT * FROM waiting ORDER BY id DESC")
    assert (row["status"], row["state"], row["email"]) == (
        "waiting", "Arkansas", "audra@example.com")

    # Asking twice is asking once.
    again = client.post("/api/waiting", json=WAITING)
    assert again.json()["waiting_id"] == res.json()["waiting_id"]
    assert client.main.db.one("SELECT COUNT(*) AS n FROM waiting")["n"] == 1
    assert "asked_again" in [e["action"] for e in client.main.db.all(
        "SELECT * FROM event WHERE subject = 'waiting'")]


def test_the_waiting_list_refuses_a_state_we_can_already_ship_to(client):
    res = client.post("/api/waiting", json={**WAITING, "state": "California"})
    assert res.status_code == 400
    assert "ask for an allocation instead" in res.json()["error"]
    res = client.post("/api/waiting", json={**WAITING, "state": "Catalonia"})
    assert res.status_code == 400
    assert "not a state we recognise" in res.json()["error"]
    assert client.main.db.one("SELECT COUNT(*) AS n FROM waiting")["n"] == 0


def test_nobody_is_invited_from_the_waiting_list_while_the_state_is_shut(client):
    waiting_id = client.post("/api/waiting", json=WAITING).json()["waiting_id"]
    res = client.post(f"/admin/waiting/{waiting_id}/invite", auth=ADMIN, follow_redirects=False)
    assert res.status_code == 303
    assert "still cannot ship to Arkansas" in unquote(res.headers["location"])
    assert client.main.db.one(
        "SELECT status FROM waiting WHERE id = ?", (waiting_id,))["status"] == "waiting"
    assert client.main.db.one("SELECT COUNT(*) AS n FROM application")["n"] == 0


def test_when_the_state_opens_they_join_the_ordinary_queue(client, monkeypatch):
    waiting_id = client.post("/api/waiting", json=WAITING).json()["waiting_id"]

    # The licence arrives: Arkansas comes out of the unlicensed list. The
    # modules import the names, so each one that holds a copy is patched —
    # which is the test's way of doing what editing config.py does for real.
    config = sys.modules["app.config"]
    opened = config.SHIPPABLE_STATES | {"Arkansas"}
    still_shut = config.UNLICENSED_STATES - {"Arkansas"}
    for module in ("app.config", "app.flow", "app.main"):
        if hasattr(sys.modules[module], "SHIPPABLE_STATES"):
            monkeypatch.setattr(sys.modules[module], "SHIPPABLE_STATES", opened)
        if hasattr(sys.modules[module], "UNLICENSED_STATES"):
            monkeypatch.setattr(sys.modules[module], "UNLICENSED_STATES", still_shut)

    page = client.get("/admin/waiting", auth=ADMIN)
    assert "Arkansas" in page.text and "open now" in page.text

    res = client.post(f"/admin/waiting/{waiting_id}/invite", auth=ADMIN, follow_redirects=False)
    assert res.status_code == 303
    row = client.main.db.one("SELECT * FROM waiting WHERE id = ?", (waiting_id,))
    assert row["status"] == "invited"

    application = client.main.db.one("SELECT * FROM application WHERE id = ?",
                                    (row["application_id"],))
    assert application["status"] == "pending"          # nothing is approved, nothing is sent
    assert application["state"] == "Arkansas"
    assert "waiting list" in application["referral"]
    assert letters(client, "/club/offer/") == [], "nothing is approved, nothing is offered"

    # And a second attempt does not raise a second application.
    client.post(f"/admin/waiting/{waiting_id}/invite", auth=ADMIN, follow_redirects=False)
    assert client.main.db.one("SELECT COUNT(*) AS n FROM application")["n"] == 1


def test_a_row_can_be_taken_off_the_waiting_list(client):
    waiting_id = client.post("/api/waiting", json=WAITING).json()["waiting_id"]
    res = client.post(f"/admin/waiting/{waiting_id}/drop", auth=ADMIN, follow_redirects=False)
    assert res.status_code == 303
    assert client.main.db.one(
        "SELECT status FROM waiting WHERE id = ?", (waiting_id,))["status"] == "dropped"
    # Which frees the unique index: they can ask again later.
    assert client.post("/api/waiting", json=WAITING).status_code == 201


def test_the_waiting_list_is_shut_without_credentials(client):
    assert client.get("/admin/waiting").status_code == 401
    assert client.post("/admin/waiting/1/invite").status_code == 401
    assert client.post("/admin/waiting/1/drop").status_code == 401
    assert client.post("/admin/waiting/new", data=WAITING).status_code == 401


# ── where the wine goes ──────────────────────────────────────────────────
def test_the_address_is_asked_for_before_payment_and_kept(client):
    apply_(client)
    token = approve(client)

    # "Join Now" leads to the address, not to the provider.
    page = client.get(f"/club/offer/{token}/address?tier=founders-reserve")
    assert page.status_code == 200
    assert "Where should the wine go?" in page.text
    assert "Glen Ellen" in page.text          # the city from their application, offered back

    pay(client, token, "founders-reserve")
    member = client.main.db.one("SELECT * FROM membership ORDER BY id DESC")
    assert member["ship_line1"] == "1204 Warm Springs Road"
    assert member["ship_line2"] == "Apt 2"
    assert member["ship_city"] == "Glen Ellen"
    assert member["ship_postcode"] == "95442"
    # And it is on the screen the box is packed from.
    assert "1204 Warm Springs Road" in client.get("/admin/shipments", auth=ADMIN).text


def test_an_address_we_cannot_deliver_to_is_refused_before_money_moves(client):
    apply_(client)
    token = approve(client)
    for bad, says in [
        ({"ship_line1": "PO Box 417"}, "PO box"),
        ({"ship_line1": "Post Office Box 417"}, "PO box"),
        ({"ship_postcode": "9544"}, "ZIP code"),
        ({"ship_line1": ""}, "We need the street"),
        ({"ship_state": "Utah"}, "not licensed"),
    ]:
        res = client.post(f"/club/offer/{token}/checkout",
                          data={"tier_key": "founders-reserve", **ADDRESS, **bad})
        assert res.status_code == 200, bad
        assert says in res.text, bad
        # The form comes back with what was typed, not emptied.
        assert ADDRESS["ship_city"] in res.text
    assert client.main.db.one("SELECT COUNT(*) AS n FROM membership")["n"] == 0


def test_an_abandoned_checkout_remembers_the_address(client):
    apply_(client)
    token = approve(client)
    client.post(f"/club/offer/{token}/checkout",
                data={"tier_key": "founders-reserve", **ADDRESS}, follow_redirects=False)
    page = client.get(f"/club/offer/{token}/address?tier=full-case")
    assert "1204 Warm Springs Road" in page.text


def test_a_member_who_moves_is_followed_by_the_wine(client):
    apply_(client)
    pay(client, approve(client), "founders-reserve")
    member_id = int(client.main.db.one("SELECT id FROM membership ORDER BY id DESC")["id"])

    moved = {**ADDRESS, "ship_line1": "88 Pine Street", "ship_line2": "",
             "ship_city": "Portland", "ship_state": "Oregon", "ship_postcode": "97204"}
    res = client.post(f"/admin/memberships/{member_id}/address", data=moved,
                      auth=ADMIN, follow_redirects=False)
    assert res.status_code == 303
    row = client.main.db.one("SELECT * FROM membership WHERE id = ?", (member_id,))
    assert (row["ship_line1"], row["ship_city"], row["ship_state"]) == (
        "88 Pine Street", "Portland", "Oregon")
    assert row["ship_line2"] is None
    assert "address_changed" in [e["action"] for e in client.main.db.all(
        "SELECT * FROM event WHERE subject = 'membership' AND subject_id = ?", (member_id,))]

    # And a bad one is refused there too, without touching what is on file.
    client.post(f"/admin/memberships/{member_id}/address",
                data={**moved, "ship_line1": "PO Box 9"}, auth=ADMIN, follow_redirects=False)
    assert client.main.db.one(
        "SELECT * FROM membership WHERE id = ?", (member_id,))["ship_line1"] == "88 Pine Street"


def test_an_older_database_grows_the_address_columns(client):
    """The migration runs on a database made before the columns existed."""
    import re
    import sqlite3

    from app.db import SCHEMA, Database

    # The schema as it was before this release: the same one, with the address
    # columns taken back out of `offer` and the comma they left behind tidied.
    start = SCHEMA.index("CREATE TABLE IF NOT EXISTS offer")
    end = SCHEMA.index(");", start) + 2
    block = re.sub(r"\n\s*ship_\w+\s+TEXT,?", "", SCHEMA[start:end])
    block = block.replace("allowed_tiers  TEXT NOT NULL,", "allowed_tiers  TEXT NOT NULL")
    was = SCHEMA[:start] + block + SCHEMA[end:]
    assert "ship_line1" not in was[start:start + len(block)]

    path = client.tmp / "old.sqlite3"
    con = sqlite3.connect(path)
    con.executescript(was)
    con.commit()
    con.close()

    Database(path)                       # opening it is what migrates it
    con = sqlite3.connect(path)
    columns = {row[1] for row in con.execute("PRAGMA table_info(offer)")}
    con.close()
    assert {"ship_line1", "ship_line2", "ship_city", "ship_state", "ship_postcode"} <= columns


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


def test_the_tier_that_charges_for_carriage_bills_it_as_its_own_line(client):
    """The Tasting Case: $120 of wine and $40 of carriage, charged as two lines.

    Carriage stays its own line because wine and shipping book to different
    accounts in the books, and a tier that charges for it must not fold it
    into the wine. This is the whole path, not `_create_order` alone — the
    member picks the tier, a release charges them, and the invoice carries
    the total of both lines.
    """
    apply_(client)
    pay(client, approve(client), "tasting-case")

    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    assert order["subtotal_cents"] == 12000
    # three bottles to California is $36 on the sheet, and this tier has no
    # credit, so the member pays all of it
    assert order["shipping_cents"] == 3600
    assert order["total_cents"] == 15600
    assert order["status"] == "paid"

    lines = client.main.db.all("SELECT * FROM order_line WHERE order_id = ? ORDER BY id",
                               (order["id"],))
    assert len(lines) == 2
    assert lines[0]["total_cents"] == 12000
    assert "3 bottles" in lines[0]["description"]
    assert "shipping" in lines[1]["description"].lower()
    assert lines[1]["total_cents"] == 3600

    invoice = client.main.db.one("SELECT * FROM invoice WHERE order_id = ?", (order["id"],))
    assert invoice["total_cents"] == 15600


def test_the_tier_credit_comes_off_the_carriage_the_member_pays(client):
    """The Full Case puts $40 towards a box that costs $57 to Glen Ellen."""
    apply_(client)
    pay(client, approve(client), "full-case")

    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    assert order["subtotal_cents"] == 55000
    assert order["shipping_cents"] == 1700
    assert order["total_cents"] == 56700


def test_two_members_on_one_tier_pay_different_carriage(client):
    """Same wine, same credit, different distance.

    This is the whole reason carriage is priced at order time rather than
    written into the catalogue: the winery gives each of them the same $20
    and they pay what their own box costs.
    """
    apply_(client, email="west@example.com")
    join(client, approve(client), "founders-reserve")
    apply_(client, email="east@example.com")
    join(client, approve(client), "founders-reserve",
         ship_state="New York", ship_city="Brooklyn", ship_postcode="11201")
    plan_release(client)

    orders = client.main.db.all('SELECT * FROM "order" ORDER BY id')
    assert [o["shipping_cents"] for o in orders] == [2600, 3500]   # $46 and $55, less $20


# ── the webhook ──────────────────────────────────────────────────────────
def test_a_replayed_webhook_does_not_create_a_second_membership(client):
    apply_(client)
    token = approve(client)

    res = client.post(f"/club/offer/{token}/checkout",
                      data={"tier_key": "founders-reserve", **ADDRESS},
                      follow_redirects=False)
    sandbox = res.headers["location"]
    confirm = sandbox.replace("/card?", "/card/confirm?")

    client.get(confirm, follow_redirects=False)
    # the provider retries the identical delivery, as they all do
    client.get(confirm, follow_redirects=False)

    assert client.main.db.one("SELECT COUNT(*) c FROM membership")["c"] == 1
    # and joining bills nobody: the money waits for a release
    assert client.main.db.one('SELECT COUNT(*) c FROM "order"')["c"] == 0
    assert client.main.db.one("SELECT COUNT(*) c FROM invoice")["c"] == 0


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
    # The reason is the desk's record, not the applicant's letter: "the list
    # is full" is kinder than quoting an internal note back at somebody.
    told = letters(client, "cannot offer you an allocation")
    assert len(told) == 1
    assert "Full for the season" not in told[0]


def test_an_application_is_decided_once(client):
    app_id = apply_(client)
    approve(client, app_id)
    res = client.post(f"/admin/applications/{app_id}/decline", data={"reason": ""},
                      auth=ADMIN, follow_redirects=False)
    assert res.status_code == 400


BY_HAND = {
    "first_name": "Richard",
    "last_name": "Deane",
    "email": "Richard.Deane@Example.com",
    "state": "Oregon",
    "city": "Portland",
    "referral": "Wrote to the club",
    "note": "Asked by mail about the six-bottle allocation.",
}


def test_an_enquiry_entered_by_hand_joins_the_same_queue(client):
    page = client.get("/admin/applications/new", auth=ADMIN)
    assert page.status_code == 200
    assert "Wrote to the club" in page.text

    res = client.post("/admin/applications/new", data=BY_HAND, auth=ADMIN,
                      follow_redirects=False)
    assert res.status_code == 303, res.text

    row = client.main.db.one("SELECT * FROM application ORDER BY id DESC")
    assert res.headers["location"].startswith(f"/admin/applications/{row['id']}?done=")
    assert row["status"] == "pending"
    assert row["email"] == "richard.deane@example.com"      # the form's casing is not kept
    assert row["referral"] == "Wrote to the club"
    # And the log says a person put it there, which the website's own requests
    # do not carry.
    actions = [e["action"] for e in client.main.db.all(
        "SELECT * FROM event WHERE subject = 'application' AND subject_id = ?", (row["id"],))]
    assert "received" in actions and "entered_by_hand" in actions

    # From here it is an ordinary application: approving it sends the invitation.
    approve(client, int(row["id"]))
    assert client.main.db.one(
        "SELECT * FROM application WHERE id = ?", (row["id"],))["status"] == "approved"


def test_entering_someone_twice_by_hand_does_not_duplicate_them(client):
    client.post("/admin/applications/new", data=BY_HAND, auth=ADMIN, follow_redirects=False)
    res = client.post("/admin/applications/new", data=BY_HAND, auth=ADMIN,
                      follow_redirects=False)
    assert res.status_code == 303
    assert "already had a request open" in unquote(res.headers["location"])
    assert client.main.db.one("SELECT COUNT(*) AS n FROM application")["n"] == 1


def test_a_refused_hand_entry_comes_back_as_the_form(client):
    res = client.post("/admin/applications/new",
                      data={**BY_HAND, "email": "not-an-address"}, auth=ADMIN)
    assert res.status_code == 200
    assert "does not look right" in res.text
    assert "not-an-address" in res.text               # what was typed is still there
    assert client.main.db.one("SELECT COUNT(*) AS n FROM application")["n"] == 0


def test_hand_entry_is_shut_without_credentials(client):
    assert client.get("/admin/applications/new").status_code == 401
    assert client.post("/admin/applications/new", data=BY_HAND).status_code == 401


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
    assert keys == ["tasting-case", "founders-reserve", "full-case"]
    # Smallest first, which is the order the landing page lays them out in.
    assert [t["bottles"] for t in body["tiers"]] == [3, 6, 12]
    # Both are chooseable today. The field is still read and still honoured —
    # see test_a_tier_that_was_not_offered_is_refused — so a
    # by-invitation tier can come back without touching the code.
    assert all(t["invite_only"] is False for t in body["tiers"])


def test_states_are_the_ones_we_can_ship_to(client):
    states = client.get("/api/states").json()["states"]
    assert "California" in states
    assert "Utah" not in states


# ── telling members before the money moves ───────────────────────────────
# The club has promised this in writing since the beginning: the welcome
# letter, every receipt and the offer page all say we write two weeks before
# a release so a member can change quantities or skip. Nothing sent it until
# now, which made the first word of a charge the charge itself.
def notices(client) -> list[str]:
    """The release notices sitting in the outbox, newest last."""
    return [p.read_text() for p in sorted((client.tmp / "outbox").glob("*.eml"))
            if "in about two weeks" in p.read_text()]


def test_a_member_is_told_what_is_coming_and_what_it_costs(client):
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client, "Spring 2027")

    res = client.post(f"/admin/releases/{release_id}/notify", auth=ADMIN,
                      follow_redirects=False)
    assert res.status_code == 303
    assert "1 member(s) told" in unquote(res.headers["location"])

    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    sent = notices(client)
    assert len(sent) == 1
    body = sent[0]
    # The figures are the order's own, not a reminder that a season is coming.
    assert f"{order['total_cents'] / 100:,.2f}" in body
    assert "Spring 2027" in body
    assert "reply to this message" in body.lower()


def test_telling_them_charges_nobody(client):
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)
    before = client.main.db.one("SELECT COUNT(*) c FROM invoice")["c"]

    client.post(f"/admin/releases/{release_id}/notify", auth=ADMIN,
                follow_redirects=False)

    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    assert order["paid_at"] is None
    assert order["status"] == "pending_payment"
    # Counted either side rather than against zero: joining already raised
    # one, and what matters is that warning somebody raises none.
    assert client.main.db.one("SELECT COUNT(*) c FROM invoice")["c"] == before


def _backdate_the_notice(client, hours: int) -> None:
    from datetime import datetime, timedelta, timezone      # noqa: PLC0415
    stamp = (datetime.now(timezone.utc)
             - timedelta(hours=hours)).isoformat(timespec="seconds")
    with client.main.db.transaction() as con:
        con.execute("UPDATE event SET created_at = ?"
                    " WHERE action = 'release_notice_sent'", (stamp,))


def test_charging_the_same_day_as_the_warning_is_refused(client):
    """The warning is only a warning if the money has not already moved.

    It is also two messages to one person on one day, which is what a relay's
    daily allowance runs out of first.
    """
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)
    client.post(f"/admin/releases/{release_id}/notify", auth=ADMIN,
                follow_redirects=False)

    res = client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN,
                      follow_redirects=False)

    assert res.status_code == 303, "a refusal, not a crash"
    said = unquote(res.headers["location"])
    assert "Not done" in said and "24h" in said
    assert "Orders" in said, "it has to name the way round"
    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    assert order["paid_at"] is None and order["status"] == "pending_payment"


def test_once_the_day_has_passed_the_release_charges(client):
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)
    client.post(f"/admin/releases/{release_id}/notify", auth=ADMIN,
                follow_redirects=False)
    _backdate_the_notice(client, 25)

    client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN,
                follow_redirects=False)

    assert client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')["paid_at"]


def test_a_release_nobody_was_warned_about_is_not_held_back(client):
    """The gap follows a notice. No notice is a different decision."""
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)

    client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN,
                follow_redirects=False)

    assert client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')["paid_at"]


def test_one_order_can_still_be_charged_while_the_release_waits(client):
    """The member who rings up and asks to be taken now.

    Holding the whole release back must not strand a single order, or the
    rule becomes something to switch off rather than work with.
    """
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)
    client.post(f"/admin/releases/{release_id}/notify", auth=ADMIN,
                follow_redirects=False)
    order_id = int(client.main.db.one('SELECT id FROM "order" ORDER BY id DESC')["id"])

    res = client.post(f"/admin/orders/{order_id}/charge", auth=ADMIN,
                      follow_redirects=False)

    assert res.status_code == 303
    assert client.main.db.one('SELECT * FROM "order" WHERE id = ?',
                              (order_id,))["paid_at"]


def test_nobody_is_warned_about_money_that_has_already_gone(client):
    """Pressing it after Charge must not tell a paid member a charge is coming."""
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)
    client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN,
                follow_redirects=False)

    res = client.post(f"/admin/releases/{release_id}/notify", auth=ADMIN,
                      follow_redirects=False)
    assert "no unpaid orders" in unquote(res.headers["location"])
    assert notices(client) == []


def test_telling_them_twice_writes_twice_and_breaks_nothing(client):
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)
    for _ in range(2):
        client.post(f"/admin/releases/{release_id}/notify", auth=ADMIN,
                    follow_redirects=False)
    assert len(notices(client)) == 2, "a nuisance, not a failure"
    assert client.main.db.one('SELECT paid_at FROM "order" ORDER BY id DESC')["paid_at"] is None


def test_the_notice_is_recorded_against_the_order(client):
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)
    client.post(f"/admin/releases/{release_id}/notify", auth=ADMIN,
                follow_redirects=False)
    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    events = client.main.db.all(
        "SELECT action FROM event WHERE subject = 'order' AND subject_id = ?",
        (order["id"],))
    assert "release_notice_sent" in [e["action"] for e in events]


def test_a_three_bottle_case_pays_its_own_carriage_in_the_notice(client):
    """The Tasting Case carries no shipping credit, so the line is money."""
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)
    client.post(f"/admin/releases/{release_id}/notify", auth=ADMIN,
                follow_redirects=False)
    order = client.main.db.one('SELECT * FROM "order" ORDER BY id DESC')
    assert order["shipping_cents"] > 0, "nothing is credited on three bottles"
    body = notices(client)[0]
    assert f"{order['shipping_cents'] / 100:,.2f}" in body
    assert "included" not in body


# ── the two silences ─────────────────────────────────────────────────────
def test_the_form_writes_back(client):
    """Somebody filled it in and heard nothing. For days, that was the winery."""
    apply_(client)
    told = letters(client, "We have your request")
    assert len(told) == 1
    assert "read by hand" in told[0]
    # No deadline is promised. Approval is a person reading a request, and a
    # "within 48 hours" that slips is worse than no promise at all.
    for promise in ("hours", "business day", "tomorrow", "immediately"):
        assert promise not in told[0].lower(), f"the letter promises {promise!r}"


def test_the_form_says_the_harder_thing_to_a_state_we_cannot_reach(client):
    res = client.post("/api/waiting", json={
        "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
        "state": "Arkansas"})
    assert res.status_code == 201
    told = letters(client, "Arkansas")
    assert len(told) == 1
    assert "cannot ship wine to Arkansas" in told[0]
    # And the one route that makes them a member this week.
    assert "address somewhere" in told[0]


def test_an_acknowledgement_that_fails_does_not_fail_the_request(client, monkeypatch):
    """The row is saved and the browser is owed its answer."""
    def boom(*a, **k):
        raise RuntimeError("smtp fell over")
    monkeypatch.setattr(client.main, "send_application_received", boom)
    app_id = apply_(client)
    assert app_id
    events = client.main.db.all(
        "SELECT action FROM event WHERE subject = 'application' AND subject_id = ?",
        (app_id,))
    assert "acknowledgement_failed" in [e["action"] for e in events]


def test_the_winery_hears_about_a_declined_card(client):
    """The member was told. Nobody was telling the winery, and it costs money."""
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)
    from app.payments.base import Charge
    client.main.payments.charge = lambda **kw: Charge(
        id="pi_x", paid=False, error="Your card has expired.", needs_member=False)
    client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN,
                follow_redirects=False)

    desk = letters(client, "refused")
    assert len(desk) == 1
    assert "1 card was refused" in desk[0]
    assert "Your card has expired." in desk[0]
    assert "wine has already left the cellar" in desk[0]


def test_many_declines_are_one_message_not_twenty(client):
    """Twenty alerts on a despatch morning is a mailbox nobody reads."""
    for n in range(3):
        pay(client, approve(client, apply_(client, email=f"m{n}@example.com")),
            "tasting-case", )
    release_id = plan_release(client)
    from app.payments.base import Charge
    client.main.payments.charge = lambda **kw: Charge(
        id="pi_x", paid=False, error="Declined.", needs_member=False)
    client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN,
                follow_redirects=False)

    desk = letters(client, "cards were refused")
    assert len(desk) == 1, "one digest for the release, not one per card"
    assert "3 cards were refused" in desk[0]


def test_the_declines_alert_goes_to_the_winery_not_the_member(client):
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)
    from app.payments.base import Charge
    client.main.payments.charge = lambda **kw: Charge(
        id="pi_x", paid=False, error="Declined.", needs_member=False)
    client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN,
                follow_redirects=False)
    desk = letters(client, "refused")[0]
    assert client.main.settings.alerts_to in desk
    assert "ada@example.com" not in desk.split("Subject:")[0]


def test_the_club_does_not_sign_letters_to_itself(client):
    """An operational alert is not a letter from Cecilia to Cecilia.

    The house wrapper signs every message "Cecilia Valdivia, Owner &
    Winemaker" and closes with "you are receiving this because you asked for
    an allocation... you must be 21 or older to purchase". On a note the
    service sends the desk, she is signing to herself and the consumer
    footer is addressed to somebody who did neither of those things.
    """
    pay(client, approve(client, apply_(client)), "tasting-case")
    release_id = plan_release(client)
    from app.payments.base import Charge
    client.main.payments.charge = lambda **kw: Charge(
        id="pi_x", paid=False, error="Declined.", needs_member=False)
    client.post(f"/admin/releases/{release_id}/charge", auth=ADMIN,
                follow_redirects=False)

    desk = letters(client, "refused")[0]
    assert "you asked for an allocation" not in desk
    assert "21 or older" not in desk
    assert "Owner & Winemaker" not in desk
    assert "Nobody outside the winery sees this" in desk

    # The member's own letter keeps both, because they are for a member.
    theirs = letters(client, "/club/card/")[0]
    assert "21 or older" in theirs
    assert "Owner & Winemaker" in theirs
