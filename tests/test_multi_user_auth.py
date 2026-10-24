import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "sb_secret_test_key")

import main  # noqa: E402
from auth import hash_password, verify_password  # noqa: E402


class DummyScheduler:
    def add_job(self, *args, **kwargs):
        return None

    def start(self):
        return None

    def shutdown(self):
        return None


class MultiUserAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.users = {}
        cls.users_by_name = {}
        cls.checkins = {}
        cls.next_user_id = 1
        cls.next_checkin_id = 1

        def reset_state():
            cls.users = {}
            cls.users_by_name = {}
            cls.checkins = {}
            cls.next_user_id = 1
            cls.next_checkin_id = 1

        cls.reset_state = staticmethod(reset_state)

        def create_user(username, password_hash, ntfy_topic=None):
            user = {
                "id": cls.next_user_id,
                "username": username,
                "password_hash": password_hash,
                "ntfy_topic": ntfy_topic,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            cls.users[user["id"]] = user
            cls.users_by_name[username] = user
            cls.next_user_id += 1
            return {
                "id": user["id"],
                "username": user["username"],
                "ntfy_topic": user["ntfy_topic"],
                "created_at": user["created_at"],
            }

        def get_user_by_username(username):
            return cls.users_by_name.get(username)

        def get_user_by_id(user_id):
            user = cls.users.get(user_id)
            if not user:
                return None
            return {
                "id": user["id"],
                "username": user["username"],
                "ntfy_topic": user["ntfy_topic"],
                "created_at": user["created_at"],
            }

        def has_users():
            return bool(cls.users)

        def add_checkin(user_id, note, category="journal", image_url=None):
            checkin = {
                "id": cls.next_checkin_id,
                "user_id": user_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "progress_note": note,
                "image_url": image_url,
                "category": category,
            }
            cls.checkins[checkin["id"]] = checkin
            cls.next_checkin_id += 1

        def get_all_checkins(user_id, category=None):
            rows = [
                c
                for c in cls.checkins.values()
                if c["user_id"] == user_id and (category is None or c["category"] == category)
            ]
            rows.sort(key=lambda c: c["timestamp"], reverse=True)
            return [
                (r["id"], r["timestamp"], r["progress_note"], r["image_url"], r["category"])
                for r in rows
            ]

        def get_checkin_by_id(checkin_id, user_id):
            c = cls.checkins.get(checkin_id)
            if not c or c["user_id"] != user_id:
                return None
            return (c["id"], c["timestamp"], c["progress_note"], c["image_url"], c["category"])

        def update_checkin(checkin_id, user_id, note, category, image_url=None):
            c = cls.checkins.get(checkin_id)
            if not c or c["user_id"] != user_id:
                return False
            c["progress_note"] = note
            c["category"] = category
            c["image_url"] = c["image_url"] if image_url is None else image_url
            return True

        def delete_checkin(checkin_id, user_id):
            c = cls.checkins.get(checkin_id)
            if not c or c["user_id"] != user_id:
                return None, False
            del cls.checkins[checkin_id]
            return c["image_url"], True

        cls.patchers = [
            patch.object(main, "init_db", lambda: None),
            patch.object(main, "BackgroundScheduler", DummyScheduler),
            patch.object(main, "bootstrap_legacy_user_if_needed", lambda: None),
            patch.object(main, "create_user", create_user),
            patch.object(main, "get_user_by_username", get_user_by_username),
            patch.object(main, "get_user_by_id", get_user_by_id),
            patch.object(main, "has_users", has_users),
            patch.object(main, "get_unowned_checkin_count", lambda: 0),
            patch.object(main, "assign_unowned_checkins", lambda user_id: None),
            patch.object(main, "add_checkin", add_checkin),
            patch.object(main, "get_all_checkins", get_all_checkins),
            patch.object(main, "get_checkin_by_id", get_checkin_by_id),
            patch.object(main, "update_checkin", update_checkin),
            patch.object(main, "delete_checkin", delete_checkin),
            patch.object(main, "upload_image", lambda *_args, **_kwargs: "fake/path.jpg"),
            patch.object(main, "get_signed_url", lambda p: f"https://cdn.example/{p}"),
            patch.object(main, "delete_image", lambda _path: True),
        ]
        for patcher in cls.patchers:
            patcher.start()

    @classmethod
    def tearDownClass(cls):
        for patcher in reversed(cls.patchers):
            patcher.stop()

    def setUp(self):
        self.reset_state()

    def test_hash_and_verify_password(self):
        hashed = hash_password("very-strong-password")
        self.assertNotEqual(hashed, "very-strong-password")
        self.assertTrue(verify_password("very-strong-password", hashed))
        self.assertFalse(verify_password("wrong-password", hashed))

    def test_register_logout_login_flow(self):
        with TestClient(main.app) as client:
            register = client.post(
                "/auth/register",
                data={
                    "username": "alice",
                    "password": "strong-password",
                    "confirm_password": "strong-password",
                },
            )
            self.assertEqual(register.status_code, 200)

            me = client.get("/auth/me")
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()["user"]["username"], "alice")

            logout = client.post("/auth/logout")
            self.assertEqual(logout.status_code, 200)

            self.assertEqual(client.get("/auth/me").status_code, 401)

            bad_login = client.post("/auth/login", data={"username": "alice", "password": "bad-pass"})
            self.assertEqual(bad_login.status_code, 401)

            good_login = client.post(
                "/auth/login", data={"username": "alice", "password": "strong-password"}
            )
            self.assertEqual(good_login.status_code, 200)
            self.assertEqual(client.get("/auth/me").status_code, 200)

    def test_checkin_ownership_isolation(self):
        with TestClient(main.app) as client_a:
            client_a.post(
                "/auth/register",
                data={
                    "username": "alice",
                    "password": "strong-password",
                    "confirm_password": "strong-password",
                },
            )
            create = client_a.post("/checkin", data={"note": "alice note", "category": "journal"})
            self.assertEqual(create.status_code, 200)
            alice_history = client_a.get("/history?category=journal")
            self.assertEqual(len(alice_history.json()), 1)
            checkin_id = alice_history.json()[0]["id"]

        with TestClient(main.app) as client_b:
            client_b.post(
                "/auth/register",
                data={
                    "username": "bob",
                    "password": "strong-password",
                    "confirm_password": "strong-password",
                },
            )
            bob_history = client_b.get("/history?category=journal")
            self.assertEqual(bob_history.status_code, 200)
            self.assertEqual(bob_history.json(), [])

            self.assertEqual(client_b.delete(f"/checkin/{checkin_id}").status_code, 404)
            self.assertEqual(
                client_b.put(
                    f"/checkin/{checkin_id}",
                    data={"note": "intrusion", "category": "journal"},
                ).status_code,
                404,
            )


if __name__ == "__main__":
    unittest.main()
