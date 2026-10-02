"""A ledger that records in memory. Used by the tests and the walkthrough.

It exists for the same reason the fake payment provider does: the whole
pipeline — queue, push, adopt, retry — should be walkable without credentials
for a company nobody has created yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import AccountingError, Invoice, Payment


@dataclass
class FakeLedger:
    name: str = "fake"
    live: bool = True

    invoices: dict[str, Invoice] = field(default_factory=dict)
    payments: dict[str, Payment] = field(default_factory=dict)
    # Set to refuse the next push, to exercise the failure path.
    fail_with: str = ""

    def status(self) -> dict:
        return {"connected": True, "company": "fake-company",
                "detail": "A ledger that only exists in this process."}

    def push_invoice(self, invoice: Invoice) -> str:
        if self.fail_with:
            raise AccountingError(self.fail_with)
        # Same number twice returns the same id, exactly as the live adapter
        # adopts an invoice QuickBooks already holds.
        self.invoices[invoice.number] = invoice
        return f"fake-inv-{invoice.number}"

    def push_payment(self, payment: Payment, invoice_external_id: str) -> str:
        if self.fail_with:
            raise AccountingError(self.fail_with)
        if not invoice_external_id:
            raise AccountingError("a payment needs an invoice to link to")
        self.payments[payment.invoice_number] = payment
        return f"fake-pay-{payment.invoice_number}"
