"""Outbound mail, as little of it as the service needs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str | None = None
    reply_to: str | None = None


@dataclass(frozen=True)
class Probe:
    """What happened when the desk asked whether mail would go out.

    `steps` is kept even when it failed: knowing the connection opened and
    STARTTLS succeeded before the password was refused is most of the
    diagnosis, and it is lost if only the exception survives.
    """

    ok: bool
    steps: list[str]
    error: str = ""


class Mailer(Protocol):
    name: str
    live: bool

    def send(self, message: Message) -> str:
        """Deliver it. Returns something identifying where it went."""
        ...

    def verify(self) -> Probe:
        """Go as far as sending without sending anything."""
        ...
