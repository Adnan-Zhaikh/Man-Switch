"""Habit tracker API: daily habits with a GitHub-style commit history and streaks."""
import re
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import habit_logic as hl
import todo_logic
from database import (
    add_habit,
    count_habits,
    delete_habit,
    delete_habit_log,
    get_habit,
    get_todo_settings,
    list_habit_logs,
    list_habits,
    set_habit_log,
    update_habit,
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class HabitIn(BaseModel):
    name: str = Field(max_length=500)
    icon: str = Field(hl.DEFAULT_ICON, max_length=50)
    color: str = Field(hl.DEFAULT_COLOR, max_length=20)
    days: str = Field(hl.DEFAULT_DAYS, max_length=20)


class HabitPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=500)
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)
    days: Optional[str] = Field(None, max_length=20)


class LogIn(BaseModel):
    done: bool = True
    note: Optional[str] = Field(None, max_length=2000)


def _check(fn, value):
    try:
        return fn(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _today_for(user_id):
    tz = (get_todo_settings(user_id) or {}).get("timezone") or "UTC"
    return tz, todo_logic.local_today(tz)


def _view(h):
    return {k: h[k] for k in ("id", "name", "icon", "color", "days", "created_day")}


def build_router(get_current_user, enforce, write_limiter):
    router = APIRouter()

    @router.get("/habits")
    def overview(user=Depends(get_current_user)):
        tz, today = _today_for(user["id"])
        data = hl.build_overview(list_habits(user["id"]), list_habit_logs(user["id"]), today.isoformat())
        data["timezone"] = tz
        data["limits"] = {"max_habits": hl.MAX_HABITS, "backfill_days": hl.BACKFILL_DAYS, "max_note": hl.MAX_NOTE_LEN}
        return data

    @router.post("/habits")
    def create_habit(body: HabitIn, user=Depends(get_current_user)):
        enforce(write_limiter, f"user:{user['id']}")
        name = _check(hl.clean_name, body.name)
        icon = _check(hl.clean_icon, body.icon)
        color = _check(hl.validate_color, body.color)
        days = _check(hl.validate_days, body.days)
        if count_habits(user["id"]) >= hl.MAX_HABITS:
            raise HTTPException(status_code=409, detail=f"At most {hl.MAX_HABITS} habits")
        _, today = _today_for(user["id"])
        return _view(add_habit(user["id"], name, icon, color, days, today.isoformat()))

    @router.patch("/habits/{habit_id:int}")
    def change_habit(habit_id: int, body: HabitPatch, user=Depends(get_current_user)):
        enforce(write_limiter, f"user:{user['id']}")
        fields = {
            "name": _check(hl.clean_name, body.name) if body.name is not None else None,
            "icon": _check(hl.clean_icon, body.icon) if body.icon is not None else None,
            "color": _check(hl.validate_color, body.color) if body.color is not None else None,
            "days": _check(hl.validate_days, body.days) if body.days is not None else None,
        }
        updated = update_habit(habit_id, user["id"], **fields)
        if not updated:
            raise HTTPException(status_code=404, detail="Habit not found")
        return _view(updated)

    @router.delete("/habits/{habit_id:int}")
    def remove_habit(habit_id: int, user=Depends(get_current_user)):
        enforce(write_limiter, f"user:{user['id']}")
        if not delete_habit(habit_id, user["id"]):
            raise HTTPException(status_code=404, detail="Habit not found")
        return {"status": "deleted"}

    @router.put("/habits/{habit_id:int}/logs/{day}")
    def commit(habit_id: int, day: str, body: LogIn, user=Depends(get_current_user)):
        """Commit (done=true, optional message) or un-commit (done=false) one habit for one day."""
        enforce(write_limiter, f"user:{user['id']}")
        try:
            if not _DATE_RE.match(day):
                raise ValueError
            when = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(status_code=422, detail="Date must look like 2026-10-31")
        _, today = _today_for(user["id"])
        if when > today:
            raise HTTPException(status_code=422, detail="You can't commit a day that hasn't happened yet")
        if when < today - timedelta(days=hl.BACKFILL_DAYS):
            raise HTTPException(status_code=422, detail=f"Only the last {hl.BACKFILL_DAYS} days can be changed")
        if not get_habit(habit_id, user["id"]):
            raise HTTPException(status_code=404, detail="Habit not found")

        if not body.done:
            delete_habit_log(habit_id, user["id"], day)
            return {"day": day, "done": False, "note": ""}
        note = _check(hl.clean_note, body.note) if body.note is not None else None
        row = set_habit_log(habit_id, user["id"], day, note)
        return {"day": day, "done": True, "note": row["note"]}

    return router
