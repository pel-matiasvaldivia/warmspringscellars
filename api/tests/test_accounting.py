"""The books: the queue, and the QuickBooks adapter's own logic.

The queue is tested against the real service, with the fake ledger standing in
for Intuit exactly as the fake payment provider stands in for Stripe.

The QuickBooks adapter is tested against a stub transport. That proves the
adapter's logic — token rotation, matching a customer by email, adopting an
invoice QuickBooks already holds, refreshing once on a 401 — and proves
nothing about Intuit's field validation, which only a sandbox company can.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.accounting import queue as ledger_queue                 # noqa: E402
from app.accounting.base import AccountingError, NotConnected     # noqa: E402
from app.accounting.fake import FakeLedger                        # noqa: E402
from app.accounting.quickbooks import (                           # noqa: E402
    MINOR_VERSION,
    PRODUCTION_API,
    SANDBOX_API,
    QuickBooksLedger,
    TokenStore,
    Tokens,
    _escape,
)

from .test_flow import ADMIN, apply_, approve, client, pay        # noqa: E402,F401


def tasks(c) -> list[dict]:
    return [dict(r) for r in c.main.db.all("SELECT * FROM ledger_task ORDER BY id")]


def take_delivery(c, tier: str = "founders-reserve") -> None:
    """One paid membership, which is one invoice and one payment."""
    apply_(c)
    token = approve(c)
    pay(c, token, tier)


# ── the queue ────────────────────────────────────────────────────────────
def test_an_invoice_queues_its_paperwork_as_it_is_issued(client):
    take_delivery(client)

    queued = tasks(client)
    assert [t["kind"] for t in queued] == ["invoice", "payment"]
    assert {t["status"] for t in queued} == {"pending"}
    # Both describe the same invoice: a payment is filed against it.
    assert len({t["subject_id"] for t in queued}) == 1


def test_nothing_is_pushed_while_quickbooks_is_not_connected(client):
    take_delivery(client)

    # The service ships with no ledger configured, which is a NullLedger.
    assert client.main.ledger.live is False
    said = ledger_queue.drain(client.main.db, client.main.ledger)
    assert "not connected" in said[0]
    assert [t["status"] for t in tasks(client)] == ["pending", "pending"]


def test_draining_files_the_invoice_and_then_its_payment(client):
    take_delivery(client)
    fake = FakeLedger()

    said = ledger_queue.drain(client.main.db, fake)
    assert len(said) == 2

    queued = tasks(client)
    assert [t["status"] for t in queued] == ["done", "done"]
    assert queued[0]["external_id"].startswith("fake-inv-WSC-INV-")
    assert queued[1]["external_id"].startswith("fake-pay-WSC-INV-")
    assert queued[0]["provider"] == "fake"

    number = queued[0]["external_id"].removeprefix("fake-inv-")
    invoice = fake.invoices[number]
    # The document carries what an accountant needs: who, how much, and the
    # split between wine and the shipping it travelled on.
    assert invoice.customer.email == "matias@example.com"
    assert invoice.total_cents == 34000
    # Every tier on offer now includes carriage, so there is one line. The
    # split is still exercised, in the adapter tests below.
    assert [line.kind for line in invoice.lines] == ["wine"]
    assert sum(line.total_cents for line in invoice.lines) == 34000
    assert fake.payments[number].amount_cents == 34000


def test_a_payment_waits_for_the_invoice_it_links_to(client):
    take_delivery(client)
    payment_task = client.main.db.one(
        "SELECT t.*, i.number FROM ledger_task t JOIN invoice i ON i.id = t.subject_id"
        " WHERE t.kind = 'payment'")

    said = ledger_queue.push_one(client.main.db, FakeLedger(), payment_task)
    assert "has not reached the ledger yet" in said
    # Held, not failed: there is nothing wrong with the document.
    row = client.main.db.one("SELECT * FROM ledger_task WHERE kind = 'payment'")
    assert row["status"] == "pending"
    assert row["attempts"] == 0


def test_filing_the_same_document_twice_does_not_duplicate_it(client):
    take_delivery(client)
    fake = FakeLedger()

    ledger_queue.drain(client.main.db, fake)
    again = ledger_queue.drain(client.main.db, fake)

    assert again == []                       # nothing was left outstanding
    assert len(fake.invoices) == 1
    assert len(fake.payments) == 1


def test_a_refused_push_is_recorded_and_can_be_retried_from_the_desk(client):
    take_delivery(client)
    broken = FakeLedger(fail_with="Business Validation Error: account is closed")
    client.main.ledger = broken

    client.post("/admin/accounting/sync", auth=ADMIN, follow_redirects=False)
    row = client.main.db.one("SELECT * FROM ledger_task WHERE kind = 'invoice'")
    assert row["status"] == "failed"
    assert row["attempts"] == 1
    assert "account is closed" in row["last_error"]

    page = client.get("/admin/accounting", auth=ADMIN)
    assert "account is closed" in page.text
    assert "Retry" in page.text

    broken.fail_with = ""
    res = client.post(f"/admin/accounting/tasks/{row['id']}/retry",
                      auth=ADMIN, follow_redirects=False)
    assert res.status_code == 303
    after = client.main.db.one("SELECT * FROM ledger_task WHERE id = ?", (row["id"],))
    assert after["status"] == "done"
    assert after["last_error"] is None
    # The failed attempt is kept: two tries is the history, not one.
    assert after["attempts"] == 2


def test_a_release_does_not_queue_the_same_invoice_twice(client):
    take_delivery(client)
    client.post("/admin/releases", data={"name": "Spring 2026", "ships_on": "2026-04-15"},
                auth=ADMIN, follow_redirects=False)
    release = client.main.db.one('SELECT * FROM "release" ORDER BY id DESC')
    for _ in range(2):
        client.post(f"/admin/releases/{release['id']}/generate", auth=ADMIN,
                    follow_redirects=False)

    # The release's orders are unpaid, so they have no invoice and nothing to
    # file. What must not happen is a second row for the invoice that exists.
    assert len([t for t in tasks(client) if t["kind"] == "invoice"]) == 1


# ── the desk ─────────────────────────────────────────────────────────────
def test_the_desk_shows_what_the_books_are_still_owed(client):
    take_delivery(client)
    page = client.get("/admin/accounting", auth=ADMIN)
    assert page.status_code == 200
    assert "Waiting to be filed" in page.text
    assert ">2<" in page.text                      # two documents outstanding
    assert "/admin/accounting/callback" in page.text    # the redirect to register


def test_the_books_page_needs_the_password(client):
    assert client.get("/admin/accounting").status_code == 401
    assert client.post("/admin/accounting/sync").status_code == 401
    assert client.get("/admin/accounting/export.csv").status_code == 401


def test_connecting_is_refused_until_credentials_exist(client):
    res = client.get("/admin/accounting/connect", auth=ADMIN, follow_redirects=False)
    assert res.status_code == 400


def test_a_callback_that_did_not_start_here_is_refused(client):
    res = client.get("/admin/accounting/callback?code=x&realmId=1&state=forged",
                     auth=ADMIN, follow_redirects=False)
    assert res.status_code == 400


# ── the export ───────────────────────────────────────────────────────────
def test_the_export_carries_every_line_of_every_invoice(client):
    take_delivery(client)
    res = client.get("/admin/accounting/export.csv", auth=ADMIN)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert len(rows) == 1                          # the allocation, carriage included
    assert rows[0]["InvoiceNo"].startswith("WSC-INV-")
    assert rows[0]["Email"] == "matias@example.com"
    assert rows[0]["Item"] == "Wine Club Allocation"
    assert rows[0]["Paid"] == "Yes"
    assert float(rows[0]["ItemAmount"]) == 340.00


def test_the_export_can_be_narrowed_to_what_is_not_filed_yet(client):
    take_delivery(client)
    ledger_queue.drain(client.main.db, FakeLedger())

    everything = client.get("/admin/accounting/export.csv", auth=ADMIN).text
    unfiled = client.get("/admin/accounting/export.csv?unsynced=1", auth=ADMIN).text
    assert len(everything.splitlines()) == 2
    assert len(unfiled.splitlines()) == 1          # the header, and nothing owed


# ── the QuickBooks adapter ───────────────────────────────────────────────
class StubIntuit:
    """Stands in for Intuit. Routes on URL, records what it was sent."""

    def __init__(self, **routes):
        self.routes = routes
        self.calls: list[tuple[str, str, dict | None]] = []
        self.token_calls = 0

    # the two entry points the adapter uses
    def post(self, url, *, data=None, json=None, content=None, headers=None,
             auth=None, timeout=None):
        if "tokens/bearer" in url:
            self.token_calls += 1
            return self._answer("token", data)
        if url.endswith("/revoke"):
            return httpx.Response(200)
        if "/query?" in url:
            statement = content.decode()
            self.calls.append(("query", statement, None))
            return self._answer_query(statement)
        return self.request("POST", url, json=json, headers=headers)

    def request(self, method, url, *, json=None, headers=None, timeout=None):
        entity = url.split("/company/")[1].split("?")[0].split("/")[-1]
        self.calls.append((method, entity, json))
        return self._answer(entity, json)

    def _answer(self, key, body):
        route = self.routes.get(key)
        if callable(route):
            return route(body)
        if route is None:
            raise AssertionError(f"StubIntuit has no route for {key!r}")
        return route

    def _answer_query(self, statement):
        table = statement.split(" FROM ")[1].split()[0].lower()
        route = self.routes.get(f"query:{table}")
        if callable(route):
            return route(statement)
        return route if route is not None else httpx.Response(200, json={"QueryResponse": {}})


def ledger_with(monkeypatch, tmp_path, stub, **kwargs) -> QuickBooksLedger:
    from app.db import Database

    monkeypatch.setattr("app.accounting.quickbooks.httpx.post", stub.post)
    monkeypatch.setattr("app.accounting.quickbooks.httpx.request", stub.request)
    store = TokenStore(Database(tmp_path / "qbo.sqlite3"))
    return QuickBooksLedger(
        client_id="abc", client_secret="shh",
        redirect_uri="https://warmspringscellars.com/admin/accounting/callback",
        store=store, **kwargs,
    )


TOKEN_BODY = httpx.Response(200, json={
    "access_token": "access-1", "refresh_token": "refresh-1",
    "expires_in": 3600, "x_refresh_token_expires_in": 8640000,
})


def test_the_consent_link_asks_for_accounting_scope_only(monkeypatch, tmp_path):
    qbo = ledger_with(monkeypatch, tmp_path, StubIntuit())
    url = qbo.authorize_url("state-123")
    assert "client_id=abc" in url
    assert "scope=com.intuit.quickbooks.accounting" in url
    assert "state=state-123" in url
    assert "admin%2Faccounting%2Fcallback" in url


def test_sandbox_and_production_are_different_companies(monkeypatch, tmp_path):
    assert ledger_with(monkeypatch, tmp_path, StubIntuit()).api_base == SANDBOX_API
    live = ledger_with(monkeypatch, tmp_path, StubIntuit(), environment="production")
    assert live.api_base == PRODUCTION_API
    assert MINOR_VERSION in live._company_url("invoice", "123")


def test_the_rotated_refresh_token_is_stored_not_the_old_one(monkeypatch, tmp_path):
    stub = StubIntuit(token=TOKEN_BODY)
    qbo = ledger_with(monkeypatch, tmp_path, stub)
    qbo.exchange_code("code-1", "realm-9")

    stored = qbo.store.read()
    assert (stored.realm_id, stored.refresh_token) == ("realm-9", "refresh-1")
    assert stored.access_valid

    stub.routes["token"] = httpx.Response(200, json={
        "access_token": "access-2", "refresh_token": "refresh-2",
        "expires_in": 3600, "x_refresh_token_expires_in": 8640000,
    })
    qbo.refresh(stored)

    # Intuit retires the token it just used. Keeping the old one would work for
    # an hour and then lock the company out for good.
    after = qbo.store.read()
    assert after.refresh_token == "refresh-2"
    assert after.realm_id == "realm-9"


def test_an_expired_access_token_is_refreshed_before_the_call(monkeypatch, tmp_path):
    stub = StubIntuit(token=TOKEN_BODY)
    qbo = ledger_with(monkeypatch, tmp_path, stub)
    qbo.store.write(Tokens(realm_id="r", access_token="stale", refresh_token="refresh-0",
                           access_expires_at="2020-01-01T00:00:00+00:00"))
    assert qbo.status()["connected"] is True

    stub.routes["query:customer"] = httpx.Response(
        200, json={"QueryResponse": {"Customer": [{"Id": "7"}]}})
    assert qbo._customer_ref(_a_customer()) == "7"
    assert stub.token_calls == 1


def test_nothing_is_attempted_before_a_company_is_authorised(monkeypatch, tmp_path):
    qbo = ledger_with(monkeypatch, tmp_path, StubIntuit())
    assert qbo.status()["connected"] is False
    with pytest.raises(NotConnected):
        qbo.push_invoice(_an_invoice())


def _a_customer():
    from app.accounting.base import Customer
    return Customer(email="matias@example.com", first_name="Matías",
                    last_name="Valdivia", state="California", city="San Francisco")


def _an_invoice():
    from app.accounting.base import Invoice, Line
    return Invoice(
        number="WSC-INV-2026-0001", issued_at="2026-04-02T10:00:00+00:00", currency="usd",
        total_cents=20500, customer=_a_customer(), reference="WSC-2026-0001",
        # Two lines on purpose, although no tier charges for carriage today:
        # the adapter has to map wine and shipping to different items, and
        # that is exactly what comes back the day a tier charges again.
        lines=(Line("The Founder's Reserve — 6 bottles", 1, 18000, 18000, "wine"),
               Line("Temperature-controlled shipping", 1, 2500, 2500, "shipping")),
    )


def _connected(monkeypatch, tmp_path, stub, **kwargs) -> QuickBooksLedger:
    qbo = ledger_with(monkeypatch, tmp_path, stub, **kwargs)
    qbo.store.write(Tokens(realm_id="realm-9", access_token="access-1",
                           refresh_token="refresh-1",
                           access_expires_at="2099-01-01T00:00:00+00:00"))
    return qbo


def test_an_invoice_becomes_a_customer_two_items_and_a_document(monkeypatch, tmp_path):
    stub = StubIntuit(
        **{"query:customer": httpx.Response(200, json={"QueryResponse": {}}),
           "query:invoice": httpx.Response(200, json={"QueryResponse": {}}),
           "query:item": lambda s: httpx.Response(200, json={"QueryResponse": {
               "Item": [{"Id": "31" if "Shipping" in s else "30"}]}}),
           "customer": httpx.Response(200, json={"Customer": {"Id": "77"}}),
           "invoice": httpx.Response(200, json={"Invoice": {"Id": "1001"}})},
    )
    qbo = _connected(monkeypatch, tmp_path, stub)

    assert qbo.push_invoice(_an_invoice()) == "1001"

    sent = next(body for method, entity, body in stub.calls
                if method == "POST" and entity == "invoice")
    assert sent["DocNumber"] == "WSC-INV-2026-0001"
    assert sent["TxnDate"] == "2026-04-02"
    assert sent["CustomerRef"] == {"value": "77"}
    assert sent["CurrencyRef"] == {"value": "USD"}
    # Cents, in decimals, and no float noise.
    assert [line["Amount"] for line in sent["Line"]] == [180.00, 25.00]
    # Wine and shipping book against different items, which is the whole
    # reason the line carries a kind.
    assert [line["SalesItemLineDetail"]["ItemRef"]["value"] for line in sent["Line"]] \
        == ["30", "31"]


def test_a_customer_already_in_the_company_is_matched_on_email(monkeypatch, tmp_path):
    stub = StubIntuit(
        **{"query:customer": httpx.Response(
            200, json={"QueryResponse": {"Customer": [{"Id": "42"}]}})},
    )
    qbo = _connected(monkeypatch, tmp_path, stub)
    assert qbo._customer_ref(_a_customer()) == "42"
    # Matched, so nothing was created.
    assert not [c for c in stub.calls if c[0] == "POST"]


def test_an_invoice_quickbooks_already_holds_is_adopted_not_duplicated(monkeypatch, tmp_path):
    stub = StubIntuit(
        **{"query:invoice": httpx.Response(
            200, json={"QueryResponse": {"Invoice": [{"Id": "900"}]}})},
    )
    qbo = _connected(monkeypatch, tmp_path, stub)

    assert qbo.push_invoice(_an_invoice()) == "900"
    # It did not even look for the customer, let alone write a second invoice.
    assert [c[0] for c in stub.calls] == ["query"]


def test_a_company_that_refuses_a_duplicate_number_still_resolves(monkeypatch, tmp_path):
    seen = {"invoice_queries": 0}

    def invoice_query(_statement):
        seen["invoice_queries"] += 1
        if seen["invoice_queries"] == 1:
            return httpx.Response(200, json={"QueryResponse": {}})
        return httpx.Response(200, json={"QueryResponse": {"Invoice": [{"Id": "901"}]}})

    stub = StubIntuit(
        **{"query:customer": httpx.Response(
               200, json={"QueryResponse": {"Customer": [{"Id": "42"}]}}),
           "query:item": httpx.Response(200, json={"QueryResponse": {"Item": [{"Id": "30"}]}}),
           "query:invoice": invoice_query,
           "invoice": httpx.Response(400, json={"Fault": {"Error": [
               {"Message": "Duplicate Document Number",
                "Detail": "Duplicate Document Number Error: You must specify a "
                          "different number."}]}})},
    )
    qbo = _connected(monkeypatch, tmp_path, stub)

    # The first attempt timed out somewhere past the write, so the document is
    # already there. The retry has to find it rather than write another.
    assert qbo.push_invoice(_an_invoice()) == "901"


def test_a_missing_item_says_which_one_and_whose_decision_it_is(monkeypatch, tmp_path):
    stub = StubIntuit(
        **{"query:invoice": httpx.Response(200, json={"QueryResponse": {}}),
           "query:customer": httpx.Response(
               200, json={"QueryResponse": {"Customer": [{"Id": "42"}]}}),
           "query:item": httpx.Response(200, json={"QueryResponse": {}})},
    )
    qbo = _connected(monkeypatch, tmp_path, stub, wine_item="Club Allocation")

    with pytest.raises(AccountingError) as caught:
        qbo.push_invoice(_an_invoice())
    assert "no item called 'Club Allocation'" in str(caught.value)
    assert "income account" in str(caught.value)


def test_a_payment_is_linked_to_the_invoice_it_settles(monkeypatch, tmp_path):
    from app.accounting.base import Payment

    stub = StubIntuit(
        **{"query:customer": httpx.Response(
               200, json={"QueryResponse": {"Customer": [{"Id": "42"}]}}),
           "payment": httpx.Response(200, json={"Payment": {"Id": "2002"}})},
    )
    qbo = _connected(monkeypatch, tmp_path, stub, deposit_account="88")

    paid = Payment(invoice_number="WSC-INV-2026-0001", amount_cents=20500, currency="usd",
                   paid_at="2026-04-02T10:05:00+00:00", customer=_a_customer(),
                   reference="pi_123")
    assert qbo.push_payment(paid, "1001") == "2002"

    sent = next(body for method, entity, body in stub.calls if entity == "payment")
    assert sent["TotalAmt"] == 205.00
    assert sent["Line"][0]["LinkedTxn"] == [{"TxnId": "1001", "TxnType": "Invoice"}]
    assert sent["DepositToAccountRef"] == {"value": "88"}


def test_intuits_nested_error_is_reduced_to_the_sentence_that_helps(monkeypatch, tmp_path):
    stub = StubIntuit(
        **{"query:invoice": httpx.Response(200, json={"QueryResponse": {}}),
           "query:customer": httpx.Response(
               200, json={"QueryResponse": {"Customer": [{"Id": "42"}]}}),
           "query:item": httpx.Response(200, json={"QueryResponse": {"Item": [{"Id": "30"}]}}),
           "invoice": httpx.Response(400, json={"Fault": {"Error": [
               {"Message": "Business Validation Error",
                "Detail": "The account period has been closed."}]}})},
    )
    qbo = _connected(monkeypatch, tmp_path, stub)

    with pytest.raises(AccountingError) as caught:
        qbo.push_invoice(_an_invoice())
    assert "Business Validation Error" in str(caught.value)
    assert "account period has been closed" in str(caught.value)


def test_a_quote_in_a_name_cannot_rewrite_the_query():
    # Intuit's query language is SQL-shaped, and a member really can be called
    # O'Brien. The escape is what keeps that a name and not a clause.
    assert _escape("O'Brien") == "O\\'Brien"
    assert _escape("a\\b") == "a\\\\b"
