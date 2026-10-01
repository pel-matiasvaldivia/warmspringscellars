"""What the service needs from a payment provider, and nothing more.

Card details never reach this code: a provider hands back a URL, the member
pays there, and the provider tells us afterwards over a signed webhook. Keeping
the surface this small is what keeps the service out of PCI scope, and it is
why swapping Stripe for another processor is one file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class PaymentError(RuntimeError):
    """The provider refused, or answered with something we cannot use."""


class WebhookError(RuntimeError):
    """A webhook failed verification. Never trust its body after this."""


@dataclass(frozen=True)
class CheckoutSession:
    id: str
    url: str


@dataclass(frozen=True)
class PaidEvent:
    """A payment the provider has confirmed.

    `event_id` is the provider's own id for the delivery, stored so a retry of
    the same event cannot create a second membership.
    """
    event_id: str
    session_id: str
    customer_id: str
    subscription_id: str
    amount_cents: int
    currency: str
    metadata: dict[str, str]


class PaymentProvider(Protocol):
    name: str
    live: bool

    def create_checkout(
        self,
        *,
        reference: str,
        description: str,
        amount_cents: int,
        currency: str,
        interval_months: int,
        customer_email: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
    ) -> CheckoutSession:
        ...

    def parse_webhook(self, payload: bytes, signature: str | None) -> PaidEvent | None:
        """Verify and decode a delivery.

        Returns None for events this service does not act on, which is most of
        them. Raises WebhookError if the signature does not check out.
        """
        ...


def cancel_url_for(base: str, token: str) -> str:
    return f"{base}/club/offer/{token}?checkout=cancelled"


def success_url_for(base: str, token: str) -> str:
    return f"{base}/club/offer/{token}/welcome"


def as_metadata(**kwargs: Any) -> dict[str, str]:
    """Providers only carry strings in metadata."""
    return {k: str(v) for k, v in kwargs.items() if v is not None}
