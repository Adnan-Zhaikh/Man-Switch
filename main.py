import os

from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from auth import (
    clear_user_session,
    get_required_session_secret,
    hash_password,
    require_authenticated_user_id,
    start_user_session,
    validate_password,
    validate_username,
    verify_password,
)
from database import (
    add_checkin,
    assign_unowned_checkins,
    create_user,
    delete_checkin,
    get_all_checkins,
    get_checkin_by_id,
    get_unowned_checkin_count,
    get_user_by_id,
    get_user_by_username,
    has_users,
    init_db,
    update_checkin,
)
from scheduler import check_deadline, send_alert
from storage import delete_image, get_signed_url, upload_image


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bootstrap_legacy_user_if_needed()
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_deadline_check, "interval", minutes=15)
    scheduler.start()
    yield
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
        os.getenv("NTFY_TOPIC"),
    )

    if get_unowned_checkin_count() > 0:
        assign_unowned_checkins(user["id"])


def get_current_user(request: Request):
    user_id = require_authenticated_user_id(request)
    user = get_user_by_id(user_id)
    if not user:
        clear_user_session(request)
        raise HTTPException(status_code=401, detail="Session expired")
    return user


@app.post("/auth/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    ntfy_topic: str = Form(default=None),
):
    normalized_username = validate_username(username)
    validated_password = validate_password(password)

    if validated_password != confirm_password:
        raise HTTPException(status_code=422, detail="Passwords do not match")

    existing = get_user_by_username(normalized_username)
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    is_first_user = not has_users()
    user = create_user(
        normalized_username,
        hash_password(validated_password),
        ntfy_topic.strip() if ntfy_topic else None,
    )

    if is_first_user and get_unowned_checkin_count() > 0:
        assign_unowned_checkins(user["id"])

    start_user_session(request, user["id"])
    return {"status": "registered", "user": {"id": user["id"], "username": user["username"]}}


@app.post("/auth/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    normalized_username = validate_username(username)
    user = get_user_by_username(normalized_username)

    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    start_user_session(request, user["id"])
    return {"status": "logged_in", "user": {"id": user["id"], "username": user["username"]}}


@app.post("/auth/logout")
def logout(request: Request):
    clear_user_session(request)
    return {"status": "logged_out"}


@app.get("/auth/me")
def me(user=Depends(get_current_user)):
    return {"authenticated": True, "user": user}


@app.post("/checkin")
async def checkin(
    note: str = Form(...),
    category: str = Form("journal"),
    image: UploadFile = File(None),
    user=Depends(get_current_user),
):
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category. Allowed: {', '.join(sorted(ALLOWED_CATEGORIES))}",
        )

    image_path = None
    if image and image.filename:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid image type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}",
            )
        file_bytes = await image.read()
        if len(file_bytes) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=400, detail="Image exceeds 5 MB limit")
        image_path = upload_image(file_bytes, image.content_type, category)

    add_checkin(user["id"], note, category, image_path)
    return {"status": "checked in"}


@app.put("/checkin/{checkin_id}")
async def edit_checkin(
    checkin_id: int,
    note: str = Form(...),
    category: str = Form("journal"),
    image: UploadFile = File(None),
    user=Depends(get_current_user),
):
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category. Allowed: {', '.join(sorted(ALLOWED_CATEGORIES))}",
        )

    existing = get_checkin_by_id(checkin_id, user["id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Checkin not found")

    image_path = None
    old_image_path = existing[3]

    if image and image.filename:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid image type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}",
            )
        file_bytes = await image.read()
        if len(file_bytes) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=400, detail="Image exceeds 5 MB limit")
        image_path = upload_image(file_bytes, image.content_type, category)
        if old_image_path:
            delete_image(old_image_path)

    if update_checkin(checkin_id, user["id"], note, category, image_path):
        return {"status": "updated"}

    raise HTTPException(status_code=404, detail="Checkin not found")


@app.delete("/checkin/{checkin_id}")
def delete_checkin_endpoint(checkin_id: int, user=Depends(get_current_user)):
    image_url, deleted = delete_checkin(checkin_id, user["id"])

    if not deleted:
        raise HTTPException(status_code=404, detail="Checkin not found")

    if image_url:
        delete_image(image_url)

    return {"status": "deleted"}


def run_deadline_check():
    due_users = check_deadline()
    for user in due_users:
        send_alert(user.get("ntfy_topic"), user.get("username"))


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
            item["image_url"] = get_signed_url(item["image_url"])
        results.append(item)
    return results


@app.get("/manifest.json")
def manifest():
    return FileResponse("static/manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse("static/sw.js", media_type="application/javascript")


@app.get("/icon-192.png")
def icon192():
    return FileResponse("static/icon-192.png")


@app.get("/icon-512.png")
def icon512():
    return FileResponse("static/icon-512.png")


@app.get("/")
def serve_ui():
    return FileResponse("static/index.html")


@app.get("/health")
def health():
    return {"status": "ok"}
