"""Dashboard: the calculations (pure) and the /dashboard endpoint."""
from datetime import date, datetime, timedelta, timezone

import dashboard_logic as dl
from fakes import AppTestCase

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 25)


def day(ago):
    return TODAY - timedelta(days=ago)


def stamp(ago, hour=8, minute=0, category="journal"):
    d = day(ago)
    return {"timestamp": datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc).isoformat(), "category": category}


def task(tid, ago, done_ago=None, postponed=0, title="Task"):
    d = day(ago)
    done = None
    if done_ago is not None:
        e = day(done_ago)
        done = datetime(e.year, e.month, e.day, 10, 0, tzinfo=timezone.utc).isoformat()
    return {"id": tid, "title": title, "day": d.isoformat(), "priority": 0,
            "created_at": datetime(d.year, d.month, d.day, 7, 0, tzinfo=timezone.utc).isoformat(), "done_at": done, "postponed": postponed}


def build(range_days=30, habits=(), logs=(), tasks=(), activity=(), tz="UTC", now=NOW):
    return dl.build_dashboard(now=now, today_iso=TODAY.isoformat(), tz_name=tz, range_days=range_days,
                              habits=list(habits), habit_logs=list(logs), tasks=list(tasks),
                              activity=list(activity), deadline_hours=24)


class ConsistencyTests(AppTestCase):
    def test_new_user_has_no_score_and_a_gentle_note(self):
        d = build()
        self.assertIsNone(d["consistency"]["score"])
        self.assertEqual(d["consistency"]["label"], "No data yet")
        self.assertEqual(len(d["insights"]), 1)
        self.assertIn("week", d["insights"][0]["text"])

    def test_every_day_active_is_100(self):
        d = build(activity=[stamp(i) for i in range(30)])
        c = d["consistency"]
        self.assertEqual((c["score"], c["active_days"], c["window_days"], c["streak"], c["best_streak"]), (100, 30, 30, 30, 30))
        self.assertEqual(c["label"], "Rock solid")

    def test_an_empty_today_is_not_held_against_you(self):
        d = build(activity=[stamp(i) for i in range(1, 11)])
        c = d["consistency"]
        self.assertEqual((c["score"], c["window_days"], c["streak"]), (100, 10, 10))

    def test_gap_resets_current_streak_but_keeps_best(self):
        d = build(activity=[stamp(i) for i in range(1, 11) if i != 5])
        c = d["consistency"]
        self.assertEqual(c["streak"], 4)
        self.assertEqual(c["best_streak"], 5)
        self.assertEqual((c["active_days"], c["window_days"]), (9, 10))

    def test_comparison_with_previous_period(self):
        activity = [stamp(i) for i in range(30)] + [stamp(i) for i in range(30, 60, 2)]
        c = build(activity=activity)["consistency"]
        self.assertEqual(c["score"], 100)
        self.assertEqual(c["prev_score"], 52)      # 15 active days of the 29 since the first action
        self.assertEqual(c["delta"], 48)

    def test_all_kinds_of_action_count(self):
        habit = {"id": 1, "name": "Gym", "icon": "x", "color": "#2d8cff", "days": "1111111", "created_day": day(5).isoformat()}
        d = build(habits=[habit], logs=[{"habit_id": 1, "day": day(3).isoformat(), "note": "", "created_at": None}],
                  tasks=[task(1, 2, done_ago=2)], activity=[stamp(4), stamp(1, category="pulse")])
        self.assertEqual(d["consistency"]["active_days"], 4)

    def test_heatmap_covers_91_days_ending_today(self):
        d = build(activity=[stamp(0), stamp(0, hour=9), stamp(90)])
        self.assertEqual(len(d["heatmap"]), 91)
        self.assertEqual(d["heatmap"][-1], {"date": TODAY.isoformat(), "count": 2})
        self.assertEqual(d["heatmap"][0]["count"], 1)

    def test_days_use_the_users_time_zone(self):
        late = {"timestamp": "2026-11-24T23:30:00+00:00", "category": "journal"}   # 05:00 on the 25th in Kolkata
        d = build(activity=[late], tz="Asia/Kolkata")
        self.assertEqual(d["heatmap"][-1]["count"], 1)
        self.assertEqual(d["patterns"]["time_of_day"]["buckets"][0], {"name": "Morning", "count": 1, "pct": 100})
        self.assertEqual(build(activity=[late], tz="UTC")["heatmap"][-1]["count"], 0)


class PatternTests(AppTestCase):
    def test_time_of_day_buckets_and_insight(self):
        activity = [stamp(i, hour=20) for i in range(1, 21)]
        d = build(activity=activity)
        evening = [b for b in d["patterns"]["time_of_day"]["buckets"] if b["name"] == "Evening"][0]
        self.assertEqual(evening["pct"], 100)
        self.assertTrue(any("in the evening" in i["text"] for i in d["insights"]))

    def test_weekday_pattern(self):
        # active only on Tuesdays (2026-11-24 is a Tuesday) for 6 weeks
        activity = [stamp(1 + 7 * w) for w in range(6)]
        w = {x["day"]: x for x in build(activity=activity)["patterns"]["weekdays"]}
        self.assertEqual(w["Tue"]["rate"], 100)
        self.assertEqual(w["Wed"]["rate"], 0)
        self.assertEqual(w["Tue"]["actions"], 5)      # five Tuesdays fall inside a 30-day window

    def test_most_and_least_consistent_weekday_insight(self):
        activity = [stamp(i) for i in range(1, 35) if (day(i).weekday() != 6)]   # everything but Sundays
        texts = " ".join(i["text"] for i in build(activity=activity)["insights"])
        self.assertIn("quietest on Sundays", texts)


class HabitTests(AppTestCase):
    HABIT = {"id": 1, "name": "Gym", "icon": "x", "color": "#2d8cff", "days": "1111111", "created_day": day(29).isoformat()}

    def logs(self, agos, habit_id=1):
        return [{"habit_id": habit_id, "day": day(a).isoformat(), "note": "", "created_at": None} for a in agos]

    def test_rate_streak_and_risk(self):
        d = build(habits=[self.HABIT], logs=self.logs(range(1, 25)))
        h = d["habits"]["habits"][0]
        self.assertEqual((h["done"], h["due"], h["rate"]), (24, 29, 83))
        self.assertEqual((h["streak"], h["best_streak"]), (24, 24))
        self.assertTrue(h["at_risk"])
        self.assertTrue(any("Gym streak (24 days)" in i["text"] for i in d["insights"]))
        self.assertEqual(d["habits"]["rate"], 83)

    def test_done_today_is_not_at_risk_and_counts(self):
        h = build(habits=[self.HABIT], logs=self.logs(range(0, 25)))["habits"]["habits"][0]
        self.assertFalse(h["at_risk"])
        self.assertEqual((h["done"], h["due"]), (25, 30))
        self.assertTrue(h["done_today"])

    def test_unscheduled_days_are_not_misses(self):
        weekdays = dict(self.HABIT, days="1111100")
        logs = [{"habit_id": 1, "day": day(i).isoformat(), "note": "", "created_at": None}
                for i in range(1, 30) if day(i).weekday() < 5]
        d = build(habits=[weekdays], logs=logs)
        self.assertEqual(d["habits"]["habits"][0]["rate"], 100)
        self.assertEqual(sum(w["due"] for w in d["habits"]["weekdays"][5:]), 0)

    def test_weekend_gap_insight(self):
        logs = [{"habit_id": 1, "day": day(i).isoformat(), "note": "", "created_at": None}
                for i in range(1, 30) if day(i).weekday() < 5]
        texts = " ".join(i["text"] for i in build(habits=[self.HABIT], logs=logs)["insights"])
        self.assertIn("weekends", texts)

    def test_best_and_weakest_habit_insights(self):
        strong = dict(self.HABIT, id=1, name="Read")
        weak = dict(self.HABIT, id=2, name="Run")
        logs = self.logs(range(1, 30), 1) + self.logs([2, 9, 16], 2)
        d = build(habits=[weak, strong], logs=logs)
        self.assertEqual([h["name"] for h in d["habits"]["habits"]], ["Read", "Run"])
        texts = " ".join(i["text"] for i in d["insights"])
        self.assertIn("Read is your most reliable habit", texts)
        self.assertIn("Run is at", texts)

    def test_no_habits_is_fine(self):
        h = build()["habits"]
        self.assertEqual((h["count"], h["rate"], h["habits"]), (0, None, []))


class TaskTests(AppTestCase):
    def test_done_planned_late_and_overdue(self):
        tasks = [task(1, 1, done_ago=1), task(2, 1, done_ago=1), task(3, 3, done_ago=0), task(4, 1), task(5, 0)]
        t = build(tasks=tasks)["tasks"]
        self.assertEqual((t["planned"], t["done"], t["rate"]), (4, 3, 75))   # today's open task isn't counted yet
        self.assertEqual((t["on_time"], t["late"], t["open_overdue"]), (2, 1, 1))

    def test_today_task_counts_once_done(self):
        t = build(tasks=[task(1, 0, done_ago=0)])["tasks"]
        self.assertEqual((t["planned"], t["done"]), (1, 1))

    def test_postponements(self):
        tasks = [task(1, 0, postponed=3, title="Write report"), task(2, 1, postponed=1), task(3, -1, postponed=1)]  # one is tomorrow's
        t = build(tasks=tasks)["tasks"]
        self.assertEqual((t["postponed_events"], t["postponed_tasks"]), (5, 3))
        self.assertEqual(t["most_postponed"], {"title": "Write report", "times": 3})
        texts = " ".join(i["text"] for i in build(tasks=tasks, activity=[stamp(i) for i in range(10)])["insights"])
        self.assertIn("pushed 3 times", texts)
        self.assertIn("moved tasks to a later day 5 times", texts)

    def test_backlog_and_low_rate_insights(self):
        tasks = [task(i, 1 + i % 9) for i in range(1, 13)]    # twelve open, all overdue
        texts = " ".join(i["text"] for i in build(tasks=tasks, activity=[stamp(i) for i in range(10)])["insights"])
        self.assertIn("older tasks are still open", texts)

    def test_high_completion_insight(self):
        tasks = [task(i, 1 + i % 9, done_ago=1 + i % 9) for i in range(1, 11)]
        texts = " ".join(i["text"] for i in build(tasks=tasks, activity=[stamp(i) for i in range(10)])["insights"])
        self.assertIn("You finished 10 of 10 planned tasks (100%)", texts)

    def test_no_tasks_is_fine(self):
        t = build()["tasks"]
        self.assertEqual((t["planned"], t["rate"], t["postponed_events"], t["most_postponed"]), (0, None, 0, None))


class CheckinHealthTests(AppTestCase):
    def test_gaps_and_deadline(self):
        activity = [{"timestamp": (NOW - timedelta(hours=50)).isoformat(), "category": "journal"},
                    {"timestamp": (NOW - timedelta(hours=10)).isoformat(), "category": "pulse"}]
        k = build(activity=activity)["checkins"]
        self.assertEqual(k["longest_gap_hours"], 40.0)
        self.assertEqual(k["past_deadline"], 1)
        self.assertEqual(k["longest_pct"], 167)
        self.assertEqual((k["pulses"], k["posts"], k["pulse_days"]), (1, 1, 1))

    def test_current_silence_counts(self):
        k = build(activity=[{"timestamp": (NOW - timedelta(hours=30)).isoformat(), "category": "pulse"}])["checkins"]
        self.assertEqual((k["longest_gap_hours"], k["past_deadline"]), (30.0, 1))

    def test_no_activity(self):
        k = build()["checkins"]
        self.assertEqual((k["longest_gap_hours"], k["avg_gap_hours"], k["past_deadline"]), (None, None, 0))

    def test_close_call_and_missed_insights(self):
        base = [stamp(i, hour=8) for i in range(10, 0, -1)]
        close = base + [{"timestamp": (NOW - timedelta(hours=1)).isoformat(), "category": "pulse"}]
        close = [a for a in close if not a["timestamp"].startswith(day(5).isoformat())]   # a day-long hole is still < 48h
        d = build(activity=close)
        self.assertTrue(any("deadline" in i["text"] for i in d["insights"]))


class InsightTests(AppTestCase):
    def test_streak_insight_needs_a_week_of_history(self):
        self.assertEqual(len(build(activity=[stamp(i) for i in range(5)])["insights"]), 1)
        d = build(activity=[stamp(i) for i in range(10)])
        self.assertTrue(any("10 days in a row" in i["text"] for i in d["insights"]))

    def test_insights_are_capped_and_well_formed(self):
        for ins in build(activity=[stamp(i, hour=20) for i in range(60)])["insights"]:
            self.assertIn(ins["kind"], {"good", "tip", "note"})
            self.assertTrue(ins["text"])
        self.assertLessEqual(len(build(activity=[stamp(i) for i in range(60)])["insights"]), dl.MAX_INSIGHTS)

    def test_ranges(self):
        for r in dl.RANGES:
            self.assertEqual(build(range_days=r, activity=[stamp(i) for i in range(100)])["range"], r)


class DashboardApiTests(AppTestCase):
    def user(self, name="alice"):
        c = self.client()
        self.assertEqual(self.register(c, name).status_code, 200)
        return c

    def test_needs_login(self):
        self.assertEqual(self.client().get("/dashboard").status_code, 401)

    def test_range_and_timezone_are_validated(self):
        c = self.user()
        self.assertEqual(c.get("/dashboard?range=30").status_code, 200)
        for bad in ("5", "0", "365", "abc"):
            self.assertEqual(c.get(f"/dashboard?range={bad}").status_code, 422, bad)
        self.assertEqual(c.get("/dashboard?tz=Mars/Base").status_code, 422)
        self.assertEqual(c.get("/dashboard?tz=Asia/Kolkata").json()["timezone"], "Asia/Kolkata")

    def test_empty_account(self):
        d = self.user().get("/dashboard").json()
        self.assertIsNone(d["consistency"]["score"])
        self.assertEqual(len(d["heatmap"]), 91)
        self.assertEqual(d["habits"]["habits"], [])

    def test_reflects_real_activity(self):
        c = self.user()
        c.post("/pulse", json={})
        self.assertEqual(c.post("/habits", json={"name": "Read", "icon": "📚", "color": "#2d8cff", "days": "1111111"}).status_code, 200)
        hid = c.get("/habits").json()["habits"][0]["id"]
        c.put(f"/habits/{hid}/logs/{c.get('/habits').json()['today']}", json={})
        task_id = c.post("/todos", json={"title": "Write", "day": "today"}).json()["id"]
        c.patch(f"/todos/{task_id}", json={"done": True})
        d = c.get("/dashboard").json()
        self.assertEqual(d["consistency"]["active_days"], 1)
        self.assertEqual(d["heatmap"][-1]["count"], 3)      # check-in + habit commit + finished task
        self.assertEqual(d["checkins"]["pulses"], 1)
        self.assertEqual(d["habits"]["habits"][0]["name"], "Read")
        self.assertEqual((d["tasks"]["planned"], d["tasks"]["done"]), (1, 1))

    def test_postponing_counts_only_moves_to_a_later_day(self):
        c = self.user()
        tid = c.post("/todos", json={"title": "Move me", "day": "today"}).json()["id"]
        self.assertEqual(c.get("/dashboard").json()["tasks"]["postponed_events"], 0)
        c.patch(f"/todos/{tid}", json={"day": "tomorrow"})
        self.assertEqual(c.get("/dashboard").json()["tasks"]["postponed_events"], 1)
        c.patch(f"/todos/{tid}", json={"day": "today"})            # earlier: not a postponement
        self.assertEqual(c.get("/dashboard").json()["tasks"]["postponed_events"], 1)
        c.patch(f"/todos/{tid}", json={"day": "tomorrow"})
        d = c.get("/dashboard").json()["tasks"]
        self.assertEqual((d["postponed_events"], d["postponed_tasks"]), (2, 1))

    def test_each_account_sees_only_its_own_data(self):
        a, b = self.user("alice"), self.user("bobby")
        a.post("/pulse", json={})
        a.post("/todos", json={"title": "secret task", "day": "today"})
        d = b.get("/dashboard").json()
        self.assertEqual(d["heatmap"][-1]["count"], 0)
        self.assertEqual(d["checkins"]["pulses"], 0)
        self.assertNotIn("secret task", str(d))
