"""Shared in-memory stand-ins for the database and storage layers."""
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "sb_secret_test_key")

import main  # noqa: E402
import push  # noqa: E402
import ratelimit  # noqa: E402
import scheduler  # noqa: E402
import habits  # noqa: E402
import dashboard  # noqa: E402
import planner  # noqa: E402
import todo_logic  # noqa: E402
import todos  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

REAL_BOOTSTRAP_ADMIN = main.bootstrap_admin_if_configured

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class FakeStore:
    def __init__(self):
        self.users, self.checkins, self.subs, self.settings = {}, {}, [], {}
        self.tasks, self.next_task = {}, 1
        self.habits, self.next_habit, self.habit_logs = {}, 1, {}
        self.events, self.next_event = {}, 1
        self.next_user, self.next_checkin = 1, 1
        self.uploads, self.deleted_images = [], []

    # users
    def create_user(self, username, password_hash, is_admin=False, email=None):
        user = {"id": self.next_user, "username": username, "password_hash": password_hash,
                "is_admin": is_admin, "created_at": now_iso(), "last_alert_at": None,
                "timezone": "UTC", "todo_reminders": True, "quiet_start": 22, "quiet_end": 7,
                "last_todo_reminder_at": None, "widget_token_hash": None, "email": email}
        self.users[user["id"]] = user
        self.next_user += 1
        return {k: user[k] for k in ("id", "username", "is_admin", "created_at", "email")}

    def get_user_by_username(self, username):
        return next((dict(u) for u in self.users.values() if u["username"] == username), None)

    def get_user_by_id(self, user_id):
        u = self.users.get(user_id)
        return None if not u else {k: u[k] for k in ("id", "username", "is_admin", "created_at", "email")}

    def set_user_admin(self, user_id, is_admin):
        self.users[user_id]["is_admin"] = is_admin

    def set_user_email(self, user_id, email):
        self.users[user_id]["email"] = email

    def has_users(self):
        return bool(self.users)

    def get_unowned_checkin_count(self):
        return 0

    def assign_unowned_checkins(self, user_id):
        return None

    def admin_list_users(self):
        rows = []
        for u in self.users.values():
            mine = [c for c in self.checkins.values() if c["user_id"] == u["id"]]
            rows.append({**{k: u[k] for k in ("id", "username", "is_admin", "created_at", "last_alert_at")},
                         "entries": len(mine),
                         "last_checkin": max((c["timestamp"] for c in mine), default=None),
                         "devices": len([s for s in self.subs if s["user_id"] == u["id"]])})
        return rows

    def admin_delete_user(self, user_id):
        if user_id not in self.users:
            return False, []
        paths = [c["image_url"] for c in self.checkins.values() if c["user_id"] == user_id and c["image_url"]]
        self.checkins = {k: c for k, c in self.checkins.items() if c["user_id"] != user_id}
        self.subs = [s for s in self.subs if s["user_id"] != user_id]
        self.tasks = {k: t for k, t in self.tasks.items() if t["user_id"] != user_id}
        self.habits = {k: h for k, h in self.habits.items() if h["user_id"] != user_id}
        self.habit_logs = {k: v for k, v in self.habit_logs.items() if v["user_id"] != user_id}
        self.events = {k: e for k, e in self.events.items() if e["user_id"] != user_id}
        del self.users[user_id]
        return True, paths

    # settings
    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    # check-ins
    def add_checkin(self, user_id, note, category="journal", image_url=None):
        c = {"id": self.next_checkin, "user_id": user_id, "timestamp": now_iso(),
             "progress_note": note, "image_url": image_url, "category": category}
        self.checkins[c["id"]] = c
        self.next_checkin += 1

    def _row(self, c):
        return (c["id"], c["timestamp"], c["progress_note"], c["image_url"], c["category"])

    def get_all_checkins(self, user_id, category=None):
        rows = [c for c in self.checkins.values()
                if c["user_id"] == user_id and (category is None or c["category"] == category)]
        rows.sort(key=lambda c: c["timestamp"], reverse=True)
        return [self._row(c) for c in rows]

    def list_activity(self, user_id, since_iso, limit=20000):
        rows = [{"timestamp": c["timestamp"], "category": c["category"]} for c in self.checkins.values()
                if c["user_id"] == user_id and c["timestamp"] >= since_iso]
        rows.sort(key=lambda r: r["timestamp"])
        return rows[:limit]

    def get_checkin_by_id(self, checkin_id, user_id):
        c = self.checkins.get(checkin_id)
        return self._row(c) if c and c["user_id"] == user_id else None

    def update_checkin(self, checkin_id, user_id, note, category, image_url=None, remove_image=False):
        c = self.checkins.get(checkin_id)
        if not c or c["user_id"] != user_id:
            return False
        c["progress_note"], c["category"] = note, category
        if image_url is not None:
            c["image_url"] = image_url
        elif remove_image:
            c["image_url"] = None
        return True

    def delete_checkin(self, checkin_id, user_id):
        c = self.checkins.get(checkin_id)
        if not c or c["user_id"] != user_id:
            return None, False
        del self.checkins[checkin_id]
        return c["image_url"], True

    def get_last_checkin_time(self, user_id):
        return max((c["timestamp"] for c in self.checkins.values() if c["user_id"] == user_id), default=None)

    def list_pulses(self, user_id, since_iso, limit=500):
        rows = [c["timestamp"] for c in self.checkins.values()
                if c["user_id"] == user_id and c["category"] == "pulse" and c["timestamp"] >= since_iso]
        return sorted(rows, reverse=True)[:limit]

    # deadline / alerts
    def get_users_for_deadline_check(self):
        out = []
        for u in self.users.values():
            mine = [c["timestamp"] for c in self.checkins.values() if c["user_id"] == u["id"]]
            out.append({"id": u["id"], "username": u["username"], "last_alert_at": u["last_alert_at"],
                        "last_checkin": max(mine, default=None)})
        return out

    def claim_alert(self, user_id, now, cutoff):
        prev = self.users[user_id]["last_alert_at"]
        if prev is not None and prev >= cutoff:
            return False, prev
        self.users[user_id]["last_alert_at"] = now
        return True, prev

    def release_alert(self, user_id, previous):
        self.users[user_id]["last_alert_at"] = previous

    # tasks
    def add_task(self, user_id, title, day, priority=0):
        t = {"id": self.next_task, "user_id": user_id, "title": title, "day": day,
             "priority": priority, "created_at": todo_logic.utcnow().isoformat(), "done_at": None, "postponed": 0}
        self.tasks[t["id"]] = t
        self.next_task += 1
        return self._task(t)

    @staticmethod
    def _task(t):
        return {k: t[k] for k in ("id", "title", "day", "priority", "created_at", "done_at", "postponed")}

    def get_task(self, task_id, user_id):
        t = self.tasks.get(task_id)
        return self._task(t) if t and t["user_id"] == user_id else None

    def list_tasks(self, user_id, start_day=None, end_day=None, only_open=False):
        rows = [t for t in self.tasks.values() if t["user_id"] == user_id
                and (start_day is None or t["day"] >= start_day)
                and (end_day is None or t["day"] <= end_day)
                and (not only_open or t["done_at"] is None)]
        rows.sort(key=lambda t: (t["day"], -t["priority"], t["id"]))
        return [self._task(t) for t in rows]

    def count_tasks_on_day(self, user_id, day):
        return len([t for t in self.tasks.values() if t["user_id"] == user_id and t["day"] == day])

    def update_task(self, task_id, user_id, title=None, day=None, priority=None, done=None):
        t = self.tasks.get(task_id)
        if not t or t["user_id"] != user_id:
            return None
        if title is not None:
            t["title"] = title
        if day is not None:
            if day > t["day"]:
                t["postponed"] += 1
            t["day"] = day
        if priority is not None:
            t["priority"] = priority
        if done is True and t["done_at"] is None:
            t["done_at"] = todo_logic.utcnow().isoformat()
        elif done is False:
            t["done_at"] = None
        return self._task(t)

    def delete_task(self, task_id, user_id):
        t = self.tasks.get(task_id)
        if not t or t["user_id"] != user_id:
            return False
        del self.tasks[task_id]
        return True

    def task_day_counts(self, user_id, start_day, end_day):
        out = {}
        for t in self.tasks.values():
            if t["user_id"] == user_id and start_day <= t["day"] <= end_day:
                r = out.setdefault(t["day"], {"day": t["day"], "planned": 0, "done": 0})
                r["planned"] += 1
                r["done"] += 1 if t["done_at"] else 0
        return sorted(out.values(), key=lambda r: r["day"])

    def get_todo_settings(self, user_id):
        u = self.users.get(user_id)
        if not u:
            return None
        return {"timezone": u["timezone"], "todo_reminders": u["todo_reminders"], "quiet_start": u["quiet_start"],
                "quiet_end": u["quiet_end"], "widget_configured": u["widget_token_hash"] is not None}

    def update_todo_settings(self, user_id, timezone_name=None, todo_reminders=None, quiet_start=None, quiet_end=None):
        u = self.users[user_id]
        for key, val in (("timezone", timezone_name), ("todo_reminders", todo_reminders),
                         ("quiet_start", quiet_start), ("quiet_end", quiet_end)):
            if val is not None:
                u[key] = val

    def set_widget_token_hash(self, user_id, token_hash):
        self.users[user_id]["widget_token_hash"] = token_hash

    def get_user_by_widget_token_hash(self, token_hash):
        u = next((u for u in self.users.values() if u["widget_token_hash"] == token_hash), None)
        return {"id": u["id"], "timezone": u["timezone"]} if u else None

    def get_users_with_open_tasks(self):
        ids = {t["user_id"] for t in self.tasks.values() if t["done_at"] is None}
        keys = ("id", "timezone", "todo_reminders", "quiet_start", "quiet_end", "last_todo_reminder_at")
        return [{k: self.users[i][k] for k in keys} for i in sorted(ids)]

    def claim_todo_reminder(self, user_id, now, cutoff):
        prev = self.users[user_id]["last_todo_reminder_at"]
        if prev is not None and prev >= cutoff:
            return False, prev
        self.users[user_id]["last_todo_reminder_at"] = now
        return True, prev

    def release_todo_reminder(self, user_id, previous):
        self.users[user_id]["last_todo_reminder_at"] = previous

    # habits
    _HABIT_KEYS = ("id", "name", "icon", "color", "days", "created_day", "created_at")

    def add_habit(self, user_id, name, icon, color, days, created_day):
        h = {"id": self.next_habit, "user_id": user_id, "name": name, "icon": icon, "color": color,
             "days": days, "created_day": created_day, "created_at": now_iso()}
        self.habits[h["id"]] = h
        self.next_habit += 1
        return {k: h[k] for k in self._HABIT_KEYS}

    def get_habit(self, habit_id, user_id):
        h = self.habits.get(habit_id)
        return {k: h[k] for k in self._HABIT_KEYS} if h and h["user_id"] == user_id else None

    def list_habits(self, user_id):
        return [{k: h[k] for k in self._HABIT_KEYS} for h in self.habits.values() if h["user_id"] == user_id]

    def count_habits(self, user_id):
        return len(self.list_habits(user_id))

    def update_habit(self, habit_id, user_id, name=None, icon=None, color=None, days=None):
        h = self.habits.get(habit_id)
        if not h or h["user_id"] != user_id:
            return None
        for key, val in (("name", name), ("icon", icon), ("color", color), ("days", days)):
            if val is not None:
                h[key] = val
        return {k: h[k] for k in self._HABIT_KEYS}

    def delete_habit(self, habit_id, user_id):
        h = self.habits.get(habit_id)
        if not h or h["user_id"] != user_id:
            return False
        del self.habits[habit_id]
        self.habit_logs = {k: v for k, v in self.habit_logs.items() if k[0] != habit_id}
        return True

    def set_habit_log(self, habit_id, user_id, day, note=None):
        row = self.habit_logs.setdefault((habit_id, day), {"user_id": user_id, "habit_id": habit_id, "day": day, "note": "", "created_at": now_iso()})
        if note is not None:
            row["note"] = note
        return {"day": day, "note": row["note"]}

    def delete_habit_log(self, habit_id, user_id, day):
        row = self.habit_logs.get((habit_id, day))
        if not row or row["user_id"] != user_id:
            return False
        del self.habit_logs[(habit_id, day)]
        return True

    def list_habit_logs(self, user_id):
        rows = [v for v in self.habit_logs.values() if v["user_id"] == user_id]
        return sorted(({"habit_id": v["habit_id"], "day": v["day"], "note": v["note"], "created_at": v.get("created_at")} for v in rows), key=lambda r: r["day"])

    # calendar events
    _EVENT_KEYS = ("id", "title", "notes", "color", "starts_at", "created_at", "reminded_at")

    def _event(self, e):
        return {k: e[k] for k in self._EVENT_KEYS}

    def add_event(self, user_id, title, notes, color, starts_at):
        e = {"id": self.next_event, "user_id": user_id, "title": title, "notes": notes, "color": color,
             "starts_at": starts_at, "created_at": now_iso(), "reminded_at": None}
        self.events[e["id"]] = e
        self.next_event += 1
        return self._event(e)

    def get_event(self, event_id, user_id):
        e = self.events.get(event_id)
        return self._event(e) if e and e["user_id"] == user_id else None

    def list_events(self, user_id, start_iso, end_iso):
        rows = [e for e in self.events.values()
                if e["user_id"] == user_id and start_iso <= e["starts_at"] < end_iso]
        rows.sort(key=lambda e: (e["starts_at"], e["id"]))
        return [self._event(e) for e in rows]

    def count_events(self, user_id):
        return len([e for e in self.events.values() if e["user_id"] == user_id])

    def update_event(self, event_id, user_id, title=None, notes=None, color=None, starts_at=None):
        e = self.events.get(event_id)
        if not e or e["user_id"] != user_id:
            return None
        for key, val in (("title", title), ("notes", notes), ("color", color), ("starts_at", starts_at)):
            if val is not None:
                e[key] = val
        if starts_at is not None:
            e["reminded_at"] = None
        return self._event(e)

    def delete_event(self, event_id, user_id):
        e = self.events.get(event_id)
        if not e or e["user_id"] != user_id:
            return False
        del self.events[event_id]
        return True

    def list_due_event_reminders(self, now_iso, until_iso, limit=50):
        out = []
        for e in sorted(self.events.values(), key=lambda e: e["starts_at"]):
            u = self.users[e["user_id"]]
            if (e["reminded_at"] is None and now_iso < e["starts_at"] <= until_iso and u.get("email")):
                out.append({"id": e["id"], "title": e["title"], "notes": e["notes"], "starts_at": e["starts_at"],
                            "email": u["email"], "username": u["username"], "timezone": u["timezone"]})
        return out[:limit]

    def claim_event_reminder(self, event_id, now_iso):
        e = self.events.get(event_id)
        if not e or e["reminded_at"] is not None:
            return False
        e["reminded_at"] = now_iso
        return True

    def release_event_reminder(self, event_id):
        if event_id in self.events:
            self.events[event_id]["reminded_at"] = None

    # push subscriptions
    def upsert_push_subscription(self, user_id, endpoint, p256dh, auth, user_agent=None):
        for s in self.subs:
            if s["endpoint"] == endpoint:
                s.update(user_id=user_id, p256dh=p256dh, auth=auth)
                return
        self.subs.append({"id": len(self.subs) + 1, "user_id": user_id, "endpoint": endpoint,
                          "p256dh": p256dh, "auth": auth})

    def get_push_subscriptions(self, user_id):
        return [dict(s) for s in self.subs if s["user_id"] == user_id]

    def count_push_subscriptions(self, user_id):
        return len([s for s in self.subs if s["user_id"] == user_id])

    def delete_push_subscription(self, endpoint, user_id=None):
        before = len(self.subs)
        self.subs = [s for s in self.subs if not (s["endpoint"] == endpoint and (user_id is None or s["user_id"] == user_id))]
        return len(self.subs) < before

    # storage
    def upload_image(self, file_bytes, content_type, category):
        path = f"{category}/img-{len(self.uploads) + 1}.{content_type.split('/')[-1]}"
        self.uploads.append((path, content_type, len(file_bytes)))
        return path

    def delete_image(self, path):
        self.deleted_images.append(path)
        return True

    def get_signed_url(self, path):
        return f"https://cdn.example/{path}"


STORE_NAMES = [
    "create_user", "get_user_by_username", "get_user_by_id", "set_user_admin", "has_users",
    "get_unowned_checkin_count", "assign_unowned_checkins", "admin_list_users", "admin_delete_user",
    "get_setting", "set_setting", "add_checkin", "get_all_checkins", "get_checkin_by_id",
    "update_checkin", "delete_checkin", "get_users_for_deadline_check", "claim_alert", "release_alert",
    "upsert_push_subscription", "get_push_subscriptions", "count_push_subscriptions",
    "delete_push_subscription", "upload_image", "delete_image", "get_signed_url",
    "add_task", "get_task", "list_tasks", "count_tasks_on_day", "update_task", "delete_task",
    "task_day_counts", "get_todo_settings", "update_todo_settings", "set_widget_token_hash",
    "get_user_by_widget_token_hash", "get_users_with_open_tasks", "claim_todo_reminder",
    "release_todo_reminder",
    "add_habit", "get_habit", "list_habits", "count_habits", "update_habit", "delete_habit",
    "set_habit_log", "delete_habit_log", "list_habit_logs",
    "set_user_email", "get_last_checkin_time", "list_pulses", "list_activity",
    "add_event", "get_event", "list_events", "count_events", "update_event", "delete_event",
    "list_due_event_reminders", "claim_event_reminder", "release_event_reminder",
]


class DummyScheduler:
    def add_job(self, *a, **k):
        return None

    def start(self):
        return None

    def shutdown(self):
        return None


class AppTestCase(unittest.TestCase):
    """Fresh fake store, cleared rate limits and no real DB/scheduler for every test."""

    def setUp(self):
        self.store = FakeStore()
        ratelimit.clear_all()
        patchers = [
            patch.object(main, "init_db", lambda: None),
            patch.object(main, "BackgroundScheduler", DummyScheduler),
            patch.object(main, "bootstrap_legacy_user_if_needed", lambda: None),
            patch.object(main, "bootstrap_admin_if_configured", lambda: None),
            patch.dict(os.environ, {"TRUSTED_PROXY_HOPS": "1"}),
        ]
        for module in (main, scheduler, push, todos, habits, planner, dashboard):
            for name in STORE_NAMES:
                if hasattr(module, name):
                    patchers.append(patch.object(module, name, getattr(self.store, name)))
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def client(self):
        return TestClient(main.app)

    def register(self, client, name="alice", **extra):
        data = {"username": name, "password": "strong-password", "confirm_password": "strong-password"}
        data.update(extra)
        return client.post("/auth/register", data=data)

    def make_admin(self, client, name="owner"):
        res = self.register(client, name)
        self.assertEqual(res.status_code, 200, res.text)
        uid = res.json()["user"]["id"]
        self.store.set_user_admin(uid, True)
        return uid
