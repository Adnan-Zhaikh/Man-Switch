import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import scheduler
from fakes import AppTestCase


def ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


class DeadlineLogicTests(unittest.TestCase):
    def test_deadline_logic(self):
        self.assertFalse(scheduler.is_deadline_exceeded(None))
        self.assertFalse(scheduler.is_deadline_exceeded(ago(23)))
        self.assertTrue(scheduler.is_deadline_exceeded(ago(25)))

    def test_naive_timestamp_is_treated_as_utc(self):
        naive = (datetime.now(timezone.utc) - timedelta(hours=30)).replace(tzinfo=None).isoformat()
        self.assertTrue(scheduler.is_deadline_exceeded(naive))


class TickTests(AppTestCase):
    def _user(self, name, hours_since_checkin, devices=1):
        user = self.store.create_user(name, "x")
        self.store.add_checkin(user["id"], "n")
        self.store.checkins[max(self.store.checkins)]["timestamp"] = ago(hours_since_checkin)
        for i in range(devices):
            self.store.upsert_push_subscription(user["id"], f"https://fcm.googleapis.com/{name}{i}", "p", "a")
        return user["id"]

    def test_overdue_user_is_alerted_once_per_repeat_window(self):
        uid = self._user("late", 30)
        self._user("fine", 2)
        with patch.object(scheduler, "send_to_user", return_value=1) as send:
            first = scheduler.run_tick("test")
            second = scheduler.run_tick("test")
        self.assertEqual((first["overdue"], first["alerted"]), (1, 1))
        self.assertEqual(second["alerted"], 0)           # de-duplicated
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[0], uid)
        self.assertIsNotNone(self.store.users[uid]["last_alert_at"])

    def test_realerts_after_repeat_window(self):
        self._user("late", 30)
        with patch.object(scheduler, "ALERT_REPEAT_MINUTES", 0), patch.object(
            scheduler, "send_to_user", return_value=1
        ) as send:
            scheduler.run_tick("test")
            scheduler.run_tick("test")
        self.assertEqual(send.call_count, 2)

    def test_no_device_releases_claim_so_next_tick_retries(self):
        uid = self._user("late", 30, devices=0)
        with patch.object(scheduler, "send_to_user", return_value=0):
            result = scheduler.run_tick("test")
        self.assertEqual((result["alerted"], result["no_device"]), (0, 1))
        self.assertIsNone(self.store.users[uid]["last_alert_at"])

    def test_one_failure_does_not_block_other_users(self):
        a = self._user("aaa", 30)
        b = self._user("bbb", 30)
        calls = []

        def flaky(user_id, payload):
            calls.append(user_id)
            if user_id == a:
                raise RuntimeError("push service down")
            return 1

        with patch.object(scheduler, "send_to_user", flaky):
            result = scheduler.run_tick("test")
        self.assertEqual(sorted(calls), [a, b])
        self.assertEqual((result["alerted"], result["errors"]), (1, 1))
        self.assertIsNone(self.store.users[a]["last_alert_at"])   # claim released for retry

    def test_corrupt_timestamp_is_skipped(self):
        self._user("bad", 1)
        self.store.checkins[1]["timestamp"] = "garbage"
        self._user("late", 30)
        with patch.object(scheduler, "send_to_user", return_value=1):
            self.assertEqual(scheduler.run_tick("test")["alerted"], 1)

    def test_overlapping_ticks_are_skipped(self):
        with scheduler._tick_lock:
            self.assertTrue(scheduler.run_tick("test")["skipped"])

    def test_result_is_recorded_for_the_admin_page(self):
        scheduler.run_tick("cron")
        saved = json.loads(self.store.settings["last_tick"])
        self.assertEqual(saved["source"], "cron")

    def test_late_wakeup_still_alerts(self):
        # a free host that slept for 3 days: the check is based on stored timestamps
        self._user("late", 24 * 3)
        with patch.object(scheduler, "send_to_user", return_value=1):
            self.assertEqual(scheduler.run_tick("startup")["alerted"], 1)


if __name__ == "__main__":
    unittest.main()
