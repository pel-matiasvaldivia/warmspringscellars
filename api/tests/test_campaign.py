"""Unsubscribing, the suppression list, and the letter the sender builds.

Everything here exists because the first mailing goes out through Microsoft
High Volume Email, which is a relay: it keeps no suppression list, sends no
list-unsubscribe header and reports nothing back. The club supplies all three,
so the club has to test all three — and the failures worth testing are the
quiet ones, the ones where a wave of nine hundred letters goes out looking
perfectly healthy and is in fact uncompliant, unstoppable, or both.

The loudest of those is at the bottom: a List-Unsubscribe header that Python
will happily turn into mojibake nobody can parse.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from app import campaign                                      # noqa: E402

ADMIN = ("cellar", "test-password")
SECRET = "test-secret-key-not-random"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setenv("ADMIN_USER", ADMIN[0])
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN[1])
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    for name in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[name]
    main = importlib.import_module("app.main")
    importlib.reload(main)
    with TestClient(main.app) as test_client:
        test_client.main = main
        test_client.tmp = tmp_path
        yield test_client


# ── the token ────────────────────────────────────────────────────────────
def test_a_token_carries_the_address_and_the_campaign():
    who = campaign.read(campaign.mint("Anita@Example.COM", "wave-1", SECRET), SECRET)
    assert who.email == "anita@example.com", "stored and compared lower-cased"
    assert who.campaign == "wave-1"


def test_a_token_signed_with_another_key_is_refused():
    token = campaign.mint("anita@example.com", "wave-1", "someone-elses-key")
    with pytest.raises(campaign.BadToken):
        campaign.read(token, SECRET)


def test_a_token_cannot_be_edited_to_unsubscribe_somebody_else():
    token = campaign.mint("anita@example.com", "wave-1", SECRET)
    body, _, signature = token.rpartition(".")
    assert body and signature
    with pytest.raises(campaign.BadToken):
        campaign.read(f"{body}x.{signature}", SECRET)


def test_an_offer_token_cannot_be_presented_as_an_unsubscribe():
    """Different salts, so a link minted for one purpose is no use for the other."""
    from itsdangerous import URLSafeTimedSerializer
    offer = URLSafeTimedSerializer(SECRET, salt="offer").dumps({"e": "x@example.com"})
    with pytest.raises(campaign.BadToken):
        campaign.read(offer, SECRET)


def test_the_link_is_absolute_and_has_exactly_one_slash():
    link = campaign.link("https://warmspringscellar.com", "a@example.com",
                         "wave-1", SECRET)
    assert link.startswith("https://warmspringscellar.com/unsubscribe?t=")
    assert "com//" not in link


# ── the page and the one click ───────────────────────────────────────────
def test_the_link_in_the_letter_asks_before_it_acts(client):
    """A GET changes nothing: mail scanners fetch every URL in a message."""
    token = campaign.mint("anita@example.com", "wave-1", SECRET)
    res = client.get(f"/unsubscribe?t={token}")
    assert res.status_code == 200
    assert "anita@example.com" in res.text
    assert 'method="post"' in res.text
    with client.main.db.connect() as con:
        assert not campaign.is_suppressed(con, "anita@example.com"), \
            "a prefetched link must not unsubscribe anybody"


def test_the_post_unsubscribes(client):
    token = campaign.mint("anita@example.com", "wave-1", SECRET)
    res = client.post("/unsubscribe", data={"t": token})
    assert res.status_code == 200
    with client.main.db.connect() as con:
        assert campaign.is_suppressed(con, "anita@example.com")
        row = campaign.suppressed(con)[0]
    assert row["source"] == "unsubscribe"
    assert row["campaign"] == "wave-1", "so the desk can see which letter did it"


def test_one_click_needs_no_session_and_no_confirmation(client):
    """RFC 8058: what Gmail and Yahoo send when the reader presses their button.

    No cookie, no referrer, no HTML, no second request. If this ever needs
    any of those, bulk mail from this domain starts going to spam.
    """
    token = campaign.mint("anita@example.com", "wave-1", SECRET)
    res = client.post("/unsubscribe", data={"t": token},
                      headers={"Accept": "*/*"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    with client.main.db.connect() as con:
        assert campaign.is_suppressed(con, "anita@example.com")


def test_unsubscribing_twice_is_not_an_error(client):
    """Retries, double clicks and a mail client acting on the header all land here."""
    token = campaign.mint("anita@example.com", "wave-1", SECRET)
    assert client.post("/unsubscribe", data={"t": token}).status_code == 200
    assert client.post("/unsubscribe", data={"t": token}).status_code == 200
    with client.main.db.connect() as con:
        assert len(campaign.suppressed(con)) == 1


def test_a_forged_token_is_refused_but_still_told_where_to_write(client):
    res = client.get("/unsubscribe?t=not-a-token")
    assert res.status_code == 400
    assert "club@warmspringscellar.com" in res.text, \
        "somebody who wants out must never be left with no way out"


def test_a_forged_one_click_is_refused(client):
    res = client.post("/unsubscribe", data={"t": "not-a-token"})
    assert res.status_code == 400


# ── the list ─────────────────────────────────────────────────────────────
def test_the_first_reason_wins(client):
    with client.main.db.transaction() as con:
        assert campaign.suppress(con, "a@example.com", "unsubscribe", "wave-1")
        assert not campaign.suppress(con, "a@example.com", "bounce", "wave-2")
    with client.main.db.connect() as con:
        row = campaign.suppressed(con)[0]
    assert row["source"] == "unsubscribe", \
        "a bounce does not overwrite the fact that a person chose this"


def test_an_unknown_source_is_a_mistake_not_a_row(client):
    with client.main.db.transaction() as con:
        with pytest.raises(ValueError):
            campaign.suppress(con, "a@example.com", "because-i-said-so")


def test_the_same_address_in_two_spellings_is_one_row(client):
    with client.main.db.transaction() as con:
        campaign.suppress(con, " Anita@Example.COM ", "unsubscribe")
    with client.main.db.connect() as con:
        assert campaign.is_suppressed(con, "anita@example.com")
        assert campaign.addresses(con) == {"anita@example.com"}


def test_the_desk_can_take_an_address_off_by_hand(client):
    res = client.post("/admin/campaigns/suppress",
                      data={"email": "Phoned@Example.com"}, auth=ADMIN,
                      follow_redirects=False)
    assert res.status_code == 303
    with client.main.db.connect() as con:
        assert campaign.is_suppressed(con, "phoned@example.com")


def test_the_suppression_list_downloads_for_a_sender_off_the_server(client):
    with client.main.db.transaction() as con:
        campaign.suppress(con, "a@example.com", "unsubscribe", "wave-1")
    res = client.get("/admin/campaigns/suppressions.csv", auth=ADMIN)
    assert res.status_code == 200
    assert res.text.splitlines()[0] == "email,created_at,source,campaign"
    assert "a@example.com" in res.text


def test_the_campaign_screen_is_behind_the_admin_password(client):
    assert client.get("/admin/campaigns").status_code == 401
    assert client.get("/admin/campaigns/suppressions.csv").status_code == 401


# ── what went out ────────────────────────────────────────────────────────
def test_only_accepted_messages_count_as_sent(client):
    with client.main.db.transaction() as con:
        campaign.record(con, "wave-1", "a@example.com", "sent")
        campaign.record(con, "wave-1", "b@example.com", "failed", "550 no such user")
    with client.main.db.connect() as con:
        assert campaign.sent_already(con, "wave-1") == {"a@example.com"}, \
            "a refusal is somebody to try again, not somebody who was written to"


def test_a_failed_message_can_become_a_sent_one(client):
    with client.main.db.transaction() as con:
        campaign.record(con, "wave-1", "a@example.com", "failed", "timed out")
    with client.main.db.transaction() as con:
        campaign.record(con, "wave-1", "a@example.com", "sent")
    with client.main.db.connect() as con:
        assert campaign.sent_already(con, "wave-1") == {"a@example.com"}
        assert len(con.execute("SELECT 1 FROM campaign_send").fetchall()) == 1


def test_the_same_campaign_never_records_an_address_twice(client):
    with client.main.db.transaction() as con:
        campaign.record(con, "wave-1", "Anita@Example.com", "sent")
        campaign.record(con, "wave-1", "anita@example.com", "sent")
    with client.main.db.connect() as con:
        assert len(con.execute("SELECT 1 FROM campaign_send").fetchall()) == 1


def test_the_tally_counts_sends_and_the_opt_outs_they_caused(client):
    with client.main.db.transaction() as con:
        campaign.record(con, "wave-1", "a@example.com", "sent")
        campaign.record(con, "wave-1", "b@example.com", "sent")
        campaign.record(con, "wave-1", "c@example.com", "failed", "550")
        campaign.suppress(con, "b@example.com", "unsubscribe", "wave-1")
        campaign.suppress(con, "z@example.com", "bounce", "wave-1")
    with client.main.db.connect() as con:
        row = campaign.tally(con)[0]
    assert (row["campaign"], row["sent"], row["failed"]) == ("wave-1", 2, 1)
    assert row["unsubscribed"] == 1, "a bounce is not somebody asking to stop"


def test_a_members_receipts_are_not_a_campaign(client):
    """Unsubscribing stops the letters. It must not stop an invoice.

    The suppression list is consulted by tools/send_campaign.py and by
    nothing else; notify.py does not look at it, which is the whole
    difference between a campaign and a receipt.
    """
    source = (ROOT / "app" / "notify.py").read_text()
    assert "campaign" not in source, \
        "transactional mail must not start checking the campaign suppression list"


# ── the sender ───────────────────────────────────────────────────────────
SENDER = REPO / "tools" / "send_campaign.py"

WAVE = """email,first,last,state,city,postcode,club,phone,link
Anita@Example.COM,Anita,Vogel,California,Los Angeles,90001,Inner Circle,,https://warmspringscellar.com/?src=w1&f=Anita#join
bob@example.com,,Stone,Oregon,Portland,97201,Inner Circle,,https://warmspringscellar.com/?src=w1&l=Stone#join
cara@example.com,Cara,Lin,Texas,Austin,78701,Inner Circle,,https://warmspringscellar.com/?src=w1&f=Cara#join
"""


def run_sender(tmp_path, *args, env=None, wave=WAVE):
    path = tmp_path / "wave-1-inner-circle.csv"
    path.write_text(wave)
    full = {"DATA_DIR": str(tmp_path / "var"), "SECRET_KEY": SECRET,
            "PUBLIC_BASE_URL": "https://warmspringscellar.com",
            "PATH": "/usr/bin:/bin", **(env or {})}
    return subprocess.run(
        [sys.executable, str(SENDER), str(path),
         "--postal", "1234 Warm Springs Rd, Glen Ellen, CA 95442", *args],
        capture_output=True, text=True, env=full, cwd=str(REPO))


@pytest.fixture()
def seeded(tmp_path):
    """A database the sender can read, with one address already opted out."""
    import app.db as db_module
    importlib.reload(db_module)
    data = tmp_path / "var"
    data.mkdir(parents=True, exist_ok=True)
    database = db_module.Database(data / "club.sqlite3")
    with database.transaction() as con:
        campaign.suppress(con, "cara@example.com", "unsubscribe", "earlier-wave")
    return tmp_path


def test_the_sender_will_not_run_without_a_persistent_secret(tmp_path):
    out = run_sender(tmp_path, env={"SECRET_KEY": ""})
    assert out.returncode != 0
    assert "SECRET_KEY is unset" in out.stdout + out.stderr, \
        "links signed with a dying key are links the server will reject"


def test_the_sender_will_not_run_without_a_postal_address(tmp_path):
    path = tmp_path / "wave-1.csv"
    path.write_text(WAVE)
    out = subprocess.run(
        [sys.executable, str(SENDER), str(path)], capture_output=True, text=True,
        env={"DATA_DIR": str(tmp_path / "var"), "SECRET_KEY": SECRET,
             "PATH": "/usr/bin:/bin"}, cwd=str(REPO))
    assert out.returncode != 0
    assert "CAN-SPAM" in out.stdout + out.stderr


def test_the_sender_refuses_when_it_cannot_read_a_suppression_list(tmp_path):
    out = run_sender(tmp_path)
    assert out.returncode != 0
    said = out.stdout + out.stderr
    assert "No club database" in said
    assert "people who have asked us to stop" in said, \
        "an empty list reads exactly like nobody having ever unsubscribed"


def test_a_dry_run_sends_nothing_and_skips_the_opted_out(seeded):
    out = run_sender(seeded)
    assert out.returncode == 0, out.stderr
    assert "3 rows → 2 to write to" in out.stdout
    assert "1 on the suppression list" in out.stdout
    assert "nothing was sent" in out.stdout
    assert "cara@example.com" not in out.stdout


def test_a_dry_run_renders_every_message_not_a_sample(seeded):
    """A merge field missing only on row 212 is the one a sample of three misses."""
    out = run_sender(seeded)
    assert "2 messages render with every field filled" in out.stdout


def test_an_address_already_written_to_is_not_written_to_again(seeded):
    import app.db as db_module
    database = db_module.Database(seeded / "var" / "club.sqlite3")
    with database.transaction() as con:
        campaign.record(con, "wave-1-inner-circle", "anita@example.com", "sent")
    out = run_sender(seeded)
    assert "1 already sent in this campaign" in out.stdout
    assert "3 rows → 1 to write to" in out.stdout


def test_sending_needs_both_the_flag_and_the_credentials(seeded):
    out = run_sender(seeded, "--send")
    assert out.returncode != 0
    assert "HVE_USER and HVE_PASSWORD are unset" in out.stdout + out.stderr


def test_sending_against_a_file_list_is_refused_because_nothing_is_recorded(tmp_path):
    """--suppressions is for a dry run away from the server, not for a send.

    Sending with no database means nothing records what went out, so the next
    run would write to the whole wave a second time.
    """
    suppressions = tmp_path / "suppressions.csv"
    suppressions.write_text("email,created_at,source,campaign\n"
                            "cara@example.com,2026-11-01T00:00:00+00:00,unsubscribe,x\n")
    out = run_sender(tmp_path, "--send", "--suppressions", str(suppressions),
                     env={"HVE_USER": "hve@warmspringscellar.com",
                          "HVE_PASSWORD": "hunter2"})
    assert out.returncode != 0
    assert "nothing records what went out" in out.stdout + out.stderr


def test_an_unfilled_merge_field_is_a_refusal(seeded, monkeypatch):
    """A letter that greets somebody as {{first_name}} must never leave."""
    sys.path.insert(0, str(REPO / "tools"))
    for name in ("send_campaign",):
        sys.modules.pop(name, None)
    import send_campaign                                      # noqa: PLC0415

    letter = send_campaign.Letter(subject="x", html="Dear {{first_name}} {{nobody}}",
                                 text="Dear {{first_name}}")
    with pytest.raises(SystemExit) as raised:
        letter.render({"first_name": "Anita"})
    assert "nobody" in str(raised.value)


def test_the_plain_text_letter_never_ships_its_editors_note():
    sys.path.insert(0, str(REPO / "tools"))
    import send_campaign                                      # noqa: PLC0415

    letter = send_campaign.Letter.load("new-kid-on-the-block",
                                       "The New Kid on the Block")
    assert "plain-text twin" not in letter.text, "the note to the editor came too"
    assert letter.text.lstrip().startswith("WARM SPRINGS CELLAR")


def test_both_halves_of_the_letter_carry_the_unsubscribe_link():
    sys.path.insert(0, str(REPO / "tools"))
    import send_campaign                                      # noqa: PLC0415

    letter = send_campaign.Letter.load("new-kid-on-the-block", "x")
    html, text = letter.render({
        "first_name": "Anita", "email": "anita@example.com",
        "link": "https://warmspringscellar.com/?src=w1#join",
        "unsubscribe_url": "https://warmspringscellar.com/unsubscribe?t=abc",
        "postal_address": "1234 Warm Springs Rd, Glen Ellen, CA 95442",
    })
    for part, name in ((html, "html"), (text, "text")):
        assert "unsubscribe?t=abc" in part, f"no way out of the {name} part"
        assert "Glen Ellen, CA 95442" in part, f"no postal address in the {name} part"
        assert "21 or older" in part


def test_the_letter_says_why_this_person_is_hearing_from_us():
    """The one line the reader is actually weighing.

    The draft opened "You've received an announcement from Cecilia Valdivia",
    which assumed Deerfield had already written to them. Under the plan that
    was chosen nobody has: this letter IS the announcement, sent by the
    winery to a list it was handed. A letter that opens by referring to one
    the reader never got reads as a mass mailing that does not know who it is
    talking to, and the footer's fine print is not where somebody looks for
    the answer.
    """
    sys.path.insert(0, str(REPO / "tools"))
    import send_campaign                                      # noqa: PLC0415

    letter = send_campaign.Letter.load("new-kid-on-the-block", "x")
    html, text = letter.render({
        "first_name": "Anita", "email": "a@example.com", "link": "https://x/",
        "unsubscribe_url": "https://x/unsubscribe?t=abc", "postal_address": "x",
    })
    import re as _re
    for part, name in ((html, "html"), (text, "text")):
        # Comments out: the file explains the change, and an explanation the
        # reader never sees must not be able to pass or fail this.
        flat = " ".join(_re.sub(r"<!--.*?-->", "", part, flags=_re.S).split())
        assert "You are hearing from us because" in flat, \
            f"the {name} part never says why this letter arrived"
        assert "You&rsquo;ve received an announcement" not in flat, \
            f"the {name} part still assumes a letter Deerfield never sent"
        assert "You've received an announcement" not in flat, \
            f"the {name} part still assumes a letter Deerfield never sent"


def test_the_letter_does_not_promise_free_shipping():
    """The draft said shipping was complimentary. It is not, and has not been
    since 19 October: every tier is billed the carrier's rate less a credit.
    Saying otherwise in the first letter and charging carriage at checkout is
    the kind of thing a member remembers.

    Read from what the reader is sent, not from the file: the file explains
    the change in a comment, and a comment the reader never sees must not be
    able to fail this.
    """
    import re as _re
    sys.path.insert(0, str(REPO / "tools"))
    import send_campaign                                      # noqa: PLC0415

    letter = send_campaign.Letter.load("new-kid-on-the-block", "x")
    html, text = letter.render({
        "first_name": "Anita", "email": "a@example.com", "link": "https://x/",
        "unsubscribe_url": "https://x/unsubscribe?t=abc", "postal_address": "x",
    })
    def flat(body: str) -> str:
        """One line, one space between words — the text part wraps at 72."""
        return _re.sub(r"\s+", " ", _re.sub(r"<!--.*?-->", "", body, flags=_re.S)).lower()

    for part, name in ((flat(html), "html"), (flat(text), "text")):
        assert "complimentary" not in part, f"the {name} part promises it"
        assert "shipping is billed at the carrier" in part, \
            f"the {name} part does not say what is actually charged"


# ── the header that fails silently ───────────────────────────────────────
def build_one(**over):
    sys.path.insert(0, str(REPO / "tools"))
    import send_campaign                                      # noqa: PLC0415

    letter = send_campaign.Letter.load("new-kid-on-the-block",
                                       "The New Kid on the Block")
    person = {"email": "anita@example.com", "first": "Anita",
              "link": "https://warmspringscellar.com/?src=w1#join"}
    fields = {"sender": "club@warmspringscellar.com",
              "reply_to": "club@warmspringscellar.com",
              "unsubscribe": campaign.link("https://warmspringscellar.com",
                                           person["email"], "wave-1-inner-circle",
                                           SECRET),
              "postal": "1234 Warm Springs Rd, Glen Ellen, CA 95442",
              "domain": "warmspringscellar.com", **over}
    return send_campaign.build(letter, person, **fields)


def test_the_unsubscribe_header_is_a_url_and_not_mojibake():
    """The quiet failure this whole file is really about.

    The signed URL has no spaces in it and is longer than a header line.
    Python's default policy cannot fold a line with no fold points, so it
    encodes the whole thing as an RFC 2047 encoded word — which is not legal
    in a structured header, which no receiver parses, and which means the
    one-click unsubscribe Gmail and Yahoo require is absent from every
    message in the wave while the send itself looks perfectly healthy.
    """
    msg = build_one()
    raw = msg.as_string()
    header = next(line for line in raw.splitlines()
                  if line.startswith("List-Unsubscribe:"))
    assert "=?utf-8?" not in header, "encoded words: no receiver can read this"
    assert header.startswith("List-Unsubscribe: <https://warmspringscellar.com/unsubscribe?t=")
    assert header.rstrip().endswith("<mailto:club@warmspringscellar.com?subject=unsubscribe>")


def test_the_one_click_header_is_there_and_exact():
    msg = build_one()
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_the_header_link_is_one_the_service_actually_honours(client):
    """Minted by the tool, verified by the server. Both halves, one key."""
    msg = build_one()
    url = msg["List-Unsubscribe"].split(">")[0].lstrip("<")
    token = url.partition("?t=")[2]
    assert client.post("/unsubscribe", data={"t": token}).status_code == 200
    with client.main.db.connect() as con:
        assert campaign.is_suppressed(con, "anita@example.com")


def test_the_message_has_both_a_text_and_an_html_part():
    """An HTML-only bulk message is one of the cheapest spam signals there is."""
    msg = build_one()
    assert msg.get_content_type() == "multipart/alternative"
    kinds = [part.get_content_type() for part in msg.iter_parts()]
    assert kinds == ["text/plain", "text/html"]


def test_an_empty_name_does_not_become_dear_comma():
    sys.path.insert(0, str(REPO / "tools"))
    import send_campaign                                      # noqa: PLC0415

    letter = send_campaign.Letter.load("new-kid-on-the-block", "x")
    msg = send_campaign.build(
        letter, {"email": "b@example.com", "first": "", "link": "https://x/"},
        sender="club@warmspringscellar.com", reply_to="club@warmspringscellar.com",
        unsubscribe="https://warmspringscellar.com/unsubscribe?t=abc",
        postal="1234 Warm Springs Rd", domain="warmspringscellar.com")
    body = msg.get_body(("plain",)).get_content()
    assert "Dear Wine Lover," in body
    assert "Dear ," not in body


def test_every_message_gets_its_own_message_id():
    assert build_one()["Message-ID"] != build_one()["Message-ID"]


def test_replies_come_back_to_the_club_and_not_to_the_relay():
    """The letter asks for a click, but somebody will always reply to it."""
    assert build_one()["Reply-To"] == "club@warmspringscellar.com"
