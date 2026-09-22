from database import get_last_checkin
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv; import os; load_dotenv(); NTFY_TOPIC = os.getenv("NTFY_TOPIC")

DEADLINE_HOURS = 24

def check_deadline():
    last = get_last_checkin()
    if last is None:
        return False

    last_dt = datetime.fromisoformat(last)
    elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    return elapsed_hours > DEADLINE_HOURS

def send_alert():
    requests.post(NTFY_TOPIC, data="You missed your check-in deadline!", headers={"Title":"Deadman Switch","Priority": "urgent"})