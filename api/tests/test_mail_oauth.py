"""Authenticating SMTP with a token instead of a password.

No socket is opened and no tenant is reached: the token endpoint is a stub
transport and the SMTP server is a recording double. What this proves is the
adapter's own logic — which mechanism it picks, the exact SASL string it
sends, and that it does not ask Microsoft for a new token on every message.
What it cannot prove is that Microsoft 365 accepts any of it; only a real
tenant does that.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings                                   # noqa: E402
from app.mail import build_mailer                                 # noqa: E402
from app.mail.base import Message                                 # noqa: E402
from app.mail.oauth import ClientCredentialsToken, OAuthError     # noqa: E402
from app.mail.smtp import SmtpMailer                              # noqa: E402

MESSAGE = Message(to="member@example.com", subject="Your allocation",
                  text="Plain text.", html="<p>Rich text.</p>",
                  reply_to="club@warmspringscellar.com")


class Recorder:
    """Stands in for smtplib.SMTP and remembers what it was told."""

    instances: list["Recorder"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.started_tls = False
        self.ehlos = 0
        self.auth_calls: list[tuple[str, str]] = []
        self.logins: list[tuple[str, str]] = []
        self.sent: list = []
        Recorder.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def ehlo(self):
        self.ehlos += 1

    def auth(self, mechanism, authobject, **kwargs):
        self.auth_calls.append((mechanism, authobject()))

    def login(self, user, password):
        self.logins.append((user, password))

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture()
def recorder(monkeypatch):
    Recorder.instances = []
    monkeypatch.setattr("app.mail.smtp.smtplib.SMTP", Recorder)
    return Recorder


def token_endpoint(expires_in=3600, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": f"tok-{len(calls or [1])}",
                                         "expires_in": expires_in,
                                         "token_type": "Bearer"})
    return handler


def with_transport(monkeypatch, handler):
    """Point the token fetcher at a stub, leaving its own code in the path."""
    transport = httpx.MockTransport(handler)
    real_post = httpx.post

    def fake_post(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr("app.mail.oauth.httpx.post", fake_post)
    return real_post


# ── which mechanism ──────────────────────────────────────────────────────
def test_a_password_still_logs_in_the_old_way(recorder):
    mailer = SmtpMailer("smtp.example.com", 587, "club@x.com", "hunter2", "club@x.com")
    assert mailer.mechanism == "LOGIN"
    mailer.send(MESSAGE)

    [server] = recorder.instances
    assert server.logins == [("club@x.com", "hunter2")]
    assert server.auth_calls == []
    assert server.started_tls is True


def test_a_server_that_wants_no_authentication_is_left_alone(recorder):
    """The Microsoft 365 relay connector authorises by IP, not by account."""
    mailer = SmtpMailer("tenant.mail.protection.outlook.com", 25, "", "", "club@x.com")
    assert mailer.mechanism == "none"
    mailer.send(MESSAGE)

    [server] = recorder.instances
    assert server.logins == [] and server.auth_calls == []
    assert server.sent and server.started_tls is True


def test_a_token_provider_switches_the_mechanism(recorder):
    mailer = SmtpMailer("smtp.office365.com", 587, "club@x.com", "", "club@x.com",
                        token=lambda: "TOKEN-123")
    assert mailer.mechanism == "XOAUTH2"
    mailer.send(MESSAGE)

    [server] = recorder.instances
    assert server.logins == []
    [(mechanism, response)] = server.auth_calls
    assert mechanism == "XOAUTH2"
    # The SASL XOAUTH2 initial response, down to the separators: Microsoft
    # rejects anything else without saying why.
    assert response == "user=club@x.com\x01auth=Bearer TOKEN-123\x01\x01"


def test_the_connection_says_hello_again_after_starttls(recorder):
    """The mechanisms a server offers change once the line is encrypted."""
    SmtpMailer("smtp.office365.com", 587, "club@x.com", "", "club@x.com",
               token=lambda: "T").send(MESSAGE)
    [server] = recorder.instances
    assert server.started_tls is True and server.ehlos >= 1


def test_the_message_itself_is_unchanged_by_any_of_this(recorder):
    SmtpMailer("smtp.office365.com", 587, "club@x.com", "", "club@x.com",
               token=lambda: "T").send(MESSAGE)
    [server] = recorder.instances
    [msg] = server.sent
    assert msg["To"] == "member@example.com"
    assert msg["Reply-To"] == "club@warmspringscellar.com"
    assert msg.get_content_type() == "multipart/alternative"


# ── the token ────────────────────────────────────────────────────────────
def test_the_token_is_asked_for_once_and_then_reused(monkeypatch):
    calls: list[dict] = []
    with_transport(monkeypatch, token_endpoint(calls=calls))
    provider = ClientCredentialsToken("tenant", "client", "secret")

    first = provider.token()
    assert [provider.token() for _ in range(5)] == [first] * 5
    assert len(calls) == 1, "one token, not one per message"

    asked = calls[0]
    assert asked["grant_type"] == "client_credentials"
    assert asked["client_id"] == "client"
    # Not a Graph scope: an SMTP endpoint refuses a Graph token, which is a
    # confusing hour if you hit it.
    assert asked["scope"] == "https://outlook.office365.com/.default"


def test_a_token_near_its_end_is_replaced_before_it_lapses(monkeypatch):
    calls: list[dict] = []
    # Shorter than the safety margin, so the first one is never fresh enough.
    with_transport(monkeypatch, token_endpoint(expires_in=60, calls=calls))
    provider = ClientCredentialsToken("tenant", "client", "secret")

    provider.token()
    provider.token()
    assert len(calls) == 2


def test_the_endpoint_is_built_for_the_tenant(monkeypatch):
    provider = ClientCredentialsToken("my-tenant-id", "c", "s")
    assert provider.endpoint == (
        "https://login.microsoftonline.com/my-tenant-id/oauth2/v2.0/token")
    sovereign = ClientCredentialsToken("t", "c", "s",
                                       authority="https://login.microsoftonline.us/")
    assert sovereign.endpoint.startswith("https://login.microsoftonline.us/t/")


def test_a_refusal_carries_microsofts_own_code(monkeypatch):
    def refuse(request):
        return httpx.Response(401, json={
            "error": "invalid_client",
            "error_description": "AADSTS7000215: Invalid client secret provided.\r\n"
                                 "Trace ID: …"})
    with_transport(monkeypatch, refuse)

    with pytest.raises(OAuthError) as caught:
        ClientCredentialsToken("tenant", "client", "wrong").token()
    said = str(caught.value)
    assert "invalid_client" in said and "AADSTS7000215" in said
    assert "Trace ID" not in said, "one readable line, not the whole blob"


def test_it_refuses_to_be_built_half_configured():
    with pytest.raises(ValueError):
        ClientCredentialsToken("", "client", "secret")


# ── the wiring ───────────────────────────────────────────────────────────
def test_the_factory_picks_oauth_when_it_is_configured(monkeypatch, tmp_path):
    for key, value in {
        "DATA_DIR": str(tmp_path), "SMTP_HOST": "smtp.office365.com",
        "SMTP_USER": "club@warmspringscellar.com",
        "SMTP_OAUTH_TENANT_ID": "t", "SMTP_OAUTH_CLIENT_ID": "c",
        "SMTP_OAUTH_CLIENT_SECRET": "s",
    }.items():
        monkeypatch.setenv(key, value)

    settings = Settings()
    assert settings.smtp_oauth is True
    assert build_mailer(settings).mechanism == "XOAUTH2"


def test_the_factory_leaves_a_password_deployment_alone(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "club@x.com")
    monkeypatch.setenv("SMTP_PASSWORD", "hunter2")
    for key in ("SMTP_OAUTH_TENANT_ID", "SMTP_OAUTH_CLIENT_ID", "SMTP_OAUTH_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)

    settings = Settings()
    assert settings.smtp_oauth is False
    assert build_mailer(settings).mechanism == "LOGIN"


def test_oauth_without_a_mailbox_is_called_out_at_boot(monkeypatch, tmp_path):
    """A token says which app is sending, never which mailbox."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "smtp.office365.com")
    monkeypatch.delenv("SMTP_USER", raising=False)
    for key, value in {"SMTP_OAUTH_TENANT_ID": "t", "SMTP_OAUTH_CLIENT_ID": "c",
                       "SMTP_OAUTH_CLIENT_SECRET": "s"}.items():
        monkeypatch.setenv(key, value)

    warnings = " ".join(Settings().warnings())
    assert "SMTP_USER is unset" in warnings
