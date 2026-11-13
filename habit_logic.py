"""Pure helpers for the habit tracker: schedules, streaks, score and the overview payload.

Nothing here touches the database, so it is easy to test.

A habit has a weekly schedule, stored as 7 characters of "0"/"1" starting on Monday
("1111111" = every day, "1111100" = weekdays). A "commit" is one done-day for one habit,
like a commit on a GitHub contribution graph.
"""
import re
from datetime import date, timedelta

MAX_HABITS = 20
MAX_NAME_LEN = 60
MAX_NOTE_LEN = 200
MAX_ICON_LEN = 8
BACKFILL_DAYS = 30     # how far back a missed day can still be committed
STRIP_DAYS = 14        # days shown in the week strip
SCORE_DAYS = 30        # window for the habit score
HISTORY_DAYS = 400     # how much commit history the page receives (53 weeks fit in this)

DEFAULT_DAYS = "1111111"
DEFAULT_ICON = "✅"
DEFAULT_COLOR = "#3fb950"

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def validate_days(mask):
    """Return the mask if it is 7 x '0'/'1' with at least one '1', else raise ValueError."""
    if not isinstance(mask, str) or len(mask) != 7 or set(mask) - {"0", "1"} or "1" not in mask:
        raise ValueError("Pick at least one day of the week")
    return mask


def validate_color(color):
    if not isinstance(color, str) or not _COLOR_RE.match(color):
        raise ValueError("Colour must look like #3fb950")
    return color.lower()


def clean_name(name):
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise ValueError("Give the habit a name")
    if len(cleaned) > MAX_NAME_LEN:
        raise ValueError(f"Name must be at most {MAX_NAME_LEN} characters")
    return cleaned


def clean_icon(icon):
    cleaned = (icon or "").strip()
    if not cleaned or len(cleaned) > MAX_ICON_LEN:
        raise ValueError("Pick an icon")
    return cleaned


def clean_note(note):
    cleaned = " ".join((note or "").split())
    if len(cleaned) > MAX_NOTE_LEN:
        raise ValueError(f"Commit message must be at most {MAX_NOTE_LEN} characters")
    return cleaned


def is_due(mask, day):
    """Is the habit scheduled on this date? (`day` is a datetime.date; Monday is index 0.)"""
    return mask[day.weekday()] == "1"


def _start_day(habit, done_days):
    """First day that counts for a habit: its creation day, or an earlier backfilled commit."""
    return min([date.fromisoformat(habit["created_day"])] + [date.fromisoformat(d) for d in done_days])


def streaks(mask, done_days, start, today):
    """(current, best) streak, counted in scheduled days.

    Days the habit isn't scheduled are skipped (they neither extend nor break a streak).
    Today doesn't break the streak until it is over, so a not-yet-done today is skipped.
    """
    run = best = 0
    d = start
    while d <= today:
        if is_due(mask, d):
            if d.isoformat() in done_days:
                run += 1
                best = max(best, run)
            elif d != today:
                run = 0
        d += timedelta(days=1)
    return run, best


def completion(mask, done_days, start, today, window=SCORE_DAYS):
    """(done, due) scheduled days in the last `window` days. Today only counts once it's done."""
    d = max(start, today - timedelta(days=window - 1))
    done = due = 0
    while d <= today:
        if is_due(mask, d):
            finished = d.isoformat() in done_days
            if d < today or finished:
                due += 1
                done += 1 if finished else 0
        d += timedelta(days=1)
    return done, due


def build_overview(habits, logs, today_iso):
    """Everything the habits page needs.

    habits: rows of {id, name, icon, color, days, created_day}
    logs:   rows of {habit_id, day, note}  (all of the user's commits)
    """
    today = date.fromisoformat(today_iso)
    by_habit = {}
    for row in logs:
        by_habit.setdefault(row["habit_id"], {})[row["day"]] = row["note"] or ""

    cutoff = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    strip_days = [today - timedelta(days=i) for i in range(STRIP_DAYS - 1, -1, -1)]
    week = {d.isoformat(): {"date": d.isoformat(), "due": 0, "done": 0} for d in strip_days}

    out, total_done, total_due = [], 0, 0
    for h in habits:
        mine = by_habit.get(h["id"], {})
        done_days = set(mine)
        start = _start_day(h, done_days)
        current, best = streaks(h["days"], done_days, start, today)
        done_n, due_n = completion(h["days"], done_days, start, today)
        total_done += done_n
        total_due += due_n

        for d in strip_days:
            if d >= start and is_due(h["days"], d):
                cell = week[d.isoformat()]
                cell["due"] += 1
                cell["done"] += 1 if d.isoformat() in done_days else 0

        out.append(
            {
                "id": h["id"],
                "name": h["name"],
                "icon": h["icon"],
                "color": h["color"],
                "days": h["days"],
                "created_day": h["created_day"],
                "streak": current,
                "best_streak": best,
                "commits": len(done_days),
                "due_today": is_due(h["days"], today),
                "done_today": today_iso in done_days,
                "logs": [{"day": d, "note": n} for d, n in sorted(mine.items()) if d >= cutoff],
            }
        )

    return {
        "today": today_iso,
        "score": round(100 * total_done / total_due, 1) if total_due else 0.0,
        "week": [week[d.isoformat()] for d in strip_days],
        "habits": out,
    }
