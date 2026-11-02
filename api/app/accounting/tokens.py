"""OAuth tokens for an accounting system, in the club's own database.

Shared by both adapters because both have the same awkward property: the
refresh token is replaced on every refresh, so a value baked into the
environment would be stale an hour after the first sync. They live on the
volume that gets backed up, one row per provider.

`realm_id` is Intuit's word for the company. Xero calls the same thing a
tenant id and it goes in the same column: a migration to rename it would
touch a backup format to gain a word.
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

    @property
    def connected(self) -> bool:
        return bool(self.realm_id and self.refresh_token)

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
            realm_id=row["realm_id"] or "",
            access_token=row["access_token"] or "",
            refresh_token=row["refresh_token"] or "",
            access_expires_at=row["access_expires_at"] or "",
            refresh_expires_at=row["refresh_expires_at"] or "",
        )

    def write(self, tokens: Tokens) -> None:
        with self.db.transaction() as con:
            con.execute(
                "INSERT INTO oauth_token (provider, realm_id, access_token, refresh_token,"
                " access_expires_at, refresh_expires_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(provider) DO UPDATE SET realm_id = excluded.realm_id,"
                " access_token = excluded.access_token,"
                " refresh_token = excluded.refresh_token,"
                " access_expires_at = excluded.access_expires_at,"
                " refresh_expires_at = excluded.refresh_expires_at,"
                " updated_at = excluded.updated_at",
                (self.provider, tokens.realm_id, tokens.access_token, tokens.refresh_token,
                 tokens.access_expires_at, tokens.refresh_expires_at, now()),
            )

    def clear(self) -> None:
        with self.db.transaction() as con:
            con.execute("DELETE FROM oauth_token WHERE provider = ?", (self.provider,))
