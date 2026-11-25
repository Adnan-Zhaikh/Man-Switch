"""Dashboard API: one read-only endpoint that summarises the signed-in user's own data."""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import dashboard_logic as dl
import todo_logic
from database import get_todo_settings, list_activity, list_habit_logs, list_habits, list_tasks
from scheduler import DEADLINE_HOURS


def build_router(get_current_user):
    router = APIRouter()

    @router.get("/dashboard")
    def dashboard(
        days: int = Query(dl.DEFAULT_RANGE, alias="range"),
        tz: Optional[str] = Query(None, max_length=64),
        user=Depends(get_current_user),
    ):
        if days not in dl.RANGES:
            raise HTTPException(status_code=422, detail="range must be 7, 30 or 90")
        if tz:
            try:
                tz_name = todo_logic.validate_timezone(tz)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        else:
            tz_name = (get_todo_settings(user["id"]) or {}).get("timezone") or "UTC"
        now = todo_logic.utcnow()
        today = todo_logic.local_today(tz_name, now)
        since_day = (today - timedelta(days=dl.HISTORY_DAYS)).isoformat()
        since_ts = (now - timedelta(days=dl.HISTORY_DAYS + 1)).isoformat()
        return dl.build_dashboard(
            now=now,
            today_iso=today.isoformat(),
            tz_name=tz_name,
            range_days=days,
            habits=list_habits(user["id"]),
            habit_logs=list_habit_logs(user["id"]),
            tasks=list_tasks(user["id"], start_day=since_day),
            activity=list_activity(user["id"], since_ts),
            deadline_hours=DEADLINE_HOURS,
        )

    return router
