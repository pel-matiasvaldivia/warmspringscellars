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


def _html(body: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#faf8f5;padding:28px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="100%" style="max-width:520px;background:#fff;
 border:1px solid #e2d8d0;padding:32px 28px;font:15px/1.65 Georgia,serif;color:#2c1e24">
<tr><td>
<p style="font:600 11px/1 system-ui,sans-serif;letter-spacing:.22em;text-transform:uppercase;
 color:#5c0037;margin:0 0 22px">Warm Springs Cellar</p>
{body}
<p style="margin:26px 0 0;font-size:13px;color:#5c4650">Cecilia Valdivia, Owner &amp; Winemaker<br>
<span style="color:#9a8890">Glen Ellen, Sonoma Valley</span></p>
</td></tr></table>
<p style="font:11px/1.5 system-ui,sans-serif;color:#9a8890;max-width:520px;margin:14px auto 0">
You are receiving this because you asked for an allocation at warmspringscellar.com.
You must be 21 or older to purchase. Please drink responsibly.</p>
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
