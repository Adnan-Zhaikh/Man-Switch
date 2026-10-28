import os
import unittest
from unittest.mock import MagicMock, patch

from fakes import PNG, REAL_BOOTSTRAP_ADMIN, AppTestCase, main
from auth import hash_password


class AdminAccessTests(AppTestCase):
    def test_non_admins_are_kept_out(self):
        with self.client() as client:
            self.assertEqual(client.get("/admin/api/overview").status_code, 401)
            self.register(client, "alice")
            self.assertEqual(client.get("/admin/api/overview").status_code, 403)
            self.assertEqual(client.post("/admin/api/tick").status_code, 403)
            self.assertEqual(client.post("/admin/api/registration", data={"open": "false"}).status_code, 403)
            self.assertEqual(client.delete("/admin/api/users/1").status_code, 403)
            page = client.get("/admin", follow_redirects=False)
            self.assertEqual((page.status_code, page.headers["location"]), (303, "/"))

    def test_admin_page_and_overview(self):
        with self.client() as client:
            self.make_admin(client)
            page = client.get("/admin")
            self.assertEqual(page.status_code, 200)
            self.assertIn("<title>Man Switch", page.text)
            data = client.get("/admin/api/overview").json()
            self.assertEqual(data["me"]["username"], "owner")
            self.assertEqual(data["users"][0]["is_admin"], True)
            self.assertFalse(data["status"]["push_configured"])

    def test_admin_sees_metadata_but_never_other_peoples_entries(self):
        with self.client() as bob:
            self.register(bob, "bob")
            bob.post("/checkin", data={"note": "my private secret"},
                     files={"image": ("a.png", PNG, "image/png")})
        with self.client() as admin:
            self.make_admin(admin)
            raw = admin.get("/admin/api/overview").text
            self.assertNotIn("my private secret", raw)
            self.assertNotIn("img-1", raw)
            row = next(u for u in admin.get("/admin/api/overview").json()["users"] if u["username"] == "bob")
            self.assertEqual((row["entries"], row["devices"]), (1, 0))
            # and bob's feed isn't reachable through the admin's own session
            self.assertEqual(admin.get("/history").json(), [])

    def test_admin_has_a_separate_journal_of_their_own(self):
        with self.client() as admin:
            self.make_admin(admin)
            admin.post("/checkin", data={"note": "admin diary", "category": "journal"})
            self.assertEqual(len(admin.get("/history").json()), 1)
        with self.client() as bob:
            self.register(bob, "bob")
            self.assertEqual(bob.get("/history").json(), [])

    def test_registration_toggle(self):
        with self.client() as admin:
            self.make_admin(admin)
            self.assertTrue(admin.get("/auth/config").json()["registration_open"])
            self.assertEqual(admin.post("/admin/api/registration", data={"open": "false"}).status_code, 200)
            self.assertFalse(admin.get("/auth/config").json()["registration_open"])
        with self.client() as newcomer:
            self.assertEqual(self.register(newcomer, "late").status_code, 403)
        with self.client() as admin:
            admin.post("/auth/login", data={"username": "owner", "password": "strong-password"})
            admin.post("/admin/api/registration", data={"open": "true"})
        with self.client() as newcomer:
            self.assertEqual(self.register(newcomer, "late").status_code, 200)

    def test_delete_user_removes_their_photos(self):
        with self.client() as bob:
            self.register(bob, "bob")
            bob.post("/checkin", data={"note": "a"}, files={"image": ("a.png", PNG, "image/png")})
            bob.post("/checkin", data={"note": "b"})
            bob_id = 1
        with self.client() as admin:
            admin_id = self.make_admin(admin)
            res = admin.delete(f"/admin/api/users/{bob_id}")
            self.assertEqual((res.status_code, res.json()["images_removed"]), (200, 1))
            self.assertEqual(self.store.deleted_images, ["journal/img-1.png"])
            self.assertNotIn(bob_id, self.store.users)
            self.assertEqual(admin.delete(f"/admin/api/users/{admin_id}").status_code, 400)
            self.assertEqual(admin.delete("/admin/api/users/999").status_code, 404)

    def test_other_admins_cannot_be_deleted(self):
        with self.client() as c2:
            self.make_admin(c2, "second")
        with self.client() as admin:
            self.make_admin(admin, "first")
            self.assertEqual(admin.delete("/admin/api/users/1").status_code, 400)

    def test_run_check_now(self):
        with self.client() as admin:
            self.make_admin(admin)
            with patch.object(main, "run_tick", return_value={"overdue": 0}) as tick:
                self.assertEqual(admin.post("/admin/api/tick").json(), {"overdue": 0})
                tick.assert_called_once_with("admin")


class AdminBootstrapTests(AppTestCase):
    def _run(self, **env):
        with patch.dict(os.environ, env):
            REAL_BOOTSTRAP_ADMIN.__globals__["create_user"] = self.store.create_user
            REAL_BOOTSTRAP_ADMIN()

    def test_creates_admin_when_missing(self):
        self._run(ADMIN_USERNAME="boss", ADMIN_PASSWORD="boss-password")
        self.assertTrue(self.store.get_user_by_username("boss")["is_admin"])

    def test_promotes_existing_account_only_with_matching_password(self):
        self.store.create_user("boss", hash_password("real-password"))
        self._run(ADMIN_USERNAME="boss", ADMIN_PASSWORD="someone-elses-guess")
        self.assertFalse(self.store.get_user_by_username("boss")["is_admin"])
        self._run(ADMIN_USERNAME="boss", ADMIN_PASSWORD="real-password")
        self.assertTrue(self.store.get_user_by_username("boss")["is_admin"])

    def test_does_nothing_when_unconfigured_or_invalid(self):
        with patch.dict(os.environ, {"ADMIN_USERNAME": "", "ADMIN_PASSWORD": ""}):
            REAL_BOOTSTRAP_ADMIN()
        self._run(ADMIN_USERNAME="x", ADMIN_PASSWORD="short")
        self.assertEqual(self.store.users, {})


class CronTickTests(AppTestCase):
    def test_disabled_without_secret(self):
        with patch.dict(os.environ, {"CRON_SECRET": ""}), self.client() as client:
            self.assertEqual(client.get("/internal/tick").status_code, 404)

    def test_requires_the_secret(self):
        with patch.dict(os.environ, {"CRON_SECRET": "s3cret"}), self.client() as client:
            self.assertEqual(client.get("/internal/tick").status_code, 401)
            self.assertEqual(client.get("/internal/tick", headers={"Authorization": "Bearer nope"}).status_code, 401)

    def test_runs_check_with_bearer_or_query_key_and_head_is_a_noop(self):
        result = {"overdue": 2, "alerted": 1, "no_device": 1, "errors": 0, "source": "cron", "at": "x"}
        with patch.dict(os.environ, {"CRON_SECRET": "s3cret"}), patch.object(
            main, "run_tick", MagicMock(return_value=result)
        ) as tick, self.client() as client:
            ok = client.get("/internal/tick", headers={"Authorization": "Bearer s3cret"})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.json(), {"overdue": 2, "alerted": 1, "no_device": 1, "errors": 0, "ok": True})
            self.assertEqual(client.post("/internal/tick?key=s3cret").status_code, 200)
            self.assertEqual(tick.call_count, 2)
            self.assertEqual(client.head("/internal/tick", headers={"Authorization": "Bearer s3cret"}).status_code, 200)
            self.assertEqual(tick.call_count, 2)

    def test_wrong_keys_are_rate_limited(self):
        with patch.dict(os.environ, {"CRON_SECRET": "s3cret"}), self.client() as client:
            codes = [client.get("/internal/tick?key=bad").status_code for _ in range(11)]
            self.assertEqual(codes[:10], [401] * 10)
            self.assertEqual(codes[10], 429)
            # even the right key is blocked while the lockout lasts
            self.assertEqual(client.get("/internal/tick?key=s3cret").status_code, 429)


if __name__ == "__main__":
    unittest.main()
