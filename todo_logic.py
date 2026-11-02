"""Pure helpers for the to-do feature: time zones, stats and reminder wording.

Nothing here touches the database, so it is easy to test.
"""
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

REMINDER_HOURS = float(os.getenv("TODO_REMINDER_HOURS", "4"))
MAX_TITLE_LEN = 200
MAX_TASKS_PER_DAY = 100


def utcnow():
    return datetime.now(timezone.utc)


def safe_zone(name):
    """ZoneInfo for a stored name, falling back to UTC if it is missing or invalid."""
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def validate_timezone(name):
    """Return the name if it is a real IANA zone, else raise ValueError."""
    if not name or len(name) > 64:
        raise ValueError("Invalid time zone")
    try:
        ZoneInfo(name)
    except Exception:
        raise ValueError("Unknown time zone")
    return name


def local_today(tz_name, now=None):
    now = now or utcnow()
    return now.astimezone(safe_zone(tz_name)).date()


def in_quiet_hours(hour, start, end):
    """True if `hour` (0-23) falls in the quiet window; the window may wrap midnight."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def reminder_due(user, open_tasks, now=None):
    """Should this user get a 'tasks left' reminder right now?

    Due when a task is outstanding for today or earlier, it's outside the user's quiet
    hours, and at least REMINDER_HOURS have passed since the later of: the last reminder,
    or when the oldest outstanding task was added.
    """
    now = now or utcnow()
    if not user.get("todo_reminders", True):
        return False
    local = now.astimezone(safe_zone(user.get("timezone")))
    if in_quiet_hours(local.hour, user.get("quiet_start", 22), user.get("quiet_end", 7)):
        return False
    today = local.date().isoformat()
    pending = [t for t in open_tasks if t["day"] <= today and not t.get("done_at")]
    if not pending:
        return False
    reference = min(datetime.fromisoformat(t["created_at"]) for t in pending)
    last = user.get("last_todo_reminder_at")
    if last:
        reference = max(reference, datetime.fromisoformat(last))
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return now - reference >= timedelta(hours=REMINDER_HOURS)


def _clip(text, n=40):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def reminder_payload(open_tasks, today):
    pending = [t for t in open_tasks if t["day"] <= today and not t.get("done_at")]
    pending.sort(key=lambda t: (-t.get("priority", 0), t["day"], t["id"]))
    overdue = sum(1 for t in pending if t["day"] < today)
    names = " · ".join(_clip(t["title"]) for t in pending[:3])
    if len(pending) > 3:
        names += f" · +{len(pending) - 3} more"
    count = len(pending)
    body = f"{count} task{'s' if count != 1 else ''} not done"
    if overdue:
        body += f" ({overdue} overdue)"
    return {
        "title": "⚠️ Tasks left",
        "body": f"{body}: {names}",
        "tag": "todo",
        "url": "/todo",
        "badge_count": count,
    }


def summarize_today(tasks, today):
    """Counts shown on the page and the widget. `tasks` = today's tasks plus any outstanding earlier ones."""
    done = sum(1 for t in tasks if t["day"] == today and t.get("done_at"))
    left_today = sum(1 for t in tasks if t["day"] == today and not t.get("done_at"))
    overdue = sum(1 for t in tasks if t["day"] < today and not t.get("done_at"))
    return {
        "date": today,
        "done": done,
        "left": left_today + overdue,
        "left_today": left_today,
        "overdue": overdue,
        "total_today": done + left_today,
    }


def build_stats(counts, today, range_name):
    """counts: rows of {day, planned, done} for the last year. Returns chart data and totals."""
    by_day = {r["day"]: r for r in counts}
    today_d = date.fromisoformat(today)

    def cell(d):
        r = by_day.get(d.isoformat())
        return {"date": d.isoformat(), "planned": int(r["planned"]) if r else 0, "done": int(r["done"]) if r else 0}

    span = 7 if range_name == "week" else 30
    days = [cell(today_d - timedelta(days=i)) for i in range(span - 1, -1, -1)]

    planned = sum(d["planned"] for d in days)
    done = sum(d["done"] for d in days)
    best = max(days, key=lambda d: d["done"])

    # Streak: consecutive days with at least one completed task. Today doesn't break it
    # until it has ended, so an empty-so-far today is skipped.
    streak = 0
    cursor = today_d
    if cell(cursor)["done"] == 0:
        cursor -= timedelta(days=1)
    while cell(cursor)["done"] > 0 and (today_d - cursor).days <= 366:
        streak += 1
        cursor -= timedelta(days=1)

    return {
        "range": range_name,
        "days": days,
        "totals": {
            "planned": planned,
            "done": done,
            "rate": round(100 * done / planned) if planned else 0,
            "streak": streak,
            "best_day": best["date"] if best["done"] else None,
            "best_done": best["done"],
            "avg_done": round(done / span, 1),
        },
    }
