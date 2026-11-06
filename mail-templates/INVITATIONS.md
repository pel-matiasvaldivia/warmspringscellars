# Writing to the Deerfield list

985 people who already belong to a wine club is the best list this winery will
ever be handed, and the fastest way to destroy it is to write to all of them on
a Tuesday afternoon.

This is what to do instead, and why each step is there.

## Before anything: two things that are not ours to decide

**The right to use the list.** It was collected by one winery, for that
winery. Whether Warm Springs Cellar may write to these people is a question for
whoever handed it over, in writing, and it is worth five minutes with somebody
who knows California privacy law — these are consumer records collected in
California, and CCPA treats passing them on as something that needs a basis.
US law does not require prior consent for a commercial email (CAN-SPAM does
require a real postal address and a working opt-out, which the template has),
but "lawful to send" and "ours to send to" are different questions.

**The sentence that says why they are hearing from us.** It is marked in
`invitation.html` and it is the only line in the letter the reader is really
weighing. What is written there now is what this website itself supports:
Robert Rex advises the winery, and one wine comes off Deerfield fruit. If the
true connection is more direct, say the direct thing — it is worth more than
every other sentence in the letter put together.

## Why not all at once

`warmspringscellar.com` has barely sent a message. Nine hundred messages in an
afternoon from a domain with no history, more than half of them to Gmail and
Yahoo, is indistinguishable from a domain bought for spamming, and the
receiving side treats it accordingly.

Since February 2024 Gmail and Yahoo both require a bulk sender to authenticate
with SPF, DKIM and DMARC, to offer one-click unsubscribe, and to keep spam
complaints **under 0.3%**. A cold list out of a cold domain is how a sender
discovers what the far side of that line looks like.

And the address that gets burned is the one the **invitations, the invoices and
the shipping notices** go out from. The club cannot pay that bill.

### So: a separate subdomain, and a ramp

1. **Send the campaign from a subdomain**, `news.warmspringscellar.com` or
   similar, with its own DKIM key. If the mailing goes badly, the club's own
   mail from the root domain is not dragged down with it. This is the single
   most important line in this file.
2. **Not from the club mailbox.** That mailbox is for letters between people.
   A mailing needs list-unsubscribe headers, a suppression list that actually
   suppresses, bounce handling and per-wave numbers, and ordinary Microsoft
   365 submission gives none of the four — the tenant's sending limits are a
   throttle, not a feature.

   **What was decided, November 2026:** a mailing platform, which supplies
   all four. Microsoft High Volume Email was considered and dropped: it lifts
   the throttle and supplies none of them, so everything a platform does for
   a subscription would have had to be built and maintained here. Which
   provider, and what has to be declared to them before a single address is
   uploaded, is in **`CAMPAIGN.md`**.

   One thing stays in the club: the winery's own do-not-contact record, at
   **/admin/campaigns**. It is subtracted from a wave before the wave is
   uploaded, and the platform's own opt-outs are brought back into it after
   each one — because an opt-out has to keep applying after the subscription
   moves.
3. **Authenticate before the first send**: SPF, DKIM and DMARC on the
   subdomain, all three passing. Send one message to a seed address and read
   the headers.
4. **Then ramp**, which is what `tools/plan_invitations.py` lays out.

## The waves

```
python3 tools/plan_invitations.py <the export.csv> ~/warm-springs-waves
```

It writes one CSV per send, outside the repository — it refuses to write
inside it, because the export carries names, home addresses, dates of birth and
telephone numbers. **Nothing from it is ever committed.**

From the October 2026 export, 986 rows:

| Day | Who | How many |
| --- | --- | --- |
| 1 | Inner Circle | 33 |
| 2 | Pick-up members | 10 |
| 3 | Twelve-bottle members | 177 |
| 4 | Six-bottle members, part 1 | 300 |
| 5 | Six-bottle members, part 2 | 171 |
| 6 | Three-bottle members | 188 |

One a day, **and only if the day before behaved**. The order is by how close
the person already is to a winery like this one, which is also the order of who
is least likely to press "spam" — and the early complaint rate is what decides
whether the later waves arrive at all. The first two waves are small enough to
be a test and valuable enough to be worth sending first either way.

Stop and look between sends. Bounces over about 2%, or any complaints at all in
a wave of thirty, mean the next wave waits.

**107 rows are held back**, in `held-back.csv`, with the reason against each:

| | |
| --- | --- |
| 81 | no state in the export, so nobody can say whether we may ship to them |
| 23 | in Alabama, Kentucky, Arkansas, Delaware or Mississippi — states the winery has no licence for |
| 2 | a role address (`info@`, `sales@`), not a person |
| 1 | a test row |

**The 81 without a state turned out not to be incomplete rows at all.**
Looked at by club, 74 of them belong to a *Pick Up* club: they collected their
wine at the winery, so the old system never had a shipping state to record.
Nothing is missing. Only the remaining 7 are genuinely partial rows.

That changes what they are owed and what they are being offered. Warm Springs
Cellar has no pick-up allocation — all three tiers ship, three times a year —
so these 74 are being asked to take up something materially different from
what they had. Somebody should decide whether that deserves a sentence of its
own in their wave, because they are the one group for whom the offer is not
simply "the same club, new winery".

They are written to all the same. The form asks every visitor for their state
and decides from the answer, so a state nobody recorded in 2019 is not a
reason to leave somebody out: the person settles it themselves in the one
place where the answer is current.

## The 23 in states we have no licence for

They come out in their own file, `letter-unlicensed-states.csv` — Alabama 8,
Kentucky 6, Arkansas 4, Delaware 4, Mississippi 1 — and they get
`invitation-unlicensed.html`, never the ordinary one.

Sending somebody a letter asking them to join something they are not allowed
to have, and letting the form tell them so after they have typed their name
into it, is worse than not writing at all. So that letter says it in the first
line: we cannot ship to your state. Then it offers the two things that are
actually true.

**An address somewhere we can reach.** An office, a second home, family they
visit. What governs the permit is where the box is delivered, not where the
member lives, so this is an ordinary delivery and not a way around anything —
and it is the one route that turns a reply into a member *today*. No link
prefills an address any more, theirs included, so there is nothing to undo:
the question is simply open, which for these 23 is the whole point.

**Or: tell us to write when it opens.** No date is promised, because none can
be. It depends on a licence the winery does not control.

### What happens when one of them replies

Set **Reply-To: wineclub@warmspringscellar.com** on the send, or the replies go
to the sending platform's own address and nobody ever reads them. That is the
trap in this letter: it is the only one of the three that asks for an answer
rather than a click.

With it set, the reply lands in the club mailbox, the automatic answer goes
out and Cecilia gets the notice. From there it is one entry on the desk:
**Waiting → "Add somebody by hand"**, with their state. That screen only
offers the states the winery has no permit for, which is the mirror of "Add an
application by hand" offering only the states it does.

They are not applications. An application is a request for an allocation and
gets an answer; this cannot be answered until a licence arrives that nobody
here controls, and a pending row that can never be decided is a lie in the
queue. So the waiting list is its own list, grouped by state, because a licence
arrives for a state and not for a person — that is the unit somebody acts on.

### The day a state opens

1. Take it out of `UNLICENSED_STATES` in `api/app/config.py`, and out of the
   `UNLICENSED` list in `index.html` — both, or the form and the server
   disagree about who may join.
2. Open **Waiting**. The people for that state now carry **"we can ship
   here"**, and a green line at the top of the screen names it, because a
   state can be licensed for weeks before anybody notices there are names
   under it. **"Into the queue"** turns each row into an ordinary application.
3. Approve them in **Applications** as usual. That is the step that writes to
   them, and they were promised a letter.
4. Re-run the planner over the export if it is still in use: those people stop
   being held back and fall into an ordinary wave.

## Where the link goes

Every row carries a personalised link to the form on the site:

```
https://warmspringscellar.com/?src=deerfield-1-inner-circle&f=Anita&l=Vogel
  &e=anita%40example.com#join
```

Name, address, wave. **Not the city and not the state**, although the export
has both. The form fills in the tedious part, the person types where the wine
should go, and presses one button. Three things come from doing it this way
rather than mailing people a membership they never asked for:

* **They ask.** The request is theirs, which is a different thing in law and a
  different thing in the morning.
* **The address is current.** The one in the export may be years old, and the
  wine is going to a door. This is why the link stopped carrying it: a
  prefilled city somebody moved out of in 2019 does not save them typing, it
  invites them to press the button without reading the field — and then the
  box goes to the old address. The form still says straight away whether we
  can ship to a state; it says it when they choose one, which is also when
  they have told us the truth rather than confirmed something stale.
* **The platform never sees it.** The link travels through a mailing
  platform, which keeps whatever the URL contains. A name and an email are
  the ordinary content of a marketing list. Where somebody lives is not.
* **It is one queue.** They land in **Applications** beside everybody else,
  and get approved the same way. Nothing special, nothing to remember.

`src` is written into the referral column, so the desk can count what each wave
actually produced. Filter Applications by referral and the answer is there —
and that is the only way the second mailing is smarter than the first.

## Expect

A good cold list to a warm audience converts at a few percent. Thirty members
off 879 letters would be a success, and would be roughly $9,000 a year at the
six-bottle price. Treat anything above that as a surprise and anything below it
as information about the letter rather than about the list.
