# Operations manual — the cellar desk

How to get into the system and work the club: requests, payments, invoices,
shipments and the books.

*Hay una versión en castellano en [`OPERACIONES.md`](OPERACIONES.md).*

The business rules live in [`api/FLOW.md`](api/FLOW.md). This is the day-to-day
document: what to open, what to press, and what to check when something does
not add up.

---

## 1. Getting in

| | |
| --- | --- |
| Address | `https://<your domain>/admin` |
| User | whatever `ADMIN_USER` says in `.env` (`cellar` by default) |
| Password | `ADMIN_PASSWORD` in `.env` |

The browser asks with its own HTTP Basic box, not with a form on the page.
There is no "log out" — close the browser.

If you get **503 "ADMIN_PASSWORD is not set, so the desk is closed"**, the desk
is shut on purpose: that variable is missing. An administration panel with no
password is worse than one that is down.

**A second lock, recommended.** In `nginx.conf`, inside `location /admin`,
two lines are commented out:

```nginx
# allow 203.0.113.0/24;   # the winery's office
# deny all;
```

Uncomment them with the winery's address range and the desk stops existing for
the rest of the internet. The password still applies: two locks, not one.

### The notice bar

At the top of every page, in **"Not configured:"** boxes, the service says
which adapter is still a stand-in. The five that matter:

- `SECRET_KEY` unset → invitation links are signed with a key that changes on
  every restart, so **every invitation already sent stops opening**.
- `STRIPE_SECRET_KEY` unset → checkout is **simulated and no money moves**. The
  whole flow works; nobody pays.
- `STRIPE_WEBHOOK_SECRET` unset, with Stripe live → webhooks cannot be verified
  and are **refused**, so a real payment would not be recorded.
- `SMTP_HOST` unset → no mail leaves; each message is written as an `.eml` file
  in `/srv/var/outbox` inside the container.
- `QBO_CLIENT_ID`/`QBO_CLIENT_SECRET` unset → invoices **queue up** instead of
  reaching QuickBooks. Nothing is lost: the queue is on the **Books** tab and
  exports as CSV (§7).

While there are boxes, what is on screen is a rehearsal, not the operation. To
read the mail that did not go out:

```bash
docker compose exec api ls -t /srv/var/outbox | head
docker compose exec api cat /srv/var/outbox/<file>.eml
```

---

## 2. The six tabs

| Tab | What it is for |
| --- | --- |
| **Applications** | Requests arriving from the form on the site |
| **Members** | Who is active, paused or cancelled |
| **Orders** | Orders and their invoice PDFs |
| **Shipments** | Shipments, from ready-to-pack to delivered |
| **Releases** | The seasonal despatches (April and October) |
| **Books** | QuickBooks: what is filed, and what is still owed |

Pending applications and undelivered shipments float to the top of their lists
on their own. What needs attention is where you look first.

---

## 3. A new member, start to finish

```
form → Applications → approve → the member picks a tier → pays
     → order → invoice → shipment → delivered
```

### 3.1 The request arrives

It lands in **Applications** as `pending`. "Open" shows the card: name, email,
phone, city and shipping state, the tier they asked for, how they heard of us,
and the note they wrote.

If the destination state is not one the winery is licensed for, the form on the
site says so before it submits **and the server refuses it as well** — both
ends, because a browser can be bypassed. The list is `SHIPPABLE_STATES`, in
`api/app/config.py`.

If the same person fills the form twice while their request is still `pending`,
**no second row is created**: it is treated as the same request and the repeat
is logged. One row in the list, which is right — an eager applicant is not two
members.

### 3.2 Approve or decline

Under **"The invitation"**, tick the tiers that person will be able to choose
from. The three selectable ones come ticked; **The Inner Circle comes unticked
because it is by invitation** — tick it only if you really mean to offer it.

**"Approve & send the invitation"** does four things at once: approves the
application, creates the offer with the ticked tiers, emails the member, and
leaves the signed link visible on the card, below, in the **"Invitations"**
table.

> **Always copy that link.** If the mail did not go out the notice says so
> plainly ("Approved, but the email did not go out…") and the link is the only
> way that person gets in. It can be pasted into a mail by hand.

**"Decline"**, with an optional reason, sends the decline mail. The reason is
kept in the internal log; the member never sees it.

An application is approved or declined **once**. The buttons do not come back:
the flow refuses the transition rather than leaving two live offers for the same
person.

### 3.3 The member picks and pays

The link opens a page with their name and the tiers offered. They choose one,
go to checkout and pay.

The link **expires after 14 days** (`OFFER_DAYS`) and **is spent once used**: a
second attempt reads "already been accepted". That is correct, not a fault. If
another one is genuinely needed, the right answer is a fresh request.

With Stripe live, the membership is only confirmed when the webhook arrives. If
a member says they paid and **Orders** does not show `paid`, the problem is the
webhook, not the member (§8).

### 3.4 What happens with nobody pressing anything

Once payment clears, the service creates the **membership** (`active`), the
**order** (`paid`), the **invoice** (sequentially numbered, no gaps) and the
**shipment** (`ready`).

The invoice is also **queued for QuickBooks** at that same moment, in the same
transaction. Reaching the books is a separate step, and it is §7.

---

## 4. Orders and invoices

**Orders** shows reference, date, member, tier, release, total, status and the
invoice number. The number is a link: it opens the PDF
(`WSC-INV-2026-0001.pdf`).

An order's states: `pending_payment` → `paid` → `fulfilled`, with `refunded` or
`failed` when something leaves the rails. `fulfilled` is set by the service when
the shipment is marked delivered — never by hand.

Sales tax is computed as zero and **printed as zero on the invoice**. That is
deliberate: if we are not collecting it yet, better that the invoice says so
than that it hides it. Recorded in `api/FLOW.md`.

---

## 5. Shipments

**Shipments** is the packing-day screen. Each row offers only the buttons its
current state allows:

| State | Buttons | What it asks for |
| --- | --- | --- |
| `ready` | "picked", "held" | "held" takes a reason |
| `picked` | "in transit", "held" | **"in transit" requires the tracking number** — the form asks for it and the server insists on it; the carrier is optional and defaults to `unknown` |
| `in_transit` | "delivered", "returned" | — |
| `held` | "ready" | back in the queue |
| `returned` | "ready" | pack it again |
| `delivered` | — | closed, and it closes the order |

**"in transit" is the only button that writes to the member**: it sends the mail
with the carrier and the tracking number. That is why the number is required —
a shipping notice without one is no use. If that mail fails, the on-screen
notice says so and it is worth resending by hand.

Steps cannot be skipped. "delivered" on a shipment that was never marked
`picked` is refused. On purpose: a shipment nobody packed that reads as
delivered is a conversation nobody can reconstruct six months later.

---

## 6. The two despatches a year

April and October. On **Releases**:

1. **"Plan it"** with a name (`Spring 2026`) and the shipping date.
2. **"Raise orders"** raises **one order per active membership**, at each
   member's own tier and price.

**"Raise orders" is safe to press twice.** The (member, release) pair is unique
in the database, so a second run skips anyone already covered rather than
billing them again. If new members joined after the first run, pressing it
again is exactly the right thing to do.

A membership left on a tier that no longer exists in the catalogue is skipped
and logged. It is not billed at whatever happens to be nearest.

### Pausing and cancelling

On **Members**, each row offers the valid changes: `active` ⇄ `paused`, and
`cancelled` from either. **Pause before pressing "Raise orders"** — once the
order is raised the charge is made and it has to be refunded. `cancelled` does
not come back.

---

## 7. The books: QuickBooks

The **Books** tab. Every invoice and every payment travels to QuickBooks Online
as two documents: the invoice, and the payment that settles it.

**The books are downstream, and never in the way.** A member who has paid is a
member whether or not Intuit answered. So issuing an invoice does not call
QuickBooks: it queues the document, and the push happens afterwards. Three
things follow:

- An order that committed **always** has its paperwork queued, and one that
  rolled back never does. They cannot disagree — they are one commit.
- A push that fails **stays visible**. The Books queue is the standing answer
  to "what do the books not know about yet?", which is better asked in March
  than discovered in January.
- **Retrying is free.** An invoice QuickBooks already holds under that number is
  adopted, not duplicated.

### 7.1 Connecting the first time

1. At [developer.intuit.com](https://developer.intuit.com) → *My Apps* → create
   an app with the **Accounting** scope.
2. Register **exactly** this redirect URI on the Intuit side:
   `https://<your domain>/admin/accounting/callback` — the Books page prints it
   for you to copy.
3. Put `QBO_CLIENT_ID` and `QBO_CLIENT_SECRET` in `.env`, with
   `QBO_ENVIRONMENT=sandbox` to practise or `production` for the real books.
   `docker compose up -d` to pick them up.
4. On **Books** → **"Connect to QuickBooks"**. Intuit asks for consent, you
   choose the company, and it comes back. That is all: the company is stored.

**Before the first push, two items must already exist in QuickBooks** under
these names (Sales → Products and services):

| Item | What it carries |
| --- | --- |
| `Wine Club Allocation` | the wine |
| `Shipping` | the carriage |

The service **will not create them**. Which income account each dollar lands in
is the bookkeeper's decision, and a default would put real money in the wrong
account quietly. If one is missing, the push fails saying which.

### 7.2 Day to day

| Button | What it does |
| --- | --- |
| **"Sync now"** | Pushes everything outstanding: invoices first, then their payments |
| **"Retry"** | Retries a document that failed (only shown on those rows) |
| **"Export all (CSV)"** | Every invoice, one row per line item |
| **"Export unfiled"** | Only what has not reached the books |
| **"Disconnect"** | Drops the connection. **The queue is kept** |

The big number under **"Waiting to be filed"** is the one thing worth a routine
glance: if it climbs and does not come down, the books are falling behind. It is
also in `/api/health`, so it can be watched from outside.

With `QBO_AUTOSYNC=true` the push happens as each payment lands. Leave it
`false` for the first few weeks and press "Sync now" by hand, watching what
happens.

### 7.3 The hundred days

Intuit **replaces the refresh token on every use** — which is why tokens live in
the database and not in `.env` — and **expires it after a hundred days unused**.
A club that invoices twice a year is precisely a club that will find itself
disconnected.

The page shows the deadline as **"Re-authorise before"**. It is not a detail:
past that date the only repair is pressing "Connect to QuickBooks" again. The
queue survives, but nothing is pushed until somebody reconnects.

### 7.4 QuickBooks Desktop, or a bookkeeper who would rather have a file

The CSV covers both. QuickBooks **Desktop has no API at all**, and some
bookkeepers would rather have a file than grant an app access to the company.
The export carries what is needed: number, customer, email, date, item,
quantity, amount, currency, the order reference, and whether it is paid.

> **What has not been tested.** The adapter has never spoken to a real
> QuickBooks company from here — this container has no route to Intuit. Its own
> logic is tested (token rotation, matching a customer by email, adopting a
> duplicate invoice, refreshing on a 401), but only a sandbox company proves
> that Intuit accepts every field. **Connect a sandbox and push one invoice
> before trusting it with the real books.**

---

## 8. When something does not add up

| Symptom | Where to look |
| --- | --- |
| The member never got the invitation | Is there an `SMTP_HOST` notice at the top? Then the mail is in `/srv/var/outbox`. Copy the link off the card instead. |
| "this invitation link is not valid" | `SECRET_KEY` changed (a restart without it fixed in `.env`). Every earlier invitation is dead: approve again. |
| "this invitation has expired" | More than 14 days. A fresh request. |
| Paid, but the order is still `pending_payment` | The webhook did not arrive. Stripe → Developers → Webhooks → Recent deliveries. It must point at `https://<domain>/api/payments/webhook` and be subscribed to `checkout.session.completed`. Replaying the delivery is safe: a repeated event does not charge or invoice twice. |
| The form on the site returns an error | There is a limit of **8 requests per hour per IP address**, and **refused attempts count too**. Testing from the office burns through it fast. |
| The desk returns 503 | `ADMIN_PASSWORD` is missing. |
| A state button is not there | Not a bug: that change is not allowed from where the row is. See the table in §5. |
| "QuickBooks is not connected" | Either the credentials are missing from `.env`, or nobody has authorised a company yet: **Books → "Connect to QuickBooks"**. Nothing was lost. |
| "QuickBooks has no item called …" | Create that item in QuickBooks, pointed at the right income account (§7.1). The service does not invent it, deliberately. |
| "Duplicate Document Number" | Resolves itself: the retry adopts the invoice QuickBooks already holds. If the row sits at `failed`, "Retry" closes it. |
| The Books queue never comes down | Has the hundred-day token lapsed? Check "Re-authorise before" and reconnect (§7.3). |
| The bookkeeper wants the data and there is no connection | "Export all (CSV)". A full substitute, not a patch (§7.4). |

### What to back up

Everything lives in one volume, `club-data` (`/srv/var` inside the container):
the `club.sqlite3` database — which holds the QuickBooks queue and the Intuit
tokens — the invoice PDFs, and the outbox.

```bash
docker compose exec api tar cz -C /srv var > club-backup-$(date +%F).tar.gz
```

Without that backup, members, orders and invoices are gone. With it, they are
not.

### Seeing what happened

Every application card carries its own log at the foot: who approved it, when
the mail went out, when the offer was opened, when the payment cleared. It is
the first place to look, before asking the member.

---

## 9. How it is deployed

The site and the API are two containers behind **one published port**, `8086`,
meant to sit behind Nginx Proxy Manager. The API is not published: only nginx
reaches it, over the internal network.

```bash
cp .env.example .env     # fill in SECRET_KEY and ADMIN_PASSWORD
openssl rand -base64 48  # for SECRET_KEY — generate once, never change it
docker compose pull
docker compose up -d
```

`docker-compose.yml` **refuses to start** without `SECRET_KEY` and
`ADMIN_PASSWORD`. Better that it comes up broken now than months later, with
signed links that will not open.

`.env` never goes into the repository. `.env.example` is the template, and the
template is the only part that is versioned.

---

## 10. Before taking real money

- [ ] `SECRET_KEY` generated, stored and **fixed** in `.env`
- [ ] `ADMIN_PASSWORD` set, and the address range uncommented in `nginx.conf`
- [ ] `PUBLIC_BASE_URL` on the real domain (the links are built from it)
- [ ] Stripe approved for alcohol sales — **they ask for it explicitly**
- [ ] Webhook pointed at `/api/payments/webhook`, subscribed to
      `checkout.session.completed`
- [ ] SMTP configured, and tested with a trial approval to your own address
- [ ] `SHIPPABLE_STATES` checked against the winery's actual permits
- [ ] QuickBooks: app created, redirect URI registered, both items created, and
      **one invoice pushed to a sandbox company** before switching to
      `production`
- [ ] `QBO_ENVIRONMENT=production` on the same day Stripe goes live — the
      service warns if one is live and the other is a practice company, but it
      is better not to get there
- [ ] Backups of the `club-data` volume automated

Two things that are **not** implemented and are worth keeping in mind: the
per-customer, per-state volume limits that come with DTC permits, and inventory
— the service raises orders without asking whether there are bottles. Both are
recorded in `api/FLOW.md`.
