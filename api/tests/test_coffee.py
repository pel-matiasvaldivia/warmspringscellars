"""The roastery's waiting list, end to end.

Deliberately thin where the wine side is thick: coffee is not alcohol, so none
of the licensed-state, age or tier machinery applies. What it does share is
the mailbox, the rate limit and the desk.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .test_flow import ADMIN, apply_, approve, client, pay        # noqa: E402,F401

WANTS = {"name": "Ana Díaz", "email": "ana@example.com", "coming_from": "coffee"}


def ask(c, **overrides):
    return c.post("/api/coffee-interest", json={**WANTS, **overrides})


def rows(c):
    return [dict(r) for r in c.main.db.all("SELECT * FROM coffee_interest ORDER BY id")]


# ── the form ─────────────────────────────────────────────────────────────
def test_a_name_reaches_the_list(client):
    res = ask(client)
    assert res.status_code == 201, res.text
    assert res.json()["ok"] is True

    [row] = rows(client)
    assert row["name"] == "Ana Díaz"
    assert row["email"] == "ana@example.com"
    assert row["coming_from"] == "coffee"
    assert row["status"] == "new"


def test_the_email_is_normalised_like_the_wine_form(client):
    ask(client, email="  ANA@Example.COM ")
    assert rows(client)[0]["email"] == "ana@example.com"


def test_asking_twice_while_waiting_is_the_same_person(client):
    first = ask(client).json()["interest_id"]
    second = ask(client).json()["interest_id"]
    assert first == second
    assert len(rows(client)) == 1


def test_a_name_that_was_already_dealt_with_can_ask_again(client):
    first = ask(client).json()["interest_id"]
    client.post(f"/admin/coffee/{first}/status", data={"target": "archived"},
                auth=ADMIN, follow_redirects=False)
    second = ask(client).json()["interest_id"]
    # The earlier row is closed, so this is a fresh request and not a duplicate.
    assert second != first
    assert len(rows(client)) == 2


def test_the_basics_are_required(client):
    assert ask(client, name="  ").status_code == 422
    assert ask(client, email="not-an-email").status_code == 422
    assert ask(client, coming_from="").status_code == 422
    assert ask(client, coming_from="smuggled").status_code == 422
    assert rows(client) == []


def test_it_shares_the_rate_limit_with_the_wine_form(client):
    # One winery behind both pages: somebody hammering either is hammering us.
    for n in range(8):
        ask(client, email=f"x{n}@example.com")
    assert ask(client, email="late@example.com").status_code == 429


def test_coffee_asks_nothing_about_wine(client):
    """No state, no age, no tier — none of the alcohol rules apply."""
    res = ask(client, email="utah@example.com")
    assert res.status_code == 201
    row = rows(client)[0]
    assert "state" not in row and "tier_key" not in row


# ── the desk ─────────────────────────────────────────────────────────────
def test_the_desk_shows_who_is_waiting(client):
    ask(client)
    page = client.get("/admin/coffee", auth=ADMIN)
    assert page.status_code == 200
    assert "ana@example.com" in page.text
    assert "Ana Díaz" in page.text
    assert "roastery" in page.text.lower()


def test_the_desk_needs_the_password(client):
    assert client.get("/admin/coffee").status_code == 401
    assert client.post("/admin/coffee/1/status").status_code == 401


def test_a_cellar_member_is_marked_as_one(client):
    """The page promises members are asked first, so the desk must see them."""
    apply_(client)                       # matias@example.com, through to paid
    pay(client, approve(client), "founders-reserve")
    ask(client, name="Matías Valdivia", email="matias@example.com", coming_from="both")
    ask(client, name="A Stranger", email="stranger@example.com")

    page = client.get("/admin/coffee", auth=ADMIN)
    assert "The Founder&#39;s Reserve" in page.text or "Founder" in page.text
    assert "not on the wine list" in page.text
    # The member sorts above the stranger, which is the order to answer in.
    assert page.text.index("matias@example.com") < page.text.index("stranger@example.com")


def test_the_match_is_by_email_and_only_for_live_memberships(client):
    apply_(client)
    pay(client, approve(client), "founders-reserve")
    membership_id = int(client.main.db.one("SELECT id FROM membership")["id"])
    client.post(f"/admin/memberships/{membership_id}/status",
                data={"target": "cancelled"}, auth=ADMIN, follow_redirects=False)

    ask(client, email="matias@example.com", coming_from="both")
    page = client.get("/admin/coffee", auth=ADMIN)
    # Cancelled is not a member. Saying otherwise would put somebody at the
    # front of a queue they left.
    assert "not on the wine list" in page.text


def test_the_desk_moves_a_request_through_its_states(client):
    interest_id = ask(client).json()["interest_id"]

    for target in ("invited", "archived", "new"):
        res = client.post(f"/admin/coffee/{interest_id}/status",
                          data={"target": target}, auth=ADMIN, follow_redirects=False)
        assert res.status_code == 303
        assert rows(client)[0]["status"] == target

    assert rows(client)[0]["decided_by"] == ADMIN[0]


def test_a_state_the_list_does_not_have_is_refused(client):
    interest_id = ask(client).json()["interest_id"]
    res = client.post(f"/admin/coffee/{interest_id}/status",
                      data={"target": "shipped"}, auth=ADMIN, follow_redirects=False)
    assert res.status_code == 400
    assert rows(client)[0]["status"] == "new"


def test_the_wine_desk_is_untouched_by_any_of_this(client):
    ask(client)
    apply_(client)
    # A coffee request must never appear as a wine application.
    assert "ana@example.com" not in client.get("/admin", auth=ADMIN).text
    assert len(client.main.db.all("SELECT * FROM application")) == 1
