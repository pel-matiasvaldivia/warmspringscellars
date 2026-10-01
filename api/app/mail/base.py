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


class Mailer(Protocol):
    name: str
    live: bool

    def send(self, message: Message) -> str:
        """Deliver it. Returns something identifying where it went."""
        ...
