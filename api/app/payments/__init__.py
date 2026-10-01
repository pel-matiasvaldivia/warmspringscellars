"""Payment adapters. One of these is chosen at boot, by whether keys exist."""

from __future__ import annotations

from ..config import Settings
from .base import (
    CheckoutSession,
    PaidEvent,
    PaymentError,
    PaymentProvider,
    WebhookError,
    as_metadata,
    cancel_url_for,
    success_url_for,
)
from .fake import FakeProvider
from .stripe_provider import StripeProvider

__all__ = [
    "CheckoutSession", "PaidEvent", "PaymentError", "PaymentProvider", "WebhookError",
    "FakeProvider", "StripeProvider", "as_metadata", "cancel_url_for", "success_url_for",
    "build_provider",
]


def build_provider(settings: Settings) -> PaymentProvider:
    if settings.payments_live:
        return StripeProvider(settings.stripe_secret_key, settings.stripe_webhook_secret)
    return FakeProvider(settings.secret_key, settings.public_base_url)
