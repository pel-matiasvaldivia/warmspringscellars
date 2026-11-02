"""Xero, over its Accounting API.

The other half of the choice. Same three methods as the QuickBooks adapter,
same queue behind it, same desk in front: which one the club talks to is one
setting, and nothing else in the service knows the difference.

NOT EXERCISED AGAINST A LIVE XERO ORGANISATION, and this time not even
against the documentation: the machine this was written on cannot reach
developer.xero.com, so the request shapes come from Xero's published API
reference as it stood in training rather than from a page read today. Treat
every field name here as a claim to check. Connect the Xero demo company,
push one invoice and one payment, and read what comes back before this goes
anywhere near the real books.

Four things about Xero that shape this file, and three of them differ from
Intuit:

  * **A tenant, not a company in the URL.** The organisation is chosen after
    consent: the token comes back with no organisation attached, and a second
    call to /connections says which ones it is good for. The club takes the
    first, which is right for a winery with one Xero organisation and wrong
    for an accountant's own login with twenty — hence the detail on the desk
    saying which one it took.
  * **Sixty days, not a hundred.** The refresh token dies after sixty days of
    not being used, and the club ships three times a year. The keep-alive is
    not an optimisation here, it is what stops the connection lapsing between
    spring and autumn.
  * **The refresh token rotates with a thirty-minute grace.** If a refresh
    response is lost the old token still works for a short while, which is
    the one mercy in the design. Two refreshes at once, though, look like a
    stolen token and can revoke the lot, so refreshing is serialised.
  * **Accounts, not items.** Where QuickBooks wants a product pointed at an
    income account, Xero takes the account code on the line itself. That is
    simpler, and it means the bookkeeper gives us codes rather than creating
    products. A payment also needs a bank account to land in, which Xero
    requires and QuickBooks does not.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from .base import AccountingError, Customer, Invoice, NotConnected, Payment
from .tokens import Tokens, TokenStore

AUTHORIZE = "https://login.xero.com/identity/connect/authorize"
TOKEN = "https://identity.xero.com/connect/token"
REVOKE = "https://identity.xero.com/connect/revocation"
CONNECTIONS = "https://api.xero.com/connections"
API = "https://api.xero.com/api.xro/2.0"

# offline_access is what makes Xero issue a refresh token at all; without it
# the connection lasts thirty minutes and then needs a human again.
SCOPE = ("offline_access accounting.transactions accounting.contacts "
         "accounting.settings.read")

# Xero's refresh token lapses after sixty days unused. The club can go a
# whole season without posting anything, so the keep-alive runs with a wide
# margin — half the window, not the last week of it.
KEEPALIVE_DAYS = 30
REFRESH_TOKEN_DAYS = 60


def _amount(cents: int) -> float:
    return round(cents / 100, 2)


def _quote(value: str) -> str:
    """Xero's `where` takes a double-quoted string; escape the quotes in it."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


@dataclass
class XeroLedger:
    """The live adapter. `live` means configured, not necessarily connected."""

    client_id: str
    client_secret: str
    redirect_uri: str
    store: TokenStore
    wine_account: str = "200"
    shipping_account: str = "200"
    bank_account: str = ""
    timeout: float = 20.0
    name: str = field(default="xero", init=False)
    live: bool = field(default=True, init=False)
    _refreshing: threading.Lock = field(default_factory=threading.Lock, init=False,
                                        repr=False)

    # ── the consent dance ────────────────────────────────────────────────
    def authorize_url(self, state: str) -> str:
        params = httpx.QueryParams({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": SCOPE,
            "state": state,
        })
        return f"{AUTHORIZE}?{params}"

    def exchange_code(self, code: str, realm_id: str = "") -> Tokens:
        """Trade the code for tokens, then ask which organisation they open.

        `realm_id` is ignored and only in the signature so the desk's callback
        can call either adapter without knowing which it has. Xero does not
        send an organisation back on the redirect; it has to be asked for.
        """
        body = self._token_call({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        })
        tokens = self._tokens_from(body, "")
        tenant_id, _name = self._first_tenant(tokens.access_token)
        tokens.realm_id = tenant_id
        self.store.write(tokens)
        return tokens

    def _first_tenant(self, access_token: str) -> tuple[str, str]:
        try:
            res = httpx.get(CONNECTIONS, headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise AccountingError(f"could not reach Xero: {exc}") from exc
        if res.status_code >= 400:
            raise AccountingError(f"Xero refused the connections call: {res.text[:300]}")
        connections = res.json() or []
        if not connections:
            raise AccountingError(
                "that Xero login has no organisation attached to this app. Authorise "
                "again and tick the organisation the winery's books live in."
            )
        first = connections[0]
        return str(first.get("tenantId", "")), str(first.get("tenantName", ""))

    def refresh(self, tokens: Tokens) -> Tokens:
        if not tokens.refresh_token:
            raise NotConnected("Xero has never been connected")
        # Serialised: two refreshes racing look to Xero like a reused token,
        # and it answers by revoking the whole family. The queue drains in a
        # background task while the desk can press Sync, so this is reachable.
        with self._refreshing:
            current = self.store.read()
            if current.access_valid and current.refresh_token != tokens.refresh_token:
                # Somebody else refreshed while this call waited on the lock.
                return current
            body = self._token_call({
                "grant_type": "refresh_token",
                "refresh_token": current.refresh_token or tokens.refresh_token,
            })
            fresh = self._tokens_from(body, current.realm_id or tokens.realm_id)
            self.store.write(fresh)
            return fresh

    def disconnect(self) -> None:
        tokens = self.store.read()
        if tokens.refresh_token:
            try:
                httpx.post(REVOKE, data={"token": tokens.refresh_token},
                           auth=(self.client_id, self.client_secret), timeout=self.timeout)
            except httpx.HTTPError:
                # Clearing locally is what matters; a revoke we could not send
                # leaves a token that expires on its own.
                pass
        self.store.clear()

    def _token_call(self, data: dict) -> dict:
        try:
            res = httpx.post(
                TOKEN, data=data, auth=(self.client_id, self.client_secret),
                headers={"Accept": "application/json"}, timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise AccountingError(f"could not reach Xero: {exc}") from exc
        if res.status_code >= 400:
            raise AccountingError(f"Xero refused the token request: {res.text[:300]}")
        return res.json()

    @staticmethod
    def _tokens_from(body: dict, realm_id: str) -> Tokens:
        issued = datetime.now(timezone.utc)
        return Tokens(
            realm_id=realm_id,
            access_token=body.get("access_token", ""),
            refresh_token=body.get("refresh_token", ""),
            access_expires_at=(
                issued + timedelta(seconds=int(body.get("expires_in", 1800)))
            ).isoformat(timespec="seconds"),
            # Xero does not say when the refresh token dies, so the adapter
            # counts the sixty days itself. Every refresh moves it on.
            refresh_expires_at=(
                issued + timedelta(days=REFRESH_TOKEN_DAYS)
            ).isoformat(timespec="seconds"),
        )

    # ── what the desk shows ──────────────────────────────────────────────
    def status(self) -> dict:
        tokens = self.store.read()
        if not tokens.connected:
            return {"connected": False, "company": None,
                    "detail": "Configured, but nobody has authorised an organisation yet."}
        return {
            "connected": True,
            "company": tokens.realm_id,
            "access_expires_at": tokens.access_expires_at,
            "reauthorise_before": tokens.refresh_expires_at,
            "detail": ("Connected. Xero drops the connection if nothing uses it for "
                       "sixty days, which is less than the gap between two releases, "
                       "so the date above is kept at arm's length by the daily "
                       "keep-alive rather than by anybody remembering."),
        }

    def keep_alive(self, within_days: int = KEEPALIVE_DAYS) -> bool:
        """Refresh if the sixty-day lapse is near. True when it refreshed."""
        tokens = self.store.read()
        if not tokens.connected or not tokens.refresh_expires_at:
            return False
        try:
            deadline = datetime.fromisoformat(tokens.refresh_expires_at)
        except ValueError:
            deadline = datetime.now(timezone.utc)
        if deadline - datetime.now(timezone.utc) > timedelta(days=within_days):
            return False
        self.refresh(tokens)
        return True

    # ── calling the organisation ─────────────────────────────────────────
    def _tokens(self) -> Tokens:
        tokens = self.store.read()
        if not tokens.connected:
            raise NotConnected("no Xero organisation is connected")
        if not tokens.access_valid:
            tokens = self.refresh(tokens)
        return tokens

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 params: dict | None = None, idempotency: str | None = None,
                 retry_auth: bool = True) -> dict:
        tokens = self._tokens()
        headers = {
            "Authorization": f"Bearer {tokens.access_token}",
            "Xero-tenant-id": tokens.realm_id,
            "Accept": "application/json",
        }
        if idempotency:
            # Xero replays the first answer for a repeated key rather than
            # writing a second document. The adapter does not lean on it
            # alone: push_invoice looks for the number first, because a key
            # Xero has forgotten and a document it kept look the same here.
            headers["Idempotency-Key"] = idempotency[:128]

        try:
            res = httpx.request(method, f"{API}/{path}", json=json_body, params=params,
                                headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise AccountingError(f"could not reach Xero: {exc}") from exc

        if res.status_code == 401 and retry_auth:
            # The access token died early. One refresh, one retry, then give up.
            self.refresh(self.store.read())
            return self._request(method, path, json_body=json_body, params=params,
                                 idempotency=idempotency, retry_auth=False)

        if res.status_code >= 400:
            raise AccountingError(_xero_error(res))
        return res.json()

    # ── contacts ─────────────────────────────────────────────────────────
    def _contact(self, customer: Customer) -> dict:
        """Match on email, then create. Never match on name.

        Two members called Robert Rex are two contacts; one member who changed
        their surname is still one. Returns the shape a line item wants, which
        is an id when we have one and a name when the contact is new.
        """
        found = self._request(
            "GET", "Contacts",
            params={"where": f'EmailAddress=="{_quote(customer.email)}"'},
        ).get("Contacts", [])
        if found:
            return {"ContactID": found[0]["ContactID"]}

        body = {"Contacts": [{
            "Name": customer.display_name or customer.email,
            "FirstName": customer.first_name,
            "LastName": customer.last_name,
            "EmailAddress": customer.email,
        }]}
        try:
            created = self._request("POST", "Contacts", json_body=body,
                                    idempotency=f"contact-{customer.email.lower()}")
        except AccountingError as exc:
            # Xero refuses a duplicate contact Name even when the email
            # differs. Fall back to the name rather than failing the invoice:
            # a wrong-looking contact is fixable, a lost invoice is a phone
            # call from an accountant.
            if "already exists" not in str(exc).lower():
                raise
            again = self._request(
                "GET", "Contacts",
                params={"where": f'Name=="{_quote(customer.display_name)}"'},
            ).get("Contacts", [])
            if not again:
                raise
            return {"ContactID": again[0]["ContactID"]}
        return {"ContactID": created["Contacts"][0]["ContactID"]}

    def _account_code(self, kind: str) -> str:
        code = self.shipping_account if kind == "shipping" else self.wine_account
        if not code:
            raise AccountingError(
                f"No Xero account code is configured for {kind}. Which account this "
                "revenue lands in is the bookkeeper's call, so the club will not "
                "guess one: set XERO_WINE_ACCOUNT and XERO_SHIPPING_ACCOUNT to codes "
                "from the chart of accounts."
            )
        return code

    # ── the two documents ────────────────────────────────────────────────
    def push_invoice(self, invoice: Invoice) -> str:
        existing = self._find_invoice(invoice.number)
        if existing:
            # Already there from an earlier attempt. Adopt it rather than
            # writing a second one with the same number.
            return existing

        body = {"Invoices": [{
            "Type": "ACCREC",
            "Contact": self._contact(invoice.customer),
            "Date": invoice.issued_at[:10],
            "DueDate": invoice.issued_at[:10],
            "InvoiceNumber": invoice.number,
            "Reference": invoice.reference or invoice.number,
            "CurrencyCode": invoice.currency.upper(),
            "Status": "AUTHORISED",
            # Tax is computed as zero and printed as zero: what is owed on a
            # direct wine shipment depends on the destination, and guessing it
            # would put a real number in the wrong place. NoTax says that
            # plainly rather than leaving Xero to apply a default rate.
            "LineAmountTypes": "NoTax",
            "LineItems": [{
                "Description": line.description,
                "Quantity": line.quantity,
                "UnitAmount": _amount(line.unit_cents),
                "AccountCode": self._account_code(line.kind),
                "TaxType": "NONE",
            } for line in invoice.lines],
        }]}

        created = self._request("POST", "Invoices", json_body=body,
                                idempotency=f"inv-{invoice.number}")
        return str(created["Invoices"][0]["InvoiceID"])

    def _find_invoice(self, number: str) -> str | None:
        found = self._request(
            "GET", "Invoices", params={"where": f'InvoiceNumber=="{_quote(number)}"'},
        ).get("Invoices", [])
        return str(found[0]["InvoiceID"]) if found else None

    def push_payment(self, payment: Payment, invoice_external_id: str) -> str:
        if not self.bank_account:
            raise AccountingError(
                "No Xero bank account is configured for payments. Xero will not take a "
                "payment without one — set XERO_BANK_ACCOUNT to the code of the "
                "account the card money lands in. A clearing account is usually the "
                "right answer, because the processor deposits net of its fee."
            )
        body = {"Payments": [{
            "Invoice": {"InvoiceID": invoice_external_id},
            "Account": {"Code": self.bank_account},
            "Date": payment.paid_at[:10],
            "Amount": _amount(payment.amount_cents),
            "Reference": f"Card payment {payment.reference}".strip(),
        }]}
        created = self._request("POST", "Payments", json_body=body,
                                idempotency=f"pay-{payment.invoice_number}")
        return str(created["Payments"][0]["PaymentID"])


def _xero_error(res: httpx.Response) -> str:
    """Xero's refusals, in words an operator can act on."""
    try:
        body = res.json()
    except ValueError:
        return f"Xero refused with {res.status_code}: {res.text[:300]}"

    # A validation refusal buries the reason inside the element it refused.
    parts: list[str] = []
    for element in body.get("Elements", []) or []:
        for problem in element.get("ValidationErrors", []) or []:
            message = problem.get("Message")
            if message:
                parts.append(str(message))
    if not parts:
        for problem in body.get("ValidationErrors", []) or []:
            message = problem.get("Message")
            if message:
                parts.append(str(message))
    detail = body.get("Detail") or body.get("Message") or body.get("Title")
    if detail:
        parts.append(str(detail))
    if res.status_code == 429:
        parts.append("Xero is rate limiting this app; the queue will keep the work.")
    return f"Xero refused with {res.status_code}: " + (
        " · ".join(parts) if parts else res.text[:300])
