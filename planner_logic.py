"""Pure helpers for the calendar: validation and the reminder email wording.

Nothing here touches the database or the network, so it is easy to test.
"""
import os
import re
from datetime import datetime, timedelta, timezone

from emailer import one_line
from todo_logic import safe_zone

REMINDER_LEAD_MINUTES = int(os.getenv("EVENT_REMINDER_MINUTES", "60"))
MAX_TITLE_LEN = 120
MAX_NOTES_LEN = 1000
MAX_EVENTS_PER_USER = 500
MAX_RANGE_DAYS = 70
DEFAULT_COLOR = "#2d8cff"
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def parse_instant(value):
    """An ISO date-time that carries its own offset (e.g. 2026-12-01T09:30:00Z) -> aware UTC, whole seconds."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Pick a date and time")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("That date and time isn't valid")
    if parsed.tzinfo is None:
        raise ValueError("The date and time must include a time zone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def check_start_window(start, now):
    if start < now - timedelta(days=366):
        raise ValueError("That date is more than a year in the past")
    if start > now + timedelta(days=3 * 366):
        raise ValueError("Pick a date within the next three years")


def clean_title(title):
    cleaned = " ".join((title or "").split())
    if not cleaned:
        raise ValueError("Give the event a title")
    if len(cleaned) > MAX_TITLE_LEN:
        raise ValueError(f"Title must be at most {MAX_TITLE_LEN} characters")
    return cleaned


def clean_notes(notes):
    cleaned = (notes or "").strip()
    if len(cleaned) > MAX_NOTES_LEN:
        raise ValueError(f"Notes must be at most {MAX_NOTES_LEN} characters")
    return cleaned


def validate_color(color):
    if not isinstance(color, str) or not _COLOR_RE.match(color):
        raise ValueError("Colour must look like #2d8cff")
    return color.lower()


def _clock(local):
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def format_when(start_utc, tz_name):
    local = start_utc.astimezone(safe_zone(tz_name))
    return f"{local.strftime('%A')} {local.day} {local.strftime('%B %Y')}, {_clock(local)} ({tz_name or 'UTC'})"


def _lead_text(minutes):
    if minutes <= 75:
        return f"about {minutes} minute{'s' if minutes != 1 else ''}"
    hours = round(minutes / 60)
    return f"about {hours} hour{'s' if hours != 1 else ''}"


def reminder_email(event, now):
    """(subject, body) for the 'starting soon' email. `event` carries starts_at, title, notes, username, timezone."""
    start = parse_instant(event["starts_at"])
    minutes = max(1, round((start - now).total_seconds() / 60))
    tz_name = event.get("timezone") or "UTC"
    title = one_line(event["title"], 100)
    local = start.astimezone(safe_zone(tz_name))
    subject = f"Reminder: {title} at {_clock(local)}"
    lines = [
        f"Hi {event.get('username') or 'there'},",
        "",
        f"Your event \"{title}\" starts in {_lead_text(minutes)}.",
        "",
        f"When: {format_when(start, tz_name)}",
    ]
    notes = (event.get("notes") or "").strip()
    if notes:
        lines += ["", "Notes:", notes]
    app_url = os.getenv("APP_URL", "").strip()
    if app_url.startswith(("https://", "http://")):
        lines += ["", f"Open your calendar: {app_url}"]
    lines += [
        "",
        "-- Man Switch",
        "You're getting this because this address is on a Man Switch account. If that isn't you, you can ignore this email.",
    ]
    return subject, "\n".join(lines)
