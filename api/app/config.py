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
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", "http://localhost:8086"))

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
    mail_from: str = field(default_factory=lambda: _env("MAIL_FROM", "club@warmspringscellars.com"))
    mail_reply_to: str = field(default_factory=lambda: _env("MAIL_REPLY_TO", "club@warmspringscellars.com"))

    offer_days: int = field(default_factory=lambda: int(_env("OFFER_DAYS", "14")))
    currency: str = field(default_factory=lambda: _env("CURRENCY", "usd"))

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
    def payments_live(self) -> bool:
        return bool(self.stripe_secret_key)

    @property
    def mail_live(self) -> bool:
        return bool(self.smtp_host)

    def warnings(self) -> list[str]:
        """What is still a stub. Printed at boot and shown in the admin header."""
        out = []
        if not self.secret_from_env:
            out.append("SECRET_KEY is unset: offer links are signed with a key that "
                       "changes on restart, so every link already sent stops working.")
        if not self.payments_live:
            out.append("STRIPE_SECRET_KEY is unset: checkout is simulated and no money moves.")
        elif not self.stripe_webhook_secret:
            out.append("STRIPE_WEBHOOK_SECRET is unset: webhooks cannot be verified and "
                       "are refused.")
        if not self.mail_live:
            out.append(f"SMTP_HOST is unset: mail is written to {self.outbox_dir} "
                       "instead of being sent.")
        if not self.admin_password:
            out.append("ADMIN_PASSWORD is unset: /admin is closed until it is set.")
        return out


# Shipping destinations the winery is licensed for. A placeholder: the real
# list comes from the winery's permits, and the per-customer volume limits that
# go with them are not implemented at all. See FLOW.md.
SHIPPABLE_STATES = {
    "Alaska", "Arizona", "California", "Colorado", "Connecticut", "District of Columbia",
    "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Missouri",
    "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Vermont",
    "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
}


settings = Settings()
