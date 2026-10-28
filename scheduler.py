import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from database import (
    claim_alert,
    get_users_for_deadline_check,
    release_alert,
    set_setting,
)
from push import send_to_user

logger = logging.getLogger(__name__)

DEADLINE_HOURS = float(os.getenv("DEADLINE_HOURS", "24"))
# How long to wait before nagging again about the same missed deadline.
ALERT_REPEAT_MINUTES = int(os.getenv("ALERT_REPEAT_MINUTES", "60"))

_tick_lock = threading.Lock()


def is_deadline_exceeded(last_checkin_timestamp, deadline_hours=None):
    if deadline_hours is None:
        deadline_hours = DEADLINE_HOURS
    if not last_checkin_timestamp:
        return False
    last_dt = datetime.fromisoformat(last_checkin_timestamp)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    return elapsed_hours > deadline_hours


def check_deadline():
    due_users = []
    for user in get_users_for_deadline_check():
        try:
            if is_deadline_exceeded(user.get("last_checkin")):
                due_users.append(user)
        except ValueError:
            logger.warning("Skipping user %s: unreadable last check-in timestamp", user.get("id"))
    return due_users


def _alert_payload():
    return {
        "title": "Man Switch",
        "body": "You missed your check-in deadline. Open the app and check in.",
        "tag": "deadline",
        "urgent": True,
        "url": "/",
    }


def run_tick(source="scheduler"):
    """Check every user's deadline and push an alert to those who are overdue.

    Safe to call from several places at once (in-app scheduler, external cron, admin
    button): an in-process lock stops overlap, and claim_alert() in the database stops
    the same alert being sent twice. It is also safe to run late: deadlines are computed
    from stored timestamps, so waking after a long sleep still alerts correctly.
    """
    if not _tick_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "a check is already running"}
    try:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(minutes=ALERT_REPEAT_MINUTES)).isoformat()
        result = {"source": source, "at": now.isoformat(), "overdue": 0, "alerted": 0, "no_device": 0, "errors": 0}

        try:
            due = check_deadline()
        except Exception:
            logger.exception("Deadline check failed")
            result["errors"] += 1
            due = []

        result["overdue"] = len(due)
        for user in due:
            claimed, previous = False, None
            try:
                claimed, previous = claim_alert(user["id"], now.isoformat(), cutoff)
                if not claimed:
                    continue
                if send_to_user(user["id"], _alert_payload()) > 0:
                    result["alerted"] += 1
                else:
                    # Nothing delivered: free the claim so the next tick tries again.
                    release_alert(user["id"], previous)
                    result["no_device"] += 1
            except Exception:
                logger.exception("Failed to alert user %s", user.get("id"))
                result["errors"] += 1
                if claimed:
                    try:
                        release_alert(user["id"], previous)
                    except Exception:
                        logger.exception("Could not release alert claim for user %s", user.get("id"))

        try:
            set_setting("last_tick", json.dumps(result))
        except Exception:
            logger.exception("Could not record tick result")
        return result
    finally:
        _tick_lock.release()
