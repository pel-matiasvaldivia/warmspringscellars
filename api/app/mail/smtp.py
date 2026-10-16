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

from .base import Message, Probe
from .oauth import OAuthError


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

    def verify(self) -> Probe:
        """Connect, encrypt, authenticate, hang up. Send nothing.

        Everything that goes wrong with outgoing mail goes wrong in one of
        those four steps, and all of them can be tried without putting a
        message in anybody's inbox. What it cannot tell you is whether the
        message would arrive: that is SPF, DKIM and a spam filter, and only
        a real send to a real address answers it.
        """
        steps: list[str] = []
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
                _code, greeting = server.ehlo()
                steps.append(f"Connected to {self._host}:{self._port} — "
                             f"{_first_line(greeting)}")

                if self._starttls:
                    server.starttls()
                    server.ehlo()
                    steps.append("STARTTLS accepted: the connection is encrypted.")

                offered = (server.esmtp_features.get("auth") or "").upper().split()
                if offered:
                    steps.append("The server offers " + ", ".join(offered) + ".")

                if self.mechanism == "none":
                    steps.append("Not authenticating: this deployment is a relay "
                                 "that authorises by address.")
                else:
                    if offered and self.mechanism not in offered:
                        return Probe(False, steps, _not_offered(self.mechanism, offered))
                    self._authenticate(server)
                    steps.append(f"Authenticated as {self._user} over "
                                 f"{self.mechanism}.")

                steps.append("Closed without sending anything.")
        except OAuthError as exc:
            return Probe(False, steps, f"No access token, so there was nothing to "
                                       f"authenticate with: {exc}")
        except smtplib.SMTPAuthenticationError as exc:
            return Probe(False, steps, f"The server refused the credentials: "
                                       f"{_said(exc)}")
        except smtplib.SMTPException as exc:
            return Probe(False, steps, f"The server refused: {exc}")
        except OSError as exc:
            return Probe(False, steps,
                         f"Could not reach {self._host}:{self._port} — {exc}")
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

        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
            if self._starttls:
                server.starttls()
                # The server advertises a different set of mechanisms once the
                # connection is encrypted, and smtplib caches the first set.
                server.ehlo()
            self._authenticate(server)
            server.send_message(msg)
        return f"smtp://{self._host}:{self._port}"


def _first_line(greeting: bytes | str) -> str:
    text = greeting.decode("utf-8", "replace") if isinstance(greeting, bytes) else str(greeting)
    return text.splitlines()[0].strip() if text.strip() else "(no greeting)"


def _said(exc: smtplib.SMTPAuthenticationError) -> str:
    """The server's own sentence. Microsoft's carries the only useful code."""
    detail = exc.smtp_error
    if isinstance(detail, bytes):
        detail = detail.decode("utf-8", "replace")
    return f"{exc.smtp_code} {detail.splitlines()[0].strip()}" if detail else str(exc)


def _not_offered(mechanism: str, offered: list[str]) -> str:
    if mechanism == "XOAUTH2":
        return ("The server does not offer XOAUTH2, only " + ", ".join(offered) +
                ". On Microsoft 365 that usually means the host is wrong: "
                "submission is smtp.office365.com, not the relay connector.")
    return ("The server does not offer LOGIN, only " + ", ".join(offered) +
            ". On Microsoft 365 that means basic authentication is switched "
            "off for this mailbox, and OAuth2 is the way in.")
