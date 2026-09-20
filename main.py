from fastapi import FastAPI
from database import init_db, add_checkin
from apscheduler.schedulers.background import BackgroundScheduler
from scheduler import check_deadline
from scheduler import send_alert

app = FastAPI()


@app.post("/checkin")
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