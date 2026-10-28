import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg2.errors
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from auth import (
    DUMMY_PASSWORD_HASH,
    clear_user_session,
    get_required_session_secret,
    get_session_user_id,
    hash_password,
    require_authenticated_user_id,
    start_user_session,
    validate_password,
    validate_username,
    verify_password,
)
from database import (
    add_checkin,
    admin_delete_user,
    admin_list_users,
    assign_unowned_checkins,
    count_push_subscriptions,
    create_user,
    delete_checkin,
    delete_push_subscription,
    get_all_checkins,
    get_checkin_by_id,
    get_push_subscriptions,
    get_setting,
    get_unowned_checkin_count,
    get_user_by_id,
    get_user_by_username,
    has_users,
    init_db,
    set_setting,
    set_user_admin,
    update_checkin,
    upsert_push_subscription,
)
from push import is_configured as push_is_configured
from push import public_key as push_public_key
from push import send_to_user, validate_subscription
from ratelimit import RateLimiter
from scheduler import ALERT_REPEAT_MINUTES, DEADLINE_HOURS, is_deadline_exceeded, run_tick
from storage import delete_image, get_signed_url, upload_image

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
NO_STORE = {"Cache-Control": "no-store"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bootstrap_legacy_user_if_needed()
    bootstrap_admin_if_configured()
    if not push_is_configured():
        logger.warning("Web Push is not configured: set VAPID_PRIVATE_KEY and VAPID_SUBJECT (see README)")

    scheduler = BackgroundScheduler()
    tick_minutes = max(1, int(os.getenv("TICK_MINUTES", "5")))
    scheduler.add_job(
        run_tick,
        "interval",
        minutes=tick_minutes,
        kwargs={"source": "scheduler"},
        coalesce=True,
        max_instances=1,
    )
    # Catch up immediately after a cold start; a free host that slept missed its ticks.
    scheduler.add_job(run_tick, "date", kwargs={"source": "startup"})
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=get_required_session_secret(),
    session_cookie="man_switch_session",
    max_age=int(os.getenv("SESSION_MAX_AGE_SECONDS", "43200")),
    same_site="lax",
    https_only=os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true",
)


ALLOWED_CATEGORIES = {"journal", "important", "task"}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_NOTE_LENGTH = 2000
MAX_DEVICES_PER_USER = 10


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

ip_limiter = RateLimiter(300, 60)             # every request, per IP
login_ip_limiter = RateLimiter(30, 600)       # login attempts, per IP
login_fail_ip_user = RateLimiter(8, 900)      # failed logins, per IP + username
login_fail_user = RateLimiter(40, 900)        # failed logins, per username (any IP)
register_limiter = RateLimiter(5, 3600)       # registrations, per IP
write_limiter = RateLimiter(60, 60)           # create/edit/delete entries, per user
push_sub_limiter = RateLimiter(20, 3600)      # subscribing devices, per user
push_test_limiter = RateLimiter(5, 600)       # test notifications, per user
cron_fail_limiter = RateLimiter(10, 300)      # wrong cron keys, per IP


def client_ip(request: Request) -> str:
    """Best-effort real client IP.

    Behind a reverse proxy (Render) every connection comes from the proxy, so the
    client address is read from X-Forwarded-For. TRUSTED_PROXY_HOPS is how many
    proxies append to it; the entry that many places from the END is the client, which
    a caller can't forge. Set it to 0 when running without a proxy. The admin page
    shows what the app currently detects so you can check it.
    """
    hops = int(os.getenv("TRUSTED_PROXY_HOPS", "1"))
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


def _too_many(retry_after: int):
    return HTTPException(
        status_code=429,
        detail=f"Too many requests. Try again in {retry_after} seconds.",
        headers={"Retry-After": str(retry_after)},
    )


def enforce(limiter: RateLimiter, key: str):
    allowed, retry_after = limiter.hit(key)
    if not allowed:
        raise _too_many(retry_after)


@app.middleware("http")
async def global_rate_limit(request: Request, call_next):
    path = request.url.path
    if path == "/health" or path.startswith("/internal/"):
        return await call_next(request)
    allowed, retry_after = ip_limiter.hit(client_ip(request))
    if not allowed:
        return JSONResponse(
            {"detail": f"Too many requests. Try again in {retry_after} seconds."},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Startup bootstrap
# ---------------------------------------------------------------------------


def _get_legacy_credentials():
    direct_user = os.getenv("BASIC_AUTH_USER")
    direct_pass = os.getenv("BASIC_AUTH_PASS")
    if direct_user and direct_pass:
        return direct_user, direct_pass

    indexed_user = os.getenv("BASIC_AUTH_USER_1")
    indexed_pass = os.getenv("BASIC_AUTH_PASS_1")
    if indexed_user and indexed_pass:
        return indexed_user, indexed_pass

    return None, None


def bootstrap_legacy_user_if_needed():
    if has_users():
        return

    username, password = _get_legacy_credentials()
    if not username or not password:
        return

    user = create_user(
        validate_username(username),
        hash_password(validate_password(password)),
    )

    if get_unowned_checkin_count() > 0:
        assign_unowned_checkins(user["id"])


def bootstrap_admin_if_configured():
    """Create or promote the owner's admin account from ADMIN_USERNAME / ADMIN_PASSWORD.

    Needed because free hosting has no shell. An existing account is only promoted if
    ADMIN_PASSWORD matches its password, so nobody can claim admin by registering the
    name first.
    """
    username = os.getenv("ADMIN_USERNAME")
    password = os.getenv("ADMIN_PASSWORD")
    if not username or not password:
        return
    try:
        username = validate_username(username)
        password = validate_password(password)
    except HTTPException as exc:
        logger.error("ADMIN_USERNAME/ADMIN_PASSWORD rejected: %s", exc.detail)
        return

    user = get_user_by_username(username)
    if user is None:
        create_user(username, hash_password(password), is_admin=True)
        logger.info("Created admin account %r", username)
    elif not user["is_admin"]:
        if verify_password(password, user["password_hash"]):
            set_user_admin(user["id"], True)
            logger.info("Promoted existing account %r to admin", username)
        else:
            logger.error(
                "Account %r exists but ADMIN_PASSWORD does not match its password; not promoting", username
            )


def registration_open() -> bool:
    if not has_users():
        return True
    stored = get_setting("registration_open")
    if stored is None:
        return os.getenv("REGISTRATION_OPEN", "true").lower() == "true"
    return stored == "true"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def get_current_user(request: Request):
    user_id = require_authenticated_user_id(request)
    user = get_user_by_id(user_id)
    if not user:
        clear_user_session(request)
        raise HTTPException(status_code=401, detail="Session expired")
    return user


def get_admin_user(user=Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


@app.get("/auth/config")
def auth_config():
    return {"registration_open": registration_open()}


@app.post("/auth/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    enforce(register_limiter, client_ip(request))

    normalized_username = validate_username(username)
    validated_password = validate_password(password)

    if validated_password != confirm_password:
        raise HTTPException(status_code=422, detail="Passwords do not match")

    if not registration_open():
        raise HTTPException(status_code=403, detail="Registration is closed")

    if get_user_by_username(normalized_username):
        raise HTTPException(status_code=409, detail="Username already exists")

    is_first_user = not has_users()
    try:
        user = create_user(normalized_username, hash_password(validated_password))
    except psycopg2.errors.UniqueViolation:
        # Lost a race with a concurrent registration of the same name.
        raise HTTPException(status_code=409, detail="Username already exists")

    if is_first_user and get_unowned_checkin_count() > 0:
        assign_unowned_checkins(user["id"])

    start_user_session(request, user["id"])
    return {
        "status": "registered",
        "user": {"id": user["id"], "username": user["username"], "is_admin": bool(user.get("is_admin"))},
    }


@app.post("/auth/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = client_ip(request)
    enforce(login_ip_limiter, ip)

    normalized_username = validate_username(username)
    name_key = normalized_username.lower()
    ip_user_key = f"{ip}|{name_key}"
    for limiter, key in ((login_fail_ip_user, ip_user_key), (login_fail_user, name_key)):
        allowed, retry_after = limiter.check(key)
        if not allowed:
            raise _too_many(retry_after)

    user = get_user_by_username(normalized_username)

    # Always run one password verification so timing doesn't reveal whether the user exists.
    password_ok = verify_password(password, user["password_hash"] if user else DUMMY_PASSWORD_HASH)
    if not user or not password_ok:
        login_fail_ip_user.record(ip_user_key)
        login_fail_user.record(name_key)
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    login_fail_ip_user.reset(ip_user_key)
    start_user_session(request, user["id"])
    return {
        "status": "logged_in",
        "user": {"id": user["id"], "username": user["username"], "is_admin": bool(user.get("is_admin"))},
    }


@app.post("/auth/logout")
def logout(request: Request):
    clear_user_session(request)
    return {"status": "logged_out"}


@app.get("/auth/me")
def me(user=Depends(get_current_user)):
    return {"authenticated": True, "user": user}


# ---------------------------------------------------------------------------
# Check-ins
# ---------------------------------------------------------------------------


def _validated_category(category: str) -> str:
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category. Allowed: {', '.join(sorted(ALLOWED_CATEGORIES))}",
        )
    return category


def _validated_note(note: str) -> str:
    cleaned = (note or "").strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="Note cannot be empty")
    if len(cleaned) > MAX_NOTE_LENGTH:
        raise HTTPException(
            status_code=422, detail=f"Note must be at most {MAX_NOTE_LENGTH} characters"
        )
    return cleaned


def _sniff_image_type(data: bytes):
    """Identify the real image type from magic bytes; the client's header can't be trusted."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def _read_validated_image(image):
    """Return (bytes, content_type) for an uploaded image, or None if none was sent."""
    if not image or not image.filename:
        return None
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid image type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}",
        )
    # Read one byte past the limit so oversized uploads are rejected without buffering them fully.
    file_bytes = await image.read(MAX_IMAGE_SIZE + 1)
    if len(file_bytes) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="Image exceeds 5 MB limit")
    detected = _sniff_image_type(file_bytes)
    if detected is None:
        raise HTTPException(status_code=400, detail="File is not a valid JPEG, PNG or WebP image")
    return file_bytes, detected


# The endpoints below are `async` only to read the upload. All blocking work
# (Postgres, Supabase) is pushed to the threadpool so it can't stall the event loop.


@app.post("/checkin")
async def checkin(
    note: str = Form(...),
    category: str = Form("journal"),
    image: UploadFile = File(None),
    user=Depends(get_current_user),
):
    enforce(write_limiter, f"user:{user['id']}")
    category = _validated_category(category)
    note = _validated_note(note)

    image_path = None
    uploaded = await _read_validated_image(image)
    if uploaded:
        file_bytes, content_type = uploaded
        image_path = await run_in_threadpool(upload_image, file_bytes, content_type, category)

    try:
        await run_in_threadpool(add_checkin, user["id"], note, category, image_path)
    except Exception:
        if image_path:
            await run_in_threadpool(delete_image, image_path)  # don't orphan the upload
        raise
    return {"status": "checked in"}


@app.put("/checkin/{checkin_id}")
async def edit_checkin(
    checkin_id: int,
    note: str = Form(...),
    category: str = Form("journal"),
    remove_image: bool = Form(False),
    image: UploadFile = File(None),
    user=Depends(get_current_user),
):
    enforce(write_limiter, f"user:{user['id']}")
    category = _validated_category(category)
    note = _validated_note(note)

    existing = await run_in_threadpool(get_checkin_by_id, checkin_id, user["id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Checkin not found")
    old_image_path = existing[3]

    new_image_path = None
    uploaded = await _read_validated_image(image)
    if uploaded:
        file_bytes, content_type = uploaded
        new_image_path = await run_in_threadpool(upload_image, file_bytes, content_type, category)

    try:
        updated = await run_in_threadpool(
            update_checkin, checkin_id, user["id"], note, category, new_image_path, remove_image
        )
    except Exception:
        if new_image_path:
            await run_in_threadpool(delete_image, new_image_path)
        raise

    if not updated:
        if new_image_path:
            await run_in_threadpool(delete_image, new_image_path)
        raise HTTPException(status_code=404, detail="Checkin not found")

    # Only drop the old image once the database no longer points at it.
    if old_image_path and (new_image_path or remove_image):
        await run_in_threadpool(delete_image, old_image_path)

    return {"status": "updated"}


@app.delete("/checkin/{checkin_id}")
def delete_checkin_endpoint(checkin_id: int, user=Depends(get_current_user)):
    enforce(write_limiter, f"user:{user['id']}")
    image_url, deleted = delete_checkin(checkin_id, user["id"])

    if not deleted:
        raise HTTPException(status_code=404, detail="Checkin not found")

    if image_url:
        delete_image(image_url)

    return {"status": "deleted"}


@app.get("/history")
def history(category: str = None, user=Depends(get_current_user)):
    rows = get_all_checkins(user["id"], category)
    results = []
    for row in rows:
        item = {
            "id": row[0],
            "timestamp": row[1],
            "progress_note": row[2],
            "image_url": row[3],
            "category": row[4],
        }
        if item["image_url"]:
            try:
                item["image_url"] = get_signed_url(item["image_url"])
            except Exception:
                # A missing/unsignable image shouldn't take the whole feed down.
                logger.warning("Could not sign image URL for check-in %s", item["id"])
                item["image_url"] = None
        results.append(item)
    return results


# ---------------------------------------------------------------------------
# Push notifications (browser Web Push)
# ---------------------------------------------------------------------------


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=600)
    auth: str = Field(max_length=600)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(max_length=600)
    keys: PushKeys


class PushEndpointIn(BaseModel):
    endpoint: str = Field(max_length=600)


@app.get("/push/public-key")
def push_key(user=Depends(get_current_user)):
    key = None
    if push_is_configured():
        try:
            key = push_public_key()
        except Exception:
            logger.exception("VAPID_PRIVATE_KEY could not be loaded")
    return {"configured": key is not None, "key": key, "devices": count_push_subscriptions(user["id"])}


@app.post("/push/subscribe")
def push_subscribe(body: PushSubscriptionIn, request: Request, user=Depends(get_current_user)):
    enforce(push_sub_limiter, f"user:{user['id']}")
    try:
        endpoint, p256dh, auth = validate_subscription(body.endpoint, body.keys.p256dh, body.keys.auth)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    already_known = False
    # A device re-syncing itself on every page load must not count against the device cap.
    for sub in get_push_subscriptions(user["id"]):
        if sub["endpoint"] == endpoint:
            already_known = True
    if not already_known and count_push_subscriptions(user["id"]) >= MAX_DEVICES_PER_USER:
        raise HTTPException(status_code=409, detail=f"At most {MAX_DEVICES_PER_USER} devices per account")

    upsert_push_subscription(user["id"], endpoint, p256dh, auth, request.headers.get("user-agent", "")[:200])
    return {"status": "subscribed"}


@app.post("/push/unsubscribe")
def push_unsubscribe(body: PushEndpointIn, user=Depends(get_current_user)):
    delete_push_subscription(body.endpoint, user["id"])
    return {"status": "unsubscribed"}


@app.post("/push/test")
def push_test(user=Depends(get_current_user)):
    enforce(push_test_limiter, f"user:{user['id']}")
    if not push_is_configured():
        raise HTTPException(status_code=503, detail="Push notifications are not configured on the server")
    delivered = send_to_user(
        user["id"],
        {"title": "Man Switch", "body": "Test notification: alerts are working.", "tag": "test", "url": "/"},
    )
    if delivered == 0:
        raise HTTPException(status_code=409, detail="No working device is subscribed. Enable alerts first.")
    return {"delivered": delivered}


# ---------------------------------------------------------------------------
# External scheduler hook (keeps deadline checks running even if the host sleeps)
# ---------------------------------------------------------------------------


@app.api_route("/internal/tick", methods=["GET", "POST", "HEAD"])
async def tick(request: Request):
    """Run the deadline check. Called by cron-job.org / GitHub Actions / UptimeRobot.

    Authenticate with `Authorization: Bearer <CRON_SECRET>` (preferred) or `?key=<CRON_SECRET>`.
    Hitting it also wakes a sleeping free-tier instance.
    """
    secret = os.getenv("CRON_SECRET")
    if not secret:
        raise HTTPException(status_code=404, detail="Not found")

    ip = client_ip(request)
    allowed, retry_after = cron_fail_limiter.check(ip)
    if not allowed:
        raise _too_many(retry_after)

    header = request.headers.get("authorization", "")
    provided = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("key", "")
    if not hmac.compare_digest(provided.encode(), secret.encode()):
        cron_fail_limiter.record(ip)
        raise HTTPException(status_code=401, detail="Unauthorized")

    if request.method == "HEAD":
        return Response(status_code=200)
    result = await run_in_threadpool(run_tick, "cron")
    return {k: result.get(k) for k in ("overdue", "alerted", "no_device", "errors", "skipped") if k in result} | {"ok": True}


# ---------------------------------------------------------------------------
# Admin (owner only). Exposes account metadata, never anyone's entries or photos.
# ---------------------------------------------------------------------------


@app.get("/admin")
def admin_page(request: Request):
    user_id = get_session_user_id(request)
    user = get_user_by_id(user_id) if user_id is not None else None
    if not user or not user.get("is_admin"):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC_DIR / "admin.html", headers=NO_STORE)


@app.get("/admin/api/overview")
def admin_overview(request: Request, admin=Depends(get_admin_user)):
    users = []
    for row in admin_list_users():
        try:
            overdue = is_deadline_exceeded(row["last_checkin"])
        except ValueError:
            overdue = False
        users.append(
            {
                "id": row["id"],
                "username": row["username"],
                "is_admin": bool(row["is_admin"]),
                "created_at": row["created_at"],
                "entries": int(row["entries"]),
                "last_checkin": row["last_checkin"],
                "devices": int(row["devices"]),
                "overdue": overdue,
                "last_alert_at": row["last_alert_at"],
            }
        )

    try:
        last_tick = json.loads(get_setting("last_tick") or "null")
    except ValueError:
        last_tick = None

    return {
        "me": {"id": admin["id"], "username": admin["username"]},
        "status": {
            "push_configured": push_is_configured(),
            "cron_configured": bool(os.getenv("CRON_SECRET")),
            "registration_open": registration_open(),
            "deadline_hours": DEADLINE_HOURS,
            "repeat_minutes": ALERT_REPEAT_MINUTES,
            "tick_minutes": max(1, int(os.getenv("TICK_MINUTES", "5"))),
            "last_tick": last_tick,
            "detected_ip": client_ip(request),
            "forwarded_for": request.headers.get("x-forwarded-for", ""),
            "trusted_proxy_hops": int(os.getenv("TRUSTED_PROXY_HOPS", "1")),
        },
        "users": users,
    }


@app.post("/admin/api/registration")
def admin_set_registration(open: bool = Form(...), admin=Depends(get_admin_user)):
    set_setting("registration_open", "true" if open else "false")
    return {"registration_open": open}


@app.post("/admin/api/tick")
def admin_run_tick(admin=Depends(get_admin_user)):
    return run_tick("admin")


@app.delete("/admin/api/users/{user_id}")
def admin_remove_user(user_id: int, admin=Depends(get_admin_user)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="You can't delete your own account here")
    target = get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("is_admin"):
        raise HTTPException(status_code=400, detail="Admin accounts can't be deleted here")
    deleted, image_paths = admin_delete_user(user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    for path in image_paths:
        delete_image(path)
    return {"status": "deleted", "images_removed": len(image_paths)}


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------


@app.get("/manifest.json")
def manifest():
    return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    # no-cache so a changed service worker is picked up promptly
    return FileResponse(
        STATIC_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"}
    )


@app.get("/icon-192.png")
def icon192():
    return FileResponse(STATIC_DIR / "icon-192.png", media_type="image/png")


@app.get("/icon-512.png")
def icon512():
    return FileResponse(STATIC_DIR / "icon-512.png", media_type="image/png")


# HEAD is allowed on these two because many uptime monitors probe with HEAD, and a 405
# there makes a healthy app look down.
@app.api_route("/", methods=["GET", "HEAD"])
def serve_ui():
    return FileResponse(STATIC_DIR / "index.html")


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}
