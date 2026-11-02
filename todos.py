"""To-do API: tasks, daily history/stats, reminder settings and the widget feed."""
import hashlib
import re
import secrets
from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

import todo_logic
from database import (
    add_task,
    count_tasks_on_day,
    delete_task,
    get_task,
    get_todo_settings,
    get_user_by_widget_token_hash,
    list_tasks,
    set_widget_token_hash,
    task_day_counts,
    update_task,
    update_todo_settings,
)
from ratelimit import RateLimiter

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
widget_fail_limiter = RateLimiter(20, 600)  # wrong widget tokens, per IP


class TaskIn(BaseModel):
    title: str = Field(max_length=1000)
    day: Literal["today", "tomorrow"] = "today"
    priority: int = Field(0, ge=0, le=1)


class TaskPatch(BaseModel):
    title: Optional[str] = Field(None, max_length=1000)
    day: Optional[Literal["today", "tomorrow"]] = None
    priority: Optional[int] = Field(None, ge=0, le=1)
    done: Optional[bool] = None


class TodoSettingsIn(BaseModel):
    timezone: Optional[str] = Field(None, max_length=64)
    reminders: Optional[bool] = None
    quiet_start: Optional[int] = Field(None, ge=0, le=23)
    quiet_end: Optional[int] = Field(None, ge=0, le=23)


def _clean_title(title):
    cleaned = " ".join((title or "").split())
    if not cleaned:
        raise HTTPException(status_code=422, detail="Task can't be empty")
    if len(cleaned) > todo_logic.MAX_TITLE_LEN:
        raise HTTPException(status_code=422, detail=f"Task must be at most {todo_logic.MAX_TITLE_LEN} characters")
    return cleaned


def _view(task):
    return {
        "id": task["id"],
        "title": task["title"],
        "day": task["day"],
        "priority": task["priority"],
        "created_at": task["created_at"],
        "done": task["done_at"] is not None,
        "done_at": task["done_at"],
    }


def _tz(user_id):
    settings = get_todo_settings(user_id) or {}
    return settings.get("timezone") or "UTC"


def _today_for(user_id):
    return todo_logic.local_today(_tz(user_id)).isoformat()


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def build_router(get_current_user, enforce, write_limiter, client_ip, too_many):
    router = APIRouter()

    # ---- tasks -----------------------------------------------------------

    @router.get("/todos")
    def todos(user=Depends(get_current_user)):
        tz = _tz(user["id"])
        today_d = todo_logic.local_today(tz)
        today = today_d.isoformat()
        tomorrow = (today_d + timedelta(days=1)).isoformat()
        # everything outstanding up to today, plus today's and tomorrow's list
        rows = list_tasks(user["id"], end_day=tomorrow)
        visible = [t for t in rows if t["day"] >= today or t["done_at"] is None]
        summary = todo_logic.summarize_today(visible, today)
        return {
            "today": today,
            "timezone": tz,
            "summary": summary,
            "overdue": [_view(t) for t in visible if t["day"] < today],
            "today_tasks": [_view(t) for t in visible if t["day"] == today],
            "tomorrow": [_view(t) for t in visible if t["day"] == tomorrow],
        }

    @router.post("/todos")
    def create_task(body: TaskIn, user=Depends(get_current_user)):
        enforce(write_limiter, f"user:{user['id']}")
        title = _clean_title(body.title)
        today_d = todo_logic.local_today(_tz(user["id"]))
        day = (today_d + timedelta(days=1 if body.day == "tomorrow" else 0)).isoformat()
        if count_tasks_on_day(user["id"], day) >= todo_logic.MAX_TASKS_PER_DAY:
            raise HTTPException(status_code=409, detail=f"At most {todo_logic.MAX_TASKS_PER_DAY} tasks per day")
        return _view(add_task(user["id"], title, day, body.priority))

    @router.patch("/todos/{task_id:int}")
    def change_task(task_id: int, body: TaskPatch, user=Depends(get_current_user)):
        enforce(write_limiter, f"user:{user['id']}")
        title = _clean_title(body.title) if body.title is not None else None
        day = None
        if body.day is not None:
            today_d = todo_logic.local_today(_tz(user["id"]))
            day = (today_d + timedelta(days=1 if body.day == "tomorrow" else 0)).isoformat()
            if count_tasks_on_day(user["id"], day) >= todo_logic.MAX_TASKS_PER_DAY:
                raise HTTPException(status_code=409, detail=f"At most {todo_logic.MAX_TASKS_PER_DAY} tasks per day")
        updated = update_task(task_id, user["id"], title=title, day=day, priority=body.priority, done=body.done)
        if not updated:
            raise HTTPException(status_code=404, detail="Task not found")
        return _view(updated)

    @router.delete("/todos/{task_id:int}")
    def remove_task(task_id: int, user=Depends(get_current_user)):
        enforce(write_limiter, f"user:{user['id']}")
        if not delete_task(task_id, user["id"]):
            raise HTTPException(status_code=404, detail="Task not found")
        return {"status": "deleted"}

    # ---- history & stats ---------------------------------------------------

    @router.get("/todos/day/{day}")
    def tasks_on_day(day: str, user=Depends(get_current_user)):
        try:
            if not _DATE_RE.match(day):
                raise ValueError
            date.fromisoformat(day)
        except ValueError:
            raise HTTPException(status_code=422, detail="Date must look like 2026-10-31")
        return {"date": day, "tasks": [_view(t) for t in list_tasks(user["id"], day, day)]}

    @router.get("/todos/stats")
    def stats(range: Literal["week", "month"] = "week", user=Depends(get_current_user)):
        today_d = todo_logic.local_today(_tz(user["id"]))
        counts = task_day_counts(user["id"], (today_d - timedelta(days=400)).isoformat(), today_d.isoformat())
        return todo_logic.build_stats(counts, today_d.isoformat(), range)

    # ---- settings ----------------------------------------------------------

    def _settings_view(user_id):
        s = get_todo_settings(user_id) or {}
        return {
            "timezone": s.get("timezone", "UTC"),
            "reminders": bool(s.get("todo_reminders", True)),
            "quiet_start": s.get("quiet_start", 22),
            "quiet_end": s.get("quiet_end", 7),
            "reminder_hours": todo_logic.REMINDER_HOURS,
            "widget_configured": bool(s.get("widget_configured")),
        }

    @router.get("/todos/settings")
    def read_settings(user=Depends(get_current_user)):
        return _settings_view(user["id"])

    @router.put("/todos/settings")
    def write_settings(body: TodoSettingsIn, user=Depends(get_current_user)):
        enforce(write_limiter, f"user:{user['id']}")
        tz = None
        if body.timezone is not None:
            try:
                tz = todo_logic.validate_timezone(body.timezone)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        update_todo_settings(
            user["id"],
            timezone_name=tz,
            todo_reminders=body.reminders,
            quiet_start=body.quiet_start,
            quiet_end=body.quiet_end,
        )
        return _settings_view(user["id"])

    # ---- Android widget feed -------------------------------------------------

    @router.post("/todos/widget-token")
    def create_widget_token(user=Depends(get_current_user)):
        enforce(write_limiter, f"user:{user['id']}")
        token = secrets.token_urlsafe(32)
        set_widget_token_hash(user["id"], hash_token(token))  # only the hash is stored
        return {"token": token}

    @router.delete("/todos/widget-token")
    def revoke_widget_token(user=Depends(get_current_user)):
        set_widget_token_hash(user["id"], None)
        return {"status": "revoked"}

    @router.get("/widget/summary")
    def widget_summary(request: Request, token: str = "", format: Literal["json", "text"] = "json"):
        """Read-only counts for a home-screen widget app. Authenticated by a personal token, not a login."""
        ip = client_ip(request)
        allowed, retry_after = widget_fail_limiter.check(ip)
        if not allowed:
            raise too_many(retry_after)
        header = request.headers.get("authorization", "")
        provided = header[7:] if header.lower().startswith("bearer ") else token
        owner = get_user_by_widget_token_hash(hash_token(provided)) if provided else None
        if not owner:
            widget_fail_limiter.record(ip)
            raise HTTPException(status_code=401, detail="Invalid token")

        today = todo_logic.local_today(owner["timezone"]).isoformat()
        rows = list_tasks(owner["id"], end_day=today)
        summary = todo_logic.summarize_today([t for t in rows if t["day"] == today or t["done_at"] is None], today)
        headers = {"Cache-Control": "no-store"}
        if format == "text":
            return PlainTextResponse(f"{summary['done']} done · {summary['left']} left", headers=headers)
        return JSONResponse(summary, headers=headers)

    return router
