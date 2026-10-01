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

## What is wired and what is not

Everything above runs end to end against the fake payment provider and the file
outbox, which is what the tests exercise. Two adapters need real credentials
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

Neither has been exercised against the live services from here: this container
has no egress to them. The shapes come from the providers' documented APIs, and
the signature verification is tested against vectors computed locally.

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
