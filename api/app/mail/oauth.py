"""An OAuth2 access token for SMTP, by the client-credentials flow.

Microsoft has been retiring basic authentication for SMTP client submission,
and the day it goes in a tenant a username and a password stop working with
no warning and no fallback. This is the path that survives it.

Client credentials, not a user's password: the club is a daemon with nobody
sitting at it, so there is no one to answer a sign-in prompt or an MFA
challenge. The app authenticates as itself and is granted the right to send
as one mailbox.

Deliberately not a dependency. MSAL would do this and a great deal more; this
is one POST and a cached string, and `httpx` is already here for Stripe and
QuickBooks.

NOT EXERCISED against a real tenant from the machine this was written on,
which has no egress to login.microsoftonline.com. The request shape follows
Microsoft's documentation and the caching is covered by tests.
"""

from __future__ import annotations

import threading
import time

import httpx

# The resource SMTP submission asks for. Not Graph — a Graph token is
# rejected by the SMTP endpoint, which is a confusing hour if you hit it.
DEFAULT_SCOPE = "https://outlook.office365.com/.default"
DEFAULT_AUTHORITY = "https://login.microsoftonline.com"

# Ask for a new token this long before the old one lapses. A token that
# expires between the check and the EHLO is a 535 that reads like a
# configuration error and is not one.
EXPIRY_SKEW_SECONDS = 120


class OAuthError(RuntimeError):
    """The token endpoint refused, or answered with something unusable."""


class ClientCredentialsToken:
    """Fetches and caches an app-only access token.

    Thread-safe because the mailer is called from request handlers and from
    a background task, and two of them racing would otherwise mean two
    pointless round trips to Microsoft for the same token.
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str,
                 scope: str = DEFAULT_SCOPE, authority: str = DEFAULT_AUTHORITY,
                 timeout: float = 20.0):
        if not (tenant_id and client_id and client_secret):
            raise ValueError("OAuth2 needs a tenant, a client id and a secret")
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._authority = authority.rstrip("/")
        self._timeout = timeout

        self._lock = threading.Lock()
        self._value = ""
        self._expires_at = 0.0

    @property
    def endpoint(self) -> str:
        return f"{self._authority}/{self._tenant_id}/oauth2/v2.0/token"

    def token(self) -> str:
        with self._lock:
            if self._value and time.time() < self._expires_at - EXPIRY_SKEW_SECONDS:
                return self._value
            self._value, self._expires_at = self._fetch()
            return self._value

    def _fetch(self) -> tuple[str, float]:
        try:
            res = httpx.post(
                self.endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": self._scope,
                },
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise OAuthError(f"could not reach the token endpoint: {exc}") from exc

        if res.status_code >= 400:
            raise OAuthError(_describe(res))

        body = res.json()
        value = body.get("access_token")
        if not value:
            raise OAuthError("the token endpoint answered without an access_token")
        return value, time.time() + int(body.get("expires_in", 3600))


def _describe(res: httpx.Response) -> str:
    """Microsoft's errors carry the one code worth reading. Surface it."""
    try:
        body = res.json()
    except ValueError:
        return f"the token endpoint refused ({res.status_code}): {res.text[:200]}"
    code = body.get("error", "")
    detail = (body.get("error_description") or "").split("\r\n")[0]
    return f"the token endpoint refused ({res.status_code}) {code}: {detail}"
