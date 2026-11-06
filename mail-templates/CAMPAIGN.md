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

## Step 3 — Prepare the files

One CSV per wave, three columns, and the headers Brevo maps without being
asked:

```
EMAIL,FIRSTNAME,LASTNAME
```

Three columns and no more, because a wave file goes to a platform that keeps
every column for good. Not a date of birth, a home address or a telephone
number — the source export carries 953, 904 and 929 of those — and not the
old club's name either, which would tell the platform exactly whose list this
is when that is the winery's own disclosure to make, in its own words, in
Step 1.

**983 people, in six waves.** Two role addresses (`info@`, `office@`) are the
only rows left out: they are not people and nobody reads them.

Then the step that is easy to skip:

- Download **`/admin/campaigns/suppressions.csv`** from the desk.
- **Remove those addresses from the wave file** before uploading it.

Brevo cannot do this for you. It does not know who asked the previous winery
to stop writing years ago, and it does not know about the person who
telephoned last week. The list is empty before the first wave and it will not
be empty before the second.

---

## Step 4 — The letter, into Brevo

Do not hand-edit the HTML. Render it:

```
python3 tools/render_for_platform.py new-kid-on-the-block brevo \
    --src wave-1-inner-circle -o ~/WSC-wave-1.html
```

That swaps the letter's neutral `{{field}}` placeholders for Brevo's own
tags, fills in the postal address from `config.POSTAL_ADDRESS`, strips the
notes to whoever edits the template — they are HTML comments, so they would
not render, but they would travel inside the source of every message — and
refuses to write a file with a field it does not know how to fill.

Paste the result into a **"paste your own code"** campaign. One campaign per
wave; `--src` is the only thing that differs between them, and it is what
tells you afterwards which wave produced which member.

Two things to check in the preview, both of which have bitten real senders:

* **Two unsubscribe footers.** Brevo injects its own. If both appear, delete
  the one in the pasted HTML — Brevo's is the one wired to its suppression
  list.
* **What the first-name attribute is really called.** It is `FIRSTNAME` in an
  English account and may be `NOMBRE` in one created in Spanish: Contacts →
  Settings → Contact attributes. Get it wrong and 983 people are greeted as
  "Wine Lover".

Set **Reply-To: club@warmspringscellar.com** on every campaign, or the
replies land in Brevo and nobody reads them.

Then **send one to yourself first**, at a personal Gmail, and in **Show
original** check `spf=pass`, `dkim=pass`, `dmarc=pass`, and that Gmail offers
its own Unsubscribe link beside the sender name. Only then send a wave.

---

## Step 5 — The waves

One a day, closest first, **and only if the day before behaved.**

| Day | Who | How many |
| --- | --- | --- |
| 1 | Inner Circle | 35 |
| 2 | Pick-up members | 84 |
| 3 | Twelve-bottle members | 184 |
| 4 | Six-bottle members, part 1 | 300 |
| 5 | Six-bottle members, part 2 | 185 |
| 6 | Three-bottle members | 195 |

The order is by how close the person already is to a winery like this one,
which is also the order of who is least likely to press "spam" — and the
early complaint rate decides whether the later waves arrive at all. Gmail and
Yahoo are 62% of this list and both junk a bulk sender above 0.3%.

**Stop and look between sends.** Bounces over about 2%, or any complaint at
all in a wave of thirty, and the next wave waits.

### Everybody gets the same letter

Including the 23 in Alabama, Arkansas, Delaware, Kentucky and Mississippi,
where the winery has no permit yet. That is safe, and it is worth knowing
why rather than taking it on trust: the moment one of them picks their state
on the form, it says *"We cannot ship wine to Alabama yet — leave us your
name and we will write the day that changes"*, the button becomes **Tell Me
When You Can Ship**, it stops asking which allocation they want, and the
submission goes to `/api/waiting` instead of `/api/applications`.

So they land in **Waiting**, grouped by state, which is exactly the number
the winery needs to decide whether a state's permit is worth applying for.
Nobody pays for wine that cannot be shipped to them. `invitation-unlicensed.html`
is not used.

### Wave 2 is not what it looks like

74 of its 84 are pick-up members: they collected their wine at the winery, so
the old system never recorded a shipping state for them. **There is no
pick-up allocation any more** — all three tiers ship — so these people are
being offered something materially different from what they had, carriage
included. They are the one group for whom this is not "the same club, new
winery", and the letter does not say so. Somebody should decide whether that
deserves a sentence of its own in that one campaign.

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
* **The name.** The draft says "Warm Springs Cellars"; every other asset says
  the singular. The letter uses the singular so it does not disagree with its
  own header.
* **Whether wave 2 says anything about pick-up**, per Step 5.

Settled since this was written: the postal address is 1310 Warm Springs Rd,
Glen Ellen, CA 95442, and it lives in `config.POSTAL_ADDRESS`. And the
shipping arrangement — the winery holds each state's permit and gives Crown a
copy, so Crown ships but does not sell. That keeps Warm Springs Cellar the
seller of record, which is what Stripe and QuickBooks already assume.
