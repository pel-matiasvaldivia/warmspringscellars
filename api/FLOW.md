# The commercial flow

From a request on the landing page to a crate on a doorstep. Every step is a
row in SQLite and an entry in the event log, so any order can be reconstructed
from what actually happened rather than from the current state of a record.

```
  landing page form
         │
         ▼
   APPLICATION ──────── declined ──▶ (notified, ends)
    (pending)
         │ approved at the cellar desk in /admin
         ▼
      OFFER ─────────── expired ───▶ (ends; can be re-sent)
   (signed link,
    14-day life)
         │ member opens the link, picks a tier
         ▼
     CHECKOUT ───────── abandoned ─▶ (offer stays live until it expires)
   (payment provider)
         │ provider confirms payment (webhook)
         ▼
    MEMBERSHIP ◀────────────────────┐
     (active)                       │ each release cycle, for the members
         │                          │ the RELEASE PLAN says there is wine for
         ├──▶ ORDER (service order) ┘
         │      │ paid
         │      ├──▶ INVOICE   (sequential, immutable, PDF)
         │      │      └──▶ LEDGER QUEUE ──▶ the books (invoice, then payment)
         │      └──▶ SHIPMENT  (picked → in transit → delivered)
         │
         ├──▶ paused (member skips a season)
         └──▶ cancelled
```

## Why it is shaped this way

**The application is not the membership.** A wine club with a thousand cases
cannot sell to everyone who asks, and the winery wants to read every request
before it becomes a customer. So the form creates an application, and nothing
is charged until a human approves it and the member chooses a tier themselves.

**The offer is a signed link, not a login.** A member who has never bought
anything has no account to log into. The approval email carries a token signed
with the service secret, carrying the application id and an expiry; the offer
page validates the signature on every request. No password, nothing to reset,
and the link cannot be guessed or edited.

**Cards live at the provider, never here.** The service sends the member to a
hosted page that saves a card and charges nothing. Card details never touch
this code or this database, which keeps the whole thing out of PCI scope.
Membership is only created when the provider's webhook confirms the card is
held — not when the member returns from that page, because a browser redirect
proves nothing.

**Money moves per release, not per signup.** A subscription bills on its own
cycle; a release happens when the wine is ready, and the two were never going
to line up — three shipments a year fall six, two and four months apart. More
to the point, at the moment somebody joins nobody knows whether there is wine
for them, and taking the money first is how a club ends up owing bottles it
does not have. So a release raises an order per active membership, and the
desk charges those orders against the saved cards in a second, deliberate
press.

**A release is composed before it is raised.** For each tier, which wines and
how many bottles of each — and from that, the bottles on hand and the members
who are owed a shipment, `stock.py` works out who can be filled. Only those
memberships get an order, and the bottles leave stock in the same commit. The
club can still be short of wine; what it cannot be is short of wine *and*
holding the money for it.

Members are filled **in the order they joined**, and that is the whole policy.
It is the one rule a winery can say out loud to the member who misses out.
Seniority is per bottle rather than one queue: somebody who joined later is
still filled if their tier's wines held out, because holding their shipment
back would not put a bottle in the earlier member's hands.

**Carriage is priced per destination, not written into the catalogue.** Every
tier bills shipping as its own line, and what the tier carries is a *credit* —
nothing on the three-bottle case, $20 on the six, $40 on the twelve. The member
pays the carrier's price for their own address less that credit, so two members
on one tier pay different shipping and the winery gives each of them the same
amount. A credit larger than the carriage ships the box free; it never refunds.

`shipping.py` is Crown's 2026 UPS Ground sheet transcribed, and it answers both
questions: what a box costs, and what to bill for it. The release plan prices
itself against the same table before anybody presses Charge, and it is an
estimate rather than a quotation — it leaves out the dollar-a-pound overweight
charge and every fee that only happens when something goes wrong.

Ice packs are the winery's, never a member's, and whether a release carries
them is a flag on the release. It starts set from the shipping month and the
desk changes it, because whether a box needs one is a decision about the
forecast at the far end that no calendar knows months ahead. Switching it
moves money on the winery's side only: the member's bill is the carriage less
their tier's credit either way. A destination the sheet cannot price falls back to the
dearest zone, so a box the carrier would not quote is never the one the winery
subsidises by accident.

**Invoices are immutable.** Once issued an invoice is never edited — a
correction is a credit note plus a new invoice. The number comes from a
sequence that no code path can reuse, because an accountant will one day ask
why 1043 is missing.

## The states

| Entity | States |
| --- | --- |
| `application` | `pending` → `approved` \| `declined` \| `withdrawn` |
| `offer` | `sent` → `opened` → `accepted` \| `expired` \| `revoked` |
| `membership` | `active` ⇄ `paused` → `cancelled` |
| `order` | `pending_payment` → `paid` → `fulfilled` \| `refunded` \| `failed` |
| `invoice` | `issued` → `credited` |
| `shipment` | `ready` → `picked` → `in_transit` → `delivered` \| `held` \| `returned` |

Transitions that are not in this table are refused by `flow.py`, not by
convention. `held` exists because wine is not shipped into a heat wave; it
returns to `ready` when the forecast allows.

**The books are downstream, and never in the way.** A member who has paid is a
member whether or not the accounting system answered. So issuing an invoice
does not call it — it writes a row to `ledger_task` inside the same transaction that
writes the invoice, and the push happens afterwards, from the queue. Three
things follow from that split, and each is the reason for it:

- An order that committed always has its paperwork queued, and an order that
  rolled back never does. The two cannot disagree, because they are one commit.
- A push that fails stays visible. The queue at `/admin/accounting` is the
  standing answer to "what do the books not know about yet?", which is better
  asked in March than discovered in January.
- Retrying is free. Every push is keyed on something QuickBooks can recognise —
  the invoice number, as its `DocNumber` — so a second attempt adopts the
  document the first one created instead of writing a second. The adapter also
  sends Intuit's own `Request-Id`, a second guard on the same risk.

Payments queue behind their invoice: a payment links to an invoice id that only
exists once the invoice has landed, so the queue does them in that order and
leaves a payment pending rather than failed if its invoice has not gone yet.

## What is wired and what is not

Everything above runs end to end against the fake payment provider and the file
outbox, which is what the tests exercise. Three adapters need real credentials
before they do anything outside this machine:

- **Payments.** `payments/stripe_provider.py` speaks Stripe Checkout in setup
  mode, and charges saved cards off-session when a release goes out. It needs
  `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`.
  Until those are set the service uses `payments/fake.py`, which moves orders
  through the same states without a network call, so the rest of the flow can
  be developed and demonstrated.
- **The books.** `accounting/quickbooks.py` speaks Intuit's Accounting API,
  behind a three-method port that nothing above it looks through. It needs
  `QBO_CLIENT_ID` and `QBO_CLIENT_SECRET`, and then a human to authorise the
  company once from `/admin/accounting`. Until that happens,
  `NullLedger` refuses every push and the work stays in `ledger_task`, where
  it is visible and exportable. There is no simulated accounting system in
  production: a club that cannot reach its books should accumulate a visible
  queue, not a pile of invoices it believes were filed.
- **Email.** `mail/smtp.py` needs `SMTP_HOST` and friends. Without them the
  service writes each message to `var/outbox/` as a `.eml` file you can open in
  any mail client — useful in development, and the only reason the approval
  link is visible in the admin page. It authenticates two ways: a password, or
  an OAuth2 token by the client-credentials flow (`mail/oauth.py`). The second
  exists because Microsoft has been retiring basic authentication for SMTP
  submission, and on the day it goes in a tenant a username and a password
  stop working with nothing to fall back on. Which one is used follows from
  the configuration, so switching is a change to `.env` and a restart.

None of these has been exercised against the live services from here: this
container has no egress to them. The shapes come from the providers' documented
APIs; the Stripe signature verification is tested against vectors computed
locally, and the QuickBooks adapter against a stub transport that proves its
own logic — token rotation, matching a customer by email, adopting a duplicate,
refreshing once on a 401 — and proves nothing about Intuit's field validation.

Two details of Intuit's OAuth are worth knowing before the first connection,
and they are why tokens live in the database rather than in `.env`:

- **The refresh token is replaced on every refresh**, so a value written into
  `.env` would be stale an hour after the first sync.
- **It expires after a hundred days of not being used.** A club that ships
  three times a year can cross that on its own, so the service refreshes the
  connection daily and the desk shows the deadline rather than burying it.

### Where the money lands

The adapter will not create anywhere to put the money, and has no default.
Which income account wine revenue and shipping book against is a bookkeeping
decision; a default would put real money in a wrong account quietly, and
quietly is the worst way to be wrong about money.

So `QBO_WINE_ITEM` must already exist in QuickBooks, pointed at an income
account by the bookkeeper, and a push fails with a sentence saying so if it
does not. `QBO_SHIPPING_ITEM` **must exist too**, and that is new: every tier
bills carriage as its own line now, so every invoice is two. Wine and shipping
book to different accounts, which is why they are separate, and a push fails
naming the item if it is missing.

### The way out that needs no API

`/admin/accounting/export.csv` is every invoice as one row per line item.
QuickBooks Desktop has no REST API at all, and some bookkeepers would rather
have a file than grant an app access to the company — both are served by the
export, which is why it exists even though the API adapter works.

## What is deliberately missing

- **Compliance.** Direct-to-consumer wine shipping in the United States is
  regulated per state: permitted states, volume limits per customer per year,
  age verification at delivery, and tax collected at the destination. This
  service records the destination state and refuses the ones the winery has not
  licensed (`config.UNLICENSED_STATES`, which is what `SHIPPABLE_STATES` is
  derived from — somebody refused for that reason can join the `waiting` list
  instead, and is turned into an ordinary application the day the state comes
  off it), but that list is a placeholder and the volume limits are not
  implemented. Before taking real money this wants a
  compliance provider (ShipCompliant, Avalara for Beverage Alcohol) wired in
  front of order creation.
- **Address verification.** The delivery address is taken before checkout and
  checked for the things that decide whether a carrier will move it at all: a
  licensed state, a street rather than a PO box (wine needs a signature from
  somebody over 21), and a ZIP of the right shape. Nothing checks that the
  address *exists* — no USPS or carrier lookup — so a typed street can still be
  wrong. The member reads it back on the welcome page and on the invoice, which
  is the cheapest correction there is, and the desk can change it on **Members**.
- **Dunning.** A declined card gets one letter with a link to replace it, and
  the order waits at `failed` until somebody charges it again from the desk.
  Nothing chases it on a schedule, and nothing gives up on it either.
- **Tax.** Orders carry a tax field and it is computed as zero. Sales tax on
  DTC wine depends on the destination and is not something to guess at.
- **Inventory beyond the club's own shelf.** `stock_move` counts the bottles
  the club may draw on and nets out what raised orders took, which is what the
  release plan needs. It is not the winery's production record: nothing here
  knows about barrels, bottling runs, the tasting room or trade sales, so the
  figure it works from is only as good as the last count somebody typed in.
  A wine that leaves by some other door leaves this ledger too high until the
  next count corrects it — which is why counting is a first-class action and
  its correction is a movement anyone can read, rather than a silent edit.
