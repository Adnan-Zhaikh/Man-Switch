# Man Switch

A self-hosted accountability journal and dead-man's switch. Check in regularly; if you go quiet past your deadline, it sends a push alert to your phone. It also has habits, tasks and a calendar that emails you before events.

<!-- Add screenshots: Check-in page, Calendar, dark and light mode. -->

## Features

- **Dashboard:** consistency score and streaks, a 13-week activity heatmap, habit completion, tasks finished vs moved to a later day, check-in safety margin, weekday and time-of-day patterns, and plain-language insights. Switch between 7, 30 and 90 days.
- **Check-in:** press and hold a button for a second (optionally pick a mood). No typing. Resets your deadline.
- **Journal, Goals, Important:** notes with optional photos, full edit and delete. Posting also resets the deadline.
- **Habits:** daily streaks with a GitHub-style commit grid.
- **Calendar:** plan events on any day. An **email arrives one hour before** each event.
- **Tasks:** daily to-do list with reminders, stats and an Android widget feed (`/todo`).
- **Alerts:** if the deadline passes (24 h by default) you get a browser push notification on every enabled device, repeating hourly until you check in.
- **Accounts:** register/login, each account private. Email is optional at signup but required to use the calendar. Owner-only `/admin` page.
- Flat, vibrant colour scheme with a colour per page, dark and light mode, installable as a PWA.

## Stack

FastAPI, Postgres (Supabase), Supabase Storage (private photo bucket), APScheduler, Web Push (VAPID), Brevo or Resend for email, plain HTML/CSS/JS with no build step. Deployed on Render.

## Setup

```bash
git clone https://github.com/Adnan-Zhaikh/Man-Switch
cd Man-Switch
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                               # then fill it in
python -m uvicorn main:app --reload
```

Open `http://127.0.0.1:8000/` and register. Tests: `SESSION_SECRET=x python -m unittest discover -s tests`.

1. **Supabase:** create a project, copy the **Transaction pooler** connection string as `DATABASE_URL`, create a **private** bucket named `post-images`, and copy the project URL and a secret key. Tables are created and upgraded automatically on start.
2. **Push alerts:** run `python generate_vapid_keys.py`, put `VAPID_PRIVATE_KEY` and `VAPID_SUBJECT` (`mailto:you@example.com`) in `.env`, then tap **Enable alerts** under *More* on each device. iPhone needs the site added to the Home Screen first.
3. **Calendar emails** (below).

### Calendar email reminders

Render's free tier blocks SMTP ports, so use an email provider's HTTPS API:

| Provider | Set | Notes |
| --- | --- | --- |
| **Brevo** (easiest) | `BREVO_API_KEY`, `EMAIL_FROM` | Free tier, sends from one verified sender address, no domain needed |
| Resend | `RESEND_API_KEY`, `EMAIL_FROM` | Needs a verified domain to email anyone but yourself |
| SMTP | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | Local use or a paid host only |

`EMAIL_FROM` must be an address the provider has verified. Optional: `EMAIL_FROM_NAME`, `APP_URL` (adds a link to the email), `EVENT_REMINDER_MINUTES` (default 60). The admin page shows which provider is active.

Reminders go out on the first scheduler check after the one-hour mark, so timing is within the check interval (5 minutes in-app, plus your cron ping). Each event is emailed once; moving it to a new time re-arms the reminder.

### Dashboard notes

- An **active day** is any day with a check-in, post, habit commit or finished task. Today never counts against you until it ends.
- **Moved to a later day** is counted whenever a task's day moves later (for example to tomorrow). It started counting when the dashboard was added, so older tasks show zero.
- Insights are simple rules with minimum-data checks (about a week of use), not AI.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL`, `SESSION_SECRET` | Required |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | Photo storage |
| `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Push alerts |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Creates/promotes your admin account at startup |
| `CRON_SECRET` | Enables `/internal/tick` for external schedulers |
| `SESSION_HTTPS_ONLY=true`, `TRUSTED_PROXY_HOPS=1` | Set in production behind Render |
| `DEADLINE_HOURS`, `ALERT_REPEAT_MINUTES`, `TICK_MINUTES`, `TODO_REMINDER_HOURS`, `REGISTRATION_OPEN` | Tuning (defaults 24, 60, 5, 4, true) |

See `.env.example` for the full list. Keep `.env` out of git; the Supabase secret key bypasses all access rules.

## Deployment (Render)

1. Push to GitHub and create a **Web Service** with start command `uvicorn main:app --host 0.0.0.0 --port $PORT`.
2. Add the environment variables, plus `SESSION_HTTPS_ONLY=true`.
3. Keep it awake: a free service sleeps after 15 idle minutes, and a sleeping app can't send alerts or reminders. Ping `GET /internal/tick` every 10 minutes with header `Authorization: Bearer <CRON_SECRET>` from cron-job.org, and/or use the included GitHub Actions workflow (`.github/workflows/tick.yml`, secrets `APP_URL` and `CRON_SECRET`). Use two independent triggers so one failing doesn't stop the switch.
4. Run a **single worker**: each worker starts its own scheduler and rate-limit counters.

## API overview

| Area | Routes |
| --- | --- |
| Auth | `/auth/register` (optional `email`), `/auth/login`, `/auth/logout`, `/auth/me`, `PUT /auth/email` |
| Dashboard | `GET /dashboard?range=7\|30\|90&tz=` (read-only, your own data) |
| Check-in | `GET/POST /pulse` (hold-to-confirm, optional `mood`) |
| Posts | `POST /checkin` (journal/task/important), `PUT/DELETE /checkin/{id}`, `GET /history?category=` |
| Calendar | `GET /events?start=&end=`, `POST /events`, `PATCH/DELETE /events/{id}` (needs an email on the account) |
| Habits | `/habits`, `/habits/{id}`, `/habits/{id}/logs/{date}` |
| Tasks | `/todos`, `/todos/{id}`, `/todos/day/{date}`, `/todos/stats`, `/todos/settings`, `/widget/summary` |
| Push | `/push/public-key`, `/push/subscribe`, `/push/unsubscribe`, `/push/test` |
| Ops | `/internal/tick` (cron secret), `/admin`, `/health` |

Over a rate limit the API returns `429` with `Retry-After`. Counters are in memory and reset on restart.

## Security notes

- Entries are stored as plain text. Keep real passwords out of the Important page.
- Photos live in a private bucket behind one-hour signed URLs. Uploads are checked by their real file bytes; metadata such as GPS tags is kept.
- Passwords use PBKDF2-SHA256. Login, registration and writes are rate limited.
- Email addresses are not verified. Reminders go only to the address on the account, one email per event, capped at 500 events per account. Close registration from the admin page once your people have signed up.
- The admin page shows account metadata only, never entries or photos.

## Roadmap

- [x] Edit/delete entries, habit streaks, multiple users
- [x] Calendar with email reminders, hold-to-confirm check-in, dashboard with insights
- [ ] Verify email addresses before sending reminders
- [ ] Recurring calendar events
- [ ] Full-text search, export as JSON/CSV
- [ ] Custom deadline per category

## License

MIT

## Author

**Adnan** ([@Adnan-Zhaikh](https://github.com/Adnan-Zhaikh))
