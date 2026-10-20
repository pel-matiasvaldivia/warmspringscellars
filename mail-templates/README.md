# What answers the club's inbox

`club@warmspringscellar.com` is published in the page footer, in the form's
own error messages, and as the Reply-To on every mail the club sends. People
write to it. Nothing in the club service reads that mailbox — the service
only sends — so without this, an enquiry by mail lives or dies in whoever
opens the inbox, and leaves no trace in the desk if nobody does.

Two things should happen when a mail arrives:

| | |
| --- | --- |
| **To the sender** | `auto-reply.html` — a welcome, and the link to the form, which is where the platform takes over |
| **To Cecilia** | a short notice that somebody wrote, so she can answer personally if it is worth it |

## Do not use Automatic Replies

The obvious tool is the mailbox's own **Automatic replies** (Out of Office),
and it is the wrong one. It answers a given sender **only once** while it is
switched on — by design, to stop mail loops — so the second person to write
from the same address gets nothing, and the message reads as an out-of-office
rather than a welcome. ([Microsoft Q&A][q1], [Microsoft Q&A][q2])

Two mechanisms do answer every message:

- a **server-side inbox rule** on the mailbox, which replies with a template
  and does not need Outlook running; or
- a **Power Automate flow**, which handles each message separately and can do
  both jobs — the reply and the notice to Cecilia — in one place.
  ([Microsoft Learn][pa], [vendor walkthrough][itb])

The flow is the one worth building, because it is the only one that covers
both halves without a second rule somewhere else.

## The flow, in outline

Names in the Power Automate designer move between versions, so take these as
the shape rather than as exact labels, and confirm each step in the tenant.

1. **Trigger** — *When a new email arrives in a shared mailbox*, with the
   mailbox set to `club@warmspringscellar.com`. Microsoft's own training
   material lists this trigger for shared mailboxes. ([Microsoft Learn][pa])
2. **Reply** — reply to the sender with the rendered `auto-reply.html`. Set
   the *Original Mailbox Address* field in the action's advanced options to
   the shared mailbox; it is not marked required and the action fails without
   it. ([community thread][pafail])
3. **Notify** — send `cecilia.valdivia@warmspringscellar.com` the notice
   below, with the sender, the subject and the time filled from the trigger.

Two conditions worth adding to the trigger before it goes live, or Cecilia
gets a notice for every newsletter the mailbox receives:

- skip messages whose own headers mark them as automatic replies or bulk mail;
- skip anything from inside the tenant, so the club's own mail to itself does
  not start a conversation with itself.

### The notice to Cecilia

Plain text on purpose: it is an internal alert, not a piece of the brand.

```
Subject: Someone wrote to the club — <From>

A new message reached club@warmspringscellar.com.

  From:     <From>
  Subject:  <Subject>
  Received: <Received Time>

They have already had the automatic reply with the link to the allocation
form, so nothing is required of you. This is only so you can write back
yourself if it is worth a personal answer.

  <Body preview>
```

## What this does not do

It does not put the enquiry into the desk by itself. The flow answers the
sender and tells Cecilia; nothing is written to the database, so until someone
acts there is no record in `/admin` and nothing in the audit log.

Closing that is a person's job and takes a minute: **Applications → "Add one
by hand"** on the desk, with *How it reached us* set to **Wrote to the club**.
The enquiry then sits in the same queue as a request from the site and is
approved the same way. Worth doing even though the automatic reply has already
sent them to the form — some people never fill it in, and a row at the desk is
what stops the lead from living only in an inbox. See §3.1b of OPERATIONS.md.

## Why the message is written the way it is

Outlook on Windows renders mail through Word, which ignores flexbox, grid,
`<style>` blocks, classes and margins on block elements. So `auto-reply.html`
is tables and inline styles and nothing else, which is also what Outlook on
the web, Apple Mail and Gmail all accept.

There is **no image in it**, deliberately. Outlook blocks remote images until
the reader allows them, and a first message whose entire branding is one image
arrives as an empty box from a winery they have not heard back from yet. The
name is set in type, in Georgia and Arial, because a mail client has only
what is installed on the reader's machine. (The signature blocks in
`email-signatures/` do carry the logo, because by then the reader has a
message from a person, and the type under it carries the name when the image
does not load.)

The last paragraph — "if you were writing about something else" — is there
because this address is on the page for every kind of enquiry, not only for
people who want an allocation. Without it, somebody asking about a visit gets
what reads as a brush-off.

[q1]: https://learn.microsoft.com/en-us/answers/questions/1215109/shared-mailbox-send-message-to-external-every-time
[q2]: https://learn.microsoft.com/en-us/answers/questions/973859/shared-mailbox-auto-reply-to-selected-messages
[pa]: https://learn.microsoft.com/en-us/training/modules/best-practices-email/1-introduction
[itb]: https://theitbros.com/send-outlook-automatic-replies-using-power-automate/
[pafail]: https://learn.microsoft.com/en-us/answers/questions/70442/exchange-2013-shared-mailbox-rule-reply-to-message
