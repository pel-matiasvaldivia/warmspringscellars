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
         │ approved by Robert or Cecilia in /admin
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
     (active)                       │ each release cycle
         │                          │
         ├──▶ ORDER (service order) ┘
         │      │ paid
         │      ├──▶ INVOICE   (sequential, immutable, PDF)
         │      │      └──▶ LEDGER QUEUE ──▶ QuickBooks (invoice, then payment)
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

**Payment happens at the provider, never here.** The service creates a checkout
session and redirects. Card details never touch this code or this database,
which keeps the whole thing out of PCI scope. Membership is only created when
the provider's webhook confirms the money moved — not when the member returns
from checkout, because a browser redirect proves nothing.

**Orders are generated per release, not per payment.** A subscription bills on
its own cycle; a release happens when the wine is ready. They are separate
clocks, so a release cycle creates an order for every active membership, and
payment attaches to the order when it settles.

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
member whether or not Intuit answered. So issuing an invoice does not call
QuickBooks — it writes a row to `ledger_task` inside the same transaction that
writes the invoice, and the push happens afterwards, from the queue. Three
things follow from that split, and each is the reason for it:

- An order that committed always has its paperwork queued, and an order that
  rolled back never does. The two cannot disagree, because they are one commit.
- A push that fails stays visible. The queue at `/admin/accounting` is the
  standing answer to "what do the books not know about yet?", which is better
  asked in March than discovered in January.
- Retrying is free. Every push is keyed on something QuickBooks can recognise —
  the invoice number as its `DocNumber` — so a second attempt adopts the
  document the first one created instead of writing a second. The adapter also
  sends Intuit's own `Request-Id`, which is a second guard on the same risk.

Payments queue behind their invoice: a payment links to an invoice id that only
exists once the invoice has landed, so the queue does them in that order and
leaves a payment pending rather than failed if its invoice has not gone yet.

## What is wired and what is not

Everything above runs end to end against the fake payment provider and the file
outbox, which is what the tests exercise. Three adapters need real credentials
before they do anything outside this machine:

- **Payments.** `payments/stripe_provider.py` speaks Stripe Checkout in
  subscription mode. It needs `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`.
  Until those are set the service uses `payments/fake.py`, which moves orders
  through the same states without a network call, so the rest of the flow can
  be developed and demonstrated.
- **Email.** `mail/smtp.py` needs `SMTP_HOST` and friends. Without them the
  service writes each message to `var/outbox/` as a `.eml` file you can open in
  any mail client — useful in development, and the only reason the approval
  link is visible in the admin page.

- **Accounting.** `accounting/quickbooks.py` speaks Intuit's Accounting API. It
  needs `QBO_CLIENT_ID` and `QBO_CLIENT_SECRET`, and then a human to authorise
  a company once from `/admin/accounting`. Without them the ledger is
  `NullLedger`, whose pushes refuse — on purpose. There is no simulated
  accounting system in production: a club that cannot reach its books should
  accumulate a visible queue, not a pile of invoices it believes were filed.

None of the three has been exercised against the live services from here: this
container has no egress to them. The shapes come from the providers' documented
APIs; the Stripe signature verification is tested against vectors computed
locally, and the QuickBooks adapter against a stub transport that proves its own
logic — token rotation, matching a customer by email, adopting a duplicate,
refreshing once on a 401 — and proves nothing about Intuit's field validation.

Two details of Intuit's OAuth are worth knowing before the first connection:
the refresh token **is replaced on every refresh**, which is why tokens live in
the database and not in `.env`; and it **expires after a hundred days of not
being used**, which makes a club that syncs twice a year a club that will find
itself disconnected. The desk shows that deadline rather than burying it.

### Where the money lands

The adapter will not create items in QuickBooks. `QBO_WINE_ITEM` and
`QBO_SHIPPING_ITEM` must already exist there, and a push fails with a sentence
saying so if they do not. Which income account wine revenue and shipping book
against is a bookkeeping decision; a default would put real money in a wrong
account quietly, and quietly is the worst way to be wrong about money.

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
  licensed (`config.SHIPPABLE_STATES`), but that list is a placeholder and the
  volume limits are not implemented. Before taking real money this wants a
  compliance provider (ShipCompliant, Avalara for Beverage Alcohol) wired in
  front of order creation.
- **Tax.** Orders carry a tax field and it is computed as zero. Sales tax on
  DTC wine depends on the destination and is not something to guess at.
- **Inventory.** Nothing decrements stock. With five wines and a few thousand
  bottles this is a real gap once the club has more members than bottles.
