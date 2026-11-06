# Getting the letter to the Deerfield list

A plan, in the order it happens. The letter is written
(`new-kid-on-the-block.html` and `.txt`), the waves are cut
(`tools/plan_invitations.py`), and what is left is getting it delivered.

Nothing in this service sends it. An earlier attempt to send it from
Microsoft High Volume Email has been removed: HVE lifts a sending limit and
supplies none of what a mailing needs — no suppression, no unsubscribe
header, no bounce handling, no numbers — so everything a platform does for a
subscription would have had to be built and kept working here. It is a
mailing platform instead.

---

## The fact the whole plan rests on

**Cecilia posted a printed announcement to everybody on this list.** They
have had a letter from this winery, through their door, with a stamp on it.

That single fact is the difference between a plan that works and one that
does not:

* **For the reader.** The email is a follow-up to something they already
  have, not a message out of nowhere. It is why Robert's draft opens
  "You've received an announcement by post from Cecilia Valdivia" — keep that
  sentence first, and keep it exactly.
* **For the platform.** Every provider asks how the addresses were obtained
  before it will let a list like this through. "Deerfield club members, and
  we have already written to all of them by post" is a real answer with a
  real paper trail. "We were given a list" is not.
* **For the complaint rate.** The 0.3% line that Gmail and Yahoo enforce is
  about people who do not recognise the sender. These people will.

It does **not** make them opted-in subscribers, and nothing below pretends it
does. It makes them people with whom the winery has a documented prior
relationship, which is the thing a provider's review is actually weighing.

---

## Step 1 — Ask before you pay, and ask in writing

**Do not open an account and upload the list.** Every major provider forbids
sending to a list acquired from a third party, and finds out at import:
Brevo calls acquired lists "strictly prohibited", Klaviyo and Mailchimp put
them outside their acceptable use policies, and AWS says purchased or rented
lists are not allowed on SES "under any circumstances". The failure is the
account suspended part-way through a wave, with the list inside it.

The distinction that gets a list approved is **business relationship versus
purchased data**. This list has the first. Say so, to their compliance or
support team, before money changes hands. Send this:

> We are a new winery in Glen Ellen, California, launching a wine club. We
> would be sending roughly 880 contacts about three times a year.
>
> I want to declare our list before opening an account, because I would
> rather be told no now than be suspended mid-send.
>
> The contacts are the wine club members of Deerfield Ranch Winery. They were
> not purchased from a data broker. Our consulting winemaker, Robert Rex, is
> the winemaker at Deerfield and is retiring from it, and the list came
> across with that relationship. **Every person on it has already received a
> printed letter from us by post announcing the winery**, and the email is
> the follow-up to it and says so in its first sentence.
>
> We have removed everyone who had previously asked Deerfield to stop
> writing. We will send in waves of 30 to 300, one a day, starting with the
> smallest, and stop if complaints appear. We send from our own subdomain
> with SPF, DKIM and DMARC.
>
> Two questions. Can we send to this list on your platform? And do you have
> any additional requirements for alcohol — we are happy to age-gate.
>
> I can provide the postal letter and the arrangement with Deerfield.

Ask **two** providers, not one, so a no does not stop the project:

| | Why it is on the list | Watch for |
| --- | --- | --- |
| **Mailchimp** | the most established with wineries; alcohol is accommodated with age-gating; priced per contact, which is cheap at 880 | per-contact pricing gets worse as the list grows |
| **Brevo** | priced per email sent, not per contact, which suits three sends a year | its anti-spam wording on "acquired" lists is the bluntest of the three, and it puts alcohol under "contact support first" |

Klaviyo is the standard in DTC wine and is worth it later, when there is a
member list to segment. It is overkill for one announcement.

**If both say no**, the fallback is Step 7 and it is not a bad outcome.

---

## Step 2 — The sending domain

Send from **`news.warmspringscellar.com`**, not the root domain, with its own
DKIM key. This has not changed and does not depend on the provider: if the
mailing goes badly, the address the invoices, the offers and the shipping
notices go out from is not dragged down with it.

1. Add the subdomain at the DNS host.
2. Publish the SPF, DKIM and DMARC records the provider gives you, and wait
   for its dashboard to show all three verified. Do not send before it does.
3. Set the reply address to **`club@warmspringscellar.com`**, the mailbox
   somebody actually reads. This letter will draw replies.

---

## Step 3 — Prepare the file

```
python3 tools/plan_invitations.py <the export.csv> ~/warm-springs-waves
```

That writes one CSV per wave, outside the repository, and holds back the rows
that must not be written to — 107 of 986 in the October export, with the
reason against each. `INVITATIONS.md` has the numbers and the reasoning.

Then, and this is the step that is easy to skip:

4. Download **`/admin/campaigns/suppressions.csv`** from the desk.
5. **Remove those addresses from every wave file** before uploading anything.

The platform cannot do this for you. It does not know who told Deerfield to
stop writing years ago, and it does not know about the person who telephoned
last week. Those people are on the export like everybody else.

---

## Step 4 — The letter, into the platform

Paste the HTML from `new-kid-on-the-block.html` into a "paste your own code"
campaign, and the text from `new-kid-on-the-block.txt` into the plain-text
part. **Send both parts** — an HTML-only bulk message is one of the cheapest
spam signals there is.

The templates use `{{name}}` placeholders. Replace each with the provider's
own merge tag:

| In the template | Mailchimp | Brevo | What it is |
| --- | --- | --- | --- |
| `{{first_name}}` | `*\|FNAME\|*` | `{{contact.FIRSTNAME}}` | greeting; set a default of "Wine Lover" for the blank ones |
| `{{email}}` | `*\|EMAIL\|*` | `{{contact.EMAIL}}` | named in the footer |
| `{{link}}` | `*\|LINK\|*` | `{{contact.LINK}}` | **upload the planner's `link` column as a custom field.** It carries the person's details into the form and records which wave they came from |
| `{{unsubscribe_url}}` | `*\|UNSUB\|*` | `{{ unsubscribe }}` | the provider's own, so it feeds the provider's suppression list |
| `{{postal_address}}` | `*\|LIST:ADDRESS\|*` | the account's address | CAN-SPAM; the provider usually injects its own footer too, which is fine |

Then **send one to yourself first**, at a personal Gmail, and in **Show
original** check `spf=pass`, `dkim=pass`, `dmarc=pass`, and that Gmail offers
its own Unsubscribe link beside the sender name. Only then send a wave.

---

## Step 5 — The waves

One a day, smallest and warmest first, **and only if the day before behaved.**
From the October export:

| Day | Who | How many |
| --- | --- | --- |
| 1 | Inner Circle | 33 |
| 2 | Pick-up members | 10 |
| 3 | Twelve-bottle members | 177 |
| 4 | Six-bottle members, part 1 | 300 |
| 5 | Six-bottle members, part 2 | 171 |
| 6 | Three-bottle members | 188 |

The order is by how close the person already is to a winery like this one,
which is also the order of who is least likely to press "spam" — and the
early complaint rate is what decides whether the later waves arrive at all.

**Stop and look between sends.** Bounces over about 2%, or any complaint at
all in a wave of thirty, and the next wave waits.

The 23 people in states the winery has no licence for get
`invitation-unlicensed.html`, never this letter. See `INVITATIONS.md`.

---

## Step 6 — After each wave

1. Export the platform's **unsubscribes and hard bounces**.
2. Paste them into **/admin/campaigns → "bring the platform's opt-outs
   across"**.

If nobody does this, those opt-outs exist only inside a subscription, and the
day the winery changes provider they are gone — while the obligation to
honour them is not.

Then count what the wave produced: **Applications**, filtered by referral.
Each wave's link carries its own `src`, and that is the only number that says
whether the letter worked. Opens tell you nothing useful and the club does
not measure them.

A good cold list to a warm audience converts at a few percent. Thirty members
off 879 letters would be a success, and roughly $9,000 a year at the
six-bottle price.

---

## Step 7 — The loop that makes all of this unnecessary next time

Everything above is awkward because the list belongs to somebody else. The
cure is to stop needing it, and Cecilia is already doing the expensive half.

**Put a short URL and a QR code on the next printed piece.** Anyone who comes
through it and fills in the form is a first-party, opted-in contact of Warm
Springs Cellar: no declaration, no provider review, no risk, permanently. The
form, the `src` tracking and the queue already exist.

That list starts at zero and is worth more within a year than the one this
plan is about. The Deerfield list is how it is seeded, once.

---

## Still not decided by anybody here

* **The right to use the list.** It was collected by one winery for that
  winery. Whether Warm Springs Cellar may write to these people is a question
  for whoever handed it over, in writing, and worth five minutes with
  somebody who knows California privacy law. "Lawful to send" and "ours to
  send to" are different questions, and only the first is settled by CAN-SPAM.
* **The winery's postal address**, for the footer. Required in the message.
* **The name.** The draft says "Warm Springs Cellars"; every other asset says
  the singular. The letter uses the singular so it does not disagree with its
  own header.
