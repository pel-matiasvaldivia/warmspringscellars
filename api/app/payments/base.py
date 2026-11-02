"""What the service needs from a payment provider, and nothing more.

Card details never reach this code: a provider hands back a URL, the member
enters their card there, and the provider tells us afterwards over a signed
webhook. Keeping the surface this small is what keeps the service out of PCI
scope, and it is why swapping Stripe for another processor is one file.

The club does not run a subscription. A member saves a card when they join and
is charged when a release ships — because that is the only moment the winery
knows there is wine to send, and because three shipments a year fall in spring,
autumn and at Christmas, which is not an interval any processor can express.
So the provider is asked for two things: a page where a card is saved, and a
charge against a card already saved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class PaymentError(RuntimeError):
    """The provider refused, or answered with something we cannot use."""


class WebhookError(RuntimeError):
    """A webhook failed verification. Never trust its body after this."""


@dataclass(frozen=True)
class SetupSession:
    """A hosted page where the member puts in a card. No money moves there."""
    id: str
    url: str


@dataclass(frozen=True)
class CardSaved:
    """A card now held by the provider, ready to be charged later.

    `event_id` is the provider's own id for the delivery, stored so a retry of
    the same event cannot create a second membership.
    """
    event_id: str
    session_id: str
    customer_id: str
    payment_method_id: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class Charge:
    """What came back from charging a card the member is not sitting in front of.

    `needs_member` means the bank wants them present — a 3-D Secure challenge.
    Retrying that in the night will fail every time; only a link they open can
    settle it, which is why it is told apart from an ordinary decline.
    """
    id: str
    paid: bool
    error: str = ""
    needs_member: bool = False


class PaymentProvider(Protocol):
    name: str
    live: bool

    def create_setup(
        self,
        *,
        reference: str,
        description: str,
        customer_email: str,
        success_url: str,
        cancel_url: str,
        metadata: dict[str, str],
    ) -> SetupSession:
        """A page where the member saves a card. Charges nothing."""
        ...

    def charge(
        self,
        *,
        reference: str,
        amount_cents: int,
        currency: str,
        customer_id: str,
        payment_method_id: str,
        description: str,
        metadata: dict[str, str],
    ) -> Charge:
        """Charge a saved card with nobody watching.

        Never raises for a refusal — a declined card is an answer, not a fault,
        and the release that is being charged has other members in it. It
        raises only when the provider could not be reached or asked at all.
        """
        ...

    def parse_webhook(self, payload: bytes, signature: str | None) -> CardSaved | None:
        """Verify and decode a delivery.

        Returns None for events this service does not act on, which is most of
        them. Raises WebhookError if the signature does not check out.
        """
        ...


def as_metadata(**kwargs: Any) -> dict[str, str]:
    """Providers only carry strings in metadata."""
    return {k: str(v) for k, v in kwargs.items() if v is not None}


def cancel_url_for(base: str, token: str) -> str:
    return f"{base}/club/offer/{token}?checkout=cancelled"


def success_url_for(base: str, token: str) -> str:
    return f"{base}/club/offer/{token}/welcome"
