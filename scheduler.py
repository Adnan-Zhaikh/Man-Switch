from datetime import datetime, timezone
import os

import requests
from dotenv import load_dotenv

from database import get_users_for_deadline_check

load_dotenv()
DEFAULT_NTFY_TOPIC = os.getenv("NTFY_TOPIC")

DEADLINE_HOURS = 24


def is_deadline_exceeded(last_checkin_timestamp, deadline_hours=DEADLINE_HOURS):
    if not last_checkin_timestamp:
        return False
    last_dt = datetime.fromisoformat(last_checkin_timestamp)
    elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    return elapsed_hours > deadline_hours


def check_deadline():
    due_users = []
    for user in get_users_for_deadline_check():
        if is_deadline_exceeded(user.get("last_checkin")):
            due_users.append(user)
    return due_users


def send_alert(topic, username=None):
    target = topic or DEFAULT_NTFY_TOPIC
    if not target:
        return False

    suffix = f" ({username})" if username else ""
    message = f"You missed your check-in deadline!{suffix}"
    requests.post(
        target,
        data=message,
        headers={"Title": "Deadman Switch", "Priority": "urgent"},
        timeout=10,
    )
    return True
