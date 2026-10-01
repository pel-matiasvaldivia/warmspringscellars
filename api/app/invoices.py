"""Invoice rendering.

An invoice is a record, not a view: once issued the row never changes, and the
PDF is written once and kept. A correction is a credit note and a new number,
never an edit — which is the whole reason the number comes from a sequence
nothing can reuse.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

WINE_RED = (0.447, 0.184, 0.227)      # #722f3a
INK = (0.173, 0.118, 0.141)
MUTED = (0.52, 0.47, 0.49)

SELLER = [
    "Warm Springs Cellars",
    "Kenwood, Sonoma Valley, California",
    "club@warmspringscellars.com · (707) 555-1982",
]


def money(cents: int, currency: str = "usd") -> str:
    symbol = "$" if currency.lower() == "usd" else ""
    return f"{symbol}{cents / 100:,.2f}"


def render_pdf(path: Path, *, invoice: sqlite3.Row, order: sqlite3.Row,
               member: sqlite3.Row, lines: list[sqlite3.Row]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = pdfcanvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    left = 22 * mm
    right = width - 22 * mm
    y = height - 28 * mm

    c.setFillColorRGB(*WINE_RED)
    c.setFont("Times-Roman", 19)
    c.drawString(left, y, "Warm Springs Cellars")
    c.setFillColorRGB(*MUTED)
    c.setFont("Helvetica", 8)
    c.drawRightString(right, y + 4, "INVOICE")
    c.setFont("Times-Roman", 13)
    c.setFillColorRGB(*INK)
    c.drawRightString(right, y - 11, invoice["number"])

    y -= 10 * mm
    c.setStrokeColorRGB(*WINE_RED)
    c.setLineWidth(0.6)
    c.line(left, y, right, y)

    # ── parties ──
    y -= 10 * mm
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(*MUTED)
    c.drawString(left, y, "FROM")
    c.drawString(left + 85 * mm, y, "BILLED TO")
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(*INK)
    for i, row in enumerate(SELLER):
        c.drawString(left, y - 6 * mm - i * 4.6 * mm, row)

    member_lines = [
        f"{member['first_name']} {member['last_name']}",
        member["email"],
        " ".join(x for x in [member["ship_city"], member["ship_state"],
                             member["ship_postcode"]] if x) or member["ship_state"],
    ]
    for i, row in enumerate(member_lines):
        c.drawString(left + 85 * mm, y - 6 * mm - i * 4.6 * mm, row)

    # ── meta ──
    y -= 28 * mm
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(*MUTED)
    for i, (label, value) in enumerate([
        ("ISSUED", invoice["issued_at"][:10]),
        ("ORDER", order["reference"]),
        ("STATUS", str(order["status"]).replace("_", " ").title()),
    ]):
        x = left + i * 58 * mm
        c.setFillColorRGB(*MUTED)
        c.drawString(x, y, label)
        c.setFillColorRGB(*INK)
        c.setFont("Helvetica", 9.5)
        c.drawString(x, y - 5 * mm, value)
        c.setFont("Helvetica", 7.5)

    # ── lines ──
    y -= 16 * mm
    c.setFillColorRGB(*MUTED)
    c.setFont("Helvetica", 7.5)
    c.drawString(left, y, "DESCRIPTION")
    c.drawRightString(right - 62 * mm, y, "QTY")
    c.drawRightString(right - 31 * mm, y, "UNIT")
    c.drawRightString(right, y, "AMOUNT")
    y -= 2.5 * mm
    c.setStrokeColorRGB(0.85, 0.82, 0.80)
    c.setLineWidth(0.4)
    c.line(left, y, right, y)

    currency = invoice["currency"]
    c.setFillColorRGB(*INK)
    for line in lines:
        y -= 7.5 * mm
        c.setFont("Helvetica", 9.5)
        c.drawString(left, y, str(line["description"]))
        c.drawRightString(right - 62 * mm, y, str(line["quantity"]))
        c.drawRightString(right - 31 * mm, y, money(int(line["unit_cents"]), currency))
        c.drawRightString(right, y, money(int(line["total_cents"]), currency))

    # ── totals ──
    y -= 6 * mm
    c.setStrokeColorRGB(0.85, 0.82, 0.80)
    c.line(right - 72 * mm, y, right, y)

    totals = [("Subtotal", int(order["subtotal_cents"]))]
    if int(order["discount_cents"]):
        totals.append(("Discount", -int(order["discount_cents"])))
    if int(order["shipping_cents"]):
        totals.append(("Shipping", int(order["shipping_cents"])))
    # Shown even at zero, so nobody assumes it was forgotten rather than
    # deliberately not computed yet. See FLOW.md.
    totals.append(("Tax", int(order["tax_cents"])))

    for label, amount in totals:
        y -= 6 * mm
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(*MUTED)
        c.drawRightString(right - 31 * mm, y, label)
        c.setFillColorRGB(*INK)
        c.drawRightString(right, y, money(amount, currency))

    y -= 8 * mm
    c.setFont("Times-Bold", 12)
    c.setFillColorRGB(*WINE_RED)
    c.drawRightString(right - 31 * mm, y, "Total")
    c.drawRightString(right, y, money(int(order["total_cents"]), currency))

    # ── foot ──
    c.setFont("Helvetica", 7.5)
    c.setFillColorRGB(*MUTED)
    c.drawString(left, 22 * mm,
                 "Tax is not yet computed on this invoice. Sales tax on direct wine "
                 "shipments depends on the destination.")
    c.drawString(left, 17 * mm,
                 "You must be 21 or older to purchase. An adult signature is required "
                 "on delivery. Please drink responsibly.")

    c.showPage()
    c.save()
    return path


def ensure_pdf(db, invoice_dir: Path, invoice_id: int) -> Path:
    """Render the PDF if it is not on disk yet, and record where it went."""
    invoice = db.one("SELECT * FROM invoice WHERE id = ?", (invoice_id,))
    if invoice is None:
        raise ValueError(f"no invoice {invoice_id}")

    if invoice["pdf_path"]:
        existing = Path(invoice["pdf_path"])
        if existing.exists():
            return existing

    order = db.one('SELECT * FROM "order" WHERE id = ?', (invoice["order_id"],))
    member = db.one("SELECT * FROM membership WHERE id = ?", (order["membership_id"],))
    lines = db.all("SELECT * FROM order_line WHERE order_id = ? ORDER BY id",
                   (invoice["order_id"],))

    path = invoice_dir / f"{invoice['number']}.pdf"
    render_pdf(path, invoice=invoice, order=order, member=member, lines=lines)
    with db.transaction() as con:
        con.execute("UPDATE invoice SET pdf_path = ? WHERE id = ?", (str(path), invoice_id))
    return path
