"""What the club needs from an accounting system, and nothing more.

Two documents go out: an invoice, and the payment that settles it. Everything
else the bookkeeper does — accounts, classes, tax codes, reconciliation — stays
in the accounting system, where an accountant can see it.

The port is deliberately one-way. The club never reads balances back, never
lets the ledger change an order, and never blocks a payment on the ledger
being reachable: a member who has paid is a member, whether or not QuickBooks
answered. That is why pushes run from a queue (see `queue.py`) rather than
inside the webhook's transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AccountingError(RuntimeError):
    """The ledger refused, was unreachable, or answered with something unusable."""


class NotConnected(AccountingError):
    """No ledger is connected yet. Work stays queued rather than being lost."""


@dataclass(frozen=True)
class Customer:
    """Who the invoice is for. Matched in the ledger by email."""
    email: str
    first_name: str
    last_name: str
    state: str = ""
    city: str = ""

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass(frozen=True)
class Line:
    description: str
    quantity: int
    unit_cents: int
    total_cents: int
    # Which configured item this line books against. Revenue and shipping land
    # in different accounts, and only the bookkeeper may decide which.
    kind: str = "wine"            # 'wine' | 'shipping'


@dataclass(frozen=True)
class Invoice:
    number: str                   # WSC-INV-2026-0001, reused as the ledger's DocNumber
    issued_at: str                # ISO 8601
    currency: str
    total_cents: int
    customer: Customer
    lines: tuple[Line, ...]
    reference: str = ""           # the order's reference, for the memo


@dataclass(frozen=True)
class Payment:
    invoice_number: str
    amount_cents: int
    currency: str
    paid_at: str
    customer: Customer
    reference: str = ""           # the processor's payment id, for the memo


class Ledger(Protocol):
    name: str
    live: bool

    def status(self) -> dict:
        """What to show on the desk: connected, to which company, until when."""
        ...

    def push_invoice(self, invoice: Invoice) -> str:
        """Record the invoice and return the ledger's own id for it.

        Must be safe to call twice with the same invoice number: the second
        call returns the same id instead of creating a second document.
        """
        ...

    def push_payment(self, payment: Payment, invoice_external_id: str) -> str:
        """Record the payment against an already-pushed invoice."""
        ...


class NullLedger:
    """No ledger configured. Pushes refuse, loudly, and work stays queued.

    Refusing rather than silently succeeding is the point: a queue of pending
    documents is a visible, recoverable state, whereas a queue that drained
    itself into nowhere is a reconciliation nobody can repair.
    """

    name = "none"
    live = False

    def status(self) -> dict:
        return {"connected": False, "company": None, "detail":
                "QuickBooks is not configured: set QBO_CLIENT_ID and QBO_CLIENT_SECRET."}

    def push_invoice(self, invoice: Invoice) -> str:
        raise NotConnected("no accounting system is connected")

    def push_payment(self, payment: Payment, invoice_external_id: str) -> str:
        raise NotConnected("no accounting system is connected")
