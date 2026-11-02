import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import scheduler
import todo_logic
import todos
from fakes import AppTestCase

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)  # a Monday, 09:00 UTC


def at(moment):
    """Freeze the app's clock."""
    return patch.object(todo_logic, "utcnow", return_value=moment)


class LogicTests(unittest.TestCase):
    def test_quiet_hours_wrap_midnight(self):
        self.assertTrue(todo_logic.in_quiet_hours(23, 22, 7))
        self.assertTrue(todo_logic.in_quiet_hours(3, 22, 7))
        self.assertFalse(todo_logic.in_quiet_hours(7, 22, 7))
        self.assertFalse(todo_logic.in_quiet_hours(12, 22, 7))
        self.assertTrue(todo_logic.in_quiet_hours(13, 12, 14))
        self.assertFalse(todo_logic.in_quiet_hours(5, 8, 8))  # equal = no quiet hours

    def test_local_today_uses_the_users_time_zone(self):
        late = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)  # 01:30 next day in India
        self.assertEqual(todo_logic.local_today("UTC", late).isoformat(), "2026-11-02")
        self.assertEqual(todo_logic.local_today("Asia/Kolkata", late).isoformat(), "2026-11-03")
        self.assertEqual(todo_logic.local_today("Not/AZone", late).isoformat(), "2026-11-02")  # falls back

    def test_validate_timezone(self):
        self.assertEqual(todo_logic.validate_timezone("Asia/Kolkata"), "Asia/Kolkata")
        for bad in ("", "Mars/Base", "x" * 80):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                todo_logic.validate_timezone(bad)

    def test_reminder_wording(self):
        tasks = [{"id": i, "title": f"Task {i}", "day": "2026-11-02", "priority": 0, "done_at": None,
                  "created_at": T0.isoformat()} for i in range(1, 6)]
        tasks[3]["priority"] = 1
        tasks[0]["day"] = "2026-11-01"
        p = todo_logic.reminder_payload(tasks, "2026-11-02")
        self.assertTrue(p["title"].startswith("⚠️"))
        self.assertIn("5 tasks not done (1 overdue)", p["body"])
        self.assertIn("+2 more", p["body"])
        self.assertTrue(p["body"].split(": ", 1)[1].startswith("Task 4"))  # starred first
        self.assertEqual((p["tag"], p["url"], p["badge_count"]), ("todo", "/todo", 5))

    def test_stats_totals_and_streak(self):
        counts = [
            {"day": "2026-10-31", "planned": 2, "done": 2},
            {"day": "2026-11-01", "planned": 3, "done": 1},
            {"day": "2026-11-02", "planned": 2, "done": 0},   # today, nothing yet: streak not broken
        ]
        s = todo_logic.build_stats(counts, "2026-11-02", "week")
        self.assertEqual(len(s["days"]), 7)
        self.assertEqual(s["days"][-1]["date"], "2026-11-02")
        t = s["totals"]
        self.assertEqual((t["planned"], t["done"], t["rate"], t["streak"]), (7, 3, 43, 2))
        self.assertEqual((t["best_day"], t["best_done"]), ("2026-10-31", 2))
        self.assertEqual(len(todo_logic.build_stats(counts, "2026-11-02", "month")["days"]), 30)

    def test_stats_empty_is_safe(self):
        t = todo_logic.build_stats([], "2026-11-02", "month")["totals"]
        self.assertEqual((t["done"], t["rate"], t["streak"], t["best_day"]), (0, 0, 0, None))


class TaskApiTests(AppTestCase):
    def test_requires_login(self):
        with self.client() as c:
            self.assertEqual(c.get("/todos").status_code, 401)
            self.assertEqual(c.post("/todos", json={"title": "x"}).status_code, 401)

    def test_add_complete_edit_delete(self):
        with self.client() as c, at(T0):
            self.register(c)
            a = c.post("/todos", json={"title": "  Buy   milk ", "priority": 1}).json()
            self.assertEqual((a["title"], a["day"], a["priority"], a["done"]), ("Buy milk", "2026-11-02", 1, False))
            c.post("/todos", json={"title": "Plan trip", "day": "tomorrow"})
            d = c.get("/todos").json()
            self.assertEqual([t["title"] for t in d["today_tasks"]], ["Buy milk"])
            self.assertEqual([t["day"] for t in d["tomorrow"]], ["2026-11-03"])
            self.assertEqual(d["summary"]["left"], 1)

            done = c.patch(f"/todos/{a['id']}", json={"done": True}).json()
            self.assertTrue(done["done"])
            s = c.get("/todos").json()["summary"]
            self.assertEqual((s["done"], s["left"], s["total_today"]), (1, 0, 1))
            self.assertFalse(c.patch(f"/todos/{a['id']}", json={"done": False}).json()["done"])

            self.assertEqual(c.patch(f"/todos/{a['id']}", json={"title": "Buy oat milk"}).json()["title"], "Buy oat milk")
            self.assertEqual(c.delete(f"/todos/{a['id']}").status_code, 200)
            self.assertEqual(c.delete(f"/todos/{a['id']}").status_code, 404)

    def test_validation(self):
        with self.client() as c, at(T0):
            self.register(c)
            self.assertEqual(c.post("/todos", json={"title": "   "}).status_code, 422)
            self.assertEqual(c.post("/todos", json={"title": "x" * 201}).status_code, 422)
            self.assertEqual(c.post("/todos", json={"title": "ok", "day": "yesterday"}).status_code, 422)
            self.assertEqual(c.post("/todos", json={"title": "ok", "priority": 5}).status_code, 422)
            with patch.object(todo_logic, "MAX_TASKS_PER_DAY", 2):
                c.post("/todos", json={"title": "1"})
                c.post("/todos", json={"title": "2"})
                self.assertEqual(c.post("/todos", json={"title": "3"}).status_code, 409)

    def test_users_cannot_touch_each_others_tasks(self):
        with self.client() as a, at(T0):
            self.register(a, "alice")
            tid = a.post("/todos", json={"title": "private"}).json()["id"]
        with self.client() as b, at(T0):
            self.register(b, "bob")
            self.assertEqual(b.get("/todos").json()["today_tasks"], [])
            self.assertEqual(b.patch(f"/todos/{tid}", json={"done": True}).status_code, 404)
            self.assertEqual(b.delete(f"/todos/{tid}").status_code, 404)
            self.assertNotIn("private", b.get("/todos/day/2026-11-02").text)

    def test_unfinished_tasks_become_overdue_and_can_be_moved_to_today(self):
        with self.client() as c:
            with at(T0):
                self.register(c)
                tid = c.post("/todos", json={"title": "carry me"}).json()["id"]
                done_id = c.post("/todos", json={"title": "finished"}).json()["id"]
                c.patch(f"/todos/{done_id}", json={"done": True})
            with at(T0 + timedelta(days=1)):
                d = c.get("/todos").json()
                self.assertEqual([t["title"] for t in d["overdue"]], ["carry me"])  # finished one is history
                self.assertEqual((d["summary"]["overdue"], d["summary"]["left"]), (1, 1))
                c.patch(f"/todos/{tid}", json={"day": "today"})
                d = c.get("/todos").json()
                self.assertEqual((d["overdue"], [t["title"] for t in d["today_tasks"]]), ([], ["carry me"]))

    def test_days_follow_the_users_time_zone(self):
        late = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
        with self.client() as c, at(late):
            self.register(c)
            self.assertEqual(c.get("/todos").json()["today"], "2026-11-02")
            res = c.put("/todos/settings", json={"timezone": "Asia/Kolkata"})
            self.assertEqual(res.json()["timezone"], "Asia/Kolkata")
            self.assertEqual(c.get("/todos").json()["today"], "2026-11-03")
            self.assertEqual(c.put("/todos/settings", json={"timezone": "Mars/Base"}).status_code, 422)

    def test_settings_validation_and_roundtrip(self):
        with self.client() as c:
            self.register(c)
            s = c.get("/todos/settings").json()
            self.assertEqual((s["reminders"], s["quiet_start"], s["quiet_end"], s["reminder_hours"]), (True, 22, 7, 4.0))
            self.assertEqual(c.put("/todos/settings", json={"quiet_start": 24}).status_code, 422)
            s = c.put("/todos/settings", json={"reminders": False, "quiet_start": 23, "quiet_end": 6}).json()
            self.assertEqual((s["reminders"], s["quiet_start"], s["quiet_end"]), (False, 23, 6))

    def test_history_and_stats(self):
        with self.client() as c:
            self.register(c)
            with at(T0 - timedelta(days=2)):
                a = c.post("/todos", json={"title": "old done"}).json()["id"]
                c.post("/todos", json={"title": "old missed"})
                c.patch(f"/todos/{a}", json={"done": True})
            with at(T0 - timedelta(days=1)):
                b = c.post("/todos", json={"title": "yday"}).json()["id"]
                c.patch(f"/todos/{b}", json={"done": True})
            with at(T0):
                c.post("/todos", json={"title": "today"})
                day = c.get("/todos/day/2026-10-31").json()
                self.assertEqual(sorted((t["title"], t["done"]) for t in day["tasks"]),
                                 [("old done", True), ("old missed", False)])
                self.assertEqual(c.get("/todos/day/2026-13-45").status_code, 422)
                self.assertEqual(c.get("/todos/day/nonsense").status_code, 422)
                st = c.get("/todos/stats?range=week").json()
                t = st["totals"]
                self.assertEqual((t["planned"], t["done"], t["streak"]), (4, 2, 2))
                self.assertEqual(st["days"][-1], {"date": "2026-11-02", "planned": 1, "done": 0})
                self.assertEqual(len(c.get("/todos/stats?range=month").json()["days"]), 30)
                self.assertEqual(c.get("/todos/stats?range=year").status_code, 422)

    def test_write_rate_limit_applies(self):
        with self.client() as c, at(T0):
            self.register(c)
            codes = [c.post("/todos", json={"title": "t"}).status_code for _ in range(61)]
            self.assertEqual(codes[60], 429)

    def test_todo_page_requires_login(self):
        with self.client() as c:
            r = c.get("/todo", follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (303, "/"))
            self.register(c)
            page = c.get("/todo")
            self.assertEqual(page.status_code, 200)
            self.assertIn("To-do", page.text)


class WidgetTests(AppTestCase):
    def _setup(self, c):
        self.register(c)
        with at(T0):
            for title in ("a", "b", "c"):
                c.post("/todos", json={"title": title})
            tid = c.get("/todos").json()["today_tasks"][0]["id"]
            c.patch(f"/todos/{tid}", json={"done": True})
        return c.post("/todos/widget-token").json()["token"]

    def test_token_gives_counts_without_login_or_titles(self):
        with self.client() as c:
            token = self._setup(c)
            self.assertNotEqual(self.store.users[1]["widget_token_hash"], token)  # only a hash is kept
            with self.client() as widget, at(T0):      # no session cookie
                j = widget.get(f"/widget/summary?token={token}")
                self.assertEqual((j.json()["done"], j.json()["left"]), (1, 2))
                self.assertEqual(j.headers["cache-control"], "no-store")
                self.assertNotIn('"a"', j.text)
                t = widget.get(f"/widget/summary?token={token}&format=text")
                self.assertEqual(t.text, "1 done · 2 left")
                bearer = widget.get("/widget/summary", headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(bearer.status_code, 200)

    def test_wrong_missing_replaced_and_revoked_tokens_fail(self):
        with self.client() as c:
            token = self._setup(c)
            with self.client() as w:
                self.assertEqual(w.get("/widget/summary").status_code, 401)
                self.assertEqual(w.get("/widget/summary?token=nope").status_code, 401)
            new = c.post("/todos/widget-token").json()["token"]
            with self.client() as w:
                self.assertEqual(w.get(f"/widget/summary?token={token}").status_code, 401)
                self.assertEqual(w.get(f"/widget/summary?token={new}").status_code, 200)
            c.delete("/todos/widget-token")
            with self.client() as w:
                self.assertEqual(w.get(f"/widget/summary?token={new}").status_code, 401)

    def test_guessing_is_rate_limited(self):
        todos.widget_fail_limiter.clear()
        with self.client() as w:
            codes = [w.get("/widget/summary?token=guess").status_code for _ in range(21)]
            self.assertEqual(codes[:20], [401] * 20)
            self.assertEqual(codes[20], 429)
        todos.widget_fail_limiter.clear()


class ReminderTests(AppTestCase):
    def _user_with_task(self, **settings):
        user = self.store.create_user("sam", "x")
        self.store.upsert_push_subscription(user["id"], "https://fcm.googleapis.com/x", "p", "a")
        self.store.users[user["id"]].update(settings)
        with at(T0):
            self.store.add_task(user["id"], "write report", "2026-11-02")
        return user["id"]

    def _tick(self, moment, delivered=1):
        with at(moment), patch.object(scheduler, "send_to_user", return_value=delivered) as send:
            result = scheduler.run_tick("test")
        return result, send

    def test_reminder_every_four_hours_not_before(self):
        self._user_with_task()
        r, send = self._tick(T0 + timedelta(hours=3, minutes=55))
        self.assertEqual((r["reminded"], send.call_count), (0, 0))
        r, send = self._tick(T0 + timedelta(hours=4, minutes=1))
        self.assertEqual((r["reminded"], send.call_count), (1, 1))
        kwargs = send.call_args.kwargs
        self.assertEqual((kwargs["topic"], kwargs["urgency"], kwargs["ttl"]), ("todo", "normal", 3600))
        self.assertIn("1 task not done", send.call_args.args[1]["body"])
        r, _ = self._tick(T0 + timedelta(hours=4, minutes=6))     # same window: no repeat
        self.assertEqual(r["reminded"], 0)
        r, _ = self._tick(T0 + timedelta(hours=8, minutes=2))
        self.assertEqual(r["reminded"], 1)

    def test_quiet_hours_hold_reminders_until_morning(self):
        uid = self._user_with_task()
        with at(T0 + timedelta(hours=12)):                         # 21:00
            self.store.users[uid]["last_todo_reminder_at"] = (T0 + timedelta(hours=12)).isoformat()
        r, _ = self._tick(T0 + timedelta(hours=17))                # 02:00, due but quiet
        self.assertEqual(r["reminded"], 0)
        r, _ = self._tick(T0 + timedelta(hours=22, minutes=30))    # 07:30
        self.assertEqual(r["reminded"], 1)

    def test_respects_off_switch_and_nothing_to_do(self):
        uid = self._user_with_task(todo_reminders=False)
        self.assertEqual(self._tick(T0 + timedelta(hours=5))[0]["reminded"], 0)
        self.store.users[uid]["todo_reminders"] = True
        self.store.update_task(1, uid, done=True)
        self.assertEqual(self._tick(T0 + timedelta(hours=5))[0]["reminded"], 0)

    def test_tomorrows_tasks_do_not_trigger_reminders(self):
        uid = self.store.create_user("sam", "x")["id"]
        self.store.upsert_push_subscription(uid, "https://fcm.googleapis.com/x", "p", "a")
        with at(T0):
            self.store.add_task(uid, "later", "2026-11-03")
        self.assertEqual(self._tick(T0 + timedelta(hours=6))[0]["reminded"], 0)

    def test_no_device_retries_next_tick(self):
        uid = self._user_with_task()
        r, _ = self._tick(T0 + timedelta(hours=5), delivered=0)
        self.assertEqual(r["reminded"], 0)
        self.assertIsNone(self.store.users[uid]["last_todo_reminder_at"])
        r, _ = self._tick(T0 + timedelta(hours=5, minutes=5), delivered=1)
        self.assertEqual(r["reminded"], 1)

    def test_push_failure_for_one_user_does_not_stop_others(self):
        a = self._user_with_task()
        b = self.store.create_user("kim", "x")["id"]
        self.store.upsert_push_subscription(b, "https://fcm.googleapis.com/y", "p", "a")
        with at(T0):
            self.store.add_task(b, "kim task", "2026-11-02")

        def flaky(user_id, payload, **kw):
            if user_id == a:
                raise RuntimeError("push down")
            return 1

        with at(T0 + timedelta(hours=5)), patch.object(scheduler, "send_to_user", flaky):
            r = scheduler.run_tick("test")
        self.assertEqual((r["reminded"], r["errors"]), (1, 1))
        self.assertIsNone(self.store.users[a]["last_todo_reminder_at"])


if __name__ == "__main__":
    unittest.main()
