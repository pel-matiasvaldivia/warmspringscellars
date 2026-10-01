"""HTTP. Routes authenticate, parse, call one thing in flow.py and render.

No business rules live here. If a handler grows a decision about money or
state, it belongs in flow.py where it can be tested without a request.
"""

from __future__ import annotations

import json
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .catalogue import catalogue
from .config import SHIPPABLE_STATES, settings
from .db import Database, log
from .flow import ALLOWED, Flow, FlowError
from .invoices import ensure_pdf, money
from .mail import build_mailer
from .notify import send_decline, send_invitation, send_receipt, send_shipment_notice
from .payments import WebhookError, build_provider
from .payments.fake import FakeProvider, unb64

TEMPLATES = Path(__file__).resolve().parent / "templates"

db = Database(settings.db_path)
payments = build_provider(settings)
mailer = build_mailer(settings)
flow = Flow(db=db, settings=settings, payments=payments)

templates = Jinja2Templates(directory=str(TEMPLATES))
templates.env.globals["money"] = money

@asynccontextmanager
async def lifespan(_: FastAPI):
    print(f"[club] payments={payments.name} mail={mailer.name} db={settings.db_path}")
    for warning in settings.warnings():
        print(f"[club] NOT CONFIGURED: {warning}")
    yield


app = FastAPI(title="Warm Springs Cellars — club", docs_url=None, redoc_url=None,
              lifespan=lifespan)
security = HTTPBasic(auto_error=False)


# ── a small rate limit on the one unauthenticated write ──────────────────
_hits: dict[str, deque[float]] = defaultdict(deque)
RATE_WINDOW = 3600.0
RATE_MAX = 8


def rate_limited(key: str) -> bool:
    now = time.monotonic()
    bucket = _hits[key]
    while bucket and now - bucket[0] > RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= RATE_MAX:
        return True
    bucket.append(now)
    return False


def client_key(request: Request) -> str:
    # Behind nginx, so the real address is in the forwarded header nginx sets.
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[0].strip() or (request.client.host if request.client else "?"))


# ── admin auth ───────────────────────────────────────────────────────────
def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not settings.admin_password:
        raise HTTPException(503, "ADMIN_PASSWORD is not set, so the desk is closed")
    unauthorized = HTTPException(401, "not you", headers={"WWW-Authenticate": "Basic"})
    if credentials is None:
        raise unauthorized
    user_ok = secrets.compare_digest(credentials.username, settings.admin_user)
    pass_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (user_ok and pass_ok):
        raise unauthorized
    return credentials.username


def admin_page(request: Request, name: str, section: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(
        request, f"admin/{name}",
        {"section": section, "warnings": settings.warnings(),
         "flash": request.query_params.get("done"),
         "tier_names": {t.key: t.name for t in catalogue().tiers},
         "bottles": {t.key: t.bottles for t in catalogue().tiers},
         "title": section.title(), **context},
    )


# ── health & catalogue ───────────────────────────────────────────────────
@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "payments": {"provider": payments.name, "live": payments.live},
        "mail": {"transport": mailer.name, "live": mailer.live},
        "not_configured": settings.warnings(),
    }


@app.get("/api/catalogue")
def api_catalogue() -> dict:
    cat = catalogue()
    return {
        "currency": cat.currency,
        "shipments_per_year": cat.shipments_per_year,
        "tiers": [
            {"key": t.key, "name": t.name, "label": t.label, "bottles": t.bottles,
             "discount_pct": t.discount_pct, "price_cents": t.price_cents,
             "shipping_cents": t.shipping_cents, "invite_only": t.invite_only,
             "featured": t.featured, "summary": t.summary, "benefits": list(t.benefits)}
            for t in cat.tiers
        ],
    }


@app.get("/api/states")
def api_states() -> dict:
    """Where the winery can ship. The form uses it so nobody fills in a page
    they were never going to be allowed to finish."""
    return {"states": sorted(SHIPPABLE_STATES)}


# ── the landing page form ────────────────────────────────────────────────
@app.post("/api/applications")
async def create_application(request: Request) -> JSONResponse:
    if rate_limited(client_key(request)):
        return JSONResponse(
            {"ok": False, "error": "That is a lot of requests from one place. "
                                   "Write to club@warmspringscellars.com instead."},
            status_code=429,
        )

    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
    else:
        data = dict(await request.form())

    try:
        app_id = flow.create_application(data)
    except FlowError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    return JSONResponse({"ok": True, "application_id": app_id}, status_code=201)


# ── the member's offer ───────────────────────────────────────────────────
def offer_problem(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "message.html",
        {"eyebrow": "Your invitation", "heading": "This link is not open",
         "body": message, "cta_url": "/", "cta_label": "Back to the cellar"},
        status_code=410,
    )


@app.get("/club/offer/{token}", response_class=HTMLResponse)
def show_offer(request: Request, token: str) -> HTMLResponse:
    try:
        offer, application, tiers = flow.open_offer(token)
    except FlowError as exc:
        return offer_problem(request, str(exc))

    return templates.TemplateResponse(
        request, "offer.html",
        {"token": token, "offer": offer, "application": application, "tiers": tiers,
         "catalogue": catalogue(), "expires_on": offer["expires_at"][:10],
         "cancelled": request.query_params.get("checkout") == "cancelled",
         "error": request.query_params.get("error"),
         "payments_live": payments.live},
    )


@app.post("/club/offer/{token}/checkout")
def start_checkout(token: str, tier_key: str = Form(...)) -> RedirectResponse:
    try:
        url = flow.start_checkout(token, tier_key)
    except FlowError as exc:
        return RedirectResponse(f"/club/offer/{token}?error={exc}", status_code=303)
    return RedirectResponse(url, status_code=303)


@app.get("/club/offer/{token}/welcome", response_class=HTMLResponse)
def welcome(request: Request, token: str) -> HTMLResponse:
    """Shown when the member comes back from checkout.

    Deliberately does not create anything: a browser redirect proves nothing
    about whether money moved. The membership is created by the webhook, so
    this page reports what it finds and says so if it finds nothing yet.
    """
    try:
        offer_id = flow.read_offer_token(token)
    except FlowError as exc:
        return offer_problem(request, str(exc))

    offer = db.one("SELECT * FROM offer WHERE id = ?", (offer_id,))
    membership = None
    tier = None
    if offer is not None:
        membership = db.one(
            "SELECT * FROM membership WHERE application_id = ? ORDER BY id DESC LIMIT 1",
            (offer["application_id"],),
        )
        if membership is not None:
            tier = catalogue().get(membership["tier_key"])

    return templates.TemplateResponse(
        request, "welcome.html", {"membership": membership, "tier": tier},
    )


# ── sandbox checkout, only when there is no real provider ────────────────
@app.get("/club/sandbox/checkout", response_class=HTMLResponse)
def sandbox_checkout(request: Request, d: str, s: str) -> HTMLResponse:
    if payments.live:
        raise HTTPException(404)
    body = json.loads(unb64(d))
    return templates.TemplateResponse(
        request, "message.html",
        {"eyebrow": "Sandbox checkout",
         "heading": f"{money(body['amount_cents'], body['currency'])} — {body['description']}",
         "body": "There are no payment credentials on this installation, so this page "
                 "stands in for the provider's. Confirming it runs exactly the same "
                 "webhook the real provider would, and no money moves.",
         "cta_url": f"/club/sandbox/confirm?d={d}&s={s}",
         "cta_label": "Confirm payment"},
    )


@app.get("/club/sandbox/confirm")
async def sandbox_confirm(d: str, s: str) -> RedirectResponse:
    if payments.live or not isinstance(payments, FakeProvider):
        raise HTTPException(404)
    body = json.loads(unb64(d))
    payload, signature = payments.sandbox_delivery(
        body["session_id"], int(body["amount_cents"]), body["currency"], body["metadata"],
    )
    await handle_payment(payload, signature)
    return RedirectResponse(body["success_url"], status_code=303)


# ── webhook ──────────────────────────────────────────────────────────────
async def handle_payment(payload: bytes, signature: str | None) -> int | None:
    event = payments.parse_webhook(payload, signature)
    if event is None:
        return None

    membership_id = flow.accept_payment(event)
    if membership_id is None:          # a retry of something already handled
        return None

    member = db.one("SELECT * FROM membership WHERE id = ?", (membership_id,))
    order = db.one('SELECT * FROM "order" WHERE membership_id = ? ORDER BY id DESC LIMIT 1',
                   (membership_id,))
    invoice = db.one("SELECT * FROM invoice WHERE order_id = ?", (order["id"],))
    tier = catalogue().get(member["tier_key"])

    try:
        ensure_pdf(db, settings.invoice_dir, int(invoice["id"]))
    except Exception as exc:                                   # noqa: BLE001
        # A PDF that will not render must not undo a payment that already
        # happened; the invoice row is the record and the file can be rebuilt.
        with db.transaction() as con:
            log(con, "invoice", int(invoice["id"]), "pdf_failed", error=str(exc))

    try:
        sent = send_receipt(mailer, member, order, invoice, tier, settings.mail_reply_to)
        with db.transaction() as con:
            log(con, "membership", membership_id, "receipt_sent", to=sent)
    except Exception as exc:                                   # noqa: BLE001
        with db.transaction() as con:
            log(con, "membership", membership_id, "receipt_failed", error=str(exc))

    return membership_id


@app.post("/api/payments/webhook")
async def payments_webhook(request: Request) -> Response:
    payload = await request.body()
    signature = request.headers.get("stripe-signature") or request.headers.get("x-signature")
    try:
        await handle_payment(payload, signature)
    except WebhookError as exc:
        # 400 so the provider marks the delivery failed and retries, rather
        # than 200 which would silently drop a real payment.
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except FlowError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    return JSONResponse({"ok": True})


# ── admin ────────────────────────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin_applications(request: Request, actor: str = Depends(require_admin)) -> HTMLResponse:
    rows = db.all(
        "SELECT * FROM application ORDER BY (status != 'pending'), created_at DESC LIMIT 200"
    )
    return admin_page(request, "applications.html", "applications", rows=rows)


@app.get("/admin/applications/{app_id}", response_class=HTMLResponse)
def admin_application(request: Request, app_id: int,
                      actor: str = Depends(require_admin)) -> HTMLResponse:
    application = db.one("SELECT * FROM application WHERE id = ?", (app_id,))
    if application is None:
        raise HTTPException(404)
    offers = db.all("SELECT * FROM offer WHERE application_id = ? ORDER BY id DESC", (app_id,))
    events = db.all(
        "SELECT * FROM event WHERE (subject = 'application' AND subject_id = ?)"
        " OR (subject = 'offer' AND subject_id IN (SELECT id FROM offer WHERE application_id = ?))"
        " ORDER BY id DESC LIMIT 60", (app_id, app_id),
    )
    return admin_page(
        request, "application.html", "applications",
        app=application, offers=offers, events=events,
        offer_urls={o["id"]: flow.offer_url(int(o["id"])) for o in offers},
        selectable=catalogue().selectable,
    )


@app.post("/admin/applications/{app_id}/approve")
async def admin_approve(request: Request, app_id: int,
                        actor: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    tiers = [str(t) for t in form.getlist("tiers")]
    try:
        offer_id, url = flow.approve_application(app_id, actor, tiers or None)
    except FlowError as exc:
        raise HTTPException(400, str(exc)) from exc

    application = db.one("SELECT * FROM application WHERE id = ?", (app_id,))
    offer = db.one("SELECT * FROM offer WHERE id = ?", (offer_id,))
    try:
        where = send_invitation(mailer, application, url, offer["expires_at"][:10],
                                settings.mail_reply_to)
        with db.transaction() as con:
            log(con, "offer", offer_id, "invitation_sent", actor=actor, to=where)
        done = f"Invitation sent to {application['email']}."
    except Exception as exc:                                   # noqa: BLE001
        with db.transaction() as con:
            log(con, "offer", offer_id, "invitation_failed", actor=actor, error=str(exc))
        done = f"Approved, but the email did not go out: {exc}. The link is below."
    return RedirectResponse(f"/admin/applications/{app_id}?done={done}", status_code=303)


@app.post("/admin/applications/{app_id}/decline")
async def admin_decline(request: Request, app_id: int,
                        actor: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    reason = str(form.get("reason") or "")
    try:
        flow.decline_application(app_id, actor, reason)
    except FlowError as exc:
        raise HTTPException(400, str(exc)) from exc

    application = db.one("SELECT * FROM application WHERE id = ?", (app_id,))
    try:
        send_decline(mailer, application, settings.mail_reply_to)
        done = "Declined, and they have been told."
    except Exception as exc:                                   # noqa: BLE001
        done = f"Declined, but the email did not go out: {exc}"
    return RedirectResponse(f"/admin/applications/{app_id}?done={done}", status_code=303)


@app.get("/admin/memberships", response_class=HTMLResponse)
def admin_memberships(request: Request, actor: str = Depends(require_admin)) -> HTMLResponse:
    rows = db.all("SELECT * FROM membership ORDER BY id DESC LIMIT 300")
    return admin_page(request, "memberships.html", "memberships", rows=rows,
                      transitions={k: sorted(v) for k, v in ALLOWED["membership"].items()})


@app.post("/admin/memberships/{membership_id}/status")
async def admin_membership_status(request: Request, membership_id: int,
                                  actor: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    try:
        flow.set_membership_status(membership_id, str(form.get("target")), actor)
    except FlowError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse("/admin/memberships?done=Member updated.", status_code=303)


@app.get("/admin/orders", response_class=HTMLResponse)
def admin_orders(request: Request, actor: str = Depends(require_admin)) -> HTMLResponse:
    rows = db.all(
        'SELECT o.*, m.first_name, m.last_name, r.name AS release_name,'
        ' i.number AS invoice_number, i.id AS invoice_id'
        ' FROM "order" o JOIN membership m ON m.id = o.membership_id'
        ' LEFT JOIN "release" r ON r.id = o.release_id'
        ' LEFT JOIN invoice i ON i.order_id = o.id'
        ' ORDER BY o.id DESC LIMIT 300'
    )
    return admin_page(request, "orders.html", "orders", rows=rows)


@app.get("/admin/shipments", response_class=HTMLResponse)
def admin_shipments(request: Request, actor: str = Depends(require_admin)) -> HTMLResponse:
    rows = db.all(
        'SELECT s.*, o.reference, o.tier_key, m.first_name, m.last_name, m.ship_state'
        ' FROM shipment s JOIN "order" o ON o.id = s.order_id'
        ' JOIN membership m ON m.id = o.membership_id'
        " ORDER BY (s.status = 'delivered'), s.id DESC LIMIT 300"
    )
    return admin_page(request, "shipments.html", "shipments", rows=rows,
                      transitions={k: sorted(v) for k, v in ALLOWED["shipment"].items()})


@app.post("/admin/shipments/{shipment_id}/advance")
async def admin_shipment_advance(request: Request, shipment_id: int,
                                 actor: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    target = str(form.get("target"))
    carrier = str(form.get("carrier") or "")
    tracking = str(form.get("tracking") or "")
    reason = str(form.get("reason") or "")
    try:
        flow.advance_shipment(shipment_id, target, actor, carrier, tracking, reason)
    except FlowError as exc:
        raise HTTPException(400, str(exc)) from exc

    done = f"Shipment {shipment_id} is {target.replace('_', ' ')}."
    if target == "in_transit":
        row = db.one(
            'SELECT s.*, o.reference, m.email, m.first_name FROM shipment s'
            ' JOIN "order" o ON o.id = s.order_id JOIN membership m ON m.id = o.membership_id'
            ' WHERE s.id = ?', (shipment_id,),
        )
        try:
            send_shipment_notice(mailer, row, row, carrier or "unknown", tracking,
                                 settings.mail_reply_to)
            done += " The member has the tracking number."
        except Exception as exc:                               # noqa: BLE001
            done += f" The tracking email did not go out: {exc}"
    return RedirectResponse(f"/admin/shipments?done={done}", status_code=303)


@app.get("/admin/releases", response_class=HTMLResponse)
def admin_releases(request: Request, actor: str = Depends(require_admin)) -> HTMLResponse:
    rows = db.all(
        'SELECT r.*, (SELECT COUNT(*) FROM "order" o WHERE o.release_id = r.id) AS order_count'
        ' FROM "release" r ORDER BY r.ships_on DESC'
    )
    return admin_page(request, "releases.html", "releases", rows=rows)


@app.post("/admin/releases")
async def admin_create_release(request: Request,
                               actor: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    flow.create_release(str(form.get("name")), str(form.get("ships_on")))
    return RedirectResponse("/admin/releases?done=Release planned.", status_code=303)


@app.post("/admin/releases/{release_id}/generate")
def admin_generate_orders(release_id: int,
                          actor: str = Depends(require_admin)) -> RedirectResponse:
    created = flow.generate_release_orders(release_id, actor)
    done = f"{len(created)} order(s) raised."
    return RedirectResponse(f"/admin/releases?done={done}", status_code=303)


@app.get("/admin/invoices/{invoice_id}.pdf")
def admin_invoice_pdf(invoice_id: int, actor: str = Depends(require_admin)) -> FileResponse:
    row = db.one("SELECT * FROM invoice WHERE id = ?", (invoice_id,))
    if row is None:
        raise HTTPException(404)
    path = ensure_pdf(db, settings.invoice_dir, invoice_id)
    return FileResponse(path, media_type="application/pdf", filename=f"{row['number']}.pdf")
