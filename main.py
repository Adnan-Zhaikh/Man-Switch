from fastapi import FastAPI
from database import init_db, add_checkin, get_all_checkins
from apscheduler.schedulers.background import BackgroundScheduler
from scheduler import check_deadline
from scheduler import send_alert
from fastapi.staticfiles import StaticFiles
from fastapi import Depends
from fastapi.responses import FileResponse
from auth import verify_credentials


app = FastAPI()


@app.post("/checkin", dependencies=[Depends(verify_credentials)])
def checkin(note: str):
    add_checkin(note)
    return {"status": "checked in"}

def run_deadline_check():
    result = check_deadline()
    print(f"Deadline exceeded? {result}") 
    if result: send_alert()

scheduler = BackgroundScheduler()
scheduler.add_job(run_deadline_check, 'interval', minutes=1)

@app.on_event("startup")
def start_scheduler():
    init_db()
    scheduler.start()

@app.on_event("shutdown")
def stop_scheduler():
    scheduler.shutdown()


@app.get("/history", dependencies=[Depends(verify_credentials)])
def history():
    return get_all_checkins()

@app.get("/", dependencies=[Depends(verify_credentials)])
def serve_ui():
    return FileResponse("static/index.html")
