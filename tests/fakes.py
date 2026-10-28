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
from fastapi.testclient import TestClient  # noqa: E402

REAL_BOOTSTRAP_ADMIN = main.bootstrap_admin_if_configured

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class FakeStore:
    def __init__(self):
        self.users, self.checkins, self.subs, self.settings = {}, {}, [], {}
        self.next_user, self.next_checkin = 1, 1
        self.uploads, self.deleted_images = [], []

    # users
    def create_user(self, username, password_hash, is_admin=False):
        user = {"id": self.next_user, "username": username, "password_hash": password_hash,
                "is_admin": is_admin, "created_at": now_iso(), "last_alert_at": None}
        self.users[user["id"]] = user
        self.next_user += 1
        return {k: user[k] for k in ("id", "username", "is_admin", "created_at")}

    def get_user_by_username(self, username):
        return next((dict(u) for u in self.users.values() if u["username"] == username), None)

    def get_user_by_id(self, user_id):
        u = self.users.get(user_id)
        return None if not u else {k: u[k] for k in ("id", "username", "is_admin", "created_at")}

    def set_user_admin(self, user_id, is_admin):
        self.users[user_id]["is_admin"] = is_admin

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
        for module in (main, scheduler, push):
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
