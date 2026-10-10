from contextlib import asynccontextmanager   
from fastapi import FastAPI, Depends, Form, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from database import init_db, add_checkin, get_all_checkins, get_checkin_by_id, update_checkin, delete_checkin
from storage import upload_image, get_signed_url, delete_image
from apscheduler.schedulers.background import BackgroundScheduler
from scheduler import check_deadline, send_alert
from auth import verify_credentials

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    init_db()
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_deadline_check, 'interval', minutes=15)
    scheduler.start()
    yield
    # --- shutdown ---
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


ALLOWED_CATEGORIES = {"journal", "important", "task"}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB


@app.post("/checkin", dependencies=[Depends(verify_credentials)])
async def checkin(
    note: str = Form(...),
    category: str = Form("journal"),
    image: UploadFile = File(None),
):
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=400,
                            detail=f"Invalid category. Allowed: {', '.join(sorted(ALLOWED_CATEGORIES))}")

    image_path = None
    if image and image.filename:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400,
                                detail=f"Invalid image type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}")
        file_bytes = await image.read()
        if len(file_bytes) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=400, detail="Image exceeds 5 MB limit")
        image_path = upload_image(file_bytes, image.content_type, category)

    add_checkin(note, category, image_path)
    return {"status": "checked in"}


@app.put("/checkin/{checkin_id}", dependencies=[Depends(verify_credentials)])
async def edit_checkin(
    checkin_id: int,
    note: str = Form(...),
    category: str = Form("journal"),
    image: UploadFile = File(None),
):
    """Edit an existing checkin. Optionally replace the image."""
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=400,
                            detail=f"Invalid category. Allowed: {', '.join(sorted(ALLOWED_CATEGORIES))}")

    # Check if the checkin exists
    existing = get_checkin_by_id(checkin_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Checkin not found")

    image_path = None
    old_image_path = existing[3]  # image_url is at index 3

    # If a new image is provided, upload it and mark old one for deletion
    if image and image.filename:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400,
                                detail=f"Invalid image type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}")
        file_bytes = await image.read()
        if len(file_bytes) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=400, detail="Image exceeds 5 MB limit")
        image_path = upload_image(file_bytes, image.content_type, category)
        # Delete the old image after uploading the new one
        if old_image_path:
            delete_image(old_image_path)

    # If no new image, image_path stays None and the old one is preserved
    if update_checkin(checkin_id, note, category, image_path):
        return {"status": "updated"}
    else:
        raise HTTPException(status_code=404, detail="Checkin not found")


@app.delete("/checkin/{checkin_id}", dependencies=[Depends(verify_credentials)])
def delete_checkin_endpoint(checkin_id: int):
    """Delete a checkin and its associated image."""
    image_url = delete_checkin(checkin_id)
    
    if image_url:
        delete_image(image_url)
    
    return {"status": "deleted"}


def run_deadline_check():
    result = check_deadline()
    print(f"Deadline exceeded? {result}")
    if result:
        send_alert()


@app.get("/history", dependencies=[Depends(verify_credentials)])
def history(category: str = None):
    rows = get_all_checkins(category)
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


@app.get("/", dependencies=[Depends(verify_credentials)])
def serve_ui():
    return FileResponse("static/index.html")   


@app.get("/health")
def health():
    return {"status": "ok"}
