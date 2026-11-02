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
from .quickbooks import QuickBooksLedger
from .tokens import TokenStore
from .xero import XeroLedger

__all__ = [
    "AccountingError", "Customer", "FakeLedger", "Invoice", "Ledger", "Line",
    "NotConnected", "NullLedger", "Payment", "QuickBooksLedger", "TokenStore",
    "XeroLedger", "build_ledger",
]


def build_ledger(settings: Settings, db: Database) -> Ledger:
    """Whichever accounting system is configured, or one that refuses.

    Note the asymmetry with payments: there is no simulated accounting system
    in production. A club that cannot reach its books should accumulate a
    visible queue, not a pile of invoices it believes were filed. That holds
    for the ambiguous case too — two systems configured and no answer about
    which is not a reason to pick one, it is a reason to queue and say so.
    """
    choice = settings.ledger_name
    if choice == "quickbooks":
        return QuickBooksLedger(
            client_id=settings.qbo_client_id,
            client_secret=settings.qbo_client_secret,
            redirect_uri=settings.qbo_redirect_uri,
            store=TokenStore(db, "quickbooks"),
            environment=settings.qbo_environment,
            wine_item=settings.qbo_wine_item,
            shipping_item=settings.qbo_shipping_item,
            deposit_account=settings.qbo_deposit_account,
        )
    if choice == "xero":
        return XeroLedger(
            client_id=settings.xero_client_id,
            client_secret=settings.xero_client_secret,
            redirect_uri=settings.xero_redirect_uri,
            store=TokenStore(db, "xero"),
            wine_account=settings.xero_wine_account,
            shipping_account=settings.xero_shipping_account,
            bank_account=settings.xero_bank_account,
        )
    return NullLedger()
