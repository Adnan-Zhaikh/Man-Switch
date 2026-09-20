# Deadman Switch

A self-hosted accountability tool: check in periodically to log your progress on a goal. Miss your deadline, and it fires a push notification to your phone to let you know you've gone silent.

Built as a learning project covering scheduling, background jobs, environment-based config, authentication, and deployment — not a toy script, an actual running service.

## How it works

- **FastAPI** backend exposes endpoints to check in and view history
- **Postgres (Supabase)** stores every check-in as a timestamped row
- **APScheduler** runs a background job on an interval, checking elapsed time since your last check-in
- **ntfy** delivers a push notification to your phone when the deadline is missed
- **HTTP Basic Auth** protects everything except a `/health` endpoint (used to keep the free-tier host awake)
- A small **retro-styled web UI** replaces manually poking the API via Swagger docs

## Stack

- Python, FastAPI, Uvicorn
- Supabase (Postgres) via `psycopg2`
- APScheduler
- ntfy.sh for notifications
- Vanilla HTML/CSS/JS frontend (no build step)
- Deployed on Render, kept alive via an external cron pinger (e.g. cron-job.org)

## Setup

### 1. Clone and install

```bash
git clone https://github.com/Adnan-Zhaikh/Man-Switch
cd Man-Switch
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Set up ntfy

1. Install the [ntfy app](https://ntfy.sh/) on your phone.
2. Pick a private, hard-to-guess topic name.
3. Subscribe to that topic in the app.

### 3. Set up Supabase

1. Create a free project at [supabase.com](https://supabase.com).
2. Go to **Project Settings → Database → Connection Pooling** and copy the **Transaction pooler** connection string (not the direct connection — it's IPv6-only and unreachable from many networks).

### 4. Configure environment variables

Create a `.env` file in the project root:

```
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
NTFY_TOPIC=your-private-topic-name
BASIC_AUTH_USER=choose-a-username
BASIC_AUTH_PASS=choose-a-strong-password
```

**Never commit `.env`** — it's already listed in `.gitignore`.

### 5. Run locally

```bash
python -m uvicorn main:app --reload
```

Visit `http://127.0.0.1:8000/` and log in with the credentials you set above.

## Configuration

In `scheduler.py`:

- `DEADLINE_HOURS` — how long you can go without checking in before it counts as missed (default: 48)

In `main.py`:

- The scheduler's `interval` (in minutes) controls how often the deadline check runs. It doesn't need to be frequent relative to your deadline — every 15 minutes is plenty for a multi-day deadline.

## Deployment (Render)

1. Push this repo to GitHub (`.env` and `deadman.db`/local data stay out of it via `.gitignore`).
2. Create a new **Web Service** on [Render](https://render.com), connected to your repo.
3. Start command:
   ```
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```
4. Add all four environment variables from your `.env` in Render's dashboard.
5. Deploy.

Render's free tier spins down after ~15 minutes of no HTTP traffic — which would silently stop the background scheduler too. To prevent this, set up a free external cron service (e.g. [cron-job.org](https://cron-job.org)) to hit your app's `/health` endpoint every 10 minutes. `/health` requires no authentication, by design, so it's safe to ping publicly.

## Endpoints

| Route | Method | Auth | Purpose |
|---|---|---|---|
| `/` | GET | Yes | Serves the web UI |
| `/checkin?note=...` | POST | Yes | Logs a new check-in with a progress note |
| `/history` | GET | Yes | Returns all check-ins as JSON |
| `/health` | GET | No | Used by the keepalive pinger |

## Security notes

- Your ntfy topic name is effectively a shared secret — anyone who has it can read your notifications or trigger fake ones. Keep it out of version control.
- Basic Auth credentials are compared using `secrets.compare_digest` to avoid timing attacks.
- All secrets live in environment variables, never hardcoded.

## License

MIT — do whatever you want with it.
