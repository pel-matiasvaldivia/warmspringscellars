"""The mail page on the cellar desk.

What it has to get right is honesty: a configuration that works today and
will stop working is not the same as one that is right, and a handshake that
succeeded is not a message that arrived. Each of those distinctions has a
test here, because each of them is one the page could quietly lose.
"""

from __future__ import annotations

import smtplib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings                          # noqa: E402
from app.mail.check import configuration, trial_message   # noqa: E402
from app.mail.outbox import OutboxMailer                 # noqa: E402
# OAuthError comes from the same module object as SmtpMailer on purpose:
# other tests reload the app package, and a second import of oauth.py
# would give a class the adapter's own except clause does not catch.
from app.mail.smtp import OAuthError, SmtpMailer         # noqa: E402


def states(checks, label):
    return next(c for c in checks if c.label == label)


def settings_for(monkeypatch, tmp_path, **env):
    for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_STARTTLS",
                "MAIL_FROM", "MAIL_REPLY_TO", "PUBLIC_BASE_URL",
                "SMTP_OAUTH_TENANT_ID", "SMTP_OAUTH_CLIENT_ID",
                "SMTP_OAUTH_CLIENT_SECRET", "SMTP_OAUTH_SCOPE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


# ── reading the settings aloud ───────────────────────────────────────────
def test_no_server_is_said_plainly_and_nothing_else_is_claimed(monkeypatch, tmp_path):
    checks = configuration(settings_for(monkeypatch, tmp_path), "outbox")
    assert [c.label for c in checks] == ["Delivery"]
    assert checks[0].state == "off", "not a setting that merely wants attention"
    assert "nothing is sent" in checks[0].detail


def test_a_password_on_microsoft_is_marked_as_borrowed_time(monkeypatch, tmp_path):
    settings = settings_for(monkeypatch, tmp_path, SMTP_HOST="smtp.office365.com",
                            SMTP_USER="club@warmspringscellar.com",
                            SMTP_PASSWORD="hunter2",
                            MAIL_FROM="club@warmspringscellar.com")
    auth = states(configuration(settings, "LOGIN"), "Authentication")
    assert auth.state == "warn"
    assert "retiring basic authentication" in auth.detail


def test_a_password_on_any_other_server_is_simply_right(monkeypatch, tmp_path):
    settings = settings_for(monkeypatch, tmp_path, SMTP_HOST="smtp.fastmail.com",
                            SMTP_USER="club@warmspringscellar.com",
                            SMTP_PASSWORD="hunter2",
                            MAIL_FROM="club@warmspringscellar.com")
    assert states(configuration(settings, "LOGIN"), "Authentication").state == "ok"


def test_oauth_is_right_and_shows_which_application_is_sending(monkeypatch, tmp_path):
    settings = settings_for(monkeypatch, tmp_path, SMTP_HOST="smtp.office365.com",
                            SMTP_USER="club@warmspringscellar.com",
                            MAIL_FROM="club@warmspringscellar.com",
                            SMTP_OAUTH_TENANT_ID="tenant-1",
                            SMTP_OAUTH_CLIENT_ID="client-1",
                            SMTP_OAUTH_CLIENT_SECRET="s")
    checks = configuration(settings, "XOAUTH2")
    assert states(checks, "Authentication").state == "ok"
    assert states(checks, "Tenant").detail == "tenant-1"
    assert states(checks, "Application").detail == "client-1"
    # The secret is never one of them.
    assert all("s" != c.detail for c in checks)


def test_a_token_with_no_mailbox_named_is_wrong_not_merely_untidy(monkeypatch, tmp_path):
    settings = settings_for(monkeypatch, tmp_path, SMTP_HOST="smtp.office365.com",
                            SMTP_OAUTH_TENANT_ID="t", SMTP_OAUTH_CLIENT_ID="c",
                            SMTP_OAUTH_CLIENT_SECRET="s")
    assert states(configuration(settings, "XOAUTH2"), "Mailbox").state == "bad"


def test_signing_in_as_one_address_and_sending_as_another_is_flagged(monkeypatch, tmp_path):
    settings = settings_for(monkeypatch, tmp_path, SMTP_HOST="smtp.office365.com",
                            SMTP_USER="noreply@warmspringscellar.com",
                            SMTP_PASSWORD="x", MAIL_FROM="club@warmspringscellar.com")
    mailbox = states(configuration(settings, "LOGIN"), "Mailbox")
    assert mailbox.state == "warn" and "Send As" in mailbox.detail


def test_starttls_turned_off_is_the_one_thing_called_wrong(monkeypatch, tmp_path):
    settings = settings_for(monkeypatch, tmp_path, SMTP_HOST="smtp.office365.com",
                            SMTP_USER="club@x.com", SMTP_PASSWORD="x",
                            MAIL_FROM="club@x.com", SMTP_STARTTLS="false")
    assert states(configuration(settings, "LOGIN"), "Encryption").state == "bad"


def test_sending_from_a_domain_the_site_does_not_use_is_flagged(monkeypatch, tmp_path):
    settings = settings_for(monkeypatch, tmp_path, SMTP_HOST="smtp.office365.com",
                            SMTP_USER="club@gmail.com", SMTP_PASSWORD="x",
                            MAIL_FROM="club@gmail.com",
                            PUBLIC_BASE_URL="https://warmspringscellar.com")
    assert states(configuration(settings, "LOGIN"), "Sender domain").state == "warn"


def test_the_relay_connector_needs_no_account_and_is_not_nagged(monkeypatch, tmp_path):
    settings = settings_for(monkeypatch, tmp_path,
                            SMTP_HOST="wsc.mail.protection.outlook.com",
                            SMTP_PORT="25", MAIL_FROM="club@warmspringscellar.com",
                            PUBLIC_BASE_URL="https://warmspringscellar.com")
    auth = states(configuration(settings, "none"), "Authentication")
    assert auth.state == "ok" and "IP address" in auth.detail


# ── the handshake ────────────────────────────────────────────────────────
class Server:
    """smtplib.SMTP, as far as verify() uses it."""

    script: dict = {}

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.esmtp_features = {"auth": Server.script.get("auth", "LOGIN XOAUTH2")}
        self.authenticated = None
        self.sent = []
        Server.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        return 250, b"smtp.office365.com Hello [1.2.3.4]\nSIZE 157286400"

    def starttls(self):
        if Server.script.get("starttls_fails"):
            raise smtplib.SMTPException("STARTTLS refused")

    def auth(self, mechanism, authobject, **kwargs):
        if Server.script.get("auth_fails"):
            raise smtplib.SMTPAuthenticationError(
                535, b"5.7.139 Authentication unsuccessful\nMore noise")
        self.authenticated = (mechanism, authobject())

    def login(self, user, password):
        if Server.script.get("auth_fails"):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.139 Authentication unsuccessful")
        self.authenticated = ("LOGIN", user)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture()
def server(monkeypatch):
    Server.script = {}
    monkeypatch.setattr("app.mail.smtp.smtplib.SMTP", Server)
    return Server


def test_a_good_handshake_reports_each_step_and_sends_nothing(server):
    mailer = SmtpMailer("smtp.office365.com", 587, "club@x.com", "", "club@x.com",
                        token=lambda: "T")
    probe = mailer.verify()

    assert probe.ok and not probe.error
    joined = " ".join(probe.steps)
    assert "Connected to smtp.office365.com:587" in joined
    assert "STARTTLS accepted" in joined
    assert "Authenticated as club@x.com over XOAUTH2" in joined
    assert Server.last.sent == [], "verify must not send a message"


def test_a_refusal_keeps_the_steps_that_did_work(server):
    Server.script = {"auth_fails": True}
    probe = SmtpMailer("smtp.office365.com", 587, "club@x.com", "pw", "club@x.com").verify()

    assert probe.ok is False
    assert any("STARTTLS accepted" in step for step in probe.steps), \
        "knowing how far it got is most of the diagnosis"
    assert "535" in probe.error and "5.7.139" in probe.error
    assert "More noise" not in probe.error, "the server's first line, not its whole reply"


def test_a_server_that_has_stopped_offering_login_says_what_to_do(server):
    Server.script = {"auth": "XOAUTH2"}
    probe = SmtpMailer("smtp.office365.com", 587, "club@x.com", "pw", "club@x.com").verify()

    assert probe.ok is False
    assert "basic authentication is switched off" in probe.error
    assert Server.last.authenticated is None, "no point trying a mechanism it refuses"


def test_a_server_that_cannot_be_reached_at_all(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("Connection timed out")
    monkeypatch.setattr("app.mail.smtp.smtplib.SMTP", refuse)

    probe = SmtpMailer("smtp.office365.com", 587, "club@x.com", "pw", "club@x.com").verify()
    assert probe.ok is False and "Could not reach smtp.office365.com:587" in probe.error


def test_a_token_that_cannot_be_got_is_not_reported_as_a_bad_password(server):
    def no_token():
        raise OAuthError("the token endpoint refused (401) invalid_client: AADSTS7000215")

    probe = SmtpMailer("smtp.office365.com", 587, "club@x.com", "", "club@x.com",
                       token=no_token).verify()
    assert probe.ok is False and "AADSTS7000215" in probe.error


def test_with_no_server_the_probe_checks_the_outbox_instead(tmp_path):
    probe = OutboxMailer(tmp_path / "outbox", "club@x.com").verify()
    assert probe.ok
    assert "nothing is reaching anybody" in " ".join(probe.steps)


# ── the test message ─────────────────────────────────────────────────────
def test_the_trial_message_carries_the_evidence_and_no_secret(monkeypatch, tmp_path):
    settings = settings_for(monkeypatch, tmp_path, SMTP_HOST="smtp.office365.com",
                            SMTP_USER="club@warmspringscellar.com",
                            MAIL_FROM="club@warmspringscellar.com",
                            MAIL_REPLY_TO="cecilia.valdivia@warmspringscellar.com",
                            SMTP_OAUTH_TENANT_ID="t", SMTP_OAUTH_CLIENT_ID="c",
                            SMTP_OAUTH_CLIENT_SECRET="super-secret")
    message = trial_message(settings, "someone@gmail.com", "XOAUTH2")

    assert message.to == "someone@gmail.com"
    assert message.reply_to == "cecilia.valdivia@warmspringscellar.com"
    assert "smtp.office365.com:587" in message.text and "XOAUTH2" in message.text
    assert "SPF and DKIM" in message.text
    assert "super-secret" not in message.text + (message.html or "")


# ── the page ─────────────────────────────────────────────────────────────
from urllib.parse import unquote  # noqa: E402

from .test_flow import ADMIN, client  # noqa: E402,F401


def said(res) -> str:
    """The flash the desk is redirected to."""
    return unquote(res.headers["location"])


def test_the_page_is_behind_the_desk_password(client):
    assert client.get("/admin/mail").status_code == 401


def test_with_no_server_the_page_says_so_without_pretending(client):
    page = client.get("/admin/mail", auth=ADMIN).text
    assert "nothing is sent" in page
    assert "outbox" in page


def test_checking_the_connection_does_not_send_anything(client, tmp_path):
    page = client.get("/admin/mail?verify=1", auth=ADMIN).text
    assert "nothing is reaching anybody" in page
    assert list((tmp_path / "outbox").glob("*.eml")) == []


def test_the_button_sends_one_message_and_says_where_it_went(client, tmp_path):
    res = client.post("/admin/mail/test", data={"to": "someone@gmail.com"},
                      auth=ADMIN, follow_redirects=False)
    assert res.status_code == 303

    [written] = list((tmp_path / "outbox").glob("*mail-test*.eml"))
    assert "someone@gmail.com" in written.read_text()
    # Nothing was invented about it: with no server, it was not sent.
    assert "Not sent" in said(res)


def test_a_sent_message_reaches_nobody_on_the_club_list(client, tmp_path):
    """The one risk of a button like this is writing to a real member."""
    from .test_flow import apply_
    apply_(client)
    client.post("/admin/mail/test", data={"to": "someone@gmail.com"},
                auth=ADMIN, follow_redirects=False)

    bodies = [p.read_text() for p in (tmp_path / "outbox").glob("*mail-test*.eml")]
    assert bodies and all("matias@example.com" not in b for b in bodies)


def test_an_address_that_is_not_one_is_refused_before_anything_opens(client, tmp_path):
    res = client.post("/admin/mail/test", data={"to": "not an address"},
                      auth=ADMIN, follow_redirects=False)
    assert "That is not an address" in said(res)
    assert list((tmp_path / "outbox").glob("*mail-test*.eml")) == []
