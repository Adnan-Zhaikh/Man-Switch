import base64
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import push
from fakes import AppTestCase, main

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def real_keys():
    """A genuine browser-style key pair, so encryption in pywebpush really runs."""
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64(pub), b64(os.urandom(16))


def vapid_env():
    out = subprocess.check_output([sys.executable, "generate_vapid_keys.py"], text=True)
    private = next(l for l in out.splitlines() if l.startswith("VAPID_PRIVATE_KEY=")).split("=", 1)[1]
    return {"VAPID_PRIVATE_KEY": private, "VAPID_SUBJECT": "mailto:me@example.com"}


class ValidationTests(unittest.TestCase):
    def test_accepts_real_push_services(self):
        p, a = real_keys()
        for host in ("fcm.googleapis.com/fcm/send/x", "updates.push.services.mozilla.com/wpush/v2/x",
                     "web.push.apple.com/x", "wns2-par02p.notify.windows.com/w/?token=x"):
            self.assertEqual(push.validate_subscription("https://" + host, p, a)[0], "https://" + host)

    def test_rejects_anything_else(self):
        p, a = real_keys()
        bad = ["http://fcm.googleapis.com/x", "https://evil.example/x", "https://127.0.0.1/x",
               "https://169.254.169.254/latest", "https://fcm.googleapis.com.evil.example/x",
               "https://user:pw@fcm.googleapis.com/x", "https://fcm.googleapis.com:8443/x", "", "nonsense"]
        for endpoint in bad:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                push.validate_subscription(endpoint, p, a)
        with self.assertRaises(ValueError):
            push.validate_subscription(ENDPOINT, "short", a)
        with self.assertRaises(ValueError):
            push.validate_subscription(ENDPOINT, p, "short")


class SubscriptionApiTests(AppTestCase):
    def _sub(self, endpoint=ENDPOINT):
        p, a = real_keys()
        return {"endpoint": endpoint, "keys": {"p256dh": p, "auth": a}}

    def test_public_key_endpoint(self):
        with self.client() as client:
            self.register(client)
            self.assertEqual(client.get("/push/public-key").json()["configured"], False)
            with patch.dict(os.environ, vapid_env()):
                info = client.get("/push/public-key").json()
            self.assertTrue(info["configured"])
            self.assertEqual(len(base64.urlsafe_b64decode(info["key"] + "==")), 65)

    def test_subscribe_validates_and_stores(self):
        with self.client() as client:
            self.register(client)
            self.assertEqual(client.post("/push/subscribe", json=self._sub()).status_code, 200)
            self.assertEqual(self.store.count_push_subscriptions(1), 1)
            self.assertEqual(client.post("/push/subscribe", json=self._sub("https://evil.example/x")).status_code, 422)
            self.assertEqual(client.post("/push/subscribe", json={"endpoint": ENDPOINT}).status_code, 422)

    def test_subscribe_requires_login(self):
        with self.client() as client:
            self.assertEqual(client.post("/push/subscribe", json=self._sub()).status_code, 401)

    def test_device_cap_but_resync_of_known_device_is_allowed(self):
        with self.client() as client:
            self.register(client)
            for i in range(10):
                self.assertEqual(client.post("/push/subscribe", json=self._sub(ENDPOINT + str(i))).status_code, 200)
            self.assertEqual(client.post("/push/subscribe", json=self._sub(ENDPOINT + "11")).status_code, 409)
            self.assertEqual(client.post("/push/subscribe", json=self._sub(ENDPOINT + "3")).status_code, 200)

    def test_unsubscribe_only_removes_own_device(self):
        with self.client() as a:
            self.register(a, "alice")
            a.post("/push/subscribe", json=self._sub())
        with self.client() as b:
            self.register(b, "bob")
            b.post("/push/unsubscribe", json={"endpoint": ENDPOINT})
            self.assertEqual(self.store.count_push_subscriptions(1), 1)
        with self.client() as a:
            a.post("/auth/login", data={"username": "alice", "password": "strong-password"})
            a.post("/push/unsubscribe", json={"endpoint": ENDPOINT})
            self.assertEqual(self.store.count_push_subscriptions(1), 0)

    def test_test_endpoint(self):
        with self.client() as client:
            self.register(client)
            self.assertEqual(client.post("/push/test").status_code, 503)  # not configured
            with patch.dict(os.environ, vapid_env()):
                with patch.object(main, "send_to_user", return_value=0):
                    self.assertEqual(client.post("/push/test").status_code, 409)
                with patch.object(main, "send_to_user", return_value=2):
                    self.assertEqual(client.post("/push/test").json(), {"delivered": 2})


class SendingTests(AppTestCase):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, vapid_env())
        env.start()
        self.addCleanup(env.stop)
        p, a = real_keys()
        self.store.create_user("alice", "x")
        self.store.upsert_push_subscription(1, ENDPOINT, p, a)

    def _post(self, status):
        resp = MagicMock(status_code=status, text="")
        resp.ok = status < 400
        return patch("requests.post", return_value=resp)

    def test_delivers_with_vapid_and_encryption(self):
        with self._post(201) as post:
            self.assertEqual(push.send_to_user(1, {"title": "t", "body": "b"}), 1)
        args, kwargs = post.call_args
        self.assertEqual(args[0], ENDPOINT)
        headers = kwargs["headers"]
        self.assertTrue(headers["authorization"].lower().startswith("vapid"))
        self.assertEqual(headers["content-encoding"], "aes128gcm")
        self.assertEqual(headers["Urgency"], "high")
        self.assertNotIn(b"title", kwargs["data"])  # payload is encrypted, not plaintext

    def test_dead_subscription_is_removed(self):
        with self._post(410):
            self.assertEqual(push.send_to_user(1, {"title": "t"}), 0)
        self.assertEqual(self.store.count_push_subscriptions(1), 0)

    def test_temporary_failure_keeps_subscription(self):
        with self._post(500):
            self.assertEqual(push.send_to_user(1, {"title": "t"}), 0)
        self.assertEqual(self.store.count_push_subscriptions(1), 1)

    def test_unconfigured_sends_nothing(self):
        with patch.dict(os.environ, {"VAPID_PRIVATE_KEY": ""}), self._post(201) as post:
            self.assertEqual(push.send_to_user(1, {"title": "t"}), 0)
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
