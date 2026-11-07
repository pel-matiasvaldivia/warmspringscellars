#!/usr/bin/env python3
"""Tell apart the four ways OAuth2 SMTP submission fails on Microsoft 365.

    python3 tools/check_smtp_oauth.py
    python3 tools/check_smtp_oauth.py --send you@example.com

OPERATIONS.md §8.3 is seven steps, and three of them fail with the identical
sentence from the server:

    535 5.7.3 Authentication unsuccessful

Admin consent not granted (step 3), the wrong Object ID on the service
principal (step 4), no mailbox permission (step 5) and SMTP AUTH left
disabled for the mailbox (step 6) are one message between them. Reading it
tells you nothing about which it was, and each is a different screen in a
different product.

This separates them, because the token itself carries the answer to half of
it. Microsoft only puts `SMTP.SendAsApp` in the `roles` claim once an
administrator has consented, so a token that lacks the role proves step 3 is
unfinished without touching SMTP at all. And a token that HAS the role and is
still refused at AUTH narrows the fault to the Exchange side — steps 4, 5 or
6 — which is a far smaller place to look.

It reads the same .env the service reads and sends nothing unless asked.
Nothing is written anywhere. The access token and the client secret are never
printed: the token is split apart only to read three of its claims, and the
signature is neither checked nor needed, since the issuer is not being trusted
here — it is being asked what it put inside.
"""

from __future__ import annotations

import argparse
import base64
import json
import smtplib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "api"))

from app.config import settings                      # noqa: E402
from app.mail.oauth import ClientCredentialsToken, OAuthError  # noqa: E402

SMTP_RESOURCE = "https://outlook.office365.com"
NEEDED_ROLE = "SMTP.SendAsApp"


def claims(token: str) -> dict:
    """The payload of a JWT, unverified.

    Deliberately unverified: this is not an authorisation decision, it is a
    question about what the issuer chose to include. Verifying the signature
    would need the tenant's keys and would answer nothing being asked.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError) as exc:
        raise ValueError(f"the access token is not a readable JWT: {exc}") from exc


def good(text: str) -> None:
    print(f"  \033[32mok\033[0m    {text}")


def bad(text: str) -> None:
    print(f"  \033[31mFAIL\033[0m  {text}")


def note(text: str) -> None:
    print(f"        {text}")


def step_settings() -> bool:
    print("\nStep 7 — the settings this process can see")
    missing = [name for name, value in (
        ("SMTP_HOST", settings.smtp_host),
        ("SMTP_USER", settings.smtp_user),
        ("SMTP_OAUTH_TENANT_ID", settings.smtp_oauth_tenant_id),
        ("SMTP_OAUTH_CLIENT_ID", settings.smtp_oauth_client_id),
        ("SMTP_OAUTH_CLIENT_SECRET", settings.smtp_oauth_client_secret),
    ) if not value]
    if missing:
        bad("not set: " + ", ".join(missing))
        note("Run this from the directory holding .env, or export them first.")
        return False

    good(f"sending through {settings.smtp_host}:{settings.smtp_port} "
         f"as {settings.smtp_user}")
    if settings.smtp_password:
        note("SMTP_PASSWORD is also set and will be ignored: a token provider "
             "takes precedence. Clear it to avoid the next reader's confusion.")
    if settings.smtp_oauth_scope != f"{SMTP_RESOURCE}/.default":
        bad(f"SMTP_OAUTH_SCOPE is {settings.smtp_oauth_scope}")
        note("SMTP submission wants https://outlook.office365.com/.default. A "
             "Graph token is a valid token that this endpoint refuses.")
        return False
    return True


def step_token() -> str | None:
    print("\nSteps 1 and 2 — is a token issued at all?")
    provider = ClientCredentialsToken(
        tenant_id=settings.smtp_oauth_tenant_id,
        client_id=settings.smtp_oauth_client_id,
        client_secret=settings.smtp_oauth_client_secret,
        scope=settings.smtp_oauth_scope,
        authority=settings.smtp_oauth_authority,
    )
    try:
        token = provider.token()
    except OAuthError as exc:
        bad(str(exc))
        note("This is before Exchange is involved at all. Either the tenant "
             "or client id is wrong, or the secret is wrong or expired. A "
             "secret lasts at most 24 months and the mail stops the day it "
             "lapses.")
        return None
    good(f"the tenant issued a token ({len(token)} characters, not shown)")
    return token


def step_consent(token: str) -> bool:
    print(f"\nStep 3 — did an administrator consent to {NEEDED_ROLE}?")
    try:
        body = claims(token)
    except ValueError as exc:
        bad(str(exc))
        return False

    audience = body.get("aud", "")
    if audience.rstrip("/") != SMTP_RESOURCE:
        bad(f"the token is for {audience or '(no audience)'}")
        note(f"SMTP submission only accepts a token whose audience is "
             f"{SMTP_RESOURCE}.")
        return False
    good(f"audience is {audience}")

    roles = body.get("roles") or []
    if NEEDED_ROLE not in roles:
        bad("the token carries " + (f"only {', '.join(roles)}" if roles
                                    else "no roles at all"))
        note(f"Microsoft puts {NEEDED_ROLE} in the token only once an "
             f"administrator has pressed Grant admin consent. Until then a "
             f"token is still issued — this one — and SMTP refuses it with a "
             f"message about authentication that names nothing.")
        note("Entra ID → App registrations → this app → API permissions.")
        return False
    good(f"{NEEDED_ROLE} is present, so consent was granted")

    if body.get("appid") and body["appid"] != settings.smtp_oauth_client_id:
        bad(f"the token was issued to {body['appid']}, not to "
            f"SMTP_OAUTH_CLIENT_ID")
        return False
    return True


def step_smtp(token: str) -> bool:
    print("\nSteps 4, 5 and 6 — will Exchange accept it for this mailbox?")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                          timeout=20.0) as server:
            server.ehlo()
            if settings.smtp_starttls:
                server.starttls()
                server.ehlo()
                good("STARTTLS accepted")

            offered = (server.esmtp_features.get("auth") or "").upper().split()
            if "XOAUTH2" not in offered:
                bad("the server does not offer XOAUTH2, only "
                    + ", ".join(offered or ["(nothing)"]))
                note("On Microsoft 365 that usually means the host is wrong: "
                     "submission is smtp.office365.com, not the relay "
                     "connector.")
                return False
            good("the server offers XOAUTH2")

            server.auth("XOAUTH2", lambda challenge=None:
                        f"user={settings.smtp_user}\x01"
                        f"auth=Bearer {token}\x01\x01")
    except smtplib.SMTPAuthenticationError as exc:
        detail = exc.smtp_error
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        bad(f"{exc.smtp_code} {detail.splitlines()[0].strip()}")
        note("The token is good and carries the right role, so this is the "
             "Exchange side. In order of likelihood:")
        note("  6. SMTP AUTH is still disabled for the mailbox —")
        note(f"     Set-CASMailbox -Identity {settings.smtp_user} "
             f"-SmtpClientAuthenticationDisabled $false")
        note("  4. New-ServicePrincipal was given the Object ID from App "
             "registrations instead of the one from Enterprise applications.")
        note(f"  5. The principal has no FullAccess on {settings.smtp_user}.")
        note("OPERATIONS.md §8.3 has each of them in full.")
        return False
    except OSError as exc:
        bad(f"could not reach {settings.smtp_host}:{settings.smtp_port} — {exc}")
        return False
    except smtplib.SMTPException as exc:
        bad(f"the server refused: {exc}")
        return False

    good(f"authenticated as {settings.smtp_user} — every step is correct")
    return True


def step_send(to: str) -> bool:
    print(f"\nA real message to {to}")
    from app.mail import build_mailer          # noqa: PLC0415
    from app.mail.check import trial_message   # noqa: PLC0415

    mailer = build_mailer(settings)
    try:
        where = mailer.send(trial_message(settings, to, mailer.mechanism))
    except Exception as exc:                    # noqa: BLE001 — it is reported
        bad(f"{type(exc).__name__}: {exc}")
        return False
    good(f"accepted by {where}")
    note("Open the message's original and check that SPF and DKIM both "
         "passed. Being let in and arriving are different questions.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find out which step of OPERATIONS.md §8.3 is unfinished.")
    parser.add_argument("--send", metavar="ADDRESS",
                        help="after the checks pass, send one real test "
                             "message to this address")
    args = parser.parse_args()

    print("Warm Springs Cellar — OAuth2 SMTP check")
    if not step_settings():
        return 1
    token = step_token()
    if token is None:
        return 1
    if not step_consent(token):
        return 1
    if not step_smtp(token):
        return 1
    if args.send and not step_send(args.send):
        return 1

    print("\nThe club can authenticate. Whether a message reaches an inbox "
          "rather than\na junk folder is SPF, DKIM and DMARC, which this "
          "does not test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
