"""The cellar and the release plan, through the service.

The planner's arithmetic is tested on its own in test_stock.py. What is here
is the part that could still be wrong once a database and a desk are involved:
that bottles come out of stock in the same commit as the orders they pay for,
that running generation twice takes the bottles once, and that a club which
has not written down its inventory yet can still ship.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ADMIN = ("cellar", "test-password")

ADDRESS = {
    "ship_line1": "1200 Arnold Drive",
    "ship_line2": "",
    "ship_city": "Glen Ellen",
    "ship_state": "California",
    "ship_postcode": "95442",
}

APPLICATION = {
    "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
    "state": "California", "tier_interest": "founders-reserve",
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
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


# ── getting a member to the point of being owed wine ─────────────────────
def join(client, email: str, tier: str = "founders-reserve", **overrides) -> int:
    """Application to membership with a card on file. No money moves."""
    res = client.post("/api/applications",
                      json={**APPLICATION, "email": email, **overrides})
    assert res.status_code == 201, res.text
    app_id = res.json()["application_id"]
    client.post(f"/admin/applications/{app_id}/approve",
                data={"tiers": ["founders-reserve", "full-case"]},
                auth=ADMIN, follow_redirects=False)
    offer = client.main.db.one(
        "SELECT * FROM offer WHERE application_id = ? ORDER BY id DESC", (app_id,))
    token = client.main.flow.offer_token(int(offer["id"]))

    address = {**ADDRESS, **{k: v for k, v in overrides.items() if k.startswith("ship_")}}
    res = client.post(f"/club/offer/{token}/checkout",
                      data={"tier_key": tier, **address}, follow_redirects=False)
    assert res.status_code == 303, res.text
    sandbox = res.headers["location"]
    assert "/club/sandbox/card" in sandbox
    page = client.get(sandbox)
    assert page.status_code == 200
    done = client.get(sandbox.replace("/card?", "/card/confirm?"), follow_redirects=False)
    assert done.status_code == 303, done.text
    return int(client.main.db.one("SELECT id FROM membership ORDER BY id DESC")["id"])


def wine(client, sku: str, name: str) -> int:
    res = client.post("/admin/cellar/wines", data={"sku": sku, "name": name, "vintage": "2021"},
                      auth=ADMIN, follow_redirects=False)
    assert res.status_code == 303, unquote(res.headers.get("location", ""))
    return int(client.main.db.one("SELECT id FROM wine ORDER BY id DESC")["id"])


def received(client, wine_id: int, bottles: int) -> None:
    res = client.post(f"/admin/cellar/wines/{wine_id}/bottles",
                      data={"bottles": str(bottles), "how": "received"},
                      auth=ADMIN, follow_redirects=False)
    assert res.status_code == 303, unquote(res.headers.get("location", ""))


def release(client, name: str = "Spring 2026") -> int:
    client.post("/admin/releases", data={"name": name, "ships_on": "2026-04-15"},
                auth=ADMIN, follow_redirects=False)
    return int(client.main.db.one('SELECT id FROM "release" ORDER BY id DESC')["id"])


def compose(client, release_id: int, tier: str, bottles: dict[int, int]) -> str:
    data = {"tier_key": tier, **{f"bottles_{k}": str(v) for k, v in bottles.items()}}
    res = client.post(f"/admin/releases/{release_id}/compose", data=data,
                      auth=ADMIN, follow_redirects=False)
    assert res.status_code == 303
    return unquote(res.headers["location"])


def on_hand(client, wine_id: int) -> int:
    row = client.main.db.one(
        "SELECT COALESCE(SUM(bottles), 0) AS n FROM stock_move WHERE wine_id = ?", (wine_id,))
    return int(row["n"])


def orders(client, release_id: int) -> int:
    return int(client.main.db.one(
        'SELECT COUNT(*) c FROM "order" WHERE release_id = ?', (release_id,))["c"])


# ── the cellar ───────────────────────────────────────────────────────────
def test_bottles_in_and_a_count_that_corrects_them(client):
    cab = wine(client, "est-cab-21", "2021 Estate Cabernet")
    received(client, cab, 120)
    assert on_hand(client, cab) == 120

    res = client.post(f"/admin/cellar/wines/{cab}/bottles",
                      data={"bottles": "114", "how": "count"},
                      auth=ADMIN, follow_redirects=False)
    assert "corrected the books by -6" in unquote(res.headers["location"])
    assert on_hand(client, cab) == 114

    # the correction is a movement of its own, not an edit of the first one
    moves = client.main.db.all("SELECT * FROM stock_move WHERE wine_id = ? ORDER BY id", (cab,))
    assert [m["bottles"] for m in moves] == [120, -6]
    assert [m["kind"] for m in moves] == ["received", "count"]
    assert "counted 114, books said 120" in moves[1]["note"]


def test_a_count_that_agrees_writes_no_movement(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    received(client, cab, 60)
    res = client.post(f"/admin/cellar/wines/{cab}/bottles",
                      data={"bottles": "60", "how": "count"},
                      auth=ADMIN, follow_redirects=False)

    assert "already said so" in unquote(res.headers["location"])
    assert client.main.db.one(
        "SELECT COUNT(*) c FROM stock_move WHERE wine_id = ?", (cab,))["c"] == 1


def test_a_wine_needs_a_code_and_a_name(client):
    res = client.post("/admin/cellar/wines", data={"sku": "", "name": "Nameless"},
                      auth=ADMIN, follow_redirects=False)
    assert "needs a code and a name" in unquote(res.headers["location"])
    assert client.main.db.one("SELECT COUNT(*) c FROM wine")["c"] == 0


def test_the_same_code_twice_is_refused(client):
    wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    res = client.post("/admin/cellar/wines",
                      data={"sku": "est-cab-21", "name": "Something else"},
                      auth=ADMIN, follow_redirects=False)
    assert "already in the cellar" in unquote(res.headers["location"])


def test_bottles_have_to_be_a_number(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    res = client.post(f"/admin/cellar/wines/{cab}/bottles",
                      data={"bottles": "a dozen", "how": "received"},
                      auth=ADMIN, follow_redirects=False)
    assert "whole number" in unquote(res.headers["location"])
    assert on_hand(client, cab) == 0


def test_the_cellar_is_shut_without_credentials(client):
    assert client.get("/admin/cellar").status_code == 401


# ── composing a release ──────────────────────────────────────────────────
def test_a_composition_that_is_not_the_promised_count_is_said_so_on_saving(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    release_id = release(client)
    join(client, "ada@example.com")

    where = compose(client, release_id, "founders-reserve", {cab: 5})
    assert "that is 5 bottles and The Founder's Reserve promises 6" in where

    where = compose(client, release_id, "founders-reserve", {cab: 6})
    assert "Composed." in where


def test_zero_takes_a_wine_out_of_the_shipment(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    chard = wine(client, "EST-CHD-23", "2023 Estate Chardonnay")
    release_id = release(client)
    compose(client, release_id, "founders-reserve", {cab: 4, chard: 2})
    assert client.main.db.one(
        "SELECT COUNT(*) c FROM release_item WHERE release_id = ?", (release_id,))["c"] == 2

    compose(client, release_id, "founders-reserve", {cab: 6, chard: 0})
    rows = client.main.db.all(
        "SELECT * FROM release_item WHERE release_id = ?", (release_id,))
    assert [(r["wine_id"], r["bottles"]) for r in rows] == [(cab, 6)]


def test_a_tier_that_is_not_in_the_catalogue_cannot_be_composed(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    release_id = release(client)
    where = compose(client, release_id, "cellar-door", {cab: 4})
    assert "not a tier in the catalogue" in where


# ── raising orders against the stock ─────────────────────────────────────
def test_a_composed_release_takes_the_bottles_it_raised_orders_for(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    chard = wine(client, "EST-CHD-23", "2023 Estate Chardonnay")
    received(client, cab, 12)
    received(client, chard, 8)
    for n in range(3):
        join(client, f"member{n}@example.com")

    release_id = release(client)
    compose(client, release_id, "founders-reserve", {cab: 4, chard: 2})
    client.post(f"/admin/releases/{release_id}/generate", auth=ADMIN, follow_redirects=False)

    assert orders(client, release_id) == 3
    assert on_hand(client, cab) == 0        # 12 − 3 × 4
    assert on_hand(client, chard) == 2      # 8 − 3 × 2

    moves = client.main.db.all(
        "SELECT * FROM stock_move WHERE release_id = ? ORDER BY wine_id", (release_id,))
    assert [m["bottles"] for m in moves] == [-12, -6]
    assert moves[0]["note"] == "3 × founders-reserve"


def test_nobody_is_billed_for_wine_that_is_not_there(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    received(client, cab, 13)               # two shipments of six, and one spare
    for n in range(3):
        join(client, f"member{n}@example.com")

    release_id = release(client)
    compose(client, release_id, "founders-reserve", {cab: 6})
    res = client.post(f"/admin/releases/{release_id}/generate",
                      auth=ADMIN, follow_redirects=False)

    assert orders(client, release_id) == 2
    assert "1 member(s) could not be filled" in unquote(res.headers["location"])
    assert on_hand(client, cab) == 1

    # the one left out is the one who joined last, and has no order at all
    last = client.main.db.one("SELECT id FROM membership ORDER BY id DESC")["id"]
    assert client.main.db.one(
        'SELECT COUNT(*) c FROM "order" WHERE membership_id = ?', (last,))["c"] == 0


def test_raising_orders_twice_takes_the_bottles_once(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    received(client, cab, 24)
    join(client, "ada@example.com")
    release_id = release(client)
    compose(client, release_id, "founders-reserve", {cab: 6})

    client.post(f"/admin/releases/{release_id}/generate", auth=ADMIN, follow_redirects=False)
    client.post(f"/admin/releases/{release_id}/generate", auth=ADMIN, follow_redirects=False)

    assert orders(client, release_id) == 1
    assert on_hand(client, cab) == 18


def test_a_member_who_joins_after_the_first_run_takes_their_own_bottles(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    received(client, cab, 24)
    join(client, "ada@example.com")
    release_id = release(client)
    compose(client, release_id, "founders-reserve", {cab: 6})
    client.post(f"/admin/releases/{release_id}/generate", auth=ADMIN, follow_redirects=False)
    assert on_hand(client, cab) == 18

    join(client, "grace@example.com")
    client.post(f"/admin/releases/{release_id}/generate", auth=ADMIN, follow_redirects=False)

    assert orders(client, release_id) == 2
    assert on_hand(client, cab) == 12


def test_an_uncomposed_release_still_ships_and_says_it_checked_nothing(client):
    """A club that has not written its inventory down has to be able to ship.

    The bottles in the cellar are deliberately not enough for the shipment, to
    prove no stock check happened rather than one that happened to pass.
    """
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    received(client, cab, 1)
    join(client, "ada@example.com")
    release_id = release(client)

    res = client.post(f"/admin/releases/{release_id}/generate",
                      auth=ADMIN, follow_redirects=False)

    assert orders(client, release_id) == 1
    assert "no composition, so nothing checked the stock" in unquote(res.headers["location"])
    assert on_hand(client, cab) == 1


def test_a_second_release_sees_what_the_first_one_committed(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    received(client, cab, 12)
    join(client, "ada@example.com")
    join(client, "grace@example.com")

    spring = release(client, "Spring 2026")
    compose(client, spring, "founders-reserve", {cab: 6})
    client.post(f"/admin/releases/{spring}/generate", auth=ADMIN, follow_redirects=False)
    assert orders(client, spring) == 2
    assert on_hand(client, cab) == 0

    autumn = release(client, "Fall 2026")
    compose(client, autumn, "founders-reserve", {cab: 6})
    res = client.post(f"/admin/releases/{autumn}/generate",
                      auth=ADMIN, follow_redirects=False)

    assert orders(client, autumn) == 0
    assert "2 member(s) could not be filled" in unquote(res.headers["location"])


def test_the_plan_screen_names_the_wine_that_ran_out(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    chard = wine(client, "EST-CHD-23", "2023 Estate Chardonnay")
    received(client, cab, 99)
    received(client, chard, 2)
    join(client, "ada@example.com")
    join(client, "grace@example.com")

    release_id = release(client)
    compose(client, release_id, "founders-reserve", {cab: 4, chard: 2})
    page = client.get(f"/admin/releases/{release_id}/plan", auth=ADMIN)

    assert page.status_code == 200
    assert "1 of 2 members can be filled" in page.text
    assert "out of 2023 Estate Chardonnay" in page.text


def test_the_plan_flags_a_member_with_no_card_without_holding_their_wine(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    received(client, cab, 12)
    membership_id = join(client, "ada@example.com")
    with client.main.db.transaction() as con:
        con.execute("UPDATE membership SET provider_payment_method = NULL WHERE id = ?",
                    (membership_id,))

    release_id = release(client)
    compose(client, release_id, "founders-reserve", {cab: 6})
    page = client.get(f"/admin/releases/{release_id}/plan", auth=ADMIN)

    assert "Allocated, but no card on file" in page.text
    client.post(f"/admin/releases/{release_id}/generate", auth=ADMIN, follow_redirects=False)
    assert orders(client, release_id) == 1
    assert on_hand(client, cab) == 6


def test_a_paused_member_is_not_owed_a_shipment_and_gets_no_bottles(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    received(client, cab, 12)
    membership_id = join(client, "ada@example.com")
    client.post(f"/admin/memberships/{membership_id}/status", data={"target": "paused"},
                auth=ADMIN, follow_redirects=False)

    release_id = release(client)
    compose(client, release_id, "founders-reserve", {cab: 6})
    client.post(f"/admin/releases/{release_id}/generate", auth=ADMIN, follow_redirects=False)

    assert orders(client, release_id) == 0
    assert on_hand(client, cab) == 12


def test_the_two_tiers_draw_on_the_same_bottles(client):
    cab = wine(client, "EST-CAB-21", "2021 Estate Cabernet")
    received(client, cab, 17)
    join(client, "ada@example.com", "founders-reserve")
    join(client, "grace@example.com", "full-case")

    release_id = release(client)
    compose(client, release_id, "founders-reserve", {cab: 6})
    compose(client, release_id, "full-case", {cab: 12})
    client.post(f"/admin/releases/{release_id}/generate", auth=ADMIN, follow_redirects=False)

    # six for the first, and eleven left is one short of a case
    assert orders(client, release_id) == 1
    assert on_hand(client, cab) == 11


def test_an_older_database_grows_the_cellar(client):
    """A database made before this release has no wine table at all."""
    from app.db import Database, SCHEMA

    # Built by running the schema and then dropping what this release added,
    # rather than by editing the schema text: a comment in there contains a
    # semicolon, and cutting statements apart on those produced a database
    # that was wrong in a way the test would not have noticed.
    path = client.tmp / "older.sqlite3"
    import sqlite3
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for table in ("release_item", "stock_move", "wine"):
        con.execute(f"DROP TABLE {table}")
    con.commit()
    assert con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = 'wine'").fetchone()[0] == 0
    con.close()

    db = Database(path)
    assert db.one("SELECT COUNT(*) c FROM wine")["c"] == 0
    assert db.one("SELECT COUNT(*) c FROM stock_move")["c"] == 0
    assert db.one("SELECT COUNT(*) c FROM release_item")["c"] == 0
