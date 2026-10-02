"""Accounting adapters. One is chosen at boot, by whether credentials exist."""

from __future__ import annotations

from ..config import Settings
from ..db import Database
from .base import (
    AccountingError,
    Customer,
    Invoice,
    Ledger,
    Line,
    NotConnected,
    NullLedger,
    Payment,
)
from .fake import FakeLedger
from .quickbooks import QuickBooksLedger, TokenStore

__all__ = [
    "AccountingError", "Customer", "FakeLedger", "Invoice", "Ledger", "Line",
    "NotConnected", "NullLedger", "Payment", "QuickBooksLedger", "TokenStore",
    "build_ledger",
]


def build_ledger(settings: Settings, db: Database) -> Ledger:
    """QuickBooks if it is configured, otherwise a ledger that refuses.

    Note the asymmetry with payments: there is no simulated accounting system
    in production. A club that cannot reach its books should accumulate a
    visible queue, not a pile of invoices it believes were filed.
    """
    if not settings.quickbooks_configured:
        return NullLedger()
    return QuickBooksLedger(
        client_id=settings.qbo_client_id,
        client_secret=settings.qbo_client_secret,
        redirect_uri=settings.qbo_redirect_uri,
        store=TokenStore(db),
        environment=settings.qbo_environment,
        wine_item=settings.qbo_wine_item,
        shipping_item=settings.qbo_shipping_item,
        deposit_account=settings.qbo_deposit_account,
    )
