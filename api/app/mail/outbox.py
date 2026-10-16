"""Writes mail to disk instead of sending it.

What the service uses until SMTP is configured. The files are real RFC 5322
messages, so they open in any mail client, and the admin page links to the one
carrying an offer — which is how an approval link is reachable in development.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from .base import Message, Probe


class OutboxMailer:
    name = "outbox"
    live = False

    def __init__(self, directory: Path, sender: str):
        self._dir = directory
        self._sender = sender
        directory.mkdir(parents=True, exist_ok=True)

    def verify(self) -> Probe:
        """There is no server to reach, so this checks the one thing there is."""
        steps = [f"No server is configured: mail is written to {self._dir}."]
        try:
            probe = self._dir / ".writable"
            probe.write_text("")
            probe.unlink()
        except OSError as exc:
            return Probe(False, steps, f"and that directory cannot be written: {exc}")
        steps.append("The directory is writable, so nothing is being lost — "
                     "but nothing is reaching anybody either.")
        return Probe(True, steps)

    def send(self, message: Message) -> str:
        msg = EmailMessage()
        msg["From"] = self._sender
        msg["To"] = message.to
        msg["Subject"] = message.subject
        msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        if message.reply_to:
            msg["Reply-To"] = message.reply_to
        msg.set_content(message.text)
        if message.html:
            msg.add_alternative(message.html, subtype="html")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        slug = re.sub(r"[^a-z0-9]+", "-", message.subject.lower()).strip("-")[:48]
        path = self._dir / f"{stamp}-{slug}.eml"
        path.write_bytes(bytes(msg))
        return str(path)
