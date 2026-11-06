# Sending the campaign through Microsoft High Volume Email

The decision is to send the first mailing through Microsoft's **High Volume
Email (HVE)** rather than through a mailing platform. This file is what that
means in practice: what HVE does, what it does not, what the club had to build
to cover the difference, and the order the first send happens in.

## Why not Brevo, Mailchimp, Klaviyo, SendGrid or SES

Because of the list, not the tool. The Deerfield list was collected by one
winery and handed to another, which makes it a third-party list, and **every
one of those platforms forbids sending to one**: Brevo calls acquired lists
"strictly prohibited", Klaviyo and Mailchimp put them outside their acceptable
use policies, Twilio's policy covers SendGrid, and AWS says purchased or
rented lists are "not allowed on Amazon SES under any circumstances".

The failure mode is not a warning. It is the account suspended part-way
through a wave, with the list inside it — Brevo has a help article on what to
do with your contacts afterwards, which is how often it happens.

Two routes were considered, November 2026:

* **Deerfield sends it.** Their own list, from their own account, with a link
  here. No third-party policy to breach, and it arrives from somebody the
  reader knows. This was the better route and was **not taken**.
* **The winery sends it through HVE.** Taken. HVE is the tenant's own
  infrastructure, so there is no third-party acceptable-use policy to breach
  — the risk moves from "the account is suspended" to "the domain's
  reputation is spent", which is what the subdomain, the ramp and the
  suppression list below exist to contain.

Once somebody has joined through the form they are a first-party, opted-in
contact and an ordinary platform is fine for writing to them. That list starts
at zero on the day the first person joins; it is not this one.

## The one thing to settle before anything else

**Confirm that your HVE account can send to recipients outside the tenant, and
what the daily ceiling is.** This is not a detail. Microsoft describes HVE as
primarily for *internal* mass communication, its public-preview announcement
put external recipients at **2,000 a day**, and third-party write-ups flatly
contradict each other on whether external sending works at all — some report a
per-account external limit, others report none. The product has also moved
since those were written.

Every address on the Deerfield list is external. If external sending is off,
or capped below a wave, this route does not work and no amount of the code
below changes that.

So before the letter, before the subdomain, before anything:

```
python3 tools/send_campaign.py <any wave.csv> --verify        # can we log in
python3 tools/send_campaign.py <any wave.csv> --seed you@gmail.com --send
```

The seed goes to an address **outside the tenant**, on a consumer provider.
If that arrives, external sending works for this account. If it is refused,
read the refusal: it will name the limit. Then ask Microsoft support what the
current per-account and per-tenant external ceilings are, in writing, and put
the answer at the bottom of this file.

## What HVE is

An authenticated SMTP relay with a raised sending limit. That is the whole
product, and it is worth being precise about it, because the gap between "a
relay" and "a mailing platform" is four jobs that somebody has to do:

| | A mailing platform | HVE | So the club |
| --- | --- | --- | --- |
| Suppression list that actually suppresses | yes | no | `email_suppression`, checked before every message |
| One-click unsubscribe (RFC 8058) | yes | no | built per message by `tools/send_campaign.py` |
| Bounce handling | yes | no | envelope sender → `HVE_BOUNCE_TO`, read by hand |
| Per-wave numbers | yes | no | `campaign_send`, shown at `/admin/campaigns` |
| Opens and clicks | yes | no | **not replaced.** Count conversions by `src` instead |

The first three are not conveniences. Without a working opt-out the mailing is
illegal under CAN-SPAM; without one-click unsubscribe Gmail and Yahoo junk a
bulk sender outright. They are built, they are tested, and the sender refuses
to run if any of them is missing. Opens and clicks are simply gone, and the
screen says so rather than guessing: what a wave produced is counted in
**Applications**, filtered by referral, because every wave's link carries its
own `src`.

Microsoft's own guidance, for what it is worth, points at Azure Communication
Services or a third-party provider for external bulk commercial mail.
`INVITATIONS.md` reached the same conclusion from the other direction before
HVE was on the table. HVE answers exactly one of the objections there — the
tenant's sending limit — and none of the other four. That is the trade being
made, with the four gaps filled in code instead.

## Setting it up

### 1. A subdomain, and its own DKIM key

**This is still the most important line in `INVITATIONS.md` and HVE does not
change it.** Send the campaign from `news.warmspringscellar.com`, not from the
root domain. If the mailing goes badly — and a cold list from a cold domain is
how a sender finds out where the 0.3% complaint line is — the address the
invoices, the offers and the shipping notices go out from is not dragged down
with it.

1. Add `news.warmspringscellar.com` as an **accepted domain** in the Exchange
   admin centre. HVE will not send as a domain the tenant does not own.
2. Publish SPF, DKIM and DMARC for the subdomain, and let DKIM finish signing
   before the first send.
3. Set `HVE_FROM=club@news.warmspringscellar.com` and leave `MAIL_REPLY_TO`
   pointing at `club@warmspringscellar.com`, so replies land in the mailbox
   somebody actually reads.

### 2. An HVE account

In the **Exchange admin centre → Mail flow → High volume email**, add an
account. It is not a mailbox: it has no inbox, it cannot receive, and it
authenticates with a username and a password. That is why this is the one
place in the club that still does basic authentication — an HVE account has no
OAuth identity to use instead.

Give it its own password, not a person's.

### 3. `.env`

```
HVE_USER=hve-club@news.warmspringscellar.com
HVE_PASSWORD=…
HVE_FROM=club@news.warmspringscellar.com
HVE_BOUNCE_TO=bounces@news.warmspringscellar.com
CAMPAIGN_POSTAL_ADDRESS=1234 Warm Springs Road, Glen Ellen, CA 95442
```

`HVE_HOST` and `HVE_PORT` default to `smtp-hve.office365.com:587` and should
not need setting. That host is deliberately **not** `smtp.office365.com`:
that one is ordinary mailbox submission and will refuse an HVE account with a
message about the credentials rather than about the host.

`SECRET_KEY` must be the same value the service runs with. The sender signs
every unsubscribe link and the service verifies them; two different keys means
every opt-out link in every letter is rejected. The sender refuses to run if
`SECRET_KEY` is unset at all.

`CAMPAIGN_POSTAL_ADDRESS` must be the winery's real street address. CAN-SPAM
requires one in the message and the footer has a slot waiting for it. The
sender refuses to run without it.

## Sending

The waves come from `tools/plan_invitations.py` — see `INVITATIONS.md` for why
they are cut the way they are, and why one a day.

```
# 1. Can we log in at all?
python3 tools/send_campaign.py ~/waves/wave-1-inner-circle.csv --verify

# 2. One copy to yourself, at an address outside the tenant.
python3 tools/send_campaign.py ~/waves/wave-1-inner-circle.csv \
    --seed you@gmail.com --send

# 3. A dry run of the real wave. Renders every message, sends nothing.
python3 tools/send_campaign.py ~/waves/wave-1-inner-circle.csv

# 4. The wave.
python3 tools/send_campaign.py ~/waves/wave-1-inner-circle.csv --send
```

**Nothing is sent without `--send`.** Step 3 is not a formality: it renders
every message in the wave, not a sample, because a merge field that is only
missing on row 212 is exactly the one a sample of three misses.

### Read the seed before the wave

Open step 2 in Gmail, **Show original**, and check four things:

* `spf=pass`, `dkim=pass`, `dmarc=pass` — all three, on the subdomain.
* `List-Unsubscribe` is a plain `<https://…>, <mailto:…>`. If it looks like
  `=?utf-8?q?=3Chttps…?=` something has regressed: that is an encoded word,
  no receiver parses it, and the one-click requirement would be silently
  absent from the whole wave. There is a test for this.
* Gmail shows its own **Unsubscribe** link beside the sender's name. That is
  Gmail saying it read and accepted the header pair.
* Press that link, then check the address appears at `/admin/campaigns`.

Only then send a wave.

### Between waves

One wave a day, and only if the day before behaved. Stop if bounces go over
about 2%, or if a wave of thirty produces any complaint at all.

Watch `/admin/campaigns` for accepted and refused counts, and the
`HVE_BOUNCE_TO` mailbox for the asynchronous bounces, which arrive hours
later and are the ones that matter. A dead address belongs on the suppression
list: add it at `/admin/campaigns`, or it goes back into the next wave.

### If a send dies half way through

Run the same command again. Every accepted message is recorded as it goes out,
one at a time, and a second run writes only to the addresses it has no
accepted row for. A **refused** message is not skipped — a refusal is somebody
to try again, not somebody who was written to.

Ctrl-C is safe and says so.

## Away from the server

The sender reads the suppression list from the club database. On a laptop that
cannot see it, download `/admin/campaigns/suppressions.csv` and pass
`--suppressions`. That works for a dry run.

It will **not** send that way, on purpose: with no database nothing records
what went out, so the next run would write to the whole wave a second time.
A real send happens where the database is readable.

## Still open

* **The external-recipient ceiling**, above. Nothing else matters until it is
  answered.
* **The right to use the Deerfield list**, and the sentence in the letter that
  says why these people are hearing from us — both unchanged from
  `INVITATIONS.md`, both somebody else's decision.
* **The name.** The draft says "Warm Springs Cellars"; every other asset says
  "Warm Springs Cellar". The letter uses the singular so it does not disagree
  with its own header.
* **Complaint feedback.** Nobody is reporting spam complaints back to the club
  — HVE does not, and the Google Postmaster Tools figures for the subdomain are
  the nearest thing available. Worth registering the subdomain there before
  the first wave; it is the only view of the 0.3% line there is.
