"""The queue between the club and the books.

An invoice is queued inside the transaction that issues it, and pushed later.
That split is the whole design, and it is worth being explicit about why:

  * The webhook that takes a member's money must not fail because Intuit is
    rebooting. Money arriving and paperwork arriving are different problems
    with different failure modes.
  * A push that fails has to stay visible. `ledger_task` rows are the record
    of what the books are still owed, which is a question the desk can answer
    at a glance instead of discovering at year end.
  * Retrying has to be free. Every push is keyed on something the ledger can
    recognise — the invoice number — so a second attempt adopts the document
    the first one created rather than writing another.

Payments queue behind their invoice: a payment cannot be linked to an invoice
the ledger has not seen, so `drain` does them in that order and leaves the
payment pending if its invoice has not landed yet.
"""

from __future__ import annotations

import csv
import io
import sqlite3

from ..db import Database, log, now
from .base import AccountingError, Customer, Invoice, Ledger, Line, NotConnected

KINDS = ("invoice", "payment")


def enqueue(con: sqlite3.Connection, kind: str, invoice_id: int) -> None:
    """Queue one document. Called from inside the caller's transaction.

    `INSERT OR IGNORE` against the (kind, subject_id) unique index: generating
    a release's orders twice, or replaying a webhook, must not queue the same
    invoice twice.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown ledger task kind {kind!r}")
    con.execute(
        "INSERT OR IGNORE INTO ledger_task (created_at, kind, subject_id) VALUES (?, ?, ?)",
        (now(), kind, invoice_id),
    )


# ── reading a document out of the club's own tables ──────────────────────
_DOC_SQL = """
SELECT i.id AS invoice_id, i.number, i.issued_at, i.currency, i.total_cents,
       o.id AS order_id, o.reference, o.paid_at, o.provider_payment_id,
       m.email, m.first_name, m.last_name, m.ship_state, m.ship_city
  FROM invoice i
  JOIN "order" o ON o.id = i.order_id
  JOIN membership m ON m.id = o.membership_id
 WHERE i.id = ?
"""


def _customer(row: sqlite3.Row) -> Customer:
    return Customer(
        email=row["email"], first_name=row["first_name"], last_name=row["last_name"],
        state=row["ship_state"] or "", city=row["ship_city"] or "",
    )


def invoice_document(db: Database, invoice_id: int) -> Invoice:
    row = db.one(_DOC_SQL, (invoice_id,))
    if row is None:
        raise AccountingError(f"no invoice with id {invoice_id}")
    lines = db.all(
        "SELECT * FROM order_line WHERE order_id = ? ORDER BY id", (row["order_id"],)
    )
    return Invoice(
        number=row["number"],
        issued_at=row["issued_at"],
        currency=row["currency"],
        total_cents=int(row["total_cents"]),
        customer=_customer(row),
        reference=row["reference"],
        lines=tuple(
            Line(
                description=line["description"],
                quantity=int(line["quantity"]),
                unit_cents=int(line["unit_cents"]),
                total_cents=int(line["total_cents"]),
                # The shipping line is the one the club adds itself, and it has
                # to book somewhere other than wine revenue.
                kind="shipping" if "shipping" in line["description"].lower() else "wine",
            )
            for line in lines
        ),
    )


def payment_document(db: Database, invoice_id: int):
    from .base import Payment

    row = db.one(_DOC_SQL, (invoice_id,))
    if row is None:
        raise AccountingError(f"no invoice with id {invoice_id}")
    if not row["paid_at"]:
        raise AccountingError(f"invoice {row['number']} is not paid, so it has no payment")
    return Payment(
        invoice_number=row["number"],
        amount_cents=int(row["total_cents"]),
        currency=row["currency"],
        paid_at=row["paid_at"],
        customer=_customer(row),
        reference=row["provider_payment_id"] or "",
    )


# ── draining ─────────────────────────────────────────────────────────────
def pending(db: Database, limit: int = 200) -> list[sqlite3.Row]:
    return db.all(
        "SELECT t.*, i.number FROM ledger_task t LEFT JOIN invoice i ON i.id = t.subject_id"
        " WHERE t.status != 'done'"
        # Invoices before payments, because a payment links to its invoice.
        " ORDER BY t.subject_id, (t.kind = 'payment'), t.id LIMIT ?",
        (limit,),
    )


def tasks(db: Database, limit: int = 200) -> list[sqlite3.Row]:
    return db.all(
        "SELECT t.*, i.number FROM ledger_task t LEFT JOIN invoice i ON i.id = t.subject_id"
        # Outstanding first, newest invoice first, and each invoice above the
        # payment that settles it — which is the order they are filed in.
        " ORDER BY (t.status = 'done'), t.subject_id DESC, (t.kind = 'payment') LIMIT ?",
        (limit,),
    )


def _external_invoice_id(db: Database, invoice_id: int) -> str | None:
    row = db.one(
        "SELECT external_id FROM ledger_task WHERE kind = 'invoice' AND subject_id = ?"
        " AND status = 'done'", (invoice_id,),
    )
    return row["external_id"] if row else None


def push_one(db: Database, ledger: Ledger, task: sqlite3.Row, actor: str = "system") -> str:
    """Push a single task. Returns a sentence for the desk.

    Raises nothing: a failure is a recorded state, not an exception for a
    route to render. The desk needs to see which document failed and why.
    """
    task_id = int(task["id"])
    invoice_id = int(task["subject_id"])

    try:
        if task["kind"] == "invoice":
            external = ledger.push_invoice(invoice_document(db, invoice_id))
        else:
            parent = _external_invoice_id(db, invoice_id)
            if not parent:
                return _hold(db, task_id, "its invoice has not reached the ledger yet")
            external = ledger.push_payment(payment_document(db, invoice_id), parent)
    except NotConnected as exc:
        return _hold(db, task_id, str(exc))
    except Exception as exc:                                   # noqa: BLE001
        # Any exception, not just AccountingError: a push that dies on an
        # unexpected shape of response must still leave a readable row behind.
        return _fail(db, task_id, f"{type(exc).__name__}: {exc}")

    with db.transaction() as con:
        con.execute(
            "UPDATE ledger_task SET status = 'done', attempts = attempts + 1,"
            " last_error = NULL, external_id = ?, provider = ?, synced_at = ?"
            " WHERE id = ?", (external, ledger.name, now(), task_id),
        )
        log(con, "invoice", invoice_id, f"ledger_{task['kind']}_pushed", actor=actor,
            provider=ledger.name, external_id=external)
    return f"{task['kind']} {task['number'] or invoice_id} recorded as {external}"


def _hold(db: Database, task_id: int, why: str) -> str:
    """Still pending, not failed: nothing was wrong with the document."""
    with db.transaction() as con:
        con.execute("UPDATE ledger_task SET status = 'pending', last_error = ? WHERE id = ?",
                    (why, task_id))
    return f"held: {why}"


def _fail(db: Database, task_id: int, why: str) -> str:
    with db.transaction() as con:
        con.execute(
            "UPDATE ledger_task SET status = 'failed', attempts = attempts + 1,"
            " last_error = ? WHERE id = ?", (why[:500], task_id),
        )
    return f"failed: {why}"


def drain(db: Database, ledger: Ledger, actor: str = "system", limit: int = 50) -> list[str]:
    """Push everything outstanding. Stops early if the ledger is not connected.

    Stopping early matters: with no connection every task would otherwise be
    walked and held one by one, writing a row per document to say the same
    thing once.
    """
    if not ledger.live or not ledger.status().get("connected"):
        # Asked once, before the loop. Walking every task to hold each one with
        # the same sentence would write a row per document to say one thing.
        return (["QuickBooks is not connected, so nothing was pushed."]
                if pending(db, 1) else [])

    return [push_one(db, ledger, task, actor) for task in pending(db, limit)]


def retry(db: Database, task_id: int) -> None:
    """Put a failed task back in the queue. Attempts are kept, deliberately."""
    with db.transaction() as con:
        con.execute(
            "UPDATE ledger_task SET status = 'pending', last_error = NULL"
            " WHERE id = ? AND status = 'failed'", (task_id,),
        )


# ── the way out that needs no API at all ─────────────────────────────────
EXPORT_COLUMNS = [
    "InvoiceNo", "Customer", "Email", "InvoiceDate", "DueDate", "Terms",
    "Item", "ItemDescription", "ItemQuantity", "ItemRate", "ItemAmount",
    "Currency", "Memo", "Paid", "PaidDate", "PaymentReference",
]


def export_csv(db: Database, only_unsynced: bool = False) -> str:
    """Every invoice as one row per line item, for an import or an accountant.

    QuickBooks Desktop has no REST API, and some bookkeepers would rather see
    a file than grant an app access to the company. Both are served by the same
    export, which is why it exists even though the API adapter works.
    """
    where = ""
    if only_unsynced:
        where = (" WHERE NOT EXISTS (SELECT 1 FROM ledger_task t WHERE t.kind = 'invoice'"
                 " AND t.subject_id = i.id AND t.status = 'done')")
    rows = db.all(
        'SELECT i.*, o.reference, o.paid_at, o.provider_payment_id, o.id AS order_id,'
        ' m.email, m.first_name, m.last_name'
        ' FROM invoice i JOIN "order" o ON o.id = i.order_id'
        ' JOIN membership m ON m.id = o.membership_id' + where + " ORDER BY i.id"
    )

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        lines = db.all("SELECT * FROM order_line WHERE order_id = ? ORDER BY id",
                       (row["order_id"],))
        for line in lines:
            shipping = "shipping" in line["description"].lower()
            writer.writerow([
                row["number"],
                f"{row['first_name']} {row['last_name']}".strip(),
                row["email"],
                row["issued_at"][:10],
                row["issued_at"][:10],          # due on issue: it is paid at issue
                "Due on receipt",
                "Shipping" if shipping else "Wine Club Allocation",
                line["description"],
                line["quantity"],
                f"{line['unit_cents'] / 100:.2f}",
                f"{line['total_cents'] / 100:.2f}",
                row["currency"].upper(),
                row["reference"],
                "Yes" if row["paid_at"] else "No",
                (row["paid_at"] or "")[:10],
                row["provider_payment_id"] or "",
            ])
    return buf.getvalue()
