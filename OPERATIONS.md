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

## 2. The eight tabs

| Tab | What it is for |
| --- | --- |
| **Applications** | Requests from the form on the site, and the ones added by hand |
| **Waiting** | People in states the winery has no permit for, who asked to be told |
| **Members** | Who is active, paused or cancelled |
| **Orders** | Orders and their invoice PDFs |
| **Shipments** | Shipments, from ready-to-pack to delivered |
| **Releases** | The seasonal despatches, and what each one can actually fill |
| **Cellar** | The bottles the club may draw on, and where they went |
| **Books** | QuickBooks: what is filed, and what is still owed |
| **Mail** | How outgoing mail is configured, and a button to test it |

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

### 3.1b Somebody who wrote instead of filling in the form

People write to `club@warmspringscellar.com`, telephone, or ask at the winery.
**"Add one by hand"**, at the top right of **Applications**, puts them into the
same queue: name, email, state, and the rest optional. From that moment they
are an ordinary application — pending, then approved, then the invitation and
the checkout link. There is no separate path, and nothing is sent to them by
adding the row; the mail goes out on approval, as it does for everyone.

Three things about the screen are worth knowing:

* **Only the licensed states are listed.** Someone writing from a state the
  winery cannot ship to has no application to make, and it is better to say so
  in a reply than to discover it at approval.
* **"How it reached us"** is the point of the exercise. Fill it in and the
  referral column answers which door brings members — the mailbox, the
  telephone, the tasting room, or the site.
* **Nobody is entered twice.** If that person later fills in the form
  themselves while the row is still pending, the system hands back the row
  that already exists. The screen says so too if the address already has one
  open.

The audit log on the card carries `entered_by_hand` with the name of whoever
was signed in, next to the ordinary `received`, so a row added at the desk is
never mistaken for one the site produced.

### 3.1c People we cannot ship to yet

A visitor who picks a state the winery has no permit for is told so on the
spot, and then offered the only thing there is to offer: **we will write the
day that changes.** The form's button changes to say so, and the same fields
go to the waiting list instead of to Applications — no allocation is asked
for, because none is being offered. Without that, the only thing a visitor in
Alabama is ever told is no, and a name worth having the day the permit arrives
is gone.

They appear under **Waiting**, grouped by state, because a licence arrives for
a state and not for a person. Somebody writing back to the letter the winery
sends to those states is entered there by hand; that form offers only the
unlicensed states, which is the mirror of §3.1b offering only the licensed
ones.

They are deliberately **not applications**. An application is a request for an
allocation and gets an answer; this one cannot be answered until a licence
nobody here controls arrives, and a pending row that can never be decided is a
lie in the queue.

**The day a state opens**, take it out of `UNLICENSED_STATES` in
`api/app/config.py` and out of the `UNLICENSED` list in `index.html` — both,
or the form and the server disagree about who may join. **Waiting** then shows
a green line naming that state, because it can be licensed for weeks before
anybody notices there are names under it. **"Into the queue"** turns a row into
an ordinary application; approving it in **Applications** is what writes to
them, as it does for everybody else. Nothing is sent by moving the row.

### 3.2 Approve or decline

Under **"The invitation"**, tick the tiers that person will be able to choose
from. Every tier in the catalogue is listed, and the ones a member may choose
for themselves come ticked. **A by-invitation tier comes unticked** — tick it
only if you really mean to offer it. There is none today: the catalogue holds
The Tasting Case, The Founder's Reserve and The Full Case, all three open.

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
**say where the wine should go**, and then go to checkout and pay.

The delivery address is asked for here, between the choice and the payment, and
the club keeps it itself rather than reading it back from the payment provider.
Three reasons, in order of how much they cost when ignored:

* **Whether we may ship at all depends on it.** The state decides, and there is
  no sense taking money for a case that cannot be delivered.
* **A carrier will not leave wine on a doorstep.** Somebody over 21 has to sign,
  so a **PO box is refused**, with a message saying why. Home or work, either.
* **One address, not two.** Stripe is deliberately not asked to collect one:
  two addresses that disagree leave nobody able to say which the box is packed
  from.

The ZIP is checked for shape (five digits, or ZIP+4), not for existing — we are
not buying address verification for a few hundred members, and the member reads
it back on the page after payment and in the invoice.

If somebody gives an address and then abandons checkout, **it is kept**: the
same link brings it back filled in, and the desk can see on the application
where the wine was going even if the payment never happened.

The link **expires after 14 days** (`OFFER_DAYS`) and **is spent once used**: a
second attempt reads "already been accepted". That is correct, not a fault. If
another one is genuinely needed, the right answer is a fresh request.

With Stripe live, the membership is only confirmed when the webhook arrives. If
a member says they paid and **Orders** does not show `paid`, the problem is the
webhook, not the member (§9).

### 3.4 Joining takes no money

Once the card is saved, the service creates the **membership** (`active`) and
stops there. No order, no invoice, no shipment, and **nothing charged**.

That is the whole billing model and it is worth saying plainly: **the club
charges when a release goes out, never when somebody joins.** At the moment a
member signs up nobody knows whether there is wine for them — that is what the
release decides — and taking money first is how a club ends up owing bottles it
does not have.

So the card sits with the payment provider until §6 charges it. The member's
welcome letter says so in its second line, because a card typed into a payment
page is a card most people assume was just charged, and a bank statement with
nothing on it is a worry rather than a relief.

There is **no subscription**. Nothing inside Stripe bills anybody on a
schedule, so when the shipment calendar moves there is no second calendar to
keep in step with it.

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

## 6. The three despatches a year

Spring, Fall and Christmas. On **Releases**:

1. **"Plan it"** with a name (`Spring 2026`) and the shipping date.
2. **Compose it** — open the release and say what each tier's shipment holds.
   §6.1 is about this press, and it is the one that is new.
3. **"Raise orders"** raises one order **for each member there is wine for**,
   at their own tier and price, and takes those bottles out of the cellar.
   They are raised `pending_payment`: **this press takes no money.**
4. **"Charge N"** charges those orders against the cards members left when
   they joined. This is the press that moves money.

**Three presses, on purpose.** Between composing and raising you can see who
is in the release and which wine runs out first. Between raising and charging
you can see exactly what was raised. Those two gaps are the whole check
standing between the cellar and a season it cannot fill. The Charge button
says how many orders are waiting, and disappears when none are.

**All of them are safe to press twice.** The (member, release) pair is unique
in the database, so raising again skips anyone already covered — and takes
bottles only for the orders that run raised. Charging again skips
anyone already paid, and the provider is given the order's own reference as
its key, so a charge that timed out settles once rather than twice.

### 6.1 Composing a release, and who gets left out

Click the release's name on **Releases**, or the **Plan** button. The page
has three parts.

**What a shipment holds.** A box per wine, per tier. Blank or zero means that
wine is not in that shipment. If the bottles do not add up to what the tier
promises — three, six or twelve — the page says so and keeps saying so
until they do. The member reads the bottle count on their invoice.

**The wine.** One row per wine: how many bottles one shipment of each tier
holds, how many the whole club needs, how many are on hand, and how short
that leaves you. *On hand* already nets out what earlier releases took.

**What it costs to send.** The freight for the shipments the plan can fill,
by Crown zone and in total, against the 2026 UPS Ground sheet at residential
rates. Every tier bills carriage, so part of this comes back from the members
and the screen says how much; the rest — this is what the winery spends to keep that promise, and the line
under the table gives it as a percentage of what the release would charge.

It is an estimate, not a quotation. It leaves out Crown's dollar-a-pound
overweight charge, which an ice pack can itself trigger, and the fees that
only happen when something goes wrong. A member whose state has no price on
the ground sheet — Alaska and Hawaii are the two the club would otherwise
accept — is named in red rather than counted as free.

**Who cannot be filled.** The names, and the reason against each. Four
reasons, and they are different conversations:

| Reason | What it means |
| --- | --- |
| out of *a wine* | The release ran out. The wine, not the member, is the problem |
| not licensed to ship to *a state* | The state closed after they joined — it will not ship, so it is not allocated wine either |
| this release does not say what that tier gets | You composed one tier and not the other |
| *a tier* is no longer in the catalogue | They are on a tier that was withdrawn |

**Who gets filled when there is not enough: whoever joined first.** That is
the whole rule, and it is deliberately one you can say out loud to the member
who misses out. One surprise worth knowing: it works per bottle, not as a
single queue. Somebody who joined later can still be filled after an earlier
member was not, when they are in a tier whose wines held out. They are
drinking different wine, and holding their shipment back would not put a
bottle in the other member's hands.

**A member with no card on file is still allocated wine**, and listed
separately so you can telephone them first. They are not held back — they have
a letter and a link, and their order waits at `failed` until they use it. But
a bottle allocated to a card that will decline is a bottle held back from
somebody who would have paid.

**A release with no composition still works the old way** — one order per
active member, nothing checked. The screen says so in red, and so does the
message after you press. It is left possible because a club that has not
written its inventory down yet still has to be able to ship.

### 6.2 The Cellar tab

Add a wine with a code and a name. Then, per wine, two buttons that look
similar and mean different things:

- **Received** adds the number you typed to what is there. Bottles arriving.
- **Counted** says the shelf holds that many, and writes the *difference*.

The difference matters. A count does not overwrite anything — it records the
correction as a movement of its own, with both numbers in the note
(`counted 114, books said 120`). A count that was itself wrong can be found
later, instead of having quietly replaced the truth. **Every movement** at the
bottom of the page is the whole history, and *On hand* is nothing but its sum.

This is the club's own shelf, not the winery's production record. Nothing
here knows about the tasting room or trade sales, so a wine that leaves by
another door leaves this count too high until somebody counts again. Counting
before composing a release is the habit that makes the rest of it true.

### When a card is declined

The order goes to **`failed`** and stays there with the reason against it in
**Orders**, in the provider's own words — a dead card and a bank asking for its
customer look identical without them. The member gets one letter: their
allocation is held, not lost, and a link to put in another card. Nothing is
charged by that link; once the new card is on file, press **Charge** on that
order again.

Nothing chases a failed order on a schedule, and nothing gives up on it
either. It waits on the Orders screen until a person deals with it.

A membership left on a tier that no longer exists in the catalogue is skipped
and logged. It is not billed at whatever happens to be nearest.

### When a member moves

The address on the membership is the one the box is packed from, so it is
editable on **Members**: "Change it", under the address, opens the fields and
saves with the same rules the member met at checkout — a licensed state, a real
street, a ZIP that is the right shape. The change is written to the audit log
with the old address and the new one.

**It applies to what has not left the cellar.** An order already despatched
keeps the address it went to, because that is where the wine is. Change it
before the release is raised and the next shipment follows them.

A membership taken before the club asked for an address has only a state; the
Members and Shipments screens say **"no address on file"** in red rather than
printing half a label.

### Pausing and cancelling

On **Members**, each row offers the valid changes: `active` ⇄ `paused`, and
`cancelled` from either. **Pause before pressing "Raise orders"** — once the
order is raised the charge is made and it has to be refunded. `cancelled` does
not come back.

---

## 7. The books: QuickBooks

The **Books** tab. Every invoice and every payment travels to QuickBooks as
two documents: the invoice, and the payment that settles it.

QuickBooks is the winery's accounting system and the only one the service
talks to. Set `QBO_CLIENT_ID` and `QBO_CLIENT_SECRET`, then authorise the
company once from the Books tab. Until that is done the queue holds
everything, and the CSV export is a full substitute.

**The books are downstream, and never in the way.** A member who has paid is a
member whether or not the accounting system answered. So issuing an invoice
does not call it: it queues the document, and the push happens afterwards. Three
things follow:

- An order that committed **always** has its paperwork queued, and one that
  rolled back never does. They cannot disagree — they are one commit.
- A push that fails **stays visible**. The Books queue is the standing answer
  to "what do the books not know about yet?", which is better asked in March
  than discovered in January.
- **Retrying is free.** An invoice already held under that number is adopted,
  not duplicated. Both adapters look the number up before writing.

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

**Before the first push, the item `Wine Club Allocation` must already exist in
QuickBooks** (Sales → Products and services), under exactly that name.

The service **will not create it**. Which income account each dollar lands in
is the bookkeeper's decision, and a default would put real money in the wrong
account quietly. If it is missing, the push fails saying so.

A second item, `Shipping`, **is needed as well**, and this changed in November
2026. Every tier now bills carriage as its own line, so every order raises two
and every invoice carries both. Wine and shipping book to different accounts,
which is why they are separate lines — and a push fails saying so if the item
is missing.

What the member pays for shipping is the carrier's price for their own address,
less what their tier puts towards it: nothing on The Tasting Case, $20 on The
Founder's Reserve, $40 on The Full Case. So two members on one tier pay
different shipping, and the winery gives each of them the same amount.

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
A club that invoices three times a year is precisely a club that will find
itself disconnected.

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

## 8. Outgoing mail

Everything the club sends — the signed invitation, the despatch notice, the
invoice — leaves through a single SMTP server. While `SMTP_HOST` is empty
nothing leaves at all: each message is written to `/srv/var/outbox` as an
`.eml` file and the desk shows the invitation link on the card. That is not a
fault; it is how the project travels until somebody decides where mail goes
out from.

### 8.1 Two ways to authenticate

| | With a password | With OAuth2 |
| --- | --- | --- |
| What is configured | `SMTP_USER` + `SMTP_PASSWORD` | `SMTP_USER` + the three `SMTP_OAUTH_*` |
| Who presents itself | the mailbox | an application, authorised over the mailbox |
| On Microsoft 365 | works until the tenant turns basic authentication off | keeps working afterwards |

The service decides by itself: with the three OAuth variables set it uses a
token, otherwise the password, and with neither it does not authenticate at all
(which is the right behaviour for §8.4). Switching between them is an edit to
`.env` and a restart; there is nothing to change in the code.

### 8.2 Microsoft 365 with a password

```
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USER=club@warmspringscellar.com
SMTP_PASSWORD=<that mailbox's password>
MAIL_FROM=club@warmspringscellar.com
MAIL_REPLY_TO=club@warmspringscellar.com
```

The mailbox needs an Exchange Online licence — an unlicensed shared mailbox
cannot authenticate — and the tenant needs **SMTP AUTH** enabled for it. If the
account has MFA, a person's password will not do: use the service mailbox.

> **The symptom to recognise.** `535 5.7.139 Authentication unsuccessful, the
> request did not meet the criteria to be authenticated successfully. Contact
> your administrator.` Almost always it means basic authentication is disabled
> for that mailbox, not that the password is wrong. That is the cue for §8.3.

### 8.3 Microsoft 365 with OAuth2

Microsoft has been retiring basic authentication for SMTP. On the day it goes
in a tenant, a username and a password stop working with no warning. So the
adapter also speaks **XOAUTH2**, which is the path that survives.

It is done once, and it needs a tenant administrator:

1. **Entra ID → App registrations → New registration.** Name it `Warm Springs
   Cellar — club mail`. No redirect URI: this application never signs in as
   anybody.
2. **Certificates & secrets → New client secret.** Copy the *Value* — not the
   Secret ID — there and then; it is never shown again. Note its expiry date
   too, because the day it lapses the mail stops going out.
3. **API permissions → Add a permission → APIs my organization uses →
   Office 365 Exchange Online → Application permissions → `SMTP.SendAsApp`**,
   then **Grant admin consent**. Without the consent a token is still issued
   and the server refuses it, which is the most confusing way to fail.
4. **Give it the mailbox**, in Exchange Online PowerShell:

   ```powershell
   Connect-ExchangeOnline
   New-ServicePrincipal -AppId <client id> -ServiceId <the app's object id>
   Add-MailboxPermission -Identity club@warmspringscellar.com `
       -User <the app's object id> -AccessRights FullAccess
   ```

   The *object id* is the Enterprise application's, not the registration's.
5. **In `.env`:**

   ```
   SMTP_HOST=smtp.office365.com
   SMTP_PORT=587
   SMTP_USER=club@warmspringscellar.com
   SMTP_PASSWORD=
   SMTP_OAUTH_TENANT_ID=<Directory (tenant) ID>
   SMTP_OAUTH_CLIENT_ID=<Application (client) ID>
   SMTP_OAUTH_CLIENT_SECRET=<the Value from step 2>
   ```

   `SMTP_USER` **stays set**. The token says which application is sending,
   never from which mailbox; the mailbox has to be named as well.

The service asks for one token, keeps it, and reuses it until two minutes
before it expires. It does not ask for one per message.

### 8.4 The third option: the relay connector

If the server has a fixed IP, Exchange Online can accept the mail by IP with no
authentication at all: **Exchange admin center → Mail flow → Connectors → from
"Your organisation's email server"**, authorising that address.

```
SMTP_HOST=<tenant>.mail.protection.outlook.com
SMTP_PORT=25
SMTP_USER=
SMTP_PASSWORD=
```

This sends to the tenant's own domains with nothing further. To send outside —
which is what this club does — the sender's domain has to be verified in the
tenant and SPF has to cover that IP. It is the option with no secret to expire,
and the one that depends on the VPS keeping its address.

### 8.5 Arriving, not merely leaving

Three records in the DNS for `warmspringscellar.com`, all three belonging to
the domain rather than the server:

- **SPF**, one single TXT record: `v=spf1 include:spf.protection.outlook.com
  -all`. With the connector of §8.4, add `ip4:<the VPS address>` before the
  `-all`. Two SPF records on a domain count as none.
- **DKIM**: Microsoft 365 Defender → Policies → Email authentication → DKIM.
  Publish the two CNAMEs `selector1._domainkey` and `selector2._domainkey` the
  page gives, and only then enable signing.
- **DMARC**, a TXT at `_dmarc`: start at `v=DMARC1; p=none;
  rua=mailto:dmarc@warmspringscellar.com`, read the reports for a fortnight,
  and only then tighten to `quarantine`.

An invitation that lands in a junk folder is an approved application nobody
answers, and on the desk it looks exactly like one that was ignored.

### 8.6 Test before promising: the **Mail** tab

No real application has to be approved to find out whether mail goes out. The
**Mail** tab on the desk answers the three questions in the order they break.

**"How it is configured"** reads `.env` and says it in words: where mail goes
out, whether it is encrypted, how it authenticates, as which mailbox. Each row
carries a mark: *right* is right, *works today* works today and will stop —
which is what it says about a password against Microsoft 365 — *wrong* is
wrong, and *not sending* means no server is configured and nothing leaves. It
is read on every page load and touches no network.

**"Check the connection"** opens the real connection: connects, encrypts,
authenticates, hangs up. **It sends nothing and nobody receives anything.** It
lists every step that did work before the one that failed, which is most of the
diagnosis: knowing the connection opened and STARTTLS succeeded before the
password was refused says something very different from "mail is broken".

**"Send a test message"** sends one real message to whatever address is typed
in. Use one **outside** the tenant — a Gmail address will do, and it also shows
whether SPF and DKIM passed under "Show original" — because a message that
travels inside the tenant proves nothing about the ones that leave. The message
names the server and the mechanism that sent it, so a forwarded copy is
evidence. Nobody on the club's list is written to, and nothing about a member
or an order changes.

At the foot of the page, **"If it does not work"** lists the errors that
actually turn up — `535 5.7.139`, `AADSTS7000215`, a token accepted but the
send refused — and what each one means.

> **What the tab cannot tell you.** A connection that works is not a message
> that arrives. That is SPF, DKIM and somebody's spam filter (§8.5), and only
> a real send to a real address answers it.

If something still does not add up: `docker compose logs api | grep -i smtp`.
The adapter logs the refusal as the server worded it, code included.

---

## 9. When something does not add up

| Symptom | Where to look |
| --- | --- |
| The member never got the invitation | Is there an `SMTP_HOST` notice at the top? Then the mail is in `/srv/var/outbox`. Copy the link off the card instead. |
| The server answers `535 5.7.139` | Microsoft 365 has turned basic authentication off for that mailbox. It is not the password: move to OAuth2 (§8.3). |
| The server answers `535 5.7.3` | The tenant refused the sign-in itself. Authenticated SMTP off for the mailbox (admin centre → the user → Mail → Manage email apps), off for the tenant, or Security Defaults / Conditional Access blocking legacy sign-ins. **A shared mailbox has no password and cannot sign in at all**, so if the club address is one, OAuth2 (§8.3) is the only way, not a setting to flip. |
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

## 10. How it is deployed

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

## 11. Before taking real money

- [ ] `SECRET_KEY` generated, stored and **fixed** in `.env`
- [ ] `ADMIN_PASSWORD` set, and the address range uncommented in `nginx.conf`
- [ ] `PUBLIC_BASE_URL` on the real domain (the links are built from it)
- [ ] Stripe approved for alcohol sales — **they ask for it explicitly**
- [ ] Webhook pointed at `/api/payments/webhook`, subscribed to
      `checkout.session.completed`
- [ ] SMTP configured, and tested from the **Mail** tab with a send to an
      address outside the tenant (§8.6). On Microsoft 365, with OAuth2 (§8.3)
      rather than a password
- [ ] SPF, DKIM and DMARC published for `warmspringscellar.com` (§8.5)
- [ ] `SHIPPABLE_STATES` checked against the winery's actual permits
- [ ] QuickBooks: app created, redirect URI registered, the item created, and
      **one invoice pushed to a sandbox company** before switching to
      `production`
- [ ] `QBO_ENVIRONMENT=production` on the same day Stripe goes live — the
      service warns if one is live and the other is a practice company, but it
      is better not to get there
- [ ] Backups of the `club-data` volume automated

One thing that is **not** implemented and is worth keeping in mind: the
per-customer, per-state volume limits that come with DTC permits. It is
recorded in `api/FLOW.md`.

Stock is now checked — a composed release raises orders only for the members
there are bottles for (§6.1) — but only against what somebody counted into the
**Cellar** tab. It is the club's own shelf and not the winery's production
record, so **count before composing the first real release**.
