"""Calendar API: events with an email reminder one hour before they start.

The calendar needs an email address on the account. The reminders themselves are sent by
scheduler.run_event_reminders.
"""
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import planner_logic as pl
import todo_logic
from database import add_event, count_events, delete_event, get_event, list_events, update_event


class EventIn(BaseModel):
    title: str = Field(max_length=500)
    starts_at: str = Field(max_length=40)
    notes: str = Field("", max_length=5000)
    color: str = Field(pl.DEFAULT_COLOR, max_length=20)


class EventPatch(BaseModel):
    title: Optional[str] = Field(None, max_length=500)
    starts_at: Optional[str] = Field(None, max_length=40)
    notes: Optional[str] = Field(None, max_length=5000)
    color: Optional[str] = Field(None, max_length=20)


def _public(event):
    return {
        "id": event["id"],
        "title": event["title"],
        "notes": event["notes"],
        "color": event["color"],
        "starts_at": event["starts_at"],
        "reminder_sent": bool(event.get("reminded_at")),
    }


def _invalid(exc):
    return HTTPException(status_code=422, detail=str(exc))


def build_router(get_current_user, enforce, write_limiter):
    router = APIRouter()

    def calendar_user(user=Depends(get_current_user)):
        if not user.get("email"):
            raise HTTPException(status_code=409, detail="Add your email to use the calendar.")
        return user

    @router.get("/events")
    def events_in_range(start: str, end: str, user=Depends(calendar_user)):
        try:
            first, last = pl.parse_instant(start), pl.parse_instant(end)
        except ValueError as exc:
            raise _invalid(exc)
        if last <= first or last - first > timedelta(days=pl.MAX_RANGE_DAYS):
            raise HTTPException(status_code=422, detail=f"Ask for at most {pl.MAX_RANGE_DAYS} days at a time")
        rows = list_events(user["id"], first.isoformat(), last.isoformat())
        return {"events": [_public(e) for e in rows]}

    @router.post("/events")
    def create_event(body: EventIn, user=Depends(calendar_user)):
        enforce(write_limiter, f"user:{user['id']}")
        try:
            title = pl.clean_title(body.title)
            notes = pl.clean_notes(body.notes)
            color = pl.validate_color(body.color)
            start = pl.parse_instant(body.starts_at)
            pl.check_start_window(start, todo_logic.utcnow())
        except ValueError as exc:
            raise _invalid(exc)
        if count_events(user["id"]) >= pl.MAX_EVENTS_PER_USER:
            raise HTTPException(status_code=409, detail=f"At most {pl.MAX_EVENTS_PER_USER} events per account")
        return _public(add_event(user["id"], title, notes, color, start.isoformat()))

    @router.patch("/events/{event_id}")
    def edit_event(event_id: int, body: EventPatch, user=Depends(calendar_user)):
        enforce(write_limiter, f"user:{user['id']}")
        try:
            fields = {}
            if body.title is not None:
                fields["title"] = pl.clean_title(body.title)
            if body.notes is not None:
                fields["notes"] = pl.clean_notes(body.notes)
            if body.color is not None:
                fields["color"] = pl.validate_color(body.color)
            if body.starts_at is not None:
                start = pl.parse_instant(body.starts_at)
                pl.check_start_window(start, todo_logic.utcnow())
                fields["starts_at"] = start.isoformat()
        except ValueError as exc:
            raise _invalid(exc)
        existing = get_event(event_id, user["id"])
        if not existing:
            raise HTTPException(status_code=404, detail="Event not found")
        if fields.get("starts_at") == existing["starts_at"]:
            del fields["starts_at"]  # same time: don't re-arm a reminder that was already emailed
        if not fields:
            return _public(existing)
        updated = update_event(event_id, user["id"], **fields)
        if not updated:
            raise HTTPException(status_code=404, detail="Event not found")
        return _public(updated)

    @router.delete("/events/{event_id}")
    def remove_event(event_id: int, user=Depends(calendar_user)):
        enforce(write_limiter, f"user:{user['id']}")
        if not delete_event(event_id, user["id"]):
            raise HTTPException(status_code=404, detail="Event not found")
        return {"status": "deleted"}

    return router
