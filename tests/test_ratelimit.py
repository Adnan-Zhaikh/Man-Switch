import unittest
from unittest.mock import patch

import ratelimit
from fakes import AppTestCase, main


class RateLimiterUnitTests(unittest.TestCase):
    def test_window_expiry_and_retry_after(self):
        limiter = ratelimit.RateLimiter(2, 10)
        with patch("ratelimit.time.monotonic", return_value=100.0):
            self.assertEqual(limiter.hit("k"), (True, 0))
            self.assertEqual(limiter.hit("k"), (True, 0))
            allowed, retry = limiter.hit("k")
            self.assertFalse(allowed)
            self.assertTrue(1 <= retry <= 11)
        with patch("ratelimit.time.monotonic", return_value=111.0):
            self.assertTrue(limiter.hit("k")[0])

    def test_check_does_not_record_and_reset_clears(self):
        limiter = ratelimit.RateLimiter(1, 60)
        self.assertTrue(limiter.check("a")[0])
        self.assertTrue(limiter.check("a")[0])
        limiter.record("a")
        self.assertFalse(limiter.check("a")[0])
        limiter.reset("a")
        self.assertTrue(limiter.check("a")[0])

    def test_keys_are_independent(self):
        limiter = ratelimit.RateLimiter(1, 60)
        self.assertTrue(limiter.hit("a")[0])
        self.assertTrue(limiter.hit("b")[0])
        self.assertFalse(limiter.hit("a")[0])


class LoginLimitTests(AppTestCase):
    def _login(self, client, name="alice", pw="wrong-password"):
        return client.post("/auth/login", data={"username": name, "password": pw})

    def test_repeated_failures_lock_out_even_the_right_password(self):
        with self.client() as client:
            self.register(client)
            client.post("/auth/logout")
            for _ in range(8):
                self.assertEqual(self._login(client).status_code, 401)
            locked = self._login(client)
            self.assertEqual(locked.status_code, 429)
            self.assertIn("Retry-After", locked.headers)
            self.assertEqual(self._login(client, pw="strong-password").status_code, 429)

    def test_lockout_is_per_username(self):
        with self.client() as client:
            self.register(client)
            self.register(client, "bob")
            client.post("/auth/logout")
            for _ in range(8):
                self._login(client)
            self.assertEqual(self._login(client, "bob", "strong-password").status_code, 200)

    def test_successful_login_resets_the_failure_count(self):
        with self.client() as client:
            self.register(client)
            client.post("/auth/logout")
            for _ in range(5):
                self._login(client)
            self.assertEqual(self._login(client, pw="strong-password").status_code, 200)
            for _ in range(5):
                self.assertEqual(self._login(client).status_code, 401)

    def test_registration_is_limited_per_ip(self):
        with self.client() as client:
            for i in range(5):
                res = client.post("/auth/register", data={"username": f"user{i}", "password": "strong-password",
                                                          "confirm_password": "mismatch-password"})
                self.assertEqual(res.status_code, 422)
            self.assertEqual(self.register(client, "another").status_code, 429)


class WriteAndGlobalLimitTests(AppTestCase):
    def test_entry_writes_are_limited_per_user(self):
        with self.client() as client:
            self.register(client)
            statuses = [client.post("/checkin", data={"note": "x"}).status_code for _ in range(61)]
            self.assertEqual(statuses[:60], [200] * 60)
            self.assertEqual(statuses[60], 429)

    def test_global_per_ip_limit_applies_but_health_is_exempt(self):
        with patch.object(main.ip_limiter, "limit", 5):
            with self.client() as client:
                codes = [client.get("/auth/config").status_code for _ in range(7)]
                self.assertEqual(codes[:5], [200] * 5)
                self.assertEqual(codes[5], 429)
                self.assertEqual(client.get("/health").status_code, 200)

    def test_client_ip_uses_forwarded_header_from_the_end(self):
        with self.client() as client:
            uid = self.make_admin(client)
            res = client.get("/admin/api/overview", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
            self.assertEqual(res.json()["status"]["detected_ip"], "203.0.113.9")  # spoofed first entry ignored
            with patch.dict("os.environ", {"TRUSTED_PROXY_HOPS": "0"}):
                res = client.get("/admin/api/overview", headers={"X-Forwarded-For": "6.6.6.6"})
                self.assertEqual(res.json()["status"]["detected_ip"], "testclient")
            self.assertTrue(uid)


if __name__ == "__main__":
    unittest.main()
