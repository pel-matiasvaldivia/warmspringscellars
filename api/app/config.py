"""Settings, read from the environment.

Nothing here has a usable default for production: the service starts with the
fake payment provider and a file outbox so it can be developed and demonstrated
without credentials, and `Settings.warnings()` says loudly which adapters are
still stubs. Real keys arrive as environment variables and never as files in
this repository.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Where the member-facing pages live, used to build absolute links in email.
    # Trailing slashes come off: every use of this glues a path starting with
    # "/" onto it, so one typed slash makes every link a double slash. Most of
    # them a browser forgives. Intuit does not — it matches the redirect URI
    # character for character and refuses the connection with a message about
    # the redirect_uri parameter that says nothing about a slash.
    public_base_url: str = field(
        default_factory=lambda: _env("PUBLIC_BASE_URL", "http://localhost:8086").rstrip("/"))

    # Signs offer links. Generated per process if unset, which means links do
    # not survive a restart — fine in development, fatal in production, so
    # warnings() calls it out.
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY") or secrets.token_urlsafe(32))
    secret_from_env: bool = field(default_factory=lambda: bool(_env("SECRET_KEY")))

    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(ROOT / "var"))))

    # Admin is behind HTTP basic auth. The proxy in front should also restrict
    # it by address; this is the second lock, not the only one.
    admin_user: str = field(default_factory=lambda: _env("ADMIN_USER", "cellar"))
    admin_password: str = field(default_factory=lambda: _env("ADMIN_PASSWORD"))

    stripe_secret_key: str = field(default_factory=lambda: _env("STRIPE_SECRET_KEY"))
    stripe_webhook_secret: str = field(default_factory=lambda: _env("STRIPE_WEBHOOK_SECRET"))

    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    smtp_starttls: bool = field(default_factory=lambda: _bool("SMTP_STARTTLS", True))
    # OAuth2 for SMTP, the client-credentials flow. Set these and the mailer
    # authenticates with a token instead of a password, which is the path that
    # survives Microsoft retiring basic authentication.
    smtp_oauth_tenant_id: str = field(default_factory=lambda: _env("SMTP_OAUTH_TENANT_ID"))
    smtp_oauth_client_id: str = field(default_factory=lambda: _env("SMTP_OAUTH_CLIENT_ID"))
    smtp_oauth_client_secret: str = field(
        default_factory=lambda: _env("SMTP_OAUTH_CLIENT_SECRET"))
    smtp_oauth_scope: str = field(
        default_factory=lambda: _env("SMTP_OAUTH_SCOPE",
                                     "https://outlook.office365.com/.default"))
    smtp_oauth_authority: str = field(
        default_factory=lambda: _env("SMTP_OAUTH_AUTHORITY",
                                     "https://login.microsoftonline.com"))
    mail_from: str = field(default_factory=lambda: _env("MAIL_FROM", "club@warmspringscellar.com"))
    mail_reply_to: str = field(default_factory=lambda: _env("MAIL_REPLY_TO", "club@warmspringscellar.com"))

    offer_days: int = field(default_factory=lambda: int(_env("OFFER_DAYS", "14")))
    currency: str = field(default_factory=lambda: _env("CURRENCY", "usd"))

    # ── QuickBooks Online ────────────────────────────────────────────────
    # The company itself is not configured here: its realm id only arrives on
    # the redirect back from Intuit, and is stored with the tokens.
    qbo_client_id: str = field(default_factory=lambda: _env("QBO_CLIENT_ID"))
    qbo_client_secret: str = field(default_factory=lambda: _env("QBO_CLIENT_SECRET"))
    qbo_environment: str = field(
        default_factory=lambda: _env("QBO_ENVIRONMENT", "sandbox").lower())
    qbo_redirect_uri_raw: str = field(default_factory=lambda: _env("QBO_REDIRECT_URI"))
    # These items must already exist in QuickBooks. The adapter will not create
    # them: which income account the money lands in is the bookkeeper's call.
    qbo_wine_item: str = field(
        default_factory=lambda: _env("QBO_WINE_ITEM", "Wine Club Allocation"))
    qbo_shipping_item: str = field(
        default_factory=lambda: _env("QBO_SHIPPING_ITEM", "Shipping"))
    qbo_deposit_account: str = field(default_factory=lambda: _env("QBO_DEPOSIT_ACCOUNT"))
    # Push as each invoice is issued, or only when the desk asks. Off by
    # default: the first weeks of a new connection are better watched than
    # automatic.
    qbo_autosync: bool = field(default_factory=lambda: _bool("QBO_AUTOSYNC", False))

    @property
    def ledger_name(self) -> str:
        """Which accounting system to talk to: 'quickbooks' or 'none'.

        QuickBooks is the winery's accounting system and the only one wired.
        The port in `accounting/base.py` still takes three methods, so a
        second adapter remains possible — but nothing here chooses between
        adapters any more, because there is nothing to choose between.
        """
        return "quickbooks" if self.quickbooks_configured else "none"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "club.sqlite3"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"

    @property
    def invoice_dir(self) -> Path:
        return self.data_dir / "invoices"

    @property
    def quickbooks_configured(self) -> bool:
        return bool(self.qbo_client_id and self.qbo_client_secret)

    @property
    def qbo_redirect_uri(self) -> str:
        """Where Intuit sends the browser back.

        Derived from the public address unless overridden, because it has to
        match the value registered in the Intuit app exactly — and a value
        typed twice is a value that disagrees with itself eventually.
        """
        return (self.qbo_redirect_uri_raw
                or f"{self.public_base_url}/admin/accounting/callback")

    @property
    def payments_live(self) -> bool:
        return bool(self.stripe_secret_key)

    @property
    def mail_live(self) -> bool:
        return bool(self.smtp_host)

    @property
    def smtp_oauth(self) -> bool:
        """Whether to authenticate with a token rather than a password."""
        return bool(self.smtp_oauth_tenant_id
                    and self.smtp_oauth_client_id
                    and self.smtp_oauth_client_secret)

    def warnings(self) -> list[str]:
        """What is still a stub. Printed at boot and shown in the admin header."""
        out = []
        if not self.secret_from_env:
            out.append("SECRET_KEY is unset: offer links are signed with a key that "
                       "changes on restart, so every link already sent stops working.")
        if not self.payments_live:
            out.append("STRIPE_SECRET_KEY is unset: the card page is simulated, no card "
                       "is stored and a release charges nobody.")
        elif not self.stripe_webhook_secret:
            out.append("STRIPE_WEBHOOK_SECRET is unset: webhooks cannot be verified and "
                       "are refused.")
        if not self.mail_live:
            out.append(f"SMTP_HOST is unset: mail is written to {self.outbox_dir} "
                       "instead of being sent.")
        elif self.smtp_oauth and not self.smtp_user:
            out.append("SMTP_USER is unset while OAuth2 is configured: a token says "
                       "which app is sending, not which mailbox, so the mailbox has "
                       "to be named too.")
        if not self.admin_password:
            out.append("ADMIN_PASSWORD is unset: /admin is closed until it is set.")
        if self.ledger_name == "none":
            out.append("QuickBooks is not configured: invoices queue up instead of "
                       "reaching it. Nothing is lost — the queue is at /admin/accounting, "
                       "and exports as CSV. Set QBO_CLIENT_ID and QBO_CLIENT_SECRET.")
        elif self.payments_live and self.qbo_environment != "production":
            out.append("Stripe is live but QBO_ENVIRONMENT is 'sandbox': real money would "
                       "be booked into a practice company.")
        return out


# The winery's street address. Not a setting: it is the same in every
# deployment and it is a fact about the business rather than about the
# machine. Two things need it and both are the kind that go wrong quietly —
# the invoice, where a US business has to name itself, and the footer of any
# bulk email, where CAN-SPAM requires a real physical address and a message
# without one is unlawful rather than merely untidy.
POSTAL_ADDRESS = "1310 Warm Springs Rd, Glen Ellen, CA 95442"


US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois",
    "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts",
    "Michigan", "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota",
    "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
    "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
}

# The states the winery has no permit for. Written as the short list rather
# than the long one, because this is the list that changes: a licence arriving
# is one line deleted here, not a name added to forty-five others. Direct wine
# shipping is licensed state by state; the real list comes from the winery's
# permits, and the per-customer volume limits that go with them are not
# implemented at all. See FLOW.md.
#
# Take one out and the same name has to come out of UNLICENSED in index.html,
# or the form and the server disagree about who may join. People already on
# the waiting list for that state are then invited from /admin/waiting.
UNLICENSED_STATES = {"Alabama", "Arkansas", "Delaware", "Kentucky", "Mississippi", "Utah"}

SHIPPABLE_STATES = US_STATES - UNLICENSED_STATES


settings = Settings()
