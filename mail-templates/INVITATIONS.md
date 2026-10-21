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
2. **Send it from a real email platform**, not from Microsoft 365. The club
   mailbox is for letters between people. A platform gives list-unsubscribe
   headers, a suppression list that actually suppresses, bounce handling and
   per-wave numbers — and M365 was never meant for this; the tenant's sending
   limits are a throttle, not a feature.
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

The 81 without a state are worth more than the other 106 together: they are
ordinary members whose export row is simply incomplete. If their state can be
recovered — from the old club system, from an invoice, from a telephone call —
they join a later wave. What they must not get is a letter inviting them to
join something that may not be able to ship to them.

Nobody writes to the 23 in unlicensed states with this letter. If they are
written to at all it is a different letter, saying plainly that we cannot ship
there yet and asking whether they would like to hear when that changes.

## Where the link goes

Every row carries a personalised link to the form on the site:

```
https://warmspringscellar.com/?src=deerfield-1-inner-circle&f=Anita&l=Vogel
  &e=anita%40example.com&c=Los%20Angeles&st=California#join
```

The form fills itself in from it, so the person presses one button and chooses
an allocation. Three things come from doing it this way rather than mailing
people a membership they never asked for:

* **They ask.** The request is theirs, which is a different thing in law and a
  different thing in the morning.
* **The address is current.** The one in the export may be years old, and the
  wine is going to a door.
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
