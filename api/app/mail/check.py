"""What the desk is told about outgoing mail.

Two different questions, kept apart because they fail differently:

  configuration()  reads the settings and says what they mean. No network,
                   no delay, safe to run on every page load.
  Mailer.verify()  actually opens the connection. That one can hang for the
                   length of a timeout, so it happens when somebody asks.

Neither proves a message would arrive. Reaching the server and being let in
is not the same as passing SPF, DKIM and somebody's spam filter, which is
why the page also offers a real send to a real address.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..config import Settings
from .base import Message

# How a row is coloured. 'ok' is right, 'warn' works today and will not keep
# working, 'bad' is wrong, 'off' is not sending at all, and 'info' is a value
# worth seeing and judging rather than a verdict.
STATES = ("ok", "warn", "bad", "off", "info")

M365_SUBMISSION = "smtp.office365.com"
M365_RELAY_SUFFIX = ".mail.protection.outlook.com"

# Providers that issue their OWN user name for SMTP, unrelated to any address.
# Filling SMTP_USER with the From address is the mistake every one of them
# answers with a sentence about the credentials that never says which field is
# wrong, so it is worth catching before the connection is even tried.
RELAY_HOSTS = {
    "mail.smtp2go.com": "SMTP2GO",
    "smtp.sendgrid.net": "SendGrid",
    "smtp.mailgun.org": "Mailgun",
    "smtp.postmarkapp.com": "Postmark",
    "smtp.resend.com": "Resend",
}


def is_microsoft(host: str) -> bool:
    return host == M365_SUBMISSION or host.endswith(M365_RELAY_SUFFIX)


def relay_name(host: str) -> str:
    """The provider's name, when the host is one that issues its own login."""
    if host in RELAY_HOSTS:
        return RELAY_HOSTS[host]
    for known, name in RELAY_HOSTS.items():
        # smtp2go answers on several regional names under the same domain.
        if host.endswith("." + known.partition(".")[2]):
            return name
    return ""


@dataclass(frozen=True)
class Check:
    label: str
    state: str
    detail: str


def _domain(address: str) -> str:
    return address.rpartition("@")[2].lower()


def configuration(settings: Settings, mechanism: str) -> list[Check]:
    """Read the settings aloud. The mechanism comes from the live mailer."""
    checks: list[Check] = []
    host = settings.smtp_host

    if not settings.mail_live:
        checks.append(Check(
            "Delivery", "off",
            f"SMTP_HOST is unset, so every message is written to "
            f"{settings.outbox_dir} as a .eml file and nothing is sent. "
            f"The invitation link is shown on the application card instead."))
        return checks

    checks.append(Check("Delivery", "ok", f"Sent through {host}:{settings.smtp_port}."))

    checks.append(Check(
        "Encryption", "ok" if settings.smtp_starttls else "bad",
        "STARTTLS, so the credentials and the message are encrypted in transit."
        if settings.smtp_starttls else
        "SMTP_STARTTLS is off: the credentials would cross the network in "
        "the clear. Turn it on unless the server is on this machine."))

    checks.append(_authentication(settings, mechanism, host))

    if mechanism == "XOAUTH2":
        checks.append(Check("Tenant", "info", settings.smtp_oauth_tenant_id))
        checks.append(Check("Application", "info", settings.smtp_oauth_client_id))
        if settings.smtp_oauth_scope != "https://outlook.office365.com/.default":
            checks.append(Check(
                "Scope", "warn",
                f"{settings.smtp_oauth_scope} — not the SMTP resource. A token "
                f"for Graph is refused by the SMTP endpoint."))

    checks.append(_mailbox(settings, mechanism))
    checks.append(Check("Replies go to", "info", settings.mail_reply_to))

    site = urlparse(settings.public_base_url).hostname or ""
    from_domain = _domain(settings.mail_from)
    if from_domain and site and not site.endswith(from_domain):
        checks.append(Check(
            "Sender domain", "warn",
            f"Mail is sent as @{from_domain} while the site answers at {site}. "
            f"SPF, DKIM and DMARC are published for the sender's domain, so it "
            f"is that one which has to be right."))

    return checks


def _authentication(settings: Settings, mechanism: str, host: str) -> Check:
    if mechanism == "XOAUTH2":
        return Check("Authentication", "ok",
                     "An OAuth2 access token, app-only. This is the path that "
                     "survives Microsoft retiring basic authentication.")
    if mechanism == "LOGIN":
        if host == M365_SUBMISSION:
            return Check("Authentication", "warn",
                         "A password. Microsoft 365 is retiring basic "
                         "authentication for SMTP, and the day it goes in this "
                         "tenant the club stops sending with no warning. "
                         "Moving to OAuth2 is three settings in .env.")
        return Check("Authentication", "ok", f"A password, as {settings.smtp_user}.")
    if host.endswith(M365_RELAY_SUFFIX):
        return Check("Authentication", "ok",
                     "None: this is the Exchange Online relay connector, which "
                     "authorises by IP address. The server's address has to stay "
                     "the one the connector names.")
    return Check("Authentication", "warn",
                 "None: neither SMTP_USER nor the OAuth2 settings are filled. "
                 "Most servers refuse to relay for an unauthenticated client.")


def _mailbox(settings: Settings, mechanism: str) -> Check:
    """Whether the account signing in and the address being sent as agree.

    What counts as agreement depends entirely on who is being signed in to.
    Microsoft 365 signs in as a mailbox, so a login that differs from the From
    address needs Send As. A relay provider signs in as an account, so a login
    that differs is not merely allowed — it is the only shape that works, and a
    login that MATCHES the From address is the error.
    """
    if mechanism == "XOAUTH2" and not settings.smtp_user:
        return Check("Mailbox", "bad",
                     "SMTP_USER is unset. The token says which application is "
                     "sending, never from which mailbox, so the mailbox has to "
                     "be named as well.")

    user, sender = settings.smtp_user, settings.mail_from
    provider = relay_name(settings.smtp_host)

    if user and user.lower() == sender.lower() and provider and mechanism == "LOGIN":
        return Check("Mailbox", "warn",
                     f"SMTP_USER is the From address. {provider} issues its own "
                     f"user name for SMTP, which is not an email address, and "
                     f"refuses this one — with a sentence about the credentials "
                     f"that never says which field is wrong. Put that user name "
                     f"in SMTP_USER and leave MAIL_FROM as it is.")

    if user and user.lower() != sender.lower():
        if provider:
            return Check("Mailbox", "ok",
                         f"Signing in to {provider} as {user} and sending as "
                         f"{sender}, which is how a relay works: the user name "
                         f"says which account, the address says who it is from.")
        if is_microsoft(settings.smtp_host):
            return Check("Mailbox", "warn",
                         f"Signing in as {user} but sending as {sender}. "
                         f"Microsoft 365 refuses that unless the account has "
                         f"Send As over the other mailbox.")
        return Check("Mailbox", "info",
                     f"Signing in as {user} and sending as {sender}. Whether a "
                     f"server allows that is the server's own rule.")

    return Check("Mailbox", "ok", f"Sending as {sender}.")


def trial_message(settings: Settings, to: str, mechanism: str) -> Message:
    """A message whose whole content is the evidence it was sent correctly."""
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    lines = [
        f"Server:         {settings.smtp_host}:{settings.smtp_port}",
        f"Authentication: {mechanism}",
        f"From:           {settings.mail_from}",
        f"Sent:           {stamp}",
    ]
    text = ("This is a test from the Warm Springs Cellar cellar desk. Nobody "
            "on the club's list received it.\n\n"
            + "\n".join(lines) +
            "\n\nIf it reached you, the club can send. Whether it reaches a "
            "member's inbox rather than their junk folder is a separate "
            "question: open this message's original and check that SPF and "
            "DKIM both passed.\n")

    rows = "".join(
        f'<tr><td style="padding:3px 14px 3px 0;color:#9a8890;white-space:nowrap">'
        f'{label}</td><td style="padding:3px 0">{value}</td></tr>'
        for label, value in (line.split(":", 1) for line in lines))
    html = f"""<!doctype html><html><body style="margin:0;background:#faf8f5;padding:28px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="100%" style="max-width:520px;background:#fff;
 border:1px solid #e2d8d0;padding:32px 28px;font:15px/1.65 Georgia,serif;color:#2c1e24">
<tr><td>
<p style="font:600 11px/1 system-ui,sans-serif;letter-spacing:.22em;text-transform:uppercase;
 color:#5c0037;margin:0 0 22px">Warm Springs Cellar</p>
<p style="margin:0 0 18px">This is a test from the cellar desk. Nobody on the
club's list received it.</p>
<table role="presentation" cellpadding="0" cellspacing="0"
 style="font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace">{rows}</table>
<p style="margin:20px 0 0;font-size:13px;color:#5c4650">If it reached you, the club
can send. Whether it reaches a member's inbox rather than their junk folder is a
separate question: open this message's original and check that SPF and DKIM both
passed.</p>
</td></tr></table></td></tr></table></body></html>"""

    return Message(to=to, subject="Warm Springs Cellar — mail test",
                   text=text, html=html, reply_to=settings.mail_reply_to)
