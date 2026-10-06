# Man-Switch

A self-hosted journal and accountability tool. Check in regularly with a note about what happened or how a goal is going. If you go silent past your deadline, it sends an alert straight to your phone.

<!-- Add a screenshot of the web UI here. -->

## Why I built it

I wanted one place to write down important events and dates, a private journal that is mine and runs on my own setup. Then I added the "switch" part: if I stop checking in, something should notice. That turned a simple notes app into a small running service.

## How it works

1. You log a check-in with a note. Each one is saved with a timestamp.
2. A background job runs on a schedule and checks how long it has been since your last check-in.
3. If that time passes your deadline (48 hours by default), it pushes a notification to your phone.

## What's inside

- **FastAPI** backend with endpoints to check in and view history
- **Postgres (Supabase)** to store every check-in
- **APScheduler** for the background deadline check
- **ntfy** for push notifications to my phone
- **HTTP Basic Auth** to protect everything except `/health`
- **A retro-styled web UI** in plain HTML, CSS, and JavaScript
- **Render** for hosting, with an external cron pinger to keep it awake

## What I learned

- **FastAPI:** building an API, defining routes, and serving a frontend from the same app.
- **APScheduler:** running background jobs inside a web service. Scheduled work and web requests live in the same process, so if the host sleeps, the scheduler sleeps too.
- **Keeping a free host awake:** Render's free tier spins down after about 15 minutes of no traffic. I fixed it with a public `/health` endpoint pinged every 10 minutes by cron-job.org.
- **Environment-based config:** database URLs, passwords, and the notification topic all live in `.env` and are never committed.
- **Authentication basics:** protecting routes with Basic Auth, and comparing credentials with `secrets.compare_digest` to avoid timing attacks.
- **Working with Postgres:** connecting to Supabase through the connection pooler, because the direct connection is IPv6-only and many networks can't reach it.
- **Deployment:** this is a running service, not a toy script.

## Run it yourself

```bash
git clone https://github.com/Adnan-Zhaikh/Man-Switch
cd Man-Switch
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file:

```
DATABASE_URL=postgresql://...          # Supabase transaction pooler URL
NTFY_TOPIC=your-private-topic-name
BASIC_AUTH_USER=choose-a-username
BASIC_AUTH_PASS=choose-a-strong-password
```

Then start it:

```bash
python -m uvicorn main:app --reload
```

Open `http://127.0.0.1:8000/` and log in. To get phone alerts, install the [ntfy app](https://ntfy.sh/) and subscribe to the same topic name.

**Settings:** the deadline is `DEADLINE_HOURS` in `scheduler.py`. How often the check runs is the scheduler interval in `main.py`.

## Endpoints

| Route | Method | Auth | What it does |
| --- | --- | --- | --- |
| `/` | GET | Yes | Serves the web UI |
| `/checkin?note=...` | POST | Yes | Logs a check-in with a note |
| `/history` | GET | Yes | Returns all check-ins as JSON |
| `/health` | GET | No | Used by the keepalive pinger |

## Security notes

- Keep your ntfy topic name private. Anyone who has it can read or fake your notifications.
- Never commit `.env`.
- Notes are stored as plain text in the database and protected by Basic Auth. Don't store real passwords or other highly sensitive secrets in it.

## Roadmap

- [ ] **New UI:** a redesigned interface
- [ ] **Photo support:** attach photos to important events

## License

MIT

## Author

**Adnan** — [@Adnan-Zhaikh](https://github.com/Adnan-Zhaikh)
