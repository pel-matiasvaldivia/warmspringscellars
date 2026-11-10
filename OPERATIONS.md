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

## 2. The ten tabs

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
| **Campaigns** | What the mailing sent, and who has asked not to be written to |

Pending applications and undelivered shipments float to the top of their lists
on their own. What needs attention is where you look first.

---

## 3. A new member, start to finish

```
form → Applications → approve → the member picks a tier → pays
     → order → invoice → shipment → delivered
```

**Approving can be automatic.** With `AUTO_APPROVE_APPLICATIONS=true` the form
approves what it receives and sends the invitation straight back, with nobody
in the loop. Off unless set.

It loosens none of the checks in front of it, because none of them was ever
the approver's work: an unlicensed state is still refused at the form, the
rate limit still holds, a malformed address is still rejected, and a repeat
submission still returns the application already on file rather than opening a
second invitation. What it gives up is a person reading the application before
the club opens its door — and that door can still be shut afterwards, by
revoking the offer or pausing the membership.

One visible difference: an auto-approved applicant gets **the invitation**
instead of the "we have your application" letter. Two messages about one form
would be noise, and the one carrying the link is the better of them. If that
letter cannot be sent, the application stays approved and the plainer
acknowledgement goes instead, so nobody is left with silence. The approval is
recorded against the application with `auto` as the decider, so **Applications**
still shows who let each member in.

**The winery hears about every new member.** One message to `ALERT_TO` (or the
club mailbox) when a membership is actually created — the card saved, the
allocation chosen — naming the allocation first, because that is what the
cellar has to find bottles for. Not when the form is filled in: plenty of
people ask and never finish, and an alert for each would teach whoever reads
it to stop reading. If that alert cannot be sent the membership still stands
and the silence is recorded against it.

### 3.1 The request arrives

It lands in **Applications** as `pending` — or, with automatic approval on, as
`approved` with its invitation already sent. "Open" shows the card: name, email,
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

People write to `wineclub@warmspringscellar.com`, telephone, or ask at the winery.
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
4. **"Tell them"**, between the two, writes to everybody in the release
   saying what is coming and what it will cost. It takes no money.
5. **"Charge N"** charges those orders against the cards members left when
   they joined. This is the press that moves money.

**Charging waits a day behind telling them.** Press Charge sooner and it
refuses, naming the hour it becomes possible. Two unrelated reasons want the
same gap, which is why it is enforced rather than remembered: a warning that
arrives minutes before the charge is a receipt with extra steps rather than a
chance to move a card or say no; and the notice and the receipt are two
messages to one person, so sending both on one day doubles the busiest day's
volume, which is what a relay's daily allowance runs out of first (§8.5).

The refusal holds the *release*. One order can still be charged on its own
from **Orders**, which is the way round for the member who rings up and asks
to be taken now. A release nobody was told about is not held back at all: the
gap follows a notice, and sending none is a different decision. The wait is
`RELEASE_NOTICE_GAP_HOURS`, 24 by default, and 0 turns it off.

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

**Ice packs** are the winery's cost and never a member's. A release starts
with them on if it ships between April and October and off otherwise, and the
button under the table changes it — the calendar cannot know what the forecast
will be at the far end. Switching it changes only what the winery pays; the
figure beside each member's name does not move.

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

Do the items in §7.1b **first**. Without them the connection works and the
first push fails.

1. At [developer.intuit.com](https://developer.intuit.com) → *My Apps* → create
   an app with the **Accounting** scope.
2. Register **exactly** this redirect URI on the Intuit side:
   `https://<your domain>/admin/accounting/callback` — the Books page prints it
   for you to copy. Intuit has **two separate boxes**, one under Development
   and one under Production; register it in the one whose keys you are about
   to use, and in both if you will use both.
3. Put `QBO_CLIENT_ID` and `QBO_CLIENT_SECRET` in `.env`, with
   `QBO_ENVIRONMENT=sandbox` to practise or `production` for the real books.
   `docker compose up -d` to pick them up.
4. On **Books** → **"Connect to QuickBooks"**. Intuit asks for consent, you
   choose the company, and it comes back. That is all: the company is stored.

**Two sets of keys that do not cross over.** Intuit issues Development keys and
Production keys separately. Development keys only reach a sandbox company,
production keys only reach the real one, and `QBO_ENVIRONMENT` has to say which
pair you pasted. Mixing them gives an authentication failure that reads like a
wrong password and is not one.

**Production keys are not just there for the taking.** Intuit asks for an app
assessment questionnaire and approves the app before releasing them — for a
private app used by one winery as much as for a published one. Start that early
if there is a date to hit.

### 7.1a When Intuit says the redirect_uri is invalid

> *The redirect_uri query parameter value is invalid. Make sure it is listed in
> the Redirect URIs section on your app's keys tab and matches it exactly.*

The address the service sent is not character-for-character the one registered.
Intuit compares the whole string, so these all fail:

| What differs | What it looks like |
| --- | --- |
| A trailing slash on `PUBLIC_BASE_URL` | `https://site.com//admin/accounting/callback`, with two slashes |
| `http` against `https` | Production **must** be `https` |
| The wrong box at Intuit | Registered under Development, connecting with production keys |
| The domain | `warmspringscellar.com` against `warmspringscellars.com` — the winery's name is singular |
| An IP address or a port | Intuit refuses an IP, and the address must be the real domain |
| Ours is not on the list at all | A new app arrives with only Intuit's OAuth Playground URI registered, which looks like a filled-in list |

**Read the exact string off the Books page and paste that**, rather than typing
it. It is printed under the Connect button, and it is literally what the
service will send.

A new app comes with `https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl`
already in the list — Intuit's own testing tool. It can stay; the winery's URI
is **added** beside it with *Add URI*. Up to 25 are allowed.

**To see what Intuit actually received**, look at the address bar of the page
showing the error: it carries the `redirect_uri=` parameter the service sent,
URL-encoded (`https%3A%2F%2F…`). Compare that against the list under *Keys &
credentials → Redirect URIs*, remembering that each of Intuit's two sections
keeps its own list.

### 7.1b The two items, which have to exist first

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

> **Not available to this winery, and it is not a setting anybody can turn
> on.** `wineclub@warmspringscellar.com` is a **shared mailbox**. A shared
> mailbox has no password and cannot sign in, so there is nothing for
> `SMTP_PASSWORD` to hold. Everything below describes an ordinary licensed
> mailbox and is kept for the day the club uses one. **Go to §8.3.**
>
> The failure, if somebody tries anyway, is `535 5.7.3` — the tenant refusing
> the sign-in itself — and it looks exactly like a wrong password. It is not.


```
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USER=wineclub@warmspringscellar.com
SMTP_PASSWORD=<that mailbox's password>
MAIL_FROM=wineclub@warmspringscellar.com
MAIL_REPLY_TO=wineclub@warmspringscellar.com
```

The mailbox needs an Exchange Online licence — an unlicensed shared mailbox
cannot authenticate — and the tenant needs **SMTP AUTH** enabled for it. If the
account has MFA, a person's password will not do: use the service mailbox.

> **The symptom to recognise.** `535 5.7.139 Authentication unsuccessful, the
> request did not meet the criteria to be authenticated successfully. Contact
> your administrator.` Almost always it means basic authentication is disabled
> for that mailbox, not that the password is wrong. That is the cue for §8.3.

### 8.3 Microsoft 365 with OAuth2

**This is the only route for this winery.** `wineclub@warmspringscellar.com`
is a shared mailbox, so §8.2 is closed: there is no password to put anywhere.

It is also the route that lasts. Microsoft's timetable, as revised in January
2026, leaves basic authentication for SMTP submission working through
**December 2026**; after that it is disabled by default on existing tenants —
an administrator can still switch it back on — and unavailable on new ones,
with the final removal date to be announced in the second half of 2027. No
retirement notice stands against OAuth2. So this is not a workaround for a
missing password; it is where the club should end up regardless.

The permission name, the token scope and the SASL string the adapter sends are
all verified against Microsoft's two reference pages, *Authenticate an IMAP,
POP or SMTP connection using OAuth* and *Enable or disable authenticated
client SMTP submission*. They match exactly.

**Two things those pages do not settle, which this winery runs into:**

* **Whether an unlicensed shared mailbox can do this at all.** Neither page
  says. Shared mailboxes carry no licence by default; the pages state no
  licence requirement for SMTP AUTH and no exemption either. If every step
  below is right and sending is still refused, assign an Exchange Online
  licence to that mailbox and try again. That is the first thing to try, not
  the last, and it is an assignment in the admin centre rather than a code
  problem.
* **A shared mailbox with an app-only token.** Microsoft's shared-mailbox
  instruction — put the shared mailbox's address in the SASL `user=` field —
  sits in the *delegated* half of its page, where a person has signed in. It
  is not repeated for client credentials. The adapter does precisely that
  with `SMTP_USER`, and it is the only shape that can work, but treat it as
  reasoned rather than quoted.

It is done once, and it needs a tenant administrator. **Three different
identifiers appear below and they are not interchangeable**; each step says
which one it wants.

Steps 1 to 3 are done in the Entra portal. **Steps 4 and 5 have no portal
equivalent at all**: Microsoft's documentation says the service principal
*must* be registered through Exchange Online PowerShell and gives no web route
for either cmdlet, so there is no admin-centre setting to go looking for. Step
6 does have a documented web path, but only for accounts under **Users →
Active users**, where a shared mailbox is not listed. Do steps 4, 5 and 6 in
one session and the question does not arise.

That session needs nothing installed. Open **shell.azure.com**, choose
PowerShell, and:

```powershell
Install-Module ExchangeOnlineManagement -Scope CurrentUser -Force
Connect-ExchangeOnline -Organization warmspringscellar.com -Device
```

`-Device` prints a code to enter at **microsoft.com/devicelogin**, because a
browser-based shell cannot open the usual sign-in window — without it, an
account with MFA is exactly where a browser-only attempt stalls. Microsoft does
not document Cloud Shell for this, so treat it as a convenience rather than a
supported path: recent module versions want PowerShell 7.6 or newer (check
`$PSVersionTable` first), and a WAM error on sign-in needs a *fresh* session
with `-DisableWAM`, since re-running the command once an auth context is set
does nothing. PowerShell 7 installed locally works the same way — on macOS and
Linux as well as Windows — and needs no `-Device`.

1. **Entra ID → App registrations → New registration.** Name it `Warm Springs
   Cellar — club mail`. No redirect URI: this application never signs in as
   anybody. From the Overview page keep the **Application (client) ID** and
   the **Directory (tenant) ID**.
2. **Certificates & secrets → New client secret.** Copy the *Value* — not the
   Secret ID — there and then; it is never shown again. Note its expiry date
   too, because the day it lapses the mail stops going out. Microsoft allows
   at most 24 months, so this is a diary entry, not a one-off.
3. **API permissions → Add a permission → APIs my organization uses →
   Office 365 Exchange Online → Application permissions → `SMTP.SendAsApp`**,
   then **Grant admin consent**. Without the consent a token is still issued
   and the server refuses it, which is the most confusing way to fail.
4. **Register the application with Exchange**, in Exchange Online PowerShell:

   ```powershell
   Connect-ExchangeOnline -Organization warmspringscellar.com

   New-ServicePrincipal -AppId <Application (client) ID> `
       -ObjectId <Enterprise applications Object ID> `
       -DisplayName "Warm Springs Cellar - club mail"
   ```

   > **The one mistake that costs an afternoon.** That `-ObjectId` is the one
   > on **Entra ID → Enterprise applications →** this app **→ Overview**, and
   > *not* the Object ID on its **App registrations** page. The two sit on
   > near-identical screens. Microsoft's own wording: *"Using the incorrect
   > Object ID will cause an authentication failure."* The failure says
   > nothing about an id — it arrives as `535 5.7.3`, which reads like a
   > credential problem and is not one.

5. **Give that principal the mailbox**, in the same session:

   ```powershell
   $sp = Get-ServicePrincipal -Identity "Warm Springs Cellar - club mail"
   Add-MailboxPermission -Identity wineclub@warmspringscellar.com `
       -User $sp.Identity -AccessRights FullAccess
   ```

   Read the id back with `Get-ServicePrincipal` rather than pasting it: the
   value `Add-MailboxPermission` wants is a **third** id, different again from
   the two in step 4. Microsoft flags this itself — *"Different IDs are used
   during creation of the Exchange service principal and also later when
   granting mailbox permissions."* Letting PowerShell carry it across removes
   the choice.

   `FullAccess` is what the page documents for this flow. If the club ever
   needs to send as a *different* address from the one it signs in as, that is
   `Add-RecipientPermission` and a separate grant.

6. **Enable SMTP AUTH for that mailbox.** This step is easy to miss and is the
   likeliest reason a correct configuration still will not send:

   ```powershell
   # What is the organisation-wide setting today?
   Get-TransportConfig | Format-List SmtpClientAuthenticationDisabled

   # Turn it on for this one mailbox, whatever that setting says.
   Set-CASMailbox -Identity wineclub@warmspringscellar.com `
       -SmtpClientAuthenticationDisabled $false
   Get-CASMailbox -Identity wineclub@warmspringscellar.com |
       Format-List SmtpClientAuthenticationDisabled
   ```

   The per-mailbox value takes precedence over the organisation's: `$null`
   means *follow the organisation*, while `$true` or `$false` overrides it.
   **Leave `Set-TransportConfig` alone.** Microsoft's own recommendation is to
   keep SMTP AUTH disabled across the organisation and enable it only on the
   mailboxes that need it, which is exactly what the command above does.

   If **security defaults** are enabled on the tenant, SMTP AUTH is already
   disabled organisation-wide; the per-mailbox `$false` is what gets past
   that. Separately, an **authentication policy** that disables basic
   authentication for SMTP blocks SMTP AUTH "even if you enable the settings"
   — Microsoft does not say whether that reaches OAuth clients too, so if
   everything here is right and authentication is still refused, look for such
   a policy before suspecting the code.

7. **In `.env`:**

   ```
   SMTP_HOST=smtp.office365.com
   SMTP_PORT=587
   SMTP_USER=wineclub@warmspringscellar.com
   SMTP_PASSWORD=
   SMTP_OAUTH_TENANT_ID=<Directory (tenant) ID>
   SMTP_OAUTH_CLIENT_ID=<Application (client) ID>
   SMTP_OAUTH_CLIENT_SECRET=<the Value from step 2>
   ```

   `SMTP_USER` **stays set**. The token says which application is sending,
   never from which mailbox; the mailbox has to be named as well. Leave
   `SMTP_OAUTH_SCOPE` at its default — the adapter asks for
   `https://outlook.office365.com/.default`, which is the resource SMTP
   submission expects. A token for Microsoft Graph is a valid token that the
   SMTP endpoint refuses, and that is another confusing hour.

Then restart and open **`/admin/mail`** on the desk. It reads the settings back
without touching the network, and offers a connection test that connects,
encrypts, authenticates and hangs up without sending anything. What you are
looking for from the server is `235 2.7.0 Authentication successful`; the
failure to recognise is `535 5.7.3 Authentication unsuccessful`, and by this
point its likeliest causes are, in order: step 6 not done, the wrong Object ID
in step 4, or admin consent not granted in step 3.

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

### 8.5 Outside Microsoft 365: a transactional provider

The three options above all end at the same two gates: a tenant administrator,
and a mailbox that has no password. When neither is available, the service
needs nothing from Microsoft at all — it speaks plain SMTP, so any provider
that issues SMTP credentials is a drop-in. No code changes; four settings and a
restart.

**SMTP2GO** is what the club uses. Free at this volume, no card, no expiry.

```
SMTP_HOST=mail.smtp2go.com
SMTP_PORT=587
SMTP_USER=<an SMTP user created in the dashboard, NOT the login>
SMTP_PASSWORD=<that user's password>
SMTP_STARTTLS=true
MAIL_FROM=wineclub@warmspringscellar.com
MAIL_REPLY_TO=wineclub@warmspringscellar.com
```

Leave every `SMTP_OAUTH_*` empty: a token provider takes precedence over the
password, and none is wanted here. If the host blocks 587, SMTP2GO also answers
STARTTLS on 2525 and 8025; 465, 8465 and 443 are implicit TLS, which this
service does not speak.

Replies are unaffected. SMTP2GO carries outgoing mail only; incoming mail keeps
arriving at the Microsoft 365 shared mailbox as before.

**The DNS is the part worth getting right, and it is less work than it looks.**
Verify the sending domain in the provider's dashboard and publish the CNAMEs it
gives — a DKIM selector and a return-path. **Do not add `include:` to the SPF
record.** SPF is checked against the envelope sender, not the address in the
From line, and the return-path CNAME makes that envelope sender a subdomain of
this domain pointed at the provider. The `-all` in §8.6 is evaluated against
that subdomain's own record, never against the root's, so it does not apply.

What a correct result looks like, from the first message that went out this
way — worth keeping, because three different things pass here for three
different reasons:

```
spf=pass    smtp.mailfrom=em1196132.warmspringscellar.com
dkim=pass   (signature was verified) header.d=warmspringscellar.com
dmarc=pass  action=none header.from=warmspringscellar.com
compauth=pass reason=100
```

The envelope sender is a subdomain of ours, so SPF passes **and** aligns. DKIM
is signed `d=warmspringscellar.com` rather than for the provider, which is what
the domain verification buys. DMARC then passes because either of those aligns
with the From domain. `compauth=pass reason=100` is Microsoft's best answer:
explicit authentication rather than a guess. What makes the mail legitimate for this domain is the DKIM
signature, and that is a CNAME. Adding the include would be unnecessary and
would spend one of the ten DNS lookups an SPF record is allowed. Adding a
*second* SPF record would break everything that works today.

Verify the domain before the first release: until it passes, sending is capped
at 25 an hour, and a release notice to a hundred members would crawl out over
four hours.

**The ceiling.** The club sends three messages per member per release — the
notice, the receipt, the shipping note — across three releases a year. The
annual total is trivial; the busiest *day* is what binds, because the free
plan's limits are 1,000 a month and 200 a day:

| Limit | Members it allows | Why |
| --- | --- | --- |
| 200/day | ~100 | notice and charges on the same day is two per member at once |
| 200/day | ~200 | notice one day, charges the next, is one per member |
| 1,000/month | ~333 | three per member in a release month |

Sending the notices and running the charges are already two separate actions at
the desk, so doing them a day apart doubles the ceiling for nothing — and warns
people before their card is charged, which is better practice regardless.

Past about two hundred members: SMTP2GO's Starter plan, or Amazon SES at $0.10
per thousand with no subscription, which at this volume is pennies a year but
needs an AWS account and a sandbox-exit request.

**One thing left unconfirmed.** Sources disagree on what a free account does at
1,000 in a month: the provider's blog says messages are queued until the cycle
renews, two third-party write-ups say sending stops. A stop during a release
month means somebody Stripe has already charged never gets a receipt. Worth
asking their support before the first release rather than finding out.

### 8.6 Arriving, not merely leaving

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

### 8.7 Test before promising: the **Mail** tab

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
> that arrives. That is SPF, DKIM and somebody's spam filter (§8.6), and only
> a real send to a real address answers it.

If something still does not add up: `docker compose logs api | grep -i smtp`.
The adapter logs the refusal as the server worded it, code included.

### 8.8 The campaign, which is not this

Everything above is the club's own mail: one message, to one person, because
something happened to their order. **The campaign is a different thing** — the
same letter to several hundred people who are not members yet — and it does
not go out through any of it, or through this service at all. It goes out
from a **mailing platform**, which holds the list, sends the letter and
reports what happened.

The plan, the provider and the reasons are in **`mail-templates/CAMPAIGN.md`**
and are not repeated here. What matters at the desk is four things:

1. **The Campaigns tab does not send and does not count.** Delivered, opened,
   unsubscribed — that is the platform's screen. What a wave actually
   *produced* is counted here, in **Applications**, filtered by referral:
   every wave's link carries its own `src`, and that is the only number that
   says whether the letter worked.

2. **Before a wave is uploaded, subtract the list.** Download the CSV from
   the Campaigns tab and remove those addresses from the file first. The
   platform cannot do this: it does not know who told Deerfield to stop
   writing years ago, and it does not know about the person who telephoned
   last week.

3. **After a wave, bring the opt-outs back.** Export the platform's
   unsubscribes and hard bounces and paste them into the Campaigns tab. If
   nobody does, those opt-outs live only inside a subscription, and the day
   the winery changes provider they are gone — while the obligation to honour
   them is not.

4. **Unsubscribing is not cancelling.** A member who comes off the letters
   still gets their invoices and their shipping notices — those are receipts,
   not marketing, and nothing about the suppression list touches them. A
   member who wants to *leave the club* is a cancellation on the **Members**
   tab, which is a different screen and a different conversation.

The rest — which provider, what to tell them before uploading anything, the
waves and what to watch between them — is in `CAMPAIGN.md` and in
`mail-templates/INVITATIONS.md`.

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
      address outside the tenant (§8.7). On Microsoft 365, with OAuth2 (§8.3)
      rather than a password
- [ ] SPF, DKIM and DMARC published for `warmspringscellar.com` (§8.6)
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
