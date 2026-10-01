"""The four emails the club sends, and nothing else.

Kept apart from the routes so the wording is in one place and can be read
without reading HTTP handling. Every message goes out as text and HTML: the
text part is the one that survives spam filters and screen readers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .catalogue import Tier
from .mail import Mailer, Message

SIGNOFF = "Robert Rex & Cecilia Valdivia\nWarm Springs Cellars, Kenwood, Sonoma Valley"


def _html(body: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#faf8f5;padding:28px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="100%" style="max-width:520px;background:#fff;
 border:1px solid #e2d8d0;padding:32px 28px;font:15px/1.65 Georgia,serif;color:#2c1e24">
<tr><td>
<p style="font:600 11px/1 system-ui,sans-serif;letter-spacing:.22em;text-transform:uppercase;
 color:#722f3a;margin:0 0 22px">Warm Springs Cellars</p>
{body}
<p style="margin:26px 0 0;font-size:13px;color:#5c4650">Robert Rex &amp; Cecilia Valdivia<br>
<span style="color:#9a8890">Kenwood, Sonoma Valley</span></p>
</td></tr></table>
<p style="font:11px/1.5 system-ui,sans-serif;color:#9a8890;max-width:520px;margin:14px auto 0">
You are receiving this because you asked for an allocation at warmspringscellars.com.
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
<p style="margin:0 0 22px"><a href="{url}" style="display:inline-block;background:#722f3a;
 color:#fff;text-decoration:none;padding:13px 28px;font:600 11px/1 system-ui,sans-serif;
 letter-spacing:.2em;text-transform:uppercase">Choose your allocation</a></p>
<p style="margin:0 0 10px;font-size:13px;color:#5c4650">The invitation is open until
{expires_on}. Nothing is charged until you pick, and you can skip a season whenever
you need to.</p>
<p style="margin:0;font-size:12px;color:#9a8890;word-break:break-all">{url}</p>""")

    return mailer.send(Message(to=app["email"], subject="Your allocation at Warm Springs Cellars",
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

    return mailer.send(Message(to=app["email"], subject="Your request at Warm Springs Cellars",
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
