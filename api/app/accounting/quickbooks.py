"""QuickBooks Online, over Intuit's Accounting API.

Written against the documented HTTP API rather than the SDK, for the same
reason the Stripe adapter is: the club needs five calls, and five readable
calls are easier to pin and to reason about than a dependency that moves.

NOT EXERCISED AGAINST A LIVE QUICKBOOKS COMPANY from the machine this was
written on — it has no egress to Intuit. The request shapes follow Intuit's
documentation and the adapter is tested against a mocked transport, which
proves the adapter's own logic (token rotation, customer matching, duplicate
adoption) and proves nothing about Intuit's current field validation. Connect
a sandbox company and push one invoice before trusting it with the real books.

Three things about Intuit's OAuth that shape this file:

  * Access tokens last an hour; refresh tokens last a hundred days AND ARE
    REPLACED on every refresh. The new one must be stored or the connection
    dies silently at the hundred-day mark. That is why tokens live in the
    database and not in the environment.
  * The company is identified by a realm id that only arrives on the redirect
    back from Intuit, so it is stored with the tokens rather than configured.
  * A refresh token that has expired cannot be recovered. The only repair is
    for a human to authorise again, which is what the desk's Connect button
    is for.

And one about the chart of accounts: this adapter will not invent items. Which
income account wine revenue and shipping land in is a bookkeeping decision,
and guessing it would put real money in the wrong place. The items must exist
in QuickBooks, by the names in the settings, or the push fails saying so.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from ..db import Database, now
from .base import AccountingError, Customer, Invoice, NotConnected, Payment

AUTHORIZE = "https://appcenter.intuit.com/connect/oauth2"
TOKEN = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REVOKE = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"

SANDBOX_API = "https://sandbox-quickbooks.api.intuit.com"
PRODUCTION_API = "https://quickbooks.api.intuit.com"

# Pinned: Intuit changes field availability between minor versions, and an
# unpinned call silently moves under the service.
MINOR_VERSION = "75"

SCOPE = "com.intuit.quickbooks.accounting"

# Refresh a little early. A token that expires between the check and the call
# is a 401 that looks like a configuration problem and is not one.
EXPIRY_SKEW_SECONDS = 120


def _cents(value: int) -> float:
    """Intuit takes decimal amounts. Only ever two places, from integer cents."""
    return round(value / 100, 2)


def _escape(value: str) -> str:
    """Quote escaping for Intuit's query language, which is SQL-shaped."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


@dataclass
class Tokens:
    realm_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    access_expires_at: str = ""
    refresh_expires_at: str = ""

    @property
    def connected(self) -> bool:
        return bool(self.realm_id and self.refresh_token)

    @property
    def access_valid(self) -> bool:
        if not self.access_token or not self.access_expires_at:
            return False
        try:
            expiry = datetime.fromisoformat(self.access_expires_at)
        except ValueError:
            return False
        return expiry - timedelta(seconds=EXPIRY_SKEW_SECONDS) > datetime.now(timezone.utc)


class TokenStore:
    """Tokens in the club's own database, on the volume that gets backed up."""

    PROVIDER = "quickbooks"

    def __init__(self, db: Database):
        self.db = db

    def read(self) -> Tokens:
        row = self.db.one("SELECT * FROM oauth_token WHERE provider = ?", (self.PROVIDER,))
        if row is None:
            return Tokens()
        return Tokens(
            realm_id=row["realm_id"] or "",
            access_token=row["access_token"] or "",
            refresh_token=row["refresh_token"] or "",
            access_expires_at=row["access_expires_at"] or "",
            refresh_expires_at=row["refresh_expires_at"] or "",
        )

    def write(self, tokens: Tokens) -> None:
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO oauth_token (provider, realm_id, access_token, refresh_token,"
                " access_expires_at, refresh_expires_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(provider) DO UPDATE SET realm_id = excluded.realm_id,"
                " access_token = excluded.access_token,"
                " refresh_token = excluded.refresh_token,"
                " access_expires_at = excluded.access_expires_at,"
                " refresh_expires_at = excluded.refresh_expires_at,"
                " updated_at = excluded.updated_at",
                (self.PROVIDER, tokens.realm_id, tokens.access_token, tokens.refresh_token,
                 tokens.access_expires_at, tokens.refresh_expires_at, now()),
            )

    def clear(self) -> None:
        with self.db.transaction() as con:
            con.execute("DELETE FROM oauth_token WHERE provider = ?", (self.PROVIDER,))


@dataclass
class QuickBooksLedger:
    """The live adapter. `live` means configured, not necessarily connected."""

    client_id: str
    client_secret: str
    redirect_uri: str
    store: TokenStore
    environment: str = "sandbox"
    wine_item: str = "Wine Club Allocation"
    shipping_item: str = "Shipping"
    deposit_account: str = ""
    timeout: float = 20.0
    name: str = field(default="quickbooks", init=False)
    live: bool = field(default=True, init=False)

    # ── where the calls go ───────────────────────────────────────────────
    @property
    def api_base(self) -> str:
        return PRODUCTION_API if self.environment == "production" else SANDBOX_API

    def _company_url(self, path: str, realm_id: str) -> str:
        return f"{self.api_base}/v3/company/{realm_id}/{path}?minorversion={MINOR_VERSION}"

    # ── the consent dance ────────────────────────────────────────────────
    def authorize_url(self, state: str) -> str:
        params = httpx.QueryParams({
            "client_id": self.client_id,
            "response_type": "code",
            "scope": SCOPE,
            "redirect_uri": self.redirect_uri,
            "state": state,
        })
        return f"{AUTHORIZE}?{params}"

    def exchange_code(self, code: str, realm_id: str) -> Tokens:
        body = self._token_call({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        })
        tokens = self._tokens_from(body, realm_id)
        self.store.write(tokens)
        return tokens

    def refresh(self, tokens: Tokens) -> Tokens:
        if not tokens.refresh_token:
            raise NotConnected("QuickBooks has never been connected")
        body = self._token_call({
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
        })
        # Intuit returns a NEW refresh token here and retires the old one.
        fresh = self._tokens_from(body, tokens.realm_id)
        self.store.write(fresh)
        return fresh

    def disconnect(self) -> None:
        tokens = self.store.read()
        if tokens.refresh_token:
            try:
                httpx.post(REVOKE, json={"token": tokens.refresh_token},
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
            raise AccountingError(f"could not reach Intuit: {exc}") from exc
        if res.status_code >= 400:
            raise AccountingError(f"Intuit refused the token request: {res.text[:300]}")
        return res.json()

    @staticmethod
    def _tokens_from(body: dict, realm_id: str) -> Tokens:
        issued = datetime.now(timezone.utc)
        return Tokens(
            realm_id=realm_id,
            access_token=body.get("access_token", ""),
            refresh_token=body.get("refresh_token", ""),
            access_expires_at=(
                issued + timedelta(seconds=int(body.get("expires_in", 3600)))
            ).isoformat(timespec="seconds"),
            refresh_expires_at=(
                issued + timedelta(seconds=int(body.get("x_refresh_token_expires_in", 8640000)))
            ).isoformat(timespec="seconds"),
        )

    # ── what the desk shows ──────────────────────────────────────────────
    def status(self) -> dict:
        tokens = self.store.read()
        if not tokens.connected:
            return {"connected": False, "company": None, "environment": self.environment,
                    "detail": "Configured, but nobody has authorised a company yet."}
        return {
            "connected": True,
            "company": tokens.realm_id,
            "environment": self.environment,
            "access_expires_at": tokens.access_expires_at,
            "reauthorise_before": tokens.refresh_expires_at,
            "detail": ("Connected. The connection lapses if nothing syncs for a hundred "
                       "days, so the date above is a deadline, not a detail."),
        }

    # ── calling the company ──────────────────────────────────────────────
    def _tokens(self) -> Tokens:
        tokens = self.store.read()
        if not tokens.connected:
            raise NotConnected("no QuickBooks company is connected")
        if not tokens.access_valid:
            tokens = self.refresh(tokens)
        return tokens

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 request_id: str | None = None, retry_auth: bool = True) -> dict:
        tokens = self._tokens()
        headers = {
            "Authorization": f"Bearer {tokens.access_token}",
            "Accept": "application/json",
        }
        if request_id:
            # Intuit's own idempotency key: the same id replays the first
            # result instead of writing a second document.
            headers["Request-Id"] = request_id

        try:
            res = httpx.request(
                method, self._company_url(path, tokens.realm_id),
                json=json_body, headers=headers, timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise AccountingError(f"could not reach QuickBooks: {exc}") from exc

        if res.status_code == 401 and retry_auth:
            # The access token died early. One refresh, one retry, then give up.
            self.refresh(self.store.read())
            return self._request(method, path, json_body=json_body,
                                 request_id=request_id, retry_auth=False)

        if res.status_code >= 400:
            raise AccountingError(_intuit_error(res))
        return res.json()

    def _query(self, statement: str) -> dict:
        tokens = self._tokens()
        url = (f"{self.api_base}/v3/company/{tokens.realm_id}/query"
               f"?minorversion={MINOR_VERSION}")
        try:
            res = httpx.post(
                url, content=statement.encode(),
                headers={"Authorization": f"Bearer {tokens.access_token}",
                         "Content-Type": "application/text", "Accept": "application/json"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise AccountingError(f"could not reach QuickBooks: {exc}") from exc
        if res.status_code == 401:
            self.refresh(self.store.read())
            return self._query(statement)
        if res.status_code >= 400:
            raise AccountingError(_intuit_error(res))
        return res.json().get("QueryResponse", {})

    # ── customers ────────────────────────────────────────────────────────
    def _customer_ref(self, customer: Customer) -> str:
        """Match on email, then create. Never match on name.

        Two members called Robert Rex are two customers; one member who
        changed their surname is still one. The email is what the club keys
        everything else on, so it is what the ledger is keyed on too.
        """
        found = self._query(
            "SELECT Id, DisplayName FROM Customer WHERE PrimaryEmailAddr = "
            f"'{_escape(customer.email)}'"
        ).get("Customer", [])
        if found:
            return str(found[0]["Id"])

        body = {
            "DisplayName": customer.display_name or customer.email,
            "GivenName": customer.first_name,
            "FamilyName": customer.last_name,
            "PrimaryEmailAddr": {"Address": customer.email},
        }
        if customer.state or customer.city:
            body["BillAddr"] = {k: v for k, v in
                                {"City": customer.city,
                                 "CountrySubDivisionCode": customer.state}.items() if v}
        try:
            created = self._request("POST", "customer", json_body=body,
                                    request_id=f"cust-{customer.email}")
        except AccountingError as exc:
            # QuickBooks rejects a duplicate DisplayName even when the email
            # differs. Fall back to the name match rather than failing the
            # invoice: a wrong-looking customer is fixable, a lost invoice is
            # a phone call from an accountant.
            if "Duplicate Name" not in str(exc):
                raise
            again = self._query(
                "SELECT Id FROM Customer WHERE DisplayName = "
                f"'{_escape(customer.display_name)}'"
            ).get("Customer", [])
            if not again:
                raise
            return str(again[0]["Id"])
        return str(created["Customer"]["Id"])

    # ── items ────────────────────────────────────────────────────────────
    def _item_ref(self, kind: str) -> str:
        wanted = self.shipping_item if kind == "shipping" else self.wine_item
        found = self._query(
            f"SELECT Id, Name FROM Item WHERE Name = '{_escape(wanted)}'"
        ).get("Item", [])
        if not found:
            raise AccountingError(
                f"QuickBooks has no item called {wanted!r}. Create it in QuickBooks "
                "(Sales → Products and services) pointed at the income account this "
                "revenue belongs in, then push again. The club will not create it: "
                "which account the money lands in is the bookkeeper's call."
            )
        return str(found[0]["Id"])

    # ── the two documents ────────────────────────────────────────────────
    def push_invoice(self, invoice: Invoice) -> str:
        existing = self._find_invoice(invoice.number)
        if existing:
            return existing

        customer_ref = self._customer_ref(invoice.customer)
        lines = []
        for line in invoice.lines:
            lines.append({
                "DetailType": "SalesItemLineDetail",
                "Description": line.description,
                "Amount": _cents(line.total_cents),
                "SalesItemLineDetail": {
                    "ItemRef": {"value": self._item_ref(line.kind)},
                    "Qty": line.quantity,
                    "UnitPrice": _cents(line.unit_cents),
                },
            })

        body = {
            "DocNumber": invoice.number,
            "TxnDate": invoice.issued_at[:10],
            "CustomerRef": {"value": customer_ref},
            "CurrencyRef": {"value": invoice.currency.upper()},
            "Line": lines,
            "PrivateNote": f"Warm Springs Cellars order {invoice.reference}",
            "CustomerMemo": {"value": "Thank you — your allocation is on its way."},
        }
        try:
            created = self._request("POST", "invoice", json_body=body,
                                    request_id=f"inv-{invoice.number}")
        except AccountingError as exc:
            # A company with "warn on duplicate number" set refuses the second
            # attempt. Adopt the document already there instead of writing a
            # second one under a different number.
            if "Duplicate Document Number" not in str(exc):
                raise
            adopted = self._find_invoice(invoice.number)
            if not adopted:
                raise
            return adopted
        return str(created["Invoice"]["Id"])

    def _find_invoice(self, number: str) -> str | None:
        found = self._query(
            f"SELECT Id FROM Invoice WHERE DocNumber = '{_escape(number)}'"
        ).get("Invoice", [])
        return str(found[0]["Id"]) if found else None

    def push_payment(self, payment: Payment, invoice_external_id: str) -> str:
        body = {
            "CustomerRef": {"value": self._customer_ref(payment.customer)},
            "TotalAmt": _cents(payment.amount_cents),
            "TxnDate": payment.paid_at[:10],
            "CurrencyRef": {"value": payment.currency.upper()},
            "PrivateNote": f"Card payment {payment.reference}".strip(),
            "Line": [{
                "Amount": _cents(payment.amount_cents),
                "LinkedTxn": [{"TxnId": invoice_external_id, "TxnType": "Invoice"}],
            }],
        }
        if self.deposit_account:
            body["DepositToAccountRef"] = {"value": self.deposit_account}
        created = self._request("POST", "payment", json_body=body,
                                request_id=f"pay-{payment.invoice_number}")
        return str(created["Payment"]["Id"])


def _intuit_error(res: httpx.Response) -> str:
    """Intuit's errors are nested and verbose. Pull out the sentence that helps."""
    try:
        fault = res.json().get("Fault", {})
        errors = fault.get("Error", [])
        if errors:
            first = errors[0]
            parts = [first.get("Message", ""), first.get("Detail", "")]
            return (f"QuickBooks refused the request ({res.status_code}): "
                    + " — ".join(p for p in parts if p))
    except ValueError:
        pass
    return f"QuickBooks refused the request ({res.status_code}): {res.text[:300]}"


def new_state() -> str:
    """CSRF state for the consent redirect, with its own timestamp."""
    return f"{int(time.time())}-{secrets.token_urlsafe(16)}"
