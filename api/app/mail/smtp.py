"""SMTP delivery, with a password or with a token.

Two ways to authenticate, because Microsoft has been retiring basic
authentication for SMTP submission and the day it goes in a tenant a username
and a password stop working with nothing to fall back on:

  LOGIN     a username and a password, which is every other SMTP server.
  XOAUTH2   an OAuth2 access token, which is what Microsoft 365 is moving to.

Which one is used follows from the configuration — a token provider means
XOAUTH2 — so switching is a change to .env and a restart, not to this file.

NOT EXERCISED against a real server from the machine this was written on,
which has no egress to one. Standard library smtplib, standard STARTTLS flow.
"""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Callable

from .base import Message


class SmtpMailer:
    name = "smtp"
    live = True

    def __init__(self, host: str, port: int, user: str, password: str,
                 sender: str, starttls: bool = True, timeout: float = 20.0,
                 token: Callable[[], str] | None = None):
        self._host, self._port = host, port
        self._user, self._password = user, password
        self._sender = sender
        self._starttls = starttls
        self._timeout = timeout
        # Returns a bearer token when the deployment authenticates by OAuth2.
        self._token = token

    @property
    def mechanism(self) -> str:
        if self._token:
            return "XOAUTH2"
        return "LOGIN" if self._user else "none"

    def _authenticate(self, server: smtplib.SMTP) -> None:
        if self._token:
            # The SASL XOAUTH2 initial response. smtplib base64-encodes
            # whatever the callable hands back, so this stays readable.
            token = self._token()
            server.auth(
                "XOAUTH2",
                lambda challenge=None: f"user={self._user}\x01auth=Bearer {token}\x01\x01",
            )
        elif self._user:
            server.login(self._user, self._password)

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
                # The server advertises a different set of mechanisms once the
                # connection is encrypted, and smtplib caches the first set.
                server.ehlo()
            self._authenticate(server)
            server.send_message(msg)
        return f"smtp://{self._host}:{self._port}"
