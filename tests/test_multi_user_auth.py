import unittest
from unittest.mock import patch

from fakes import JPEG, PNG, AppTestCase, main
from auth import hash_password, verify_password


class AuthTests(AppTestCase):
    def test_hash_and_verify_password(self):
        hashed = hash_password("very-strong-password")
        self.assertNotEqual(hashed, "very-strong-password")
        self.assertTrue(verify_password("very-strong-password", hashed))
        self.assertFalse(verify_password("wrong-password", hashed))

    def test_register_logout_login_flow(self):
        with self.client() as client:
            self.assertEqual(self.register(client).status_code, 200)
            me = client.get("/auth/me")
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()["user"]["username"], "alice")
            self.assertEqual(client.post("/auth/logout").status_code, 200)
            self.assertEqual(client.get("/auth/me").status_code, 401)

            bad = client.post("/auth/login", data={"username": "alice", "password": "bad-pass-x"})
            self.assertEqual(bad.status_code, 401)
            good = client.post("/auth/login", data={"username": "alice", "password": "strong-password"})
            self.assertEqual(good.status_code, 200)
            self.assertFalse(good.json()["user"]["is_admin"])
            self.assertEqual(client.get("/auth/me").status_code, 200)

    def test_login_checks_password_even_for_unknown_user(self):
        with patch.object(main, "verify_password", wraps=main.verify_password) as spy:
            with self.client() as client:
                res = client.post("/auth/login", data={"username": "ghost", "password": "whatever-pass"})
            self.assertEqual(res.status_code, 401)
            spy.assert_called_once()

    def test_duplicate_username_is_409(self):
        with self.client() as client:
            self.assertEqual(self.register(client).status_code, 200)
        with self.client() as client:
            self.assertEqual(self.register(client).status_code, 409)

    def test_ntfy_is_gone(self):
        with self.client() as client:
            # a stale client still sending the old field must not break registration
            self.assertEqual(self.register(client, ntfy_topic="https://ntfy.sh/x").status_code, 200)
        self.assertNotIn("ntfy_topic", self.store.users[1])


class CheckinTests(AppTestCase):
    def test_checkin_ownership_isolation(self):
        with self.client() as a:
            self.register(a, "alice")
            self.assertEqual(a.post("/checkin", data={"note": "alice note", "category": "journal"}).status_code, 200)
            history = a.get("/history?category=journal").json()
            self.assertEqual(len(history), 1)
            checkin_id = history[0]["id"]

        with self.client() as b:
            self.register(b, "bob")
            self.assertEqual(b.get("/history?category=journal").json(), [])
            self.assertEqual(b.delete(f"/checkin/{checkin_id}").status_code, 404)
            res = b.put(f"/checkin/{checkin_id}", data={"note": "intrusion", "category": "journal"})
            self.assertEqual(res.status_code, 404)

    def test_note_must_be_non_blank_and_within_limit(self):
        with self.client() as client:
            self.register(client)
            self.assertEqual(client.post("/checkin", data={"note": "   "}).status_code, 422)
            self.assertEqual(client.post("/checkin", data={"note": "x" * 2001}).status_code, 422)
            self.assertEqual(client.post("/checkin", data={"note": "x" * 2000}).status_code, 200)

    def test_upload_rejects_spoofed_content_type(self):
        with self.client() as client:
            self.register(client)
            res = client.post("/checkin", data={"note": "hi"},
                              files={"image": ("evil.png", b"<html>not an image</html>", "image/png")})
            self.assertEqual(res.status_code, 400)
            self.assertEqual(self.store.uploads, [])
            self.assertEqual(self.store.checkins, {})

    def test_upload_uses_detected_type_and_rejects_oversize(self):
        with self.client() as client:
            self.register(client)
            ok = client.post("/checkin", data={"note": "hi"}, files={"image": ("a.jpg", PNG, "image/jpeg")})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(self.store.uploads[0][1], "image/png")
            big = client.post("/checkin", data={"note": "hi"},
                              files={"image": ("big.png", PNG + b"\x00" * (5 * 1024 * 1024), "image/png")})
            self.assertEqual(big.status_code, 400)

    def test_edit_replacing_image_deletes_old_one_after_update(self):
        with self.client() as client:
            self.register(client)
            client.post("/checkin", data={"note": "n"}, files={"image": ("a.png", PNG, "image/png")})
            old = self.store.checkins[1]["image_url"]
            res = client.put("/checkin/1", data={"note": "edited", "category": "journal"},
                             files={"image": ("b.jpg", JPEG, "image/jpeg")})
            self.assertEqual(res.status_code, 200)
            self.assertNotEqual(self.store.checkins[1]["image_url"], old)
            self.assertEqual(self.store.deleted_images, [old])

    def test_edit_can_remove_image(self):
        with self.client() as client:
            self.register(client)
            client.post("/checkin", data={"note": "n"}, files={"image": ("a.png", PNG, "image/png")})
            old = self.store.checkins[1]["image_url"]
            res = client.put("/checkin/1", data={"note": "e", "category": "journal", "remove_image": "true"})
            self.assertEqual(res.status_code, 200)
            self.assertIsNone(self.store.checkins[1]["image_url"])
            self.assertEqual(self.store.deleted_images, [old])

    def test_edit_without_image_fields_keeps_image(self):
        with self.client() as client:
            self.register(client)
            client.post("/checkin", data={"note": "n"}, files={"image": ("a.png", PNG, "image/png")})
            old = self.store.checkins[1]["image_url"]
            client.put("/checkin/1", data={"note": "edited", "category": "journal"})
            self.assertEqual(self.store.checkins[1]["image_url"], old)
            self.assertEqual(self.store.deleted_images, [])

    def test_history_survives_unsignable_image(self):
        def boom(_path):
            raise RuntimeError("storage down")

        with self.client() as client:
            self.register(client)
            client.post("/checkin", data={"note": "n"}, files={"image": ("a.png", PNG, "image/png")})
            with patch.object(main, "get_signed_url", boom):
                res = client.get("/history?category=journal")
            self.assertEqual(res.status_code, 200)
            self.assertIsNone(res.json()[0]["image_url"])


class StaticTests(AppTestCase):
    def test_icons_are_real_pngs(self):
        with self.client() as client:
            for path in ("/icon-192.png", "/icon-512.png"):
                res = client.get(path)
                self.assertEqual(res.headers["content-type"], "image/png")
                self.assertTrue(res.content.startswith(b"\x89PNG"))

    def test_health_and_home_answer_head_requests(self):
        # uptime monitors often probe with HEAD; a 405 makes a healthy app look down
        with self.client() as client:
            self.assertEqual(client.head("/health").status_code, 200)
            self.assertEqual(client.head("/").status_code, 200)
            self.assertEqual(client.get("/health").json(), {"status": "ok"})

    def test_service_worker_handles_push(self):
        with self.client() as client:
            res = client.get("/sw.js")
            self.assertIn("addEventListener(\"push\"", res.text)
            self.assertEqual(res.headers["cache-control"], "no-cache")


if __name__ == "__main__":
    unittest.main()
