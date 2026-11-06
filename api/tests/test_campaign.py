"""The do-not-contact record, and the letter that goes out from a platform.

The club does not send the campaign. A mailing platform does, and it keeps
its own suppression list, its own unsubscribe link and its own numbers. What
is tested here is the narrow thing the club still owns — a record of who must
not be written to that outlives the provider — and the two factual claims in
the letter that would cost the winery something if they were wrong.
"""

from __future__ import annotations

import importlib
import re
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


def test_the_list_downloads_so_it_can_be_subtracted_from_a_wave(client):
    """The step between the planner and the platform, and the easy one to skip."""
    with client.main.db.transaction() as con:
        campaign.suppress(con, "a@example.com", "unsubscribe", "wave-1")
    res = client.get("/admin/campaigns/suppressions.csv", auth=ADMIN)
    assert res.status_code == 200
    assert res.text.splitlines()[0] == "email,created_at,source,campaign"
    assert "a@example.com" in res.text


def test_the_screen_is_behind_the_admin_password(client):
    assert client.get("/admin/campaigns").status_code == 401
    assert client.get("/admin/campaigns/suppressions.csv").status_code == 401


# ── bringing the platform's opt-outs back ────────────────────────────────
def said(res) -> str:
    """What the desk is told, out of the redirect. The flash is URL-encoded."""
    from urllib.parse import unquote
    return unquote(res.headers["location"])

def test_an_export_is_imported_and_counted(client):
    res = client.post("/admin/campaigns/import", auth=ADMIN,
                      follow_redirects=False,
                      data={"addresses": "One@Example.com\ntwo@example.com",
                            "source": "unsubscribe", "note": "wave-1"})
    assert res.status_code == 303
    assert "2 added" in said(res)
    with client.main.db.connect() as con:
        assert campaign.addresses(con) == {"one@example.com", "two@example.com"}
        assert campaign.suppressed(con)[0]["campaign"] == "wave-1"


def test_importing_the_same_export_twice_adds_nothing(client):
    data = {"addresses": "one@example.com", "source": "unsubscribe", "note": ""}
    client.post("/admin/campaigns/import", data=data, auth=ADMIN,
                follow_redirects=False)
    res = client.post("/admin/campaigns/import", data=data, auth=ADMIN,
                      follow_redirects=False)
    assert "0 added, 1 already" in said(res)


def test_a_pasted_spreadsheet_column_does_not_store_its_header(client):
    """Nobody pastes a clean list. Headers, blanks and names come with it."""
    res = client.post("/admin/campaigns/import", auth=ADMIN,
                      follow_redirects=False,
                      data={"addresses": "Email Address,Reason\n"
                                         "one@example.com,Unsubscribed\n"
                                         "\n"
                                         "Anita Vogel\n"
                                         "two@example.com,Bounced\n",
                            "source": "bounce", "note": ""})
    assert "2 added" in said(res)
    with client.main.db.connect() as con:
        assert campaign.addresses(con) == {"one@example.com", "two@example.com"}


def test_an_import_of_nothing_says_so_rather_than_claiming_success(client):
    res = client.post("/admin/campaigns/import", auth=ADMIN,
                      follow_redirects=False,
                      data={"addresses": "Anita Vogel\n   \n", "source": "bounce",
                            "note": ""})
    assert "No addresses found" in said(res)


def test_an_unknown_reason_imports_nothing(client):
    res = client.post("/admin/campaigns/import", auth=ADMIN,
                      follow_redirects=False,
                      data={"addresses": "one@example.com", "source": "whatever",
                            "note": ""})
    assert "nothing was imported" in said(res)
    with client.main.db.connect() as con:
        assert campaign.addresses(con) == set()


def test_the_counts_by_reason_are_for_the_screen(client):
    with client.main.db.transaction() as con:
        campaign.suppress(con, "a@example.com", "unsubscribe")
        campaign.suppress(con, "b@example.com", "unsubscribe")
        campaign.suppress(con, "c@example.com", "bounce")
    with client.main.db.connect() as con:
        counts = {row["source"]: row["how_many"] for row in campaign.by_source(con)}
    assert counts == {"unsubscribe": 2, "bounce": 1}


def test_a_members_receipts_are_not_a_campaign(client):
    """Unsubscribing stops the letters. It must not stop an invoice.

    The suppression list is applied when a wave is prepared and nowhere else;
    notify.py does not look at it, which is the whole difference between a
    campaign and a receipt.
    """
    source = (ROOT / "app" / "notify.py").read_text()
    assert "campaign" not in source, \
        "transactional mail must not start checking the campaign suppression list"


# ── the service does not send campaigns ──────────────────────────────────
def test_nothing_in_the_service_sends_a_campaign(client):
    """The sender, its relay and its unsubscribe endpoint are gone.

    They existed because the plan was to send through Microsoft High Volume
    Email, which supplied none of what a mailing needs. The plan changed, and
    a half-removed sender is worse than either version of it.
    """
    assert not (REPO / "tools" / "send_campaign.py").exists()
    assert not (REPO / "mail-templates" / "HVE.md").exists()
    assert client.get("/unsubscribe").status_code == 404
    assert client.post("/unsubscribe").status_code == 404


def test_no_trace_of_the_relay_is_left_in_the_code_or_the_setup():
    for path in (ROOT / "app" / "main.py", ROOT / "app" / "campaign.py",
                 ROOT / "app" / "db.py", REPO / ".env.example"):
        assert "HVE" not in path.read_text(), f"{path.name} still mentions the relay"


# ── the letter ───────────────────────────────────────────────────────────
def letter() -> tuple[str, str]:
    """Both parts of the letter, rendered, with the merge fields filled."""
    html = (REPO / "mail-templates" / "new-kid-on-the-block.html").read_text()
    text = (REPO / "mail-templates" / "new-kid-on-the-block.txt").read_text()
    # The plain-text file opens with a note to whoever edits it, cut at the
    # first full rule. The reader must never see it.
    _before, rule, after = text.partition("-" * 72)
    assert rule, "the editor's note has no rule to cut it from the letter"
    fields = {"first_name": "Anita", "email": "anita@example.com",
              "link": "https://warmspringscellar.com/?src=w1#join",
              "unsubscribe_url": "https://example.invalid/u",
              "postal_address": "1234 Warm Springs Road, Glen Ellen, CA 95442"}
    out = []
    for part in (html, after.lstrip("\n")):
        for key, value in fields.items():
            part = part.replace("{{" + key + "}}", value)
        out.append(part)
    return out[0], out[1]


def flat(body: str) -> str:
    """One line, comments out: the text part wraps at 72 and the HTML part
    carries notes to whoever edits it that the reader never sees."""
    return " ".join(re.sub(r"<!--.*?-->", "", body, flags=re.S).split())


def test_the_letter_opens_on_the_printed_announcement():
    """Cecilia posted a printed letter to everybody on this list.

    That is the fact the whole mailing rests on — the reader is not being
    written to out of nowhere — and it is also the answer the platform will
    want when it asks how these addresses were obtained. It belongs in the
    first sentence, where both of them see it.
    """
    for part, name in zip(letter(), ("html", "text")):
        body = flat(part)
        assert "announcement by post from Cecilia Valdivia" in body, \
            f"the {name} part no longer says the printed letter came first"


def test_the_letter_does_not_promise_free_shipping():
    """The draft said shipping was complimentary. It is not, and has not been
    since 19 October: every tier is billed the carrier's rate less a credit.
    Promising it in the first letter and charging carriage at checkout is the
    kind of thing a member remembers."""
    for part, name in zip(letter(), ("html", "text")):
        body = flat(part).lower()
        assert "complimentary" not in body, f"the {name} part promises it"
        assert "shipping is billed at the carrier" in body, \
            f"the {name} part does not say what is actually charged"


def test_both_halves_carry_the_postal_address_and_the_age_line():
    """CAN-SPAM wants a physical address in a commercial message. The platform
    inserts its own footer, but the letter carries these itself so that a
    preview, a forward or a paste into a different tool cannot lose them."""
    for part, name in zip(letter(), ("html", "text")):
        body = flat(part)
        assert "Glen Ellen, CA 95442" in body, f"no postal address in the {name} part"
        assert "21 or older" in body, f"no age line in the {name} part"


def test_the_letter_leaves_a_slot_for_the_platforms_unsubscribe_link():
    """Whoever sends it fills {{unsubscribe_url}} with its own link. The slot
    has to be there, in both parts, or the footer offers no way out at all."""
    for name in ("new-kid-on-the-block.html", "new-kid-on-the-block.txt"):
        body = (REPO / "mail-templates" / name).read_text()
        assert "{{unsubscribe_url}}" in body, f"{name} has nowhere for the link"


def test_the_plain_text_letter_never_ships_its_editors_note():
    _html, text = letter()
    assert "plain-text twin" not in text, "the note to the editor came too"
    assert text.lstrip().startswith("WARM SPRINGS CELLAR")
