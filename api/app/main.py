"""HTTP. Routes authenticate, parse, call one thing in flow.py and render.

No business rules live here. If a handler grows a decision about money or
state, it belongs in flow.py where it can be tested without a request.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .accounting import AccountingError, build_ledger
from .accounting import queue as ledger_queue
from .accounting.quickbooks import new_state
from . import campaign
from .catalogue import catalogue
from .config import SHIPPABLE_STATES, UNLICENSED_STATES, settings
from .db import Database, log
from .flow import ALLOWED, Address, Flow, FlowError
from .invoices import ensure_pdf, money
from .mail import build_mailer, check as mail_check
from .notify import (
    send_charge_failed,
    send_decline,
    send_invitation,
    send_application_received,
    send_declines_alert,
    send_receipt,
    send_release_notice,
    send_shipment_notice,
    send_welcome,
)
from .payments import WebhookError, build_provider
from .payments.fake import FakeProvider, unb64
from . import shipping

TEMPLATES = Path(__file__).resolve().parent / "templates"

db = Database(settings.db_path)
payments = build_provider(settings)
mailer = build_mailer(settings)
ledger = build_ledger(settings, db)
flow = Flow(db=db, settings=settings, payments=payments)

templates = Jinja2Templates(directory=str(TEMPLATES))
templates.env.globals["money"] = money

@asynccontextmanager
async def lifespan(_: FastAPI):
    print(f"[club] payments={payments.name} mail={mailer.name} ledger={ledger.name}"
          f" db={settings.db_path}")
    for warning in settings.warnings():
        print(f"[club] NOT CONFIGURED: {warning}")
    keeper = asyncio.create_task(keep_books_connected())
    try:
        yield
    finally:
        keeper.cancel()


KEEPALIVE_EVERY_SECONDS = 24 * 3600


async def keep_books_connected() -> None:
    """Once a day, refresh the accounting connection if its lapse is near.

    Releases are months apart. Without this, the connection would lapse
    between two of them and the next release would queue with nowhere to go.
    Intuit's refresh token expires after a hundred days of not being used,
    and a club that syncs three times a year can cross that on its own.
    """
    keep_alive = getattr(ledger, "keep_alive", None)
    if keep_alive is None:
        return
    while True:
        try:
            if await asyncio.to_thread(keep_alive):
                print(f"[club] {ledger.name} connection refreshed by the keep-alive")
        except Exception as exc:                               # noqa: BLE001
            print(f"[club] {ledger.name} keep-alive failed: {exc}")
        await asyncio.sleep(KEEPALIVE_EVERY_SECONDS)


app = FastAPI(title="Warm Springs Cellar — club", docs_url=None, redoc_url=None,
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
        # The outstanding count is here on purpose: a queue that is quietly
        # growing is the one failure in this service nobody would otherwise
        # notice until an accountant asked.
        "books": {"ledger": ledger.name, "live": ledger.live,
                  "outstanding": len(ledger_queue.pending(db))},
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
             "shipping_credit_cents": t.shipping_credit_cents,
             "invite_only": t.invite_only,
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
def acknowledge(data: dict, subject_id: int, kind: str, waiting_for: str = "") -> None:
    """Write back to whoever just filled the form in.

    Never lets a mail failure fail the request: the row is already saved and
    the browser is owed its answer. A silence here is recorded against the
    row instead, where somebody can see it and write by hand.
    """
    first = str(data.get("first_name") or "").strip() or "there"
    email = str(data.get("email") or "").strip()
    if not email:
        return
    try:
        send_application_received(mailer, first, email, waiting_for,
                                  settings.mail_reply_to)
    except Exception as exc:                                   # noqa: BLE001
        with db.transaction() as con:
            log(con, kind, subject_id, "acknowledgement_failed", error=str(exc))
    else:
        with db.transaction() as con:
            log(con, kind, subject_id, "acknowledged")


@app.post("/api/applications")
async def create_application(request: Request) -> JSONResponse:
    if rate_limited(client_key(request)):
        return JSONResponse(
            {"ok": False, "error": "That is a lot of requests from one place. "
                                   "Write to wineclub@warmspringscellar.com instead."},
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

    acknowledge(data, app_id, "application")
    return JSONResponse({"ok": True, "application_id": app_id}, status_code=201)


@app.post("/api/waiting")
async def join_waiting_list(request: Request) -> JSONResponse:
    """Somebody the winery cannot ship to, asking to be told when it can.

    The form offers this instead of a dead end. Without it the only thing a
    visitor in Alabama is told is no, and a name that would have been worth
    having the day the permit arrives is gone.
    """
    if rate_limited(client_key(request)):
        return JSONResponse(
            {"ok": False, "error": "That is a lot of requests from one place. "
                                   "Write to wineclub@warmspringscellar.com instead."},
            status_code=429,
        )

    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
    else:
        data = dict(await request.form())

    try:
        waiting_id = flow.join_waiting_list(data)
    except FlowError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    acknowledge(data, waiting_id, "waiting",
                waiting_for=str(data.get("state") or "").strip())
    return JSONResponse({"ok": True, "waiting_id": waiting_id}, status_code=201)


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


def address_page(request: Request, token: str, tier, values: dict,
                 error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "address.html",
        {"token": token, "tier": tier, "values": values, "error": error,
         "states": sorted(SHIPPABLE_STATES), "catalogue": catalogue(),
         "payments_live": payments.live},
    )


@app.get("/club/offer/{token}/address", response_class=HTMLResponse)
def show_address(request: Request, token: str, tier: str = "") -> HTMLResponse:
    """Where the wine should go, asked before anyone pays for it.

    Before, because whether we may ship at all depends on the answer: there is
    no sense taking money for a case that cannot be delivered.
    """
    try:
        offer, application, tiers = flow.open_offer(token)
    except FlowError as exc:
        return offer_problem(request, str(exc))
    chosen = next((t for t in tiers if t.key == tier), None)
    if chosen is None:
        return RedirectResponse(f"/club/offer/{token}", status_code=303)

    # What they gave on an earlier run at checkout, or failing that the city
    # and state from their application — which is usually where they live.
    values = {key: offer[key] for key in
              ("ship_line1", "ship_line2", "ship_city", "ship_state", "ship_postcode")}
    values["ship_city"] = values["ship_city"] or application["city"]
    values["ship_state"] = values["ship_state"] or application["state"]
    return address_page(request, token, chosen, values)


@app.post("/club/offer/{token}/checkout")
async def start_checkout(request: Request, token: str) -> Response:
    form = await request.form()
    tier_key = str(form.get("tier_key") or "")
    try:
        offer, _application, tiers = flow.open_offer(token)
    except FlowError as exc:
        return offer_problem(request, str(exc))
    chosen = next((t for t in tiers if t.key == tier_key), None)
    if chosen is None:
        return RedirectResponse(
            f"/club/offer/{token}?error=that allocation was not offered to you",
            status_code=303)

    data = {key: str(value) for key, value in form.items()}
    try:
        address = Address.parse(data)
        url = flow.start_card_setup(token, tier_key, address)
    except FlowError as exc:
        # Back to the form with what they typed. A redirect carrying the error
        # would empty five fields to complain about one of them.
        return address_page(request, token, chosen, data, str(exc))
    return RedirectResponse(url, status_code=303)


@app.get("/club/card/{token}", response_class=HTMLResponse)
def show_card_page(request: Request, token: str) -> HTMLResponse:
    """Where a member whose card was declined puts in another one."""
    try:
        membership_id = flow.read_card_token(token)
    except FlowError as exc:
        return offer_problem(request, str(exc))
    member = db.one("SELECT * FROM membership WHERE id = ?", (membership_id,))
    if member is None:
        return offer_problem(request, "this link is not valid")

    return templates.TemplateResponse(
        request, "message.html",
        {"eyebrow": "Your membership", "heading": f"{member['first_name']}, a new card",
         "body": "The card we had on file was declined when we came to charge this "
                 "season's shipment. Putting in another one takes a minute, and nothing "
                 "is charged on this page — we charge again once the card is saved, "
                 "and your wine goes out with the rest of the release.",
         "cta_url": f"/club/card/{token}/start", "cta_label": "Put in a card"},
    )


@app.get("/club/card/{token}/start")
def start_card_replacement(request: Request, token: str) -> Response:
    try:
        url = flow.start_card_replacement(token)
    except FlowError as exc:
        return offer_problem(request, str(exc))
    return RedirectResponse(url, status_code=303)


@app.get("/club/card/{token}/done", response_class=HTMLResponse)
def card_replaced(request: Request, token: str) -> HTMLResponse:
    """Shown on the way back. Like the welcome page, it reports rather than acts:
    the card is saved by the webhook, not by a browser arriving here."""
    try:
        membership_id = flow.read_card_token(token)
    except FlowError as exc:
        return offer_problem(request, str(exc))
    member = db.one("SELECT * FROM membership WHERE id = ?", (membership_id,))
    saved = bool(member and member["provider_payment_method"])
    return templates.TemplateResponse(
        request, "message.html",
        {"eyebrow": "Your membership",
         "heading": "Thank you" if saved else "Still working on it",
         "body": "We have the new card. Nothing has been charged yet — the cellar "
                 "will run this season's charge again, and you will get the receipt by "
                 "email."
                 if saved else
                 "Our provider has not confirmed the card yet. It is usually seconds. "
                 "If you do not hear from us today, write to wineclub@warmspringscellar.com.",
         "cta_url": "/", "cta_label": "Back to the cellar"},
    )


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
@app.get("/club/sandbox/card", response_class=HTMLResponse)
def sandbox_card(request: Request, d: str, s: str) -> HTMLResponse:
    if payments.live:
        raise HTTPException(404)
    body = json.loads(unb64(d))
    return templates.TemplateResponse(
        request, "message.html",
        {"eyebrow": "Sandbox", "heading": f"Save a card — {body['description']}",
         "body": "There are no payment credentials on this installation, so this page "
                 "stands in for the provider's. Confirming it runs exactly the same "
                 "webhook the real provider would. Nothing is charged here and nothing "
                 "would be charged at the real one either: the card is charged when a "
                 "release ships.",
         "cta_url": f"/club/sandbox/card/confirm?d={d}&s={s}",
         "cta_label": "Save the card"},
    )


@app.get("/club/sandbox/card/confirm")
async def sandbox_card_confirm(d: str, s: str,
                               background: BackgroundTasks) -> RedirectResponse:
    if payments.live or not isinstance(payments, FakeProvider):
        raise HTTPException(404)
    body = json.loads(unb64(d))
    payload, signature = payments.sandbox_delivery(body["session_id"], body["metadata"])
    await handle_card(payload, signature, background)
    return RedirectResponse(body["success_url"], status_code=303)


# ── webhook ──────────────────────────────────────────────────────────────
def push_books() -> None:
    """Drain the ledger queue. Never raises: `push_one` records failures.

    Run after the response, not inside it. A payment confirmation that waited
    on Intuit would be a payment confirmation that Stripe times out on, and a
    timed-out webhook is retried, which is a second chance to get the same
    invoice pushed — not a reason to make the member wait.
    """
    ledger_queue.drain(db, ledger, actor="autosync")


async def handle_card(payload: bytes, signature: str | None,
                      background: BackgroundTasks | None = None) -> int | None:
    """A card the provider has taken and is holding for us.

    Two kinds arrive on the same path: a new member finishing their invitation,
    and an existing one replacing a card that was declined. The metadata on the
    session says which, because only the service that made the session knows.
    """
    event = payments.parse_webhook(payload, signature)
    if event is None:
        return None

    if event.metadata.get("membership_id"):
        return flow.replace_card(event)

    membership_id = flow.accept_card(event)
    if membership_id is None:          # a retry of something already handled
        return None

    member = db.one("SELECT * FROM membership WHERE id = ?", (membership_id,))
    tier = catalogue().get(member["tier_key"])
    try:
        sent = send_welcome(mailer, member, tier, next_release(), settings.mail_reply_to)
        with db.transaction() as con:
            log(con, "membership", membership_id, "welcome_sent", to=sent)
    except Exception as exc:                                   # noqa: BLE001
        with db.transaction() as con:
            log(con, "membership", membership_id, "welcome_failed", error=str(exc))

    return membership_id


def next_release() -> str:
    """The next release a new member will be in, for the welcome letter.

    A name and a date if one is planned, and an honest nothing if not — better
    than a promise the winery has not made yet.
    """
    row = db.one("SELECT name, ships_on FROM \"release\" WHERE ships_on >= date('now')"
                 " AND status != 'cancelled' ORDER BY ships_on LIMIT 1")
    return f"{row['name']} ({row['ships_on']})" if row else ""


@app.post("/api/payments/webhook")
async def payments_webhook(request: Request, background: BackgroundTasks) -> Response:
    payload = await request.body()
    signature = request.headers.get("stripe-signature") or request.headers.get("x-signature")
    try:
        await handle_card(payload, signature, background)
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


# How a hand-entered enquiry reached the winery. The first three are what the
# screen exists for; the rest repeat the website's own list, so the referral
# column can still be counted across both doors.
BY_HAND_REFERRALS = (
    "Wrote to the club",
    "Telephoned",
    "Asked at the winery",
    "Friend or Family",
    "Restaurant or Wine Bar",
    "Wine Event or Festival",
    "Social Media",
    "Press or Publication",
    "Deerfield Ranch Winery",
    "Other",
)


def application_form(request: Request, values: dict, error: str | None = None) -> HTMLResponse:
    return admin_page(request, "application-new.html", "applications",
                      states=sorted(SHIPPABLE_STATES), tiers=catalogue().tiers,
                      referrals=BY_HAND_REFERRALS, values=values, error=error)


# Declared above /admin/applications/{app_id}: routes are matched in order, and
# the other one would take "new" for an id and answer 422.
@app.get("/admin/applications/new", response_class=HTMLResponse)
def admin_application_new(request: Request,
                          actor: str = Depends(require_admin)) -> HTMLResponse:
    return application_form(request, {})


@app.post("/admin/applications/new")
async def admin_application_create(request: Request, actor: str = Depends(require_admin)):
    """An enquiry that arrived by mail or telephone, put into the same queue.

    It calls the function the public form calls, so there is one definition of
    what an application is and one set of rules about it — including the guard
    that stops the same person being entered twice if they later fill in the
    form themselves.
    """
    data = {key: str(value) for key, value in (await request.form()).items()}
    email = data.get("email", "").strip().lower()
    # Asked before the insert, because create_application answers a second
    # pending request from one address with the first one's id. Without this
    # the desk would be told it had added someone it had not.
    open_before = db.one(
        "SELECT id FROM application WHERE email = ? AND status = 'pending'", (email,))
    try:
        app_id = flow.create_application(data)
    except FlowError as exc:
        # Rendered back into the form, not raised: somebody is typing, and an
        # error page throws away what they typed.
        return application_form(request, data, str(exc))

    if open_before and int(open_before["id"]) == app_id:
        done = "That address already had a request open — this is the one that was waiting."
    else:
        # The audit log already holds "received"; this says a person at the
        # desk put it there, and which door it came through.
        with db.transaction() as con:
            log(con, "application", app_id, "entered_by_hand", actor=actor,
                referral=data.get("referral") or "")
        done = "Added by hand. It is in the queue, waiting on a decision."
    return RedirectResponse(f"/admin/applications/{app_id}?done={done}", status_code=303)


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
        tiers=catalogue().tiers,
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


@app.get("/admin/waiting", response_class=HTMLResponse)
def admin_waiting(request: Request, actor: str = Depends(require_admin)) -> HTMLResponse:
    rows = db.all("SELECT * FROM waiting ORDER BY (status != 'waiting'), state, created_at")
    # Grouped by state, because a licence arrives for a state and not for a
    # person: that is the unit the desk acts on.
    states: dict[str, list] = {}
    for row in rows:
        states.setdefault(row["state"], []).append(row)
    return admin_page(
        request, "waiting.html", "waiting", rows=rows,
        states=dict(sorted(states.items())),
        open_now=sorted(SHIPPABLE_STATES & {r["state"] for r in rows if r["status"] == "waiting"}),
        unlicensed=sorted(UNLICENSED_STATES),
    )


@app.post("/admin/waiting/new")
async def admin_waiting_new(request: Request,
                            actor: str = Depends(require_admin)) -> RedirectResponse:
    """What somebody replying to the letter is written down as."""
    form = await request.form()
    try:
        flow.join_waiting_list({k: str(v) for k, v in form.items()})
    except FlowError as exc:
        return RedirectResponse(f"/admin/waiting?done=Not saved: {exc}", status_code=303)
    return RedirectResponse("/admin/waiting?done=On the list.", status_code=303)


@app.post("/admin/waiting/{waiting_id}/invite")
def admin_waiting_invite(waiting_id: int,
                         actor: str = Depends(require_admin)) -> RedirectResponse:
    try:
        app_id = flow.invite_from_waiting(waiting_id, actor)
    except FlowError as exc:
        return RedirectResponse(f"/admin/waiting?done=Not done: {exc}", status_code=303)
    return RedirectResponse(
        f"/admin/applications/{app_id}?done=Off the waiting list and into the queue. "
        "Nothing has been sent to them yet — approving is what writes.", status_code=303)


@app.post("/admin/waiting/{waiting_id}/drop")
def admin_waiting_drop(waiting_id: int,
                       actor: str = Depends(require_admin)) -> RedirectResponse:
    try:
        flow.drop_from_waiting(waiting_id, actor)
    except FlowError as exc:
        return RedirectResponse(f"/admin/waiting?done=Not done: {exc}", status_code=303)
    return RedirectResponse("/admin/waiting?done=Taken off the list.", status_code=303)


@app.get("/admin/memberships", response_class=HTMLResponse)
def admin_memberships(request: Request, actor: str = Depends(require_admin)) -> HTMLResponse:
    rows = db.all("SELECT * FROM membership ORDER BY id DESC LIMIT 300")
    return admin_page(request, "memberships.html", "memberships", rows=rows,
                      states=sorted(SHIPPABLE_STATES),
                      transitions={k: sorted(v) for k, v in ALLOWED["membership"].items()})


@app.post("/admin/memberships/{membership_id}/address")
async def admin_membership_address(request: Request, membership_id: int,
                                   actor: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    try:
        flow.change_address(membership_id,
                            Address.parse({k: str(v) for k, v in form.items()}), actor)
    except FlowError as exc:
        return RedirectResponse(f"/admin/memberships?done=Not saved: {exc}", status_code=303)
    return RedirectResponse("/admin/memberships?done=Address changed.", status_code=303)


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
        'SELECT s.*, o.reference, o.tier_key, m.first_name, m.last_name,'
        ' m.ship_line1, m.ship_line2, m.ship_city, m.ship_state, m.ship_postcode'
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
        'SELECT r.*, (SELECT COUNT(*) FROM "order" o WHERE o.release_id = r.id) AS order_count,'
        ' (SELECT COUNT(*) FROM "order" o WHERE o.release_id = r.id'
        "  AND o.status IN ('pending_payment', 'failed')) AS unpaid_count,"
        " (SELECT COUNT(DISTINCT wine_id) FROM release_item i"
        "  WHERE i.release_id = r.id) AS composed"
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
    try:
        plan = flow.plan_release(release_id)
    except FlowError as exc:
        return RedirectResponse(f"/admin/releases?done=Not done: {exc}", status_code=303)
    created = flow.generate_release_orders(release_id, actor)
    done = f"{len(created)} order(s) raised. Nothing is charged until you press Charge."
    if not plan.composed:
        done += (" This release has no composition, so nothing checked the stock —"
                 " see Plan.")
    elif plan.unfilled:
        done += (f" {len(plan.unfilled)} member(s) could not be filled and were left out"
                 " — they are listed under Plan.")
    return RedirectResponse(f"/admin/releases?done={done}", status_code=303)


# ── the cellar: bottles, and what a release is made of ───────────────────
@app.get("/admin/cellar", response_class=HTMLResponse)
def admin_cellar(request: Request, actor: str = Depends(require_admin)) -> HTMLResponse:
    moves = db.all(
        "SELECT s.*, w.sku, w.name FROM stock_move s JOIN wine w ON w.id = s.wine_id"
        " ORDER BY s.id DESC LIMIT 60"
    )
    return admin_page(request, "cellar.html", "cellar", wines=flow.cellar(), moves=moves)


@app.post("/admin/cellar/wines")
async def admin_add_wine(request: Request,
                         actor: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    try:
        flow.add_wine(str(form.get("sku", "")), str(form.get("name", "")),
                      str(form.get("vintage", "")), actor)
    except FlowError as exc:
        return RedirectResponse(f"/admin/cellar?done=Not saved: {exc}", status_code=303)
    return RedirectResponse("/admin/cellar?done=In the cellar.", status_code=303)


@app.post("/admin/cellar/wines/{wine_id}/bottles")
async def admin_stock(request: Request, wine_id: int,
                      actor: str = Depends(require_admin)) -> RedirectResponse:
    """Bottles arriving, or a physical count. Deliberately two buttons.

    "I received twelve more" and "I counted twelve" are different sentences
    about the same wine, and a single box would make the wrong one easy.
    """
    form = await request.form()
    try:
        if str(form.get("how")) == "count":
            delta = flow.count_bottles(wine_id, str(form.get("bottles", "")), actor)
            done = ("Counted, and the books already said so."
                    if delta == 0 else f"Counted. That corrected the books by {delta:+d}.")
        else:
            flow.receive_bottles(wine_id, str(form.get("bottles", "")),
                                 str(form.get("note", "")), actor)
            done = "Bottles in."
    except FlowError as exc:
        return RedirectResponse(f"/admin/cellar?done=Not done: {exc}", status_code=303)
    return RedirectResponse(f"/admin/cellar?done={done}", status_code=303)


@app.post("/admin/cellar/wines/{wine_id}/status")
async def admin_wine_status(request: Request, wine_id: int,
                            actor: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    try:
        flow.set_wine_status(wine_id, str(form.get("status", "")), actor)
    except FlowError as exc:
        return RedirectResponse(f"/admin/cellar?done=Not done: {exc}", status_code=303)
    return RedirectResponse("/admin/cellar?done=Done.", status_code=303)


@app.get("/admin/releases/{release_id}/plan", response_class=HTMLResponse)
def admin_release_plan(request: Request, release_id: int,
                       actor: str = Depends(require_admin)) -> HTMLResponse:
    release = db.one('SELECT * FROM "release" WHERE id = ?', (release_id,))
    if release is None:
        raise HTTPException(status_code=404, detail="no such release")
    plan = flow.plan_release(release_id)
    composition: dict[str, dict[int, int]] = {}
    for row in db.all("SELECT * FROM release_item WHERE release_id = ?", (release_id,)):
        composition.setdefault(row["tier_key"], {})[int(row["wine_id"])] = int(row["bottles"])
    raised = db.one(
        'SELECT COUNT(*) AS n FROM "order" WHERE release_id = ?', (release_id,))

    # What the carrier would charge for what the plan says it can fill. An
    # estimate against the rate sheet, not a quotation: see shipping.py for
    # what it deliberately does not compute.
    tiers = {t.key: t for t in catalogue().tiers}
    freight = shipping.estimate([
        (f.name, f.state, tiers[f.tier_key].bottles,
         tiers[f.tier_key].price_cents,
         shipping.billed(f.state, tiers[f.tier_key].bottles,
                         tiers[f.tier_key].shipping_credit_cents))
        for f in plan.filled if f.tier_key in tiers
    ], ice_packs=bool(release["ice_packs"]))

    return admin_page(
        request, "plan.html", "releases", release=release, plan=plan,
        composition=composition, raised=int(raised["n"]), freight=freight,
        wines=[w for w in flow.cellar() if w["status"] == "active"],
        tiers=catalogue().tiers,
    )


@app.post("/admin/releases/{release_id}/ice")
async def admin_release_ice(request: Request, release_id: int,
                            actor: str = Depends(require_admin)) -> RedirectResponse:
    form = await request.form()
    back = f"/admin/releases/{release_id}/plan"
    try:
        flow.set_release_ice(release_id, str(form.get("on")) == "1", actor)
    except FlowError as exc:
        return RedirectResponse(f"{back}?done=Not done: {exc}", status_code=303)
    done = ("Ice packs on: the estimate below now includes them."
            if str(form.get("on")) == "1" else
            "Ice packs off. Nothing a member pays changes either way.")
    return RedirectResponse(f"{back}?done={done}", status_code=303)


@app.post("/admin/releases/{release_id}/compose")
async def admin_compose(request: Request, release_id: int,
                        actor: str = Depends(require_admin)) -> RedirectResponse:
    """One tier's whole shipment, saved in one press.

    Every wine in the cellar is a box, and a blank or a zero means that wine is
    not in this shipment — so correcting a composition is editing what is on
    the screen rather than remembering to delete something that is not.
    """
    form = await request.form()
    tier_key = str(form.get("tier_key", ""))
    back = f"/admin/releases/{release_id}/plan"
    try:
        for field, value in form.items():
            if not field.startswith("bottles_"):
                continue
            wine_id = int(field.removeprefix("bottles_"))
            flow.set_release_item(release_id, tier_key, wine_id,
                                  str(value).strip() or "0", actor)
    except (FlowError, ValueError) as exc:
        return RedirectResponse(f"{back}?done=Not saved: {exc}", status_code=303)

    plan = flow.plan_release(release_id)
    tier = next((t for t in plan.tiers if t.tier_key == tier_key), None)
    done = "Composed."
    if tier is not None and not tier.balanced:
        done = (f"Saved, but that is {tier.composed} bottles and {tier.name} promises"
                f" {tier.bottles}.")
    return RedirectResponse(f"{back}?done={done}", status_code=303)


def settle(result: dict, background: BackgroundTasks | None = None) -> None:
    """Everything after a charge that went through: the PDF, the receipt, the books.

    Separate from the charge itself because none of it may undo a payment that
    already happened. A receipt that will not send is a line in the log and a
    thing for the desk to do by hand, not a reason to leave money taken and no
    order to show for it.
    """
    order = db.one('SELECT * FROM "order" WHERE id = ?', (result["order_id"],))
    member = db.one("SELECT * FROM membership WHERE id = ?", (result["membership_id"],))
    invoice = db.one("SELECT * FROM invoice WHERE id = ?", (result["invoice_id"],))
    tier = catalogue().get(member["tier_key"])

    try:
        ensure_pdf(db, settings.invoice_dir, int(invoice["id"]))
    except Exception as exc:                                   # noqa: BLE001
        with db.transaction() as con:
            log(con, "invoice", int(invoice["id"]), "pdf_failed", error=str(exc))

    try:
        sent = send_receipt(mailer, member, order, invoice, tier, settings.mail_reply_to)
        with db.transaction() as con:
            log(con, "order", int(order["id"]), "receipt_sent", to=sent)
    except Exception as exc:                                   # noqa: BLE001
        with db.transaction() as con:
            log(con, "order", int(order["id"]), "receipt_failed", error=str(exc))

    if settings.qbo_autosync and ledger.live and background is not None:
        background.add_task(push_books)


def tell_them_the_card_failed(result: dict) -> None:
    """A declined card is only worth recording if the member hears about it.

    The letter carries a link to put in another one, because for a bank asking
    for the cardholder there is nothing the desk can do from this end.
    """
    member = db.one("SELECT * FROM membership WHERE id = ?", (result["membership_id"],))
    order = db.one('SELECT * FROM "order" WHERE id = ?', (result["order_id"],))
    try:
        sent = send_charge_failed(mailer, member, order, result["error"],
                                  flow.card_url(int(member["id"])), settings.mail_reply_to)
        with db.transaction() as con:
            log(con, "order", int(order["id"]), "decline_notice_sent", to=sent)
    except Exception as exc:                                   # noqa: BLE001
        with db.transaction() as con:
            log(con, "order", int(order["id"]), "decline_notice_failed", error=str(exc))


def tell_the_winery_about_declines(failures: list[dict]) -> None:
    """One message to the desk for a whole release, not one per card.

    The member already knows. This is the half that was missing: an order
    that failed goes quiet, the wine is out of the cellar, and nothing says
    so unless somebody opens Orders. Twenty separate alerts on a despatch
    morning is a mailbox nobody reads by the eleventh, so they arrive
    together.
    """
    if not failures:
        return
    detailed = []
    for result in failures:
        member = db.one("SELECT * FROM membership WHERE id = ?",
                        (result["membership_id"],))
        order = db.one('SELECT * FROM "order" WHERE id = ?', (result["order_id"],))
        detailed.append({
            "reference": order["reference"] if order else "?",
            "name": f"{member['first_name']} {member['last_name']}" if member else "",
            "total_cents": order["total_cents"] if order else 0,
            "error": result.get("error", ""),
        })
    try:
        send_declines_alert(mailer, detailed, settings.alerts_to,
                            settings.mail_reply_to)
    except Exception as exc:                                   # noqa: BLE001
        for result in failures:
            with db.transaction() as con:
                log(con, "order", int(result["order_id"]), "decline_alert_failed",
                    error=str(exc))


@app.post("/admin/releases/{release_id}/notify")
def admin_notify_release(release_id: int,
                         actor: str = Depends(require_admin)) -> RedirectResponse:
    """Tell everybody in this release what is coming, two weeks before it does.

    Between Generate and Charge, and only there: before Generate there are no
    orders and so no figures, and after Charge the warning is an apology.

    Only unpaid orders are written to, so pressing it twice does not tell a
    member who has already been charged that a charge is coming. Writing to
    somebody twice is a nuisance; writing to them after the money has gone is
    worse.
    """
    rows = db.all(
        'SELECT o.*, m.email, m.first_name, m.ship_city, m.ship_state '
        'FROM "order" o JOIN membership m ON m.id = o.membership_id '
        'WHERE o.release_id = ? AND o.paid_at IS NULL ORDER BY o.id',
        (release_id,))
    if not rows:
        return RedirectResponse(
            "/admin/releases?done=Nothing to tell anybody: this release has no "
            "unpaid orders. Raise them with Generate first.", status_code=303)

    release = db.one('SELECT * FROM "release" WHERE id = ?', (release_id,))
    when = (release["name"] if release and release["name"] else "the coming release")
    tiers = {t.key: t for t in catalogue().tiers}

    sent = failed = 0
    for row in rows:
        tier = tiers.get(row["tier_key"])
        if tier is None:          # a tier retired since the order was raised
            failed += 1
            continue
        try:
            send_release_notice(mailer, row, row, tier, when, settings.mail_reply_to)
        except Exception as exc:                               # noqa: BLE001
            failed += 1
            log_line = f"{type(exc).__name__}: {exc}"
            with db.transaction() as con:
                log(con, "order", int(row["id"]), "release_notice_failed",
                    actor=actor, error=log_line)
        else:
            sent += 1
            with db.transaction() as con:
                log(con, "order", int(row["id"]), "release_notice_sent", actor=actor)

    done = f"{sent} member(s) told what is coming and what it will cost."
    if failed:
        done += (f" {failed} could not be written to — the reason is against each "
                 "order in its history.")
    return RedirectResponse(f"/admin/releases?done={done}", status_code=303)


@app.post("/admin/releases/{release_id}/charge")
def admin_charge_release(release_id: int, background: BackgroundTasks,
                         actor: str = Depends(require_admin)) -> RedirectResponse:
    try:
        results = flow.charge_release(release_id, actor)
    except FlowError as exc:
        return RedirectResponse(f"/admin/releases?done=Not done: {exc}", status_code=303)

    failures = []
    for result in results:
        if result["paid"]:
            settle(result, background)
        else:
            tell_them_the_card_failed(result)
            failures.append(result)
    tell_the_winery_about_declines(failures)

    paid = sum(1 for r in results if r["paid"])
    failed = len(results) - paid
    done = f"{paid} charged."
    if failed:
        done += (f" {failed} did not go through — they are in Orders as failed, "
                 "and each of them has been written to.")
    return RedirectResponse(f"/admin/releases?done={done}", status_code=303)


@app.post("/admin/orders/{order_id}/charge")
def admin_charge_order(order_id: int, background: BackgroundTasks,
                       actor: str = Depends(require_admin)) -> RedirectResponse:
    try:
        result = flow.charge_order(order_id, actor)
    except FlowError as exc:
        return RedirectResponse(f"/admin/orders?done=Not done: {exc}", status_code=303)
    if result["paid"]:
        settle(result, background)
        return RedirectResponse(f"/admin/orders?done={result['reference']} is paid.",
                                status_code=303)
    tell_them_the_card_failed(result)
    tell_the_winery_about_declines([result])
    return RedirectResponse(
        f"/admin/orders?done=Declined again: {result['error']} They have been written to.",
        status_code=303)


@app.get("/admin/invoices/{invoice_id}.pdf")
def admin_invoice_pdf(invoice_id: int, actor: str = Depends(require_admin)) -> FileResponse:
    row = db.one("SELECT * FROM invoice WHERE id = ?", (invoice_id,))
    if row is None:
        raise HTTPException(404)
    path = ensure_pdf(db, settings.invoice_dir, invoice_id)
    return FileResponse(path, media_type="application/pdf", filename=f"{row['number']}.pdf")


# ── the books ────────────────────────────────────────────────────────────
# The state parameter is signed rather than stored. A one-row table for a
# value that lives ninety seconds is a table to back up and migrate forever.
_state_signer = URLSafeTimedSerializer(settings.secret_key, salt="qbo-state")
STATE_MAX_AGE = 900


@app.get("/admin/accounting", response_class=HTMLResponse)
def admin_accounting(request: Request, actor: str = Depends(require_admin)) -> HTMLResponse:
    return admin_page(
        request, "accounting.html", "accounting",
        status=ledger.status(), ledger_name=ledger.name,
        configured=ledger.live,
        autosync=settings.qbo_autosync,
        wine_item=settings.qbo_wine_item,
        shipping_item=settings.qbo_shipping_item,
        redirect_uri=settings.qbo_redirect_uri,
        rows=ledger_queue.tasks(db),
        outstanding=len(ledger_queue.pending(db)),
    )


@app.get("/admin/accounting/connect")
def admin_accounting_connect(actor: str = Depends(require_admin)) -> RedirectResponse:
    if not ledger.live:
        raise HTTPException(400, "no accounting system is configured")
    url = ledger.authorize_url(_state_signer.dumps(new_state()))
    return RedirectResponse(url, status_code=303)


@app.get("/admin/accounting/callback", response_class=HTMLResponse)
def admin_accounting_callback(request: Request, actor: str = Depends(require_admin),
                              code: str = "", realmId: str = "",   # noqa: N803
                              state: str = "", error: str = "") -> RedirectResponse:
    """Where QuickBooks sends the browser back after consent.

    Behind the desk's own password on purpose — Intuit redirects the staff
    browser, not a server, so the address restriction and the password both
    apply to this hop too.
    """
    if error:
        return RedirectResponse(f"/admin/accounting?done={ledger.name} returned: {error}",
                                status_code=303)
    try:
        _state_signer.loads(state, max_age=STATE_MAX_AGE)
    except BadSignature:
        raise HTTPException(400, "that authorisation did not start here") from None
    # Intuit names the company on the way back, in realmId.
    if not code:
        raise HTTPException(400, "the accounting system sent no code")

    try:
        tokens = ledger.exchange_code(code, realmId)
    except AccountingError as exc:
        return RedirectResponse(f"/admin/accounting?done=Could not finish: {exc}",
                                status_code=303)
    company = getattr(tokens, "realm_id", "") or realmId
    return RedirectResponse(
        f"/admin/accounting?done=Connected to {ledger.name} company {company}.",
        status_code=303)


@app.post("/admin/accounting/disconnect")
def admin_accounting_disconnect(actor: str = Depends(require_admin)) -> RedirectResponse:
    if hasattr(ledger, "disconnect"):
        ledger.disconnect()
    return RedirectResponse("/admin/accounting?done=Disconnected. The queue is untouched.",
                            status_code=303)


@app.post("/admin/accounting/sync")
def admin_accounting_sync(actor: str = Depends(require_admin)) -> RedirectResponse:
    results = ledger_queue.drain(db, ledger, actor=actor)
    done = " · ".join(results) if results else "Nothing was waiting."
    return RedirectResponse(f"/admin/accounting?done={done}", status_code=303)


@app.post("/admin/accounting/tasks/{task_id}/retry")
def admin_accounting_retry(task_id: int,
                           actor: str = Depends(require_admin)) -> RedirectResponse:
    ledger_queue.retry(db, task_id)
    task = db.one(
        "SELECT t.*, i.number FROM ledger_task t LEFT JOIN invoice i ON i.id = t.subject_id"
        " WHERE t.id = ?", (task_id,))
    if task is None:
        raise HTTPException(404)
    done = ledger_queue.push_one(db, ledger, task, actor) if ledger.live else \
        "No accounting system is connected, so nothing was pushed."
    return RedirectResponse(f"/admin/accounting?done={done}", status_code=303)


@app.get("/admin/accounting/export.csv")
def admin_accounting_export(unsynced: int = 0,
                            actor: str = Depends(require_admin)) -> PlainTextResponse:
    body = ledger_queue.export_csv(db, only_unsynced=bool(unsynced))
    name = "warm-springs-invoices.csv"
    return PlainTextResponse(
        body, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ── outgoing mail ────────────────────────────────────────────────────────
# Three questions, in the order they go wrong: are the settings right, does
# the server let us in, and does a message actually arrive. The first is free
# and shown on every load; the other two cost a network round trip and happen
# when somebody asks for them.
def _mechanism() -> str:
    return getattr(mailer, "mechanism", "none" if mailer.live else "outbox")


@app.get("/admin/mail", response_class=HTMLResponse)
def admin_mail(request: Request, verify: int = 0,
               actor: str = Depends(require_admin)) -> HTMLResponse:
    # A GET because it changes nothing: it can be refreshed, and a browser
    # that re-sends it is not a browser that sends mail twice.
    probe = mailer.verify() if verify else None
    return admin_page(
        request, "mail.html", "mail",
        checks=mail_check.configuration(settings, _mechanism()),
        probe=probe,
        live=mailer.live,
        mechanism=_mechanism(),
        outbox_dir=settings.outbox_dir,
        mail_from=settings.mail_from,
        from_domain=settings.mail_from.rpartition('@')[2],
        port=settings.smtp_port,
        suggested=settings.mail_reply_to,
        # Which failures are worth listing depends on who refuses them. The
        # Microsoft errors are meaningless against a relay, and the relay's
        # are meaningless against Microsoft; showing both would bury the one
        # that applies.
        host=settings.smtp_host,
        microsoft=mail_check.is_microsoft(settings.smtp_host),
        provider=mail_check.relay_name(settings.smtp_host),
    )


# ── do not write to these ────────────────────────────────────────────────
# The campaign is sent from a mailing platform (mail-templates/CAMPAIGN.md).
# Nothing here sends anything. This screen holds the one thing the platform
# cannot hold for the winery: who must not be written to, in a record that
# survives changing provider.
@app.get("/admin/campaigns", response_class=HTMLResponse)
def admin_campaigns(request: Request,
                    actor: str = Depends(require_admin)) -> HTMLResponse:
    with db.connect() as con:
        return admin_page(request, "campaigns.html", "campaigns",
                          suppressed=campaign.suppressed(con),
                          by_source=campaign.by_source(con))


@app.get("/admin/campaigns/suppressions.csv")
def admin_suppressions_csv(actor: str = Depends(require_admin)) -> PlainTextResponse:
    """The list as a file, to subtract from a wave before uploading it.

    This is the step between the planner and the mailing platform, and the
    one that is easy to skip: the platform does not know who told Deerfield
    to stop writing years ago, and it cannot know about the person who
    telephoned last week. Download this, remove these addresses from the
    wave, then upload.
    """
    with db.connect() as con:
        rows = campaign.suppressed(con)
    lines = ["email,created_at,source,campaign"]
    lines += [f'{r["email"]},{r["created_at"]},{r["source"]},{r["campaign"] or ""}'
              for r in rows]
    return PlainTextResponse(
        "\n".join(lines) + "\n", media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="suppressions.csv"'})


@app.post("/admin/campaigns/suppress")
def admin_suppress(email: str = Form(...),
                   actor: str = Depends(require_admin)) -> RedirectResponse:
    """Take an address off by hand — somebody who replied, or telephoned."""
    address = campaign.tidy(email)
    if "@" not in address or " " in address:
        return RedirectResponse(
            f"/admin/campaigns?done=That is not an address: {email}", status_code=303)
    with db.transaction() as con:
        added = campaign.suppress(con, address, "hand", detail=f"added by {actor}")
    said = (f"{address} will not be written to again." if added
            else f"{address} was already on the list.")
    return RedirectResponse(f"/admin/campaigns?done={said}", status_code=303)


@app.post("/admin/campaigns/import")
def admin_suppress_import(addresses: str = Form(...), source: str = Form("unsubscribe"),
                          note: str = Form(""),
                          actor: str = Depends(require_admin)) -> RedirectResponse:
    """Bring a platform's unsubscribes and bounces across after a wave.

    The provider knows who opted out of the letter it sent. The winery needs
    to know too, or the opt-out is lost the day the subscription moves. Paste
    the export here — one address a line, or a column copied out of a
    spreadsheet; anything that is not an address is skipped rather than
    stored.
    """
    if source not in campaign.SOURCES:
        return RedirectResponse("/admin/campaigns?done=Unknown reason, nothing "
                                "was imported.", status_code=303)
    rows = [part for line in addresses.splitlines() for part in line.split(",")]
    with db.transaction() as con:
        added, seen = campaign.suppress_many(
            con, rows, source, campaign=note.strip(),
            detail=f"imported by {actor}")
    said = (f"{added} added, {seen} already on the list." if added or seen
            else "No addresses found in that — nothing was imported.")
    return RedirectResponse(f"/admin/campaigns?done={said}", status_code=303)


@app.post("/admin/mail/test")
def admin_mail_test(to: str = Form(...),
                    actor: str = Depends(require_admin)) -> RedirectResponse:
    to = to.strip()
    if "@" not in to or " " in to:
        return RedirectResponse(f"/admin/mail?done=That is not an address: {to}",
                                status_code=303)
    try:
        where = mailer.send(mail_check.trial_message(settings, to, _mechanism()))
    except Exception as exc:  # noqa: BLE001 — whatever went wrong belongs on the page
        return RedirectResponse(
            f"/admin/mail?done=Not sent. {type(exc).__name__}: {exc}", status_code=303)
    sent = f"Sent to {to} via {where}." if mailer.live else \
        f"Not sent — written to {where}, because SMTP_HOST is unset."
    return RedirectResponse(f"/admin/mail?done={sent}", status_code=303)
