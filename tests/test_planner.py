"""Account email, calendar events, one-hour email reminders and the hold-to-confirm check-in."""
import os
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import emailer
import planner_logic
import ratelimit
import scheduler
from fakes import AppTestCase

WHEN = "%Y-%m-%dT%H:%M:%SZ"


def at(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime(WHEN)


class FakeMailbox:
    """Stands in for emailer: records messages, or fails on demand."""

    def __init__(self):
        self.sent, self.fail = [], False

    def send_email(self, to, subject, text):
        if self.fail:
            raise emailer.EmailError("boom")
        self.sent.append((to, subject, text))


class PlannerCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.mail = FakeMailbox()
        for p in (
            patch.object(emailer, "is_configured", lambda: True),
            patch.object(emailer, "send_email", self.mail.send_email),
        ):
            p.start()
            self.addCleanup(p.stop)

    def user(self, name="alice", email="alice@example.com"):
        c = self.client()
        res = self.register(c, name, email=email) if email else self.register(c, name)
        self.assertEqual(res.status_code, 200, res.text)
        return c, res.json()["user"]["id"]

    def make_event(self, c, title="Dentist", minutes=50, **extra):
        res = c.post("/events", json={"title": title, "starts_at": at(minutes), **extra})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()


class AccountEmailTests(PlannerCase):
    def test_email_is_optional_at_signup(self):
        c, uid = self.user(email=None)
        self.assertIsNone(self.store.users[uid]["email"])
        self.assertIsNone(c.get("/auth/me").json()["user"]["email"])

    def test_email_saved_lowercased_at_signup(self):
        c, uid = self.user(email="  Alice@Example.COM ")
        self.assertEqual(self.store.users[uid]["email"], "alice@example.com")
        self.assertEqual(c.get("/auth/me").json()["user"]["email"], "alice@example.com")

    def test_bad_email_rejected_at_signup(self):
        for bad in ("nope", "a@b", "a b@c.com", "a@@c.com", "x@y.com, z@y.com", "x@y.com\nBcc: z@y.com"):
            ratelimit.clear_all()  # signup is limited to 5 per hour per IP
            res = self.register(self.client(), "bob", email=bad)
            self.assertEqual(res.status_code, 422, bad)
        self.assertFalse(self.store.users)

    def test_login_returns_email(self):
        self.user()
        res = self.client().post("/auth/login", data={"username": "alice", "password": "strong-password"})
        self.assertEqual(res.json()["user"]["email"], "alice@example.com")

    def test_set_change_and_clear_email(self):
        c, uid = self.user(email=None)
        self.assertEqual(c.put("/auth/email", json={"email": "me@example.com"}).json()["email"], "me@example.com")
        self.assertEqual(self.store.users[uid]["email"], "me@example.com")
        self.assertEqual(c.put("/auth/email", json={"email": "new@example.com"}).status_code, 200)
        self.assertEqual(c.put("/auth/email", json={"email": ""}).json()["email"], None)
        self.assertIsNone(self.store.users[uid]["email"])
        self.assertEqual(c.put("/auth/email", json={"email": "bad"}).status_code, 422)

    def test_email_change_needs_login_and_is_rate_limited(self):
        self.assertEqual(self.client().put("/auth/email", json={"email": "a@b.co"}).status_code, 401)
        c, _ = self.user()
        codes = [c.put("/auth/email", json={"email": f"u{i}@example.com"}).status_code for i in range(12)]
        self.assertEqual(codes[:10], [200] * 10)
        self.assertEqual(codes[10], 429)


class CalendarApiTests(PlannerCase):
    def test_calendar_needs_an_email(self):
        c, _ = self.user(email=None)
        res = c.post("/events", json={"title": "x", "starts_at": at(90)})
        self.assertEqual(res.status_code, 409)
        self.assertIn("email", res.json()["detail"].lower())
        self.assertEqual(c.get("/events", params={"start": at(0), "end": at(1000)}).status_code, 409)
        c.put("/auth/email", json={"email": "me@example.com"})
        self.assertEqual(c.post("/events", json={"title": "x", "starts_at": at(90)}).status_code, 200)

    def test_calendar_needs_login(self):
        self.assertEqual(self.client().get("/events", params={"start": at(0), "end": at(60)}).status_code, 401)
        self.assertEqual(self.client().post("/events", json={"title": "x", "starts_at": at(60)}).status_code, 401)

    def test_create_list_edit_delete(self):
        c, _ = self.user()
        ev = self.make_event(c, "Dentist", 120, notes="bring forms", color="#ff3b5c")
        self.assertEqual((ev["title"], ev["notes"], ev["color"], ev["reminder_sent"]), ("Dentist", "bring forms", "#ff3b5c", False))
        rows = c.get("/events", params={"start": at(0), "end": at(600)}).json()["events"]
        self.assertEqual([r["id"] for r in rows], [ev["id"]])
        self.assertEqual(c.get("/events", params={"start": at(300), "end": at(600)}).json()["events"], [])
        res = c.patch(f"/events/{ev['id']}", json={"title": "Dentist (moved)", "starts_at": at(240)})
        self.assertEqual(res.json()["title"], "Dentist (moved)")
        self.assertEqual(c.delete(f"/events/{ev['id']}").status_code, 200)
        self.assertEqual(c.delete(f"/events/{ev['id']}").status_code, 404)
        self.assertEqual(c.get("/events", params={"start": at(0), "end": at(600)}).json()["events"], [])

    def test_events_are_private(self):
        a, _ = self.user("alice", "alice@example.com")
        b, _ = self.user("bobby", "bob@example.com")
        ev = self.make_event(a, "Secret", 120)
        self.assertEqual(b.get("/events", params={"start": at(0), "end": at(600)}).json()["events"], [])
        self.assertEqual(b.patch(f"/events/{ev['id']}", json={"title": "mine now"}).status_code, 404)
        self.assertEqual(b.delete(f"/events/{ev['id']}").status_code, 404)
        self.assertEqual(self.store.events[ev["id"]]["title"], "Secret")

    def test_validation(self):
        c, _ = self.user()
        bad = [
            {"title": "  ", "starts_at": at(60)},
            {"title": "x" * 200, "starts_at": at(60)},
            {"title": "x", "starts_at": "tomorrow"},
            {"title": "x", "starts_at": "2030-01-01T10:00:00"},  # no time zone
            {"title": "x", "starts_at": at(60), "color": "purple"},
            {"title": "x", "starts_at": at(60), "notes": "n" * 2000},
            {"title": "x", "starts_at": at(60 * 24 * 366 * 5)},
            {"title": "x", "starts_at": at(-60 * 24 * 400)},
        ]
        for body in bad:
            self.assertEqual(c.post("/events", json=body).status_code, 422, body)
        self.assertFalse(self.store.events)

    def test_offsets_are_stored_as_utc(self):
        c, _ = self.user()
        day = (datetime.now(timezone.utc) + timedelta(days=100)).strftime("%Y-%m-%d")
        res = c.post("/events", json={"title": "x", "starts_at": f"{day}T09:30:00+05:30"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["starts_at"], f"{day}T04:00:00+00:00")

    def test_range_limits(self):
        c, _ = self.user()
        self.assertEqual(c.get("/events", params={"start": at(100), "end": at(0)}).status_code, 422)
        self.assertEqual(c.get("/events", params={"start": at(0), "end": at(60 * 24 * 90)}).status_code, 422)
        self.assertEqual(c.get("/events", params={"start": "junk", "end": at(60)}).status_code, 422)

    def test_event_cap(self):
        c, uid = self.user()
        with patch.object(planner_logic, "MAX_EVENTS_PER_USER", 2):
            self.make_event(c, "a", 100)
            self.make_event(c, "b", 100)
            res = c.post("/events", json={"title": "c", "starts_at": at(100)})
        self.assertEqual(res.status_code, 409)

    def test_deleting_account_removes_events(self):
        c, uid = self.user()
        self.make_event(c)
        admin = self.client()
        admin_id = self.make_admin(admin)
        self.assertEqual(admin.delete(f"/admin/api/users/{uid}").status_code, 200)
        self.assertFalse(self.store.events)


class ReminderTests(PlannerCase):
    def tick(self):
        return scheduler.run_event_reminders(datetime.now(timezone.utc))

    def test_event_inside_the_hour_is_emailed_once(self):
        c, _ = self.user()
        ev = self.make_event(c, "Dentist", 50, notes="bring forms")
        self.assertEqual(self.tick(), (1, 0))
        to, subject, body = self.mail.sent[0]
        self.assertEqual(to, "alice@example.com")
        self.assertIn("Dentist", subject)
        self.assertIn("bring forms", body)
        self.assertIn("minutes", body)
        self.assertTrue(self.store.events[ev["id"]]["reminded_at"])
        self.assertEqual(self.tick(), (0, 0))
        self.assertEqual(len(self.mail.sent), 1)

    def test_far_past_and_started_events_are_left_alone(self):
        c, _ = self.user()
        self.make_event(c, "later today", 61 * 3)
        self.make_event(c, "already started", -5)
        self.make_event(c, "long gone", -600)
        self.assertEqual(self.tick(), (0, 0))
        self.assertFalse(self.mail.sent)

    def test_reminder_opens_one_hour_before(self):
        c, _ = self.user()
        self.make_event(c, "soon", 62)
        self.assertEqual(self.tick(), (0, 0))
        later = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.assertEqual(scheduler.run_event_reminders(later), (1, 0))

    def test_no_email_no_reminder(self):
        c, uid = self.user()
        self.make_event(c, "x", 30)
        c.put("/auth/email", json={"email": ""})
        self.assertEqual(self.tick(), (0, 0))
        self.assertFalse(self.mail.sent)

    def test_failed_send_is_retried_next_tick(self):
        c, _ = self.user()
        ev = self.make_event(c, "x", 30)
        self.mail.fail = True
        self.assertEqual(self.tick(), (0, 1))
        self.assertIsNone(self.store.events[ev["id"]]["reminded_at"])
        self.mail.fail = False
        self.assertEqual(self.tick(), (1, 0))

    def test_moving_an_event_rearms_its_reminder(self):
        c, _ = self.user()
        ev = self.make_event(c, "x", 30)
        self.tick()
        self.assertEqual(len(self.mail.sent), 1)
        c.patch(f"/events/{ev['id']}", json={"starts_at": at(45)})
        self.tick()
        self.assertEqual(len(self.mail.sent), 2)

    def test_saving_the_same_time_does_not_rearm(self):
        c, _ = self.user()
        ev = self.make_event(c, "x", 30)
        self.tick()
        res = c.patch(f"/events/{ev['id']}", json={"title": "renamed", "starts_at": ev["starts_at"]})
        self.assertEqual(res.status_code, 200)
        self.tick()
        self.assertEqual(len(self.mail.sent), 1)

    def test_editing_only_the_title_does_not_rearm(self):
        c, _ = self.user()
        ev = self.make_event(c, "x", 30)
        self.tick()
        c.patch(f"/events/{ev['id']}", json={"title": "renamed"})
        self.tick()
        self.assertEqual(len(self.mail.sent), 1)

    def test_nothing_is_claimed_when_email_is_not_configured(self):
        c, _ = self.user()
        ev = self.make_event(c, "x", 30)
        with patch.object(emailer, "is_configured", lambda: False):
            self.assertEqual(self.tick(), (0, 0))
        self.assertIsNone(self.store.events[ev["id"]]["reminded_at"])

    def test_run_tick_reports_emailed_count(self):
        c, _ = self.user()
        self.make_event(c, "x", 30)
        result = scheduler.run_tick("test")
        self.assertEqual(result["emailed"], 1)

    def test_times_use_the_account_time_zone(self):
        c, uid = self.user()
        self.store.users[uid]["timezone"] = "Asia/Kolkata"
        self.store.add_event(uid, "Call", "", "#2d8cff", "2030-01-01T04:00:00+00:00")
        event = self.store.list_due_event_reminders("2000-01-01", "2100-01-01")[0]
        subject, body = planner_logic.reminder_email(event, datetime(2030, 1, 1, 3, 0, tzinfo=timezone.utc))
        self.assertIn("9:30 AM", subject)
        self.assertIn("Asia/Kolkata", body)
        self.assertIn("about 60 minutes", body)

    def test_title_cannot_inject_headers(self):
        event = {"starts_at": "2030-01-01T04:00:00+00:00", "title": "Hi\r\nBcc: evil@example.com", "username": "a", "timezone": "UTC"}
        subject, _ = planner_logic.reminder_email(event, datetime(2030, 1, 1, 3, 0, tzinfo=timezone.utc))
        self.assertNotIn("\n", subject)
        self.assertNotIn("\r", subject)


class EmailerTests(AppTestCase):
    ENV = ("BREVO_API_KEY", "RESEND_API_KEY", "SMTP_HOST", "EMAIL_FROM", "SMTP_USER", "EMAIL_FROM_NAME")

    def env(self, **values):
        clean = {k: "" for k in self.ENV}
        clean.update(values)
        p = patch.dict(os.environ, clean)
        p.start()
        self.addCleanup(p.stop)

    def test_provider_selection(self):
        self.env()
        self.assertIsNone(emailer.provider())
        self.env(BREVO_API_KEY="k")
        self.assertIsNone(emailer.provider(), "needs a from address too")
        self.env(BREVO_API_KEY="k", EMAIL_FROM="me@example.com")
        self.assertEqual(emailer.provider(), "brevo")
        self.env(RESEND_API_KEY="k", EMAIL_FROM="me@example.com")
        self.assertEqual(emailer.provider(), "resend")
        self.env(SMTP_HOST="smtp.example.com", SMTP_USER="me@example.com")
        self.assertEqual(emailer.provider(), "smtp")

    def test_send_without_config_raises(self):
        self.env()
        with self.assertRaises(emailer.EmailError):
            emailer.send_email("a@b.co", "s", "t")

    def test_brevo_request(self):
        self.env(BREVO_API_KEY="secret-key", EMAIL_FROM="me@example.com")
        response = MagicMock()
        response.__enter__.return_value = response
        with patch("urllib.request.urlopen", return_value=response) as opened:
            emailer.send_email("to@example.com", "Hello\r\nBcc: x@y.com", "body")
        request = opened.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(request.get_header("Api-key"), "secret-key")
        import json
        payload = json.loads(request.data)
        self.assertEqual(payload["to"], [{"email": "to@example.com"}])
        self.assertEqual(payload["sender"]["email"], "me@example.com")
        self.assertNotIn("\n", payload["subject"])

    def test_resend_request(self):
        self.env(RESEND_API_KEY="rk", EMAIL_FROM="me@example.com")
        response = MagicMock()
        response.__enter__.return_value = response
        with patch("urllib.request.urlopen", return_value=response) as opened:
            emailer.send_email("to@example.com", "Hi", "body")
        request = opened.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_header("Authorization"), "Bearer rk")

    def test_provider_errors_become_email_errors_without_leaking(self):
        self.env(BREVO_API_KEY="k", EMAIL_FROM="me@example.com")
        err = urllib.error.HTTPError("u", 401, "no", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(emailer.EmailError) as ctx:
                emailer.send_email("to@example.com", "s", "t")
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn("to@example.com", str(ctx.exception))
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(emailer.EmailError):
                emailer.send_email("to@example.com", "s", "t")

    def test_rejects_newline_in_recipient(self):
        self.env(BREVO_API_KEY="k", EMAIL_FROM="me@example.com")
        with self.assertRaises(emailer.EmailError):
            emailer.send_email("a@b.co\nBcc: x@y.com", "s", "t")


class CheckInTests(AppTestCase):
    def user(self):
        c = self.client()
        res = self.register(c)
        return c, res.json()["user"]["id"]

    def test_hold_checkin_resets_the_deadline(self):
        c, uid = self.user()
        old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        self.store.add_checkin(uid, "old post", "journal")
        next(iter(self.store.checkins.values()))["timestamp"] = old
        self.assertTrue(scheduler.is_deadline_exceeded(self.store.get_last_checkin_time(uid)))
        res = c.post("/pulse", json={"mood": "good"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertFalse(scheduler.is_deadline_exceeded(self.store.get_last_checkin_time(uid)))
        self.assertEqual(len(res.json()["pulses"]), 1)

    def test_mood_is_optional_and_validated(self):
        c, _ = self.user()
        self.assertEqual(c.post("/pulse", json={}).status_code, 200)
        self.assertEqual(c.post("/pulse", json={"mood": "okay"}).status_code, 200)
        self.assertEqual(c.post("/pulse", json={"mood": "<script>"}).status_code, 422)

    def test_status_lists_only_my_checkins(self):
        a, _ = self.user()
        b = self.client()
        self.register(b, "bobby")
        a.post("/pulse", json={})
        a.post("/pulse", json={})
        self.assertEqual(len(a.get("/pulse").json()["pulses"]), 2)
        self.assertEqual(b.get("/pulse").json()["pulses"], [])
        self.assertIsNone(b.get("/pulse").json()["last_checkin"])
        self.assertEqual(a.get("/pulse").json()["deadline_hours"], scheduler.DEADLINE_HOURS)

    def test_checkins_do_not_show_up_in_journal_pages(self):
        c, _ = self.user()
        c.post("/pulse", json={})
        c.post("/checkin", data={"note": "a real post", "category": "journal"})
        notes = [p["progress_note"] for p in c.get("/history?category=journal").json()]
        self.assertEqual(notes, ["a real post"])

    def test_all_filter_returns_every_post_but_no_checkins(self):
        c, _ = self.user()
        for note, cat in (("a journal note", "journal"), ("a goal", "task"), ("keep this", "important")):
            c.post("/checkin", data={"note": note, "category": cat})
        c.post("/pulse", json={})
        for url in ("/history?category=all", "/history"):
            rows = c.get(url).json()
            self.assertEqual(sorted(r["progress_note"] for r in rows), ["a goal", "a journal note", "keep this"], url)
            self.assertEqual({r["category"] for r in rows}, {"journal", "task", "important"})
        self.assertEqual([r["progress_note"] for r in c.get("/history?category=task").json()], ["a goal"])

    def test_all_filter_is_private_and_newest_first(self):
        a, _ = self.user()
        b = self.client()
        self.register(b, "bobby")
        a.post("/checkin", data={"note": "first", "category": "journal"})
        a.post("/checkin", data={"note": "second", "category": "important"})
        self.assertEqual([r["progress_note"] for r in a.get("/history?category=all").json()], ["second", "first"])
        self.assertEqual(b.get("/history?category=all").json(), [])

    def test_pulse_category_cannot_be_posted_directly(self):
        c, _ = self.user()
        self.assertEqual(c.post("/checkin", data={"note": "x", "category": "pulse"}).status_code, 400)

    def test_needs_login(self):
        self.assertEqual(self.client().post("/pulse", json={}).status_code, 401)
        self.assertEqual(self.client().get("/pulse").status_code, 401)
