"""SMTP delivery.

NOT EXERCISED against a real server from the machine this was written on, which
has no egress to one. Standard library smtplib, standard STARTTLS flow.
"""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from .base import Message


class SmtpMailer:
    name = "smtp"
    live = True

    def __init__(self, host: str, port: int, user: str, password: str,
                 sender: str, starttls: bool = True, timeout: float = 20.0):
        self._host, self._port = host, port
        self._user, self._password = user, password
        self._sender = sender
        self._starttls = starttls
        self._timeout = timeout

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

        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
            if self._starttls:
                server.starttls()
            if self._user:
                server.login(self._user, self._password)
            server.send_message(msg)
        return f"smtp://{self._host}:{self._port}"
