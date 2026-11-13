import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import habit_logic as hl
import todo_logic
from fakes import AppTestCase

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)  # a Tuesday


def at(moment):
    return patch.object(todo_logic, "utcnow", return_value=moment)


def d(s):
    return date.fromisoformat(s)


ALL = "1111111"
WEEKDAYS = "1111100"


class LogicTests(unittest.TestCase):
    def test_days_validation(self):
        self.assertEqual(hl.validate_days(ALL), ALL)
        for bad in ("", "111", "0000000", "11111112", "abcdefg", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                hl.validate_days(bad)

    def test_color_and_text_cleaning(self):
        self.assertEqual(hl.validate_color("#3FB950"), "#3fb950")
        for bad in ("red", "#12345", "#gggggg", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                hl.validate_color(bad)
        self.assertEqual(hl.clean_name("  Read   20  pages "), "Read 20 pages")
        with self.assertRaises(ValueError):
            hl.clean_name("   ")
        with self.assertRaises(ValueError):
            hl.clean_note("x" * 201)

    def test_streak_counts_consecutive_days(self):
        done = {"2026-11-07", "2026-11-08", "2026-11-09", "2026-11-10"}
        self.assertEqual(hl.streaks(ALL, done, d("2026-11-01"), d("2026-11-10")), (4, 4))

    def test_today_not_done_yet_does_not_break_streak(self):
        done = {"2026-11-08", "2026-11-09"}
        self.assertEqual(hl.streaks(ALL, done, d("2026-11-01"), d("2026-11-10")), (2, 2))

    def test_a_missed_day_resets_current_but_keeps_best(self):
        done = {"2026-11-01", "2026-11-02", "2026-11-03", "2026-11-05", "2026-11-06"}
        self.assertEqual(hl.streaks(ALL, done, d("2026-11-01"), d("2026-11-06")), (2, 3))

    def test_unscheduled_days_do_not_break_a_streak(self):
        # Fri 6 Nov, then Mon 9 Nov and Tue 10 Nov: the weekend is not scheduled
        done = {"2026-11-06", "2026-11-09", "2026-11-10"}
        self.assertEqual(hl.streaks(WEEKDAYS, done, d("2026-11-02"), d("2026-11-10")), (3, 3))  # weekend skipped
        done |= {"2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"}
        self.assertEqual(hl.streaks(WEEKDAYS, done, d("2026-11-02"), d("2026-11-10")), (7, 7))

    def test_completion_only_counts_today_when_done(self):
        done = {"2026-11-09"}
        self.assertEqual(hl.completion(ALL, done, d("2026-11-08"), d("2026-11-10")), (1, 2))
        self.assertEqual(hl.completion(ALL, done | {"2026-11-10"}, d("2026-11-08"), d("2026-11-10")), (2, 3))

    def test_overview(self):
        habits = [
            {"id": 1, "name": "Gym", "icon": "💪", "color": "#e5c33a", "days": ALL, "created_day": "2026-11-08"},
            {"id": 2, "name": "Read", "icon": "📚", "color": "#58a6ff", "days": "1000000", "created_day": "2026-11-01"},
        ]
        logs = [
            {"habit_id": 1, "day": "2026-11-09", "note": "legs"},
            {"habit_id": 1, "day": "2026-11-10", "note": ""},
        ]
        o = hl.build_overview(habits, logs, "2026-11-10")
        gym, read = o["habits"]
        self.assertEqual((gym["streak"], gym["commits"], gym["done_today"], gym["due_today"]), (2, 2, True, True))
        self.assertEqual((read["streak"], read["done_today"], read["due_today"]), (0, False, False))  # Tuesday: not due
        self.assertEqual(len(o["week"]), hl.STRIP_DAYS)
        last = o["week"][-1]
        self.assertEqual((last["date"], last["due"], last["done"]), ("2026-11-10", 1, 1))
        self.assertEqual(o["week"][-3]["due"], 1)   # Sun 8 Nov: Gym only; Read is Monday-only
        self.assertEqual(o["week"][-2]["due"], 2)   # Mon 9 Nov: both
        self.assertEqual(o["week"][0]["due"], 0)    # before either habit existed
        self.assertGreater(o["score"], 0)

    def test_empty_overview_is_safe(self):
        o = hl.build_overview([], [], "2026-11-10")
        self.assertEqual((o["habits"], o["score"]), ([], 0.0))


class HabitApiTests(AppTestCase):
    def test_requires_login(self):
        with self.client() as c:
            self.assertEqual(c.get("/habits").status_code, 401)
            self.assertEqual(c.post("/habits", json={"name": "x"}).status_code, 401)
            self.assertEqual(c.put("/habits/1/logs/2026-11-10", json={}).status_code, 401)

    def test_create_commit_streak_and_undo(self):
        with self.client() as c, at(T0):
            self.register(c)
            h = c.post("/habits", json={"name": "  Gym ", "icon": "💪", "color": "#E5C33A"}).json()
            self.assertEqual((h["name"], h["color"], h["days"], h["created_day"]), ("Gym", "#e5c33a", ALL, "2026-11-10"))
            hid = h["id"]

            res = c.put(f"/habits/{hid}/logs/2026-11-10", json={"note": "  chest   day "})
            self.assertEqual(res.json(), {"day": "2026-11-10", "done": True, "note": "chest day"})
            o = c.get("/habits").json()
            g = o["habits"][0]
            self.assertEqual((g["streak"], g["commits"], g["done_today"]), (1, 1, True))
            self.assertEqual(g["logs"], [{"day": "2026-11-10", "note": "chest day"}])

            # committing again without a note keeps the message
            c.put(f"/habits/{hid}/logs/2026-11-10", json={})
            self.assertEqual(c.get("/habits").json()["habits"][0]["logs"][0]["note"], "chest day")

            self.assertFalse(c.put(f"/habits/{hid}/logs/2026-11-10", json={"done": False}).json()["done"])
            g = c.get("/habits").json()["habits"][0]
            self.assertEqual((g["streak"], g["commits"], g["done_today"]), (0, 0, False))

    def test_backfill_window_and_future(self):
        with self.client() as c, at(T0):
            self.register(c)
            hid = c.post("/habits", json={"name": "Gym"}).json()["id"]
            self.assertEqual(c.put(f"/habits/{hid}/logs/2026-11-11", json={}).status_code, 422)  # tomorrow
            self.assertEqual(c.put(f"/habits/{hid}/logs/2026-11-09", json={}).status_code, 200)   # yesterday
            self.assertEqual(c.put(f"/habits/{hid}/logs/2026-10-11", json={}).status_code, 200)   # 30 days ago
            self.assertEqual(c.put(f"/habits/{hid}/logs/2026-10-10", json={}).status_code, 422)   # 31 days ago
            for bad in ("yesterday", "2026-13-40", "26-11-10"):
                self.assertEqual(c.put(f"/habits/{hid}/logs/{bad}", json={}).status_code, 422, bad)

    def test_validation_and_limit(self):
        with self.client() as c, at(T0):
            self.register(c)
            self.assertEqual(c.post("/habits", json={"name": "  "}).status_code, 422)
            self.assertEqual(c.post("/habits", json={"name": "x" * 61}).status_code, 422)
            self.assertEqual(c.post("/habits", json={"name": "ok", "color": "red"}).status_code, 422)
            self.assertEqual(c.post("/habits", json={"name": "ok", "days": "0000000"}).status_code, 422)
            hid = c.post("/habits", json={"name": "ok"}).json()["id"]
            self.assertEqual(c.put(f"/habits/{hid}/logs/2026-11-10", json={"note": "x" * 201}).status_code, 422)
            with patch.object(hl, "MAX_HABITS", 2):
                c.post("/habits", json={"name": "two"})
                self.assertEqual(c.post("/habits", json={"name": "three"}).status_code, 409)

    def test_edit_and_delete_removes_commits(self):
        with self.client() as c, at(T0):
            self.register(c)
            hid = c.post("/habits", json={"name": "Gym"}).json()["id"]
            c.put(f"/habits/{hid}/logs/2026-11-10", json={})
            e = c.patch(f"/habits/{hid}", json={"name": "Lift", "days": WEEKDAYS}).json()
            self.assertEqual((e["name"], e["days"]), ("Lift", WEEKDAYS))
            self.assertEqual(c.patch(f"/habits/{hid}", json={"days": "0000000"}).status_code, 422)
            self.assertEqual(c.delete(f"/habits/{hid}").status_code, 200)
            self.assertEqual(c.delete(f"/habits/{hid}").status_code, 404)
            self.assertEqual(self.store.habit_logs, {})
            self.assertEqual(c.get("/habits").json()["habits"], [])

    def test_users_cannot_touch_each_others_habits(self):
        with self.client() as a, at(T0):
            self.register(a, "alice")
            hid = a.post("/habits", json={"name": "private"}).json()["id"]
        with self.client() as b, at(T0):
            self.register(b, "bob")
            self.assertEqual(b.get("/habits").json()["habits"], [])
            self.assertEqual(b.put(f"/habits/{hid}/logs/2026-11-10", json={}).status_code, 404)
            self.assertEqual(b.patch(f"/habits/{hid}", json={"name": "mine"}).status_code, 404)
            self.assertEqual(b.delete(f"/habits/{hid}").status_code, 404)

    def test_days_follow_the_users_time_zone(self):
        late = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)  # already 11 Nov in India
        with self.client() as c, at(late):
            self.register(c)
            self.assertEqual(c.get("/habits").json()["today"], "2026-11-10")
            c.put("/todos/settings", json={"timezone": "Asia/Kolkata"})
            self.assertEqual(c.get("/habits").json()["today"], "2026-11-11")

    def test_streak_survives_across_days(self):
        with self.client() as c:
            with at(T0):
                self.register(c)
                hid = c.post("/habits", json={"name": "Gym"}).json()["id"]
                c.put(f"/habits/{hid}/logs/2026-11-10", json={})
            with at(T0 + timedelta(days=1)):   # today not done yet: still 1
                self.assertEqual(c.get("/habits").json()["habits"][0]["streak"], 1)
                c.put(f"/habits/{hid}/logs/2026-11-11", json={})
                self.assertEqual(c.get("/habits").json()["habits"][0]["streak"], 2)
            with at(T0 + timedelta(days=3)):   # 12th was missed and the 13th is over: reset
                g = c.get("/habits").json()["habits"][0]
                self.assertEqual((g["streak"], g["best_streak"]), (0, 2))


if __name__ == "__main__":
    unittest.main()
