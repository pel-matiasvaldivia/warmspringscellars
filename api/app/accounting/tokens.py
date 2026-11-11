"""OAuth tokens for the accounting system, in the club's own database.

Here rather than in the environment because Intuit replaces the refresh
token on every refresh: a value baked into `.env` would be stale an hour
after the first sync. These live on the volume that gets backed up.

The row is keyed by provider although only QuickBooks writes one. That
costs nothing, and it is the column that would let a second adapter be
connected beside this one without a migration.

`realm_id` is Intuit's word for the company.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..db import Database, now

# Refresh a little early. A token that expires between the check and the call
# is a 401 that looks like a configuration problem and is not one.
EXPIRY_SKEW_SECONDS = 120


@dataclass
class Tokens:
    realm_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    access_expires_at: str = ""
    refresh_expires_at: str = ""
    # Which world authorised these: "sandbox", "production", or "" for a
    # connection made before this was written down. Empty is not a synonym
    # for sandbox — see `authorised_for`.
    environment: str = ""

    @property
    def connected(self) -> bool:
        return bool(self.realm_id and self.refresh_token)

    def authorised_for(self, environment: str) -> bool:
        """Were these minted for the world the club is now pointed at?

        An unrecorded environment is taken on trust while the club is in the
        sandbox — that is where every such connection was made, and refusing
        it would break a working practice setup for nothing. It is NOT taken
        on trust against production: real books are the one place where a
        wrong guess costs something, and the repair is one click.
        """
        if self.environment:
            return self.environment == environment
        return environment != "production"

    @property
    def access_valid(self) -> bool:
        if not self.access_token or not self.access_expires_at:
            return False
        try:
            expiry = datetime.fromisoformat(self.access_expires_at)
        except ValueError:
            return False
        return expiry - timedelta(seconds=EXPIRY_SKEW_SECONDS) > datetime.now(timezone.utc)


class TokenStore:
    """Tokens in the club's own database, on the volume that gets backed up.

    Keyed by provider, so a winery that tried one accounting system and moved
    to the other does not have the first one's tokens overwritten — and the
    row left behind is harmless, since only the configured adapter reads.
    """

    def __init__(self, db: Database, provider: str = "quickbooks"):
        self.db = db
        self.provider = provider

    def read(self) -> Tokens:
        row = self.db.one("SELECT * FROM oauth_token WHERE provider = ?", (self.provider,))
        if row is None:
            return Tokens()
        return Tokens(
            # A column added after the fact: absent in a row written by an
            # older build, which `authorised_for` is what reads.
            environment=(row["environment"] if "environment" in row.keys() else "") or "",
            realm_id=row["realm_id"] or "",
            access_token=row["access_token"] or "",
            refresh_token=row["refresh_token"] or "",
            access_expires_at=row["access_expires_at"] or "",
            refresh_expires_at=row["refresh_expires_at"] or "",
        )

    def write(self, tokens: Tokens) -> None:
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO oauth_token (provider, environment, realm_id, access_token,"
                " refresh_token, access_expires_at, refresh_expires_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(provider) DO UPDATE SET environment = excluded.environment,"
                " realm_id = excluded.realm_id,"
                " access_token = excluded.access_token,"
                " refresh_token = excluded.refresh_token,"
                " access_expires_at = excluded.access_expires_at,"
                " refresh_expires_at = excluded.refresh_expires_at,"
                " updated_at = excluded.updated_at",
                (self.provider, tokens.environment, tokens.realm_id, tokens.access_token,
                 tokens.refresh_token,
                 tokens.access_expires_at, tokens.refresh_expires_at, now()),
            )

    def clear(self) -> None:
        with self.db.transaction() as con:
            con.execute("DELETE FROM oauth_token WHERE provider = ?", (self.provider,))
