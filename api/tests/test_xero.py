"""The Xero adapter, as far as it can be checked without reaching Xero.

Same standing as the QuickBooks tests: a stubbed transport proves the
adapter's own logic — tenant discovery, token rotation, contact matching,
duplicate adoption, the shape of the two documents — and proves nothing about
Xero's current field validation. Connect the demo company and push one
invoice before trusting it with real books.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.accounting.base import (                              # noqa: E402
    AccountingError,
    Customer,
    Invoice,
    Line,
    NotConnected,
    Payment,
)
from app.accounting.tokens import TokenStore, Tokens           # noqa: E402
from app.accounting.xero import SCOPE, XeroLedger              # noqa: E402

MEMBER = Customer(email="eleanor.hart@example.com", first_name="Eleanor",
                  last_name="Hart", state="California", city="San Francisco")

INVOICE = Invoice(
    number="WSC-INV-2026-0001", issued_at="2026-04-15T09:00:00+00:00", currency="usd",
    total_cents=27500, customer=MEMBER, reference="WSC-2026-0001",
    lines=(Line(description="The Founder's Reserve — 6 bottles", quantity=1,
                unit_cents=27500, total_cents=27500),),
)


class StubXero:
    """Xero, answering whatever each test needs it to."""

    def __init__(self, token=None, connections=None, **routes):
        self.token = token or httpx.Response(200, json={
            "access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 1800})
        self.connections = connections if connections is not None else httpx.Response(
            200, json=[{"tenantId": "tenant-7", "tenantName": "Warm Springs Cellar"}])
        self.routes = routes
        self.sent: list[dict] = []

    def post(self, url, **kwargs):
        self.sent.append({"method": "POST", "url": url, **kwargs})
        return self.token

    def get(self, url, **kwargs):
        self.sent.append({"method": "GET", "url": url, **kwargs})
        return self.connections

    def request(self, method, url, **kwargs):
        self.sent.append({"method": method, "url": url, **kwargs})
        resource = url.rstrip("/").rsplit("/", 1)[1].lower()
        key = f"{method.lower()}:{resource}"
        route = self.routes.get(key)
        if callable(route):
            return route(kwargs)
        if route is not None:
            return route
        return httpx.Response(200, json={})


def ledger_with(monkeypatch, tmp_path, stub, **kwargs) -> XeroLedger:
    from app.db import Database

    monkeypatch.setattr("app.accounting.xero.httpx.post", stub.post)
    monkeypatch.setattr("app.accounting.xero.httpx.get", stub.get)
    monkeypatch.setattr("app.accounting.xero.httpx.request", stub.request)
    store = TokenStore(Database(tmp_path / "xero.sqlite3"), "xero")
    return XeroLedger(
        client_id="abc", client_secret="shh",
        redirect_uri="https://warmspringscellar.com/admin/accounting/callback",
        store=store, **kwargs,
    )


def connected(ledger: XeroLedger) -> None:
    ledger.store.write(Tokens(realm_id="tenant-7", access_token="access-1",
                              refresh_token="refresh-1",
                              access_expires_at="2099-01-01T00:00:00+00:00",
                              refresh_expires_at="2099-01-01T00:00:00+00:00"))


# ── consent ──────────────────────────────────────────────────────────────
def test_the_consent_link_asks_for_offline_access(monkeypatch, tmp_path):
    xero = ledger_with(monkeypatch, tmp_path, StubXero())
    url = xero.authorize_url("state-123")
    assert url.startswith("https://login.xero.com/identity/connect/authorize?")
    assert "client_id=abc" in url
    assert "state=state-123" in url
    # Without offline_access Xero issues no refresh token at all, and the
    # connection would last half an hour.
    assert "offline_access" in SCOPE and "offline_access" in url


def test_the_organisation_is_asked_for_after_consent(monkeypatch, tmp_path):
    """Xero names no organisation on the redirect: it has to be fetched."""
    stub = StubXero()
    xero = ledger_with(monkeypatch, tmp_path, stub)
    tokens = xero.exchange_code("code-1", "")
    assert tokens.realm_id == "tenant-7"
    assert xero.store.read().realm_id == "tenant-7"
    assert any(call["url"] == "https://api.xero.com/connections" for call in stub.sent)


def test_a_login_with_no_organisation_says_so(monkeypatch, tmp_path):
    xero = ledger_with(monkeypatch, tmp_path,
                       StubXero(connections=httpx.Response(200, json=[])))
    with pytest.raises(AccountingError, match="no organisation"):
        xero.exchange_code("code-1", "")


def test_the_rotated_refresh_token_is_stored_not_the_old_one(monkeypatch, tmp_path):
    stub = StubXero(token=httpx.Response(200, json={
        "access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 1800}))
    xero = ledger_with(monkeypatch, tmp_path, stub)
    xero.store.write(Tokens(realm_id="tenant-7", access_token="stale",
                            refresh_token="refresh-1"))
    xero.refresh(xero.store.read())
    stored = xero.store.read()
    assert stored.refresh_token == "refresh-2"
    assert stored.realm_id == "tenant-7"
    # Xero does not say when the refresh token dies, so the adapter counts.
    assert stored.refresh_expires_at


def test_the_keep_alive_refreshes_before_the_sixty_days_run_out(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone

    stub = StubXero()
    xero = ledger_with(monkeypatch, tmp_path, stub)
    soon = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(timespec="seconds")
    far = (datetime.now(timezone.utc) + timedelta(days=55)).isoformat(timespec="seconds")

    xero.store.write(Tokens(realm_id="tenant-7", access_token="a",
                            refresh_token="refresh-1", refresh_expires_at=far))
    assert xero.keep_alive() is False           # nothing to do yet

    xero.store.write(Tokens(realm_id="tenant-7", access_token="a",
                            refresh_token="refresh-1", refresh_expires_at=soon))
    assert xero.keep_alive() is True
    assert xero.store.read().refresh_token == "refresh-1"   # the stub returns the same


def test_nothing_is_pushed_before_an_organisation_is_connected(monkeypatch, tmp_path):
    xero = ledger_with(monkeypatch, tmp_path, StubXero())
    with pytest.raises(NotConnected):
        xero.push_invoice(INVOICE)


# ── the two documents ────────────────────────────────────────────────────
def test_an_invoice_carries_the_account_code_and_no_tax(monkeypatch, tmp_path):
    stub = StubXero(**{
        "get:invoices": httpx.Response(200, json={"Invoices": []}),
        "get:contacts": httpx.Response(200, json={"Contacts": [{"ContactID": "c-1"}]}),
        "post:invoices": httpx.Response(200, json={"Invoices": [{"InvoiceID": "inv-1"}]}),
    })
    xero = ledger_with(monkeypatch, tmp_path, stub, wine_account="200")
    connected(xero)

    assert xero.push_invoice(INVOICE) == "inv-1"

    posted = [c for c in stub.sent if c["method"] == "POST" and "Invoices" in c["url"]][0]
    body = posted["json"]["Invoices"][0]
    assert body["Type"] == "ACCREC"
    assert body["InvoiceNumber"] == "WSC-INV-2026-0001"
    assert body["Contact"] == {"ContactID": "c-1"}
    assert body["Status"] == "AUTHORISED"
    # Tax is zero and said to be zero, rather than left to a default rate.
    assert body["LineAmountTypes"] == "NoTax"
    line = body["LineItems"][0]
    assert line["AccountCode"] == "200"
    assert line["UnitAmount"] == 275.0
    assert line["TaxType"] == "NONE"
    # and the tenant goes in the header, not the path
    assert posted["headers"]["Xero-tenant-id"] == "tenant-7"
    assert posted["headers"]["Idempotency-Key"] == "inv-WSC-INV-2026-0001"


def test_an_invoice_already_in_xero_is_adopted_not_written_twice(monkeypatch, tmp_path):
    stub = StubXero(**{
        "get:invoices": httpx.Response(200, json={"Invoices": [{"InvoiceID": "inv-9"}]}),
    })
    xero = ledger_with(monkeypatch, tmp_path, stub, wine_account="200")
    connected(xero)

    assert xero.push_invoice(INVOICE) == "inv-9"
    assert not [c for c in stub.sent if c["method"] == "POST" and "Invoices" in c["url"]]


def test_a_contact_is_matched_on_email_and_created_when_missing(monkeypatch, tmp_path):
    seen: list[dict] = []

    def contacts(kwargs):
        if "json" in kwargs and kwargs["json"]:
            seen.append(kwargs["json"])
            return httpx.Response(200, json={"Contacts": [{"ContactID": "c-new"}]})
        return httpx.Response(200, json={"Contacts": []})

    stub = StubXero(**{
        "get:invoices": httpx.Response(200, json={"Invoices": []}),
        "get:contacts": contacts, "post:contacts": contacts,
        "post:invoices": httpx.Response(200, json={"Invoices": [{"InvoiceID": "inv-1"}]}),
    })
    xero = ledger_with(monkeypatch, tmp_path, stub, wine_account="200")
    connected(xero)
    xero.push_invoice(INVOICE)

    assert seen and seen[0]["Contacts"][0]["EmailAddress"] == MEMBER.email
    lookup = [c for c in stub.sent if c["method"] == "GET" and "Contacts" in c["url"]][0]
    assert 'EmailAddress=="eleanor.hart@example.com"' == lookup["params"]["where"]


def test_a_payment_names_the_invoice_and_the_bank_account(monkeypatch, tmp_path):
    stub = StubXero(**{
        "post:payments": httpx.Response(200, json={"Payments": [{"PaymentID": "pay-1"}]}),
    })
    xero = ledger_with(monkeypatch, tmp_path, stub, bank_account="090")
    connected(xero)

    payment = Payment(invoice_number="WSC-INV-2026-0001", amount_cents=27500,
                      currency="usd", paid_at="2026-04-15T09:05:00+00:00",
                      customer=MEMBER, reference="pi_123")
    assert xero.push_payment(payment, "inv-1") == "pay-1"

    body = [c for c in stub.sent if c["method"] == "POST"][0]["json"]["Payments"][0]
    assert body["Invoice"] == {"InvoiceID": "inv-1"}
    assert body["Account"] == {"Code": "090"}
    assert body["Amount"] == 275.0
    assert body["Date"] == "2026-04-15"


def test_a_payment_without_a_bank_account_refuses_and_says_why(monkeypatch, tmp_path):
    xero = ledger_with(monkeypatch, tmp_path, StubXero())
    connected(xero)
    payment = Payment(invoice_number="WSC-INV-2026-0001", amount_cents=27500,
                      currency="usd", paid_at="2026-04-15T09:05:00+00:00",
                      customer=MEMBER, reference="pi_123")
    with pytest.raises(AccountingError, match="XERO_BANK_ACCOUNT"):
        xero.push_payment(payment, "inv-1")


def test_an_unset_account_code_refuses_rather_than_guessing(monkeypatch, tmp_path):
    stub = StubXero(**{
        "get:invoices": httpx.Response(200, json={"Invoices": []}),
        "get:contacts": httpx.Response(200, json={"Contacts": [{"ContactID": "c-1"}]}),
    })
    xero = ledger_with(monkeypatch, tmp_path, stub, wine_account="")
    connected(xero)
    with pytest.raises(AccountingError, match="XERO_WINE_ACCOUNT"):
        xero.push_invoice(INVOICE)


# ── refusals ─────────────────────────────────────────────────────────────
def test_a_validation_refusal_is_read_out_of_the_element_that_carried_it(
        monkeypatch, tmp_path):
    refusal = httpx.Response(400, json={"Elements": [{"ValidationErrors": [
        {"Message": "Account code 'ZZZ' is not a valid code for this organisation."}]}]})
    stub = StubXero(**{
        "get:invoices": httpx.Response(200, json={"Invoices": []}),
        "get:contacts": httpx.Response(200, json={"Contacts": [{"ContactID": "c-1"}]}),
        "post:invoices": refusal,
    })
    xero = ledger_with(monkeypatch, tmp_path, stub, wine_account="ZZZ")
    connected(xero)
    with pytest.raises(AccountingError, match="not a valid code"):
        xero.push_invoice(INVOICE)


def test_one_401_is_retried_after_a_refresh_and_no_more(monkeypatch, tmp_path):
    attempts = {"n": 0}

    def invoices(kwargs):
        attempts["n"] += 1
        return httpx.Response(401, json={"Detail": "token expired"})

    stub = StubXero(**{"get:invoices": invoices})
    xero = ledger_with(monkeypatch, tmp_path, stub, wine_account="200")
    connected(xero)
    with pytest.raises(AccountingError):
        xero.push_invoice(INVOICE)
    assert attempts["n"] == 2            # once, refreshed, once more, then given up
