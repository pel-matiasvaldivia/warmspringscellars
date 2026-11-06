"""The emails the club sends, and nothing else.

Kept apart from the routes so the wording is in one place and can be read
without reading HTTP handling. Every message goes out as text and HTML: the
text part is the one that survives spam filters and screen readers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .catalogue import Tier
from .mail import Mailer, Message

SIGNOFF = "Cecilia Valdivia, Owner & Winemaker\nWarm Springs Cellar, Glen Ellen, Sonoma Valley"


def _plain(name: str) -> str:
    """A tier name after a possessive. "Your The Tasting Case" is nobody's English."""
    return name[4:] if name.lower().startswith("the ") else name


def _html(body: str, internal: bool = False) -> str:
    """The house wrapper. `internal` is for mail the club sends to itself.

    An operational alert signed "Cecilia Valdivia, Owner & Winemaker" is
    Cecilia signing a note to herself, and the consumer footer below —
    "you asked for an allocation", "you must be 21 or older to purchase" —
    is addressed to somebody who did neither. Both come off.
    """
    signature = "" if internal else """
<p style="margin:26px 0 0;font-size:13px;color:#5c4650">Cecilia Valdivia, Owner &amp; Winemaker<br>
<span style="color:#9a8890">Glen Ellen, Sonoma Valley</span></p>"""
    footer = """
<p style="font:11px/1.5 system-ui,sans-serif;color:#9a8890;max-width:520px;margin:14px auto 0">
Sent by the club service to the cellar desk. Nobody outside the winery sees this.</p>""" \
        if internal else """
<p style="font:11px/1.5 system-ui,sans-serif;color:#9a8890;max-width:520px;margin:14px auto 0">
You are receiving this because you asked for an allocation at warmspringscellar.com.
You must be 21 or older to purchase. Please drink responsibly.</p>"""
    return f"""<!doctype html><html><body style="margin:0;background:#faf8f5;padding:28px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="100%" style="max-width:520px;background:#fff;
 border:1px solid #e2d8d0;padding:32px 28px;font:15px/1.65 Georgia,serif;color:#2c1e24">
<tr><td>
<p style="font:600 11px/1 system-ui,sans-serif;letter-spacing:.22em;text-transform:uppercase;
 color:#5c0037;margin:0 0 22px">Warm Springs Cellar</p>
{body}{signature}
</td></tr></table>{footer}
</td></tr></table></body></html>"""


def send_invitation(mailer: Mailer, app: sqlite3.Row, url: str, expires_on: str,
                    reply_to: str) -> str:
    text = f"""Dear {app['first_name']},

A place has opened on our list, and we have kept you an allocation.

Choose the one that suits your table here:
{url}

The invitation is open until {expires_on}. Nothing is charged until you pick,
and you can skip a season whenever you need to.

{SIGNOFF}"""

    html = _html(f"""
<p style="margin:0 0 14px">Dear {app['first_name']},</p>
<p style="margin:0 0 14px">A place has opened on our list, and we have kept you an allocation.</p>
<p style="margin:0 0 22px"><a href="{url}" style="display:inline-block;background:#5c0037;
 color:#fff;text-decoration:none;padding:13px 28px;font:600 11px/1 system-ui,sans-serif;
 letter-spacing:.2em;text-transform:uppercase">Choose your allocation</a></p>
<p style="margin:0 0 10px;font-size:13px;color:#5c4650">The invitation is open until
{expires_on}. Nothing is charged until you pick, and you can skip a season whenever
you need to.</p>
<p style="margin:0;font-size:12px;color:#9a8890;word-break:break-all">{url}</p>""")

    return mailer.send(Message(to=app["email"], subject="Your allocation at Warm Springs Cellar",
                               text=text, html=html, reply_to=reply_to))


def send_decline(mailer: Mailer, app: sqlite3.Row, reply_to: str) -> str:
    text = f"""Dear {app['first_name']},

Thank you for asking after our wines. We make a thousand cases, and this season
the list is full — so we cannot offer you an allocation right now.

We keep every request. When a place opens we work through them in the order
they arrived, and yours is in that queue.

{SIGNOFF}"""

    html = _html(f"""
<p style="margin:0 0 14px">Dear {app['first_name']},</p>
<p style="margin:0 0 14px">Thank you for asking after our wines. We make a thousand cases,
and this season the list is full — so we cannot offer you an allocation right now.</p>
<p style="margin:0">We keep every request. When a place opens we work through them in the
order they arrived, and yours is in that queue.</p>""")

    return mailer.send(Message(to=app["email"], subject="Your request at Warm Springs Cellar",
                               text=text, html=html, reply_to=reply_to))


def send_receipt(mailer: Mailer, member: sqlite3.Row, order: sqlite3.Row,
                 invoice: sqlite3.Row, tier: Tier, reply_to: str,
                 pdf: Path | None = None) -> str:
    total = f"${int(order['total_cents']) / 100:,.2f}"
    text = f"""Dear {member['first_name']},

You are in. Your {tier.name} allocation is reserved.

  Order    {order['reference']}
  Invoice  {invoice['number']}
  Total    {total}

We write two weeks before every release so you can change quantities or skip
the season. Your wine travels in a pine crate on a bed of wood wool, with a
card from whoever made it.

{SIGNOFF}"""

    html = _html(f"""
<p style="margin:0 0 14px">Dear {member['first_name']},</p>
<p style="margin:0 0 18px">You are in. Your <strong>{tier.name}</strong> allocation is reserved.</p>
<table role="presentation" style="font:13px/1.8 system-ui,sans-serif;color:#5c4650;
 border-top:1px solid #e2d8d0;border-bottom:1px solid #e2d8d0;padding:10px 0;width:100%">
<tr><td style="color:#9a8890">Order</td><td align="right">{order['reference']}</td></tr>
<tr><td style="color:#9a8890">Invoice</td><td align="right">{invoice['number']}</td></tr>
<tr><td style="color:#9a8890">Total</td><td align="right"><strong>{total}</strong></td></tr>
</table>
<p style="margin:18px 0 0;font-size:13px;color:#5c4650">We write two weeks before every
release so you can change quantities or skip the season. Your wine travels in a pine crate
on a bed of wood wool, with a card from whoever made it.</p>""")

    message = Message(to=member["email"],
                      subject=f"Welcome to the cellar — invoice {invoice['number']}",
                      text=text, html=html, reply_to=reply_to)
    # The PDF is linked from the admin desk and kept on disk; attaching it is a
    # one-line change here once the winery decides it wants it in the mail.
    return mailer.send(message)


def send_release_notice(mailer: Mailer, member: sqlite3.Row, order: sqlite3.Row,
                        tier: Tier, when: str, reply_to: str) -> str:
    """Two weeks before a release is charged: what it will cost, and how to stop it.

    The club has promised this one in writing since the beginning — the
    welcome mail, the receipt and the offer page all say "we write two weeks
    before every release so you can change quantities or skip the season" —
    and until now nothing sent it. Without it the first a member hears of a
    charge is the charge.

    It is sent AFTER the orders are raised and BEFORE anything is charged,
    which is why it can give real figures rather than a reminder that a
    season is coming. The member sees their own wine, their own carriage for
    their own address, and the total that will leave their card.

    There is no self-service skip, so this does not pretend there is one: it
    asks them to reply, and the reply reaches a mailbox a person reads. For a
    club of this size that is not a workaround, it is the right size of
    machinery — and a member who replies gets a person, which is most of what
    they are paying for.
    """
    wine = f"${int(order['subtotal_cents']) / 100:,.2f}"
    total = f"${int(order['total_cents']) / 100:,.2f}"
    carriage = int(order["shipping_cents"])
    # Carriage is a line only when there is one to pay. On a box cheaper than
    # the tier's credit it comes to nothing, and "Shipping $0.00" invites a
    # question that has no interesting answer.
    freight = f"${carriage / 100:,.2f}" if carriage else "included"

    rows = [(f"Wine ({tier.bottles} bottles)", wine),
            ("Shipping", freight),
            ("Total", total)]
    width = max(len(label) for label, _ in rows)
    table = "\n".join(f"  {label.ljust(width)}   {value}" for label, value in rows)

    text = f"""Dear {member['first_name']},

Your {_plain(tier.name)} for {when} is ready, and we will charge your card in
about two weeks.

{table}

It goes to {member['ship_city']}, {member['ship_state']}.

If you would like more or fewer bottles this time, or would rather skip the
season altogether, reply to this message and we will sort it out. No reason
needed. If everything is right, there is nothing to do.

{SIGNOFF}"""

    html = _html(f"""
<p style="margin:0 0 14px">Dear {member['first_name']},</p>
<p style="margin:0 0 18px">Your <strong>{_plain(tier.name)}</strong> for {when} is ready,
and we will charge your card in about two weeks.</p>
<table role="presentation" style="font:13px/1.8 system-ui,sans-serif;color:#5c4650;
 border-top:1px solid #e2d8d0;border-bottom:1px solid #e2d8d0;padding:10px 0;width:100%">
<tr><td style="color:#9a8890">Wine ({tier.bottles} bottles)</td><td align="right">{wine}</td></tr>
<tr><td style="color:#9a8890">Shipping</td><td align="right">{freight}</td></tr>
<tr><td style="color:#9a8890">Total</td><td align="right"><strong>{total}</strong></td></tr>
</table>
<p style="margin:14px 0 18px;font-size:13px;color:#5c4650">It goes to
{member['ship_city']}, {member['ship_state']}.</p>
<p style="margin:0">If you would like more or fewer bottles this time, or would rather
skip the season altogether, <strong>reply to this message</strong> and we will sort it
out. No reason needed. If everything is right, there is nothing to do.</p>""")

    return mailer.send(Message(to=member["email"],
                               subject=f"Your {_plain(tier.name)} ships "
                                       f"{when} - {total}",
                               text=text, html=html, reply_to=reply_to))


def send_shipment_notice(mailer: Mailer, member: sqlite3.Row, order: sqlite3.Row,
                         carrier: str, tracking: str, reply_to: str) -> str:
    text = f"""Dear {member['first_name']},

Your {order['reference']} shipment left the cellar today.

  Carrier   {carrier}
  Tracking  {tracking}

Someone over 21 needs to sign for it. If the forecast turns we hold wine rather
than risk it, and we tell you when we do.

{SIGNOFF}"""

    html = _html(f"""
<p style="margin:0 0 14px">Dear {member['first_name']},</p>
<p style="margin:0 0 18px">Your <strong>{order['reference']}</strong> shipment left the
cellar today.</p>
<table role="presentation" style="font:13px/1.8 system-ui,sans-serif;color:#5c4650;
 border-top:1px solid #e2d8d0;border-bottom:1px solid #e2d8d0;width:100%">
<tr><td style="color:#9a8890">Carrier</td><td align="right">{carrier}</td></tr>
<tr><td style="color:#9a8890">Tracking</td><td align="right">{tracking}</td></tr>
</table>
<p style="margin:18px 0 0;font-size:13px;color:#5c4650">Someone over 21 needs to sign for it.
If the forecast turns we hold wine rather than risk it, and we tell you when we do.</p>""")

    return mailer.send(Message(to=member["email"],
                               subject=f"Your {order['reference']} shipment is on the way",
                               text=text, html=html, reply_to=reply_to))


def send_welcome(mailer: Mailer, member: sqlite3.Row, tier: Tier, next_release: str,
                 reply_to: str) -> str:
    """Sent when a member's card is saved. Nothing has been charged yet.

    It says so in the second line, because a card entered on a payment page is
    a card most people assume was just charged, and a bank statement with
    nothing on it a week later is a worry, not a relief.
    """
    when = f"in {next_release}" if next_release else "with the next release"
    text = f"""Dear {member['first_name']},

You are in. Your {tier.name} allocation is reserved.

Nothing has been charged. We take payment when a release goes out, not before
— your card is held safely with our payment provider until then, and your
first shipment goes {when}.

We write two weeks before every release so you can change quantities or skip
the season. Your wine travels in a pine crate on a bed of wood wool, with a
card from whoever made it.

{SIGNOFF}"""

    html = _html(f"""
<p style="margin:0 0 14px">Dear {member['first_name']},</p>
<p style="margin:0 0 18px">You are in. Your <strong>{tier.name}</strong> allocation is reserved.</p>
<p style="margin:0 0 18px"><strong>Nothing has been charged.</strong> We take payment when a
release goes out, not before — your card is held safely with our payment provider until
then, and your first shipment goes {when}.</p>
<p style="margin:18px 0 0;font-size:13px;color:#5c4650">We write two weeks before every
release so you can change quantities or skip the season. Your wine travels in a pine crate
on a bed of wood wool, with a card from whoever made it.</p>""")

    return mailer.send(Message(to=member["email"],
                               subject="Welcome to the cellar",
                               text=text, html=html, reply_to=reply_to))


def send_charge_failed(mailer: Mailer, member: sqlite3.Row, order: sqlite3.Row,
                       reason: str, card_url: str, reply_to: str) -> str:
    """The card on file was refused when the release was charged.

    No blame and no jargon: most declines are an expired card or a bank that
    wants to hear from its customer, and neither is the member's fault. The
    wine is held rather than lost, which is the fact that matters to them.
    """
    total = f"${int(order['total_cents']) / 100:,.2f}"
    text = f"""Dear {member['first_name']},

We came to charge this season's shipment and the card we have on file was
declined. It happens — usually an expired card, or a bank wanting to hear
from you first.

  Order   {order['reference']}
  Amount  {total}

Your allocation is held, not lost. Put in another card here and we will charge
it and send the wine out with the rest of the release:

  {card_url}

If you would rather we stopped, or you need a hand, just reply to this message.

{SIGNOFF}"""

    html = _html(f"""
<p style="margin:0 0 14px">Dear {member['first_name']},</p>
<p style="margin:0 0 18px">We came to charge this season's shipment and the card we have
on file was declined. It happens — usually an expired card, or a bank wanting to hear
from you first.</p>
<table role="presentation" style="font:13px/1.8 system-ui,sans-serif;color:#5c4650;
 border-top:1px solid #e2d8d0;border-bottom:1px solid #e2d8d0;padding:10px 0;width:100%">
<tr><td style="color:#9a8890">Order</td><td align="right">{order['reference']}</td></tr>
<tr><td style="color:#9a8890">Amount</td><td align="right"><strong>{total}</strong></td></tr>
</table>
<p style="margin:18px 0 18px"><strong>Your allocation is held, not lost.</strong></p>
<p style="margin:0 0 18px"><a href="{card_url}" style="background:#5c0037;color:#fff;
 text-decoration:none;padding:12px 24px;display:inline-block;font:600 12px/1 system-ui,
 sans-serif;letter-spacing:.14em;text-transform:uppercase">Put in another card</a></p>
<p style="margin:0;font-size:13px;color:#5c4650">We will charge it and send the wine out
with the rest of the release. If you would rather we stopped, just reply to this
message.</p>""")

    return mailer.send(Message(to=member["email"],
                               subject=f"Your card was declined — {order['reference']}",
                               text=text, html=html, reply_to=reply_to))


def send_application_received(mailer: Mailer, first_name: str, email: str,
                              waiting_for: str, reply_to: str) -> str:
    """Acknowledge the form, because silence is the worst answer to a request.

    Nothing sent this before. Somebody filled the form in, the browser said
    thank you, and then nothing arrived — no record in their inbox, no name
    to reply to, no way to tell a request that was received from one that was
    lost. For however long approval takes, that silence is the whole of their
    relationship with this winery.

    It promises no date. Approval is a person reading a request, and a
    promised "within 48 hours" that slips is worse than no promise at all.

    `waiting_for` is the state when the applicant is in one the winery cannot
    ship to: the form has already told them so on screen, and repeating it
    here is kinder than an acknowledgement that reads as though the request
    is going through.
    """
    if waiting_for:
        body = (f"""We cannot ship wine to {waiting_for} yet, so we have put your name
down instead of your request through. Direct wine shipping is licensed state
by state, and {waiting_for} is not one of ours this year.

The day that changes you will hear from us. If you have an address somewhere
we can already reach — an office, a second home, family you visit — reply to
this message and we can make you a member this week instead.""")
        subject = f"We have your name — {waiting_for}"
    else:
        body = ("""We have your request for an allocation, and it is in the queue.

Every one is read by hand, so this takes a few days rather than a few
minutes. You will hear back either way — we do not keep a list that people
never hear from again.""")
        subject = "We have your request"

    text = f"""Dear {first_name},

{body}

{SIGNOFF}"""

    paragraphs = "".join(
        f'<p style="margin:0 0 16px">{line}</p>'
        for line in body.replace("\n", " ").split("  ") if line.strip())
    html = _html(f'<p style="margin:0 0 14px">Dear {first_name},</p>{paragraphs}')

    return mailer.send(Message(to=email, subject=subject, text=text, html=html,
                               reply_to=reply_to))


def send_declines_alert(mailer: Mailer, failures: list[dict], to: str,
                        reply_to: str) -> str:
    """Tell the winery that cards were refused. Nobody was telling it.

    The member heard; the winery did not. A declined card is the one failure
    here that costs money and then goes quiet: the order sits unpaid in
    Orders, the wine is already out of the cellar, and unless somebody
    happens to look nothing says so. The member cannot be relied on to chase
    it — half the time it is an expired card they will replace next month, on
    their own schedule, for some other merchant.

    One message for a whole release rather than one per card. Twenty separate
    alerts on the morning of a despatch is a mailbox nobody reads by the
    eleventh.
    """
    lines, total = [], 0
    for f in failures:
        total += int(f.get("total_cents") or 0)
        money = f"${int(f.get('total_cents') or 0) / 100:,.2f}"
        lines.append(f"  {f.get('reference', '?')}  {f.get('name', '')} — {money}\n"
                     f"      {f.get('error', 'no reason given')}")
    count = len(failures)
    noun = "card was" if count == 1 else "cards were"
    held = f"${total / 100:,.2f}"

    text = f"""{count} {noun} refused when this release was charged, holding {held}.

""" + "\n".join(lines) + f"""

Each of them has been written to with a link to put in another card, so the
usual next step is to wait a day. What nobody else will do is notice if they
do not: these orders sit in Orders as failed, and the wine has already left
the cellar.

Retry a card from Orders once the member says they have replaced it.
"""  # no signature: the club does not sign letters to itself

    rows = "".join(
        f'<tr><td style="padding:6px 0;border-bottom:1px solid #e2d8d0">'
        f'<strong>{f.get("reference", "?")}</strong> {f.get("name", "")}'
        f'<div style="color:#9a8890;font-size:12px">{f.get("error", "")}</div></td>'
        f'<td align="right" style="padding:6px 0;border-bottom:1px solid #e2d8d0">'
        f'${int(f.get("total_cents") or 0) / 100:,.2f}</td></tr>'
        for f in failures)
    html = _html(f"""
<p style="margin:0 0 18px"><strong>{count} {noun} refused</strong> when this release was
charged, holding {held}.</p>
<table role="presentation" style="font:13px/1.6 system-ui,sans-serif;color:#2c1e24;width:100%">
{rows}</table>
<p style="margin:18px 0 0;font-size:13px;color:#5c4650">Each of them has been written to
with a link to put in another card, so the usual next step is to wait a day. What nobody
else will do is notice if they do not: these orders sit in Orders as failed, and the wine
has already left the cellar.</p>""", internal=True)

    return mailer.send(Message(to=to, subject=f"{count} {noun} refused - {held} unpaid",
                               text=text, html=html, reply_to=reply_to))
