# Man Switch

A self-hosted accountability journal. You check in regularly with posts, optionally with photos. If you go quiet past your deadline, it sends a push alert straight to your phone. Full CRUD support—create, read, edit, and delete entries anytime.

<!-- Add screenshots: Journal page, Important page, dark and light mode. -->

## Why I built it

I wanted one private place to write down what's happening and keep things worth remembering, running on my own setup. Then I added the "switch" part: if I stop checking in, something should notice.

## What it does

Posts live on three pages:

- **Journal:** casual notes and day-to-day thoughts
- **Tasks / Goals:** what I'm working on and what's next
- **Important:** links, numbers, and notes worth keeping

Any new post on any page resets the deadline clock. Posts can include a photo, uploaded from the gallery or straight from the camera. The UI has a different accent color for each page and a dark/light mode toggle.

If the deadline passes (24 hours by default), the app sends a **browser push notification** (Chrome's own notification system) to every device where you turned alerts on. It repeats hourly until you post again.

**Edit & Delete:** Click the Edit or Delete button on any card to modify or remove an entry. Editing opens a modal where you can change the text, category, and optionally replace the photo. Deleting removes the entry and cleans up its image from storage.

## How it works

- **FastAPI** serves the API and the web UI.
- **Postgres (Supabase)** stores every post with a timestamp and category.
- **Supabase Storage** holds photos in a **private bucket**. The database stores only the file path, and the API creates short-lived signed URLs when a page loads.
- **APScheduler** runs inside the app every 5 minutes and checks how long it has been since each user's last check-in. An **external cron hook** (`/internal/tick`) does the same check and also wakes a sleeping host.
- **Web Push (VAPID)** delivers the alert to the browser's push service, so it arrives even when the app isn't open. No third-party app is needed.
- **Rate limiting** protects login, registration, writes and the cron hook (in-memory, per process).
- **Admin page** (`/admin`) for the owner: account overview, system status, registration switch.
- **Session auth** with register/login/logout keeps each account isolated.
- **Frontend:** plain HTML/CSS/JS, no framework and no build step.

## Stack

Python, FastAPI, Uvicorn, psycopg2, APScheduler, pywebpush, Supabase (Postgres + Storage), vanilla HTML/CSS/JS. Deployed on Render.

## What I learned

- **FastAPI:** routes, form and file uploads, and serving a frontend from the same app.
- **APScheduler:** running background jobs inside a web service. The scheduler and the web server share one process, so if the host sleeps, the scheduler sleeps too.
- **Keeping a free host awake:** Render's free tier spins down after about 15 minutes without traffic and takes about a minute to start again. Pingers with short timeouts see that cold start as a failure. See *Keeping it alive on Render's free tier* below.
- **Private file storage:** photos go in a private Supabase bucket, and the app hands out signed URLs that expire after an hour instead of public links.
- **Validating uploads on the server:** checking the category, the image type (JPEG, PNG, WebP), and the size limit (5 MB) before accepting anything.
- **Environment-based config:** database URLs, passwords and keys all live in `.env` and are never committed.
- **Authentication basics:** session cookies + PBKDF2 password hashing for account login.
- **Postgres through Supabase:** using the connection pooler, because the direct connection is IPv6-only and many networks can't reach it.
- **Deployment:** a real running service, not a toy script.
- **Full CRUD in FastAPI:** building PUT and DELETE endpoints with proper validation and cascading deletes for related files.

### 1. Clone and install

```bash
git clone https://github.com/Adnan-Zhaikh/Man-Switch
cd Man-Switch
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` lists everything the app needs (including `itsdangerous`, which session cookies require, and `httpx`, which is only used by the tests). Run the tests with `python -m unittest discover -s tests` (set `SESSION_SECRET` to any value first).

### 2. Browser notifications (Web Push)

1. On your own computer run `python generate_vapid_keys.py`. It prints `VAPID_PRIVATE_KEY` and `VAPID_SUBJECT`.
2. Put both in your environment (`.env` locally, Render's Environment tab in production) and set `VAPID_SUBJECT` to `mailto:` plus your real email address.
3. Open the app, sign in and tap **Enable alerts** in the top bar, allow notifications, then tap **Test**. Do this on every device that should get alerts (up to 10 per account).

Keep `VAPID_PRIVATE_KEY` secret. If you change it, every device has to tap **Enable alerts** again. Alerts need HTTPS (Render provides it). On iPhone the site must first be added to the Home Screen. Chrome on Android and desktop work directly.

Push endpoints are only accepted from real browser push services (Google, Mozilla, Apple, Microsoft), so users can't make the server call arbitrary addresses.

### 3. Supabase

1. Create a free project at [supabase.com](https://supabase.com).
2. **Database:** copy the **Transaction pooler** connection string (Project Settings → Database). Don't use the direct connection.
3. **Storage:** create a bucket named `post-images` with **Public bucket turned off**. Optionally limit it to `image/jpeg`, `image/png`, `image/webp` and 5 MB.
4. **API keys:** copy your Project URL (just `https://<ref>.supabase.co`, nothing after `.co`) and a **secret key** (Project Settings → API Keys).
5. The app creates/updates tables on first start (`users`, `checkins`, and ownership indexes). If your table is from an older version, these are the key compatibility columns:

```sql
ALTER TABLE checkins ADD COLUMN IF NOT EXISTS image_url TEXT;
ALTER TABLE checkins ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'journal';
ALTER TABLE checkins ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE;
```

### 4. Environment variables

Create a `.env` file in the project root:

```
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
SESSION_SECRET=generate-a-long-random-secret
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=sb_secret_...
VAPID_PRIVATE_KEY=...        # from generate_vapid_keys.py
VAPID_SUBJECT=mailto:you@example.com
ADMIN_USERNAME=your-name     # your admin account, created at startup
ADMIN_PASSWORD=a-long-password
CRON_SECRET=long-random-value   # enables /internal/tick for external schedulers
```

Optional (see `.env.example` for all of them):

```
SESSION_HTTPS_ONLY=true      # set this in production so the session cookie is Secure
TRUSTED_PROXY_HOPS=1         # proxies in front of the app (Render: 1, local: 0)
DEADLINE_HOURS=24
ALERT_REPEAT_MINUTES=60
TICK_MINUTES=5
REGISTRATION_OPEN=true
SESSION_MAX_AGE_SECONDS=43200
```

`ADMIN_USERNAME` / `ADMIN_PASSWORD` are how you become admin on a host with no shell. If the account doesn't exist it is created; if it already exists it is promoted only when `ADMIN_PASSWORD` matches its current password, so nobody can take admin by registering the name first. Your admin account is also an ordinary account, so it has its own private journal like everyone else's.

Legacy single-user upgrade compatibility (optional):

```
BASIC_AUTH_USER=old-username
BASIC_AUTH_PASS=old-password
```

If no users exist yet, these legacy values are used one time to bootstrap the first account and migrate unowned check-ins.

`.env` must stay out of version control. The Supabase secret key bypasses all access rules, so treat it like a database password.

### 5. Run locally

```bash
python -m uvicorn main:app --reload
```

Open `http://127.0.0.1:8000/` and register a new account (or log in).

## Admin page

Signed in as the admin, an **Admin** link appears in the top bar (or go to `/admin`). It shows when the last deadline check ran and what it did, whether alerts and the cron hook are configured, and every account with its entry count, last check-in, number of alert devices and an *overdue* badge. You can run a check now, send yourself a test alert, open or close registration, and delete an account (its photos are removed too). It never shows anyone's entries or photos. The page also shows the client address the app detects, so you can confirm `TRUSTED_PROXY_HOPS` is right.

## Rate limits

| What | Limit |
| --- | --- |
| Every request, per IP | 300 per minute (`/health` and `/internal/*` exempt) |
| Login attempts, per IP | 30 per 10 minutes |
| Failed logins | 8 per 15 minutes for one IP and username, 40 per 15 minutes for one username from anywhere |
| Registrations, per IP | 5 per hour |
| Creating, editing, deleting entries, per user | 60 per minute |
| Adding devices, per user | 20 per hour; test alerts 5 per 10 minutes |
| Wrong cron key, per IP | 10 per 5 minutes |

Over a limit the API returns `429` with a `Retry-After` header. Counters live in memory, so they reset on restart and aren't shared between workers.

## Configuration

Environment variables (see above): `DEADLINE_HOURS` is how long you can go without checking in, `ALERT_REPEAT_MINUTES` is how often to re-alert while overdue, `TICK_MINUTES` is the in-app check interval.

## Endpoints

| Route | Method | Auth | Purpose |
| --- | --- | --- | --- |
| `/` | GET, HEAD | No | Serves the web UI (login/register + app) |
| `/auth/config` | GET | No | `registration_open` flag |
| `/auth/register` | POST | No | Registers a new account and starts a session (`username`, `password`, `confirm_password`) |
| `/auth/login` | POST | No | Starts a session (`username`, `password`) |
| `/auth/logout` | POST | No | Clears the active session |
| `/auth/me` | GET | Yes | Returns current authenticated user |
| `/checkin` | POST | Yes | Creates a post. Multipart form: `note`, `category` (`journal`, `important` or `task`), optional `image` |
| `/checkin/{id}` | PUT | Yes | Updates a post. Multipart form: `note`, `category`, optional `image`, optional `remove_image=true`. If neither is sent, the old image is preserved. |
| `/checkin/{id}` | DELETE | Yes | Deletes a post and its associated image from storage |
| `/history?category=` | GET | Yes | Returns only the current user's posts, newest first. Optional category filter. Image paths are replaced with signed URLs |
| `/push/public-key` | GET | Yes | Whether alerts are configured, the public key, and how many devices you have |
| `/push/subscribe`, `/push/unsubscribe` | POST | Yes | Register or remove this device (JSON) |
| `/push/test` | POST | Yes | Sends a test alert to your devices |
| `/internal/tick` | GET, POST, HEAD | Cron secret | Runs the deadline check. `Authorization: Bearer <CRON_SECRET>` (or `?key=`) |
| `/admin`, `/admin/api/*` | various | Admin | Owner-only page and its API |
| `/health` | GET, HEAD | No | Cheap liveness check (does not touch the database) |

## Deployment (Render)

1. Push the repo to GitHub, with `.env` ignored.
2. Create a **Web Service** on [Render](https://render.com) from the repo.
3. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add the environment variables from step 4 above in Render's Environment tab, plus `SESSION_HTTPS_ONLY=true`.
5. Set up the heartbeat described next.

### Keeping it alive on Render's free tier

A dead-man's switch only works if something is running when your deadline passes, and a free Render service sleeps after 15 idle minutes (about a minute to wake), can be restarted at any time, and shares 750 free hours per month with every other free service in the workspace. One service kept awake 24/7 uses about 744 of them. Once the hours run out Render suspends all free services until next month.

What this app does about it:

- **`/internal/tick`** runs the deadline check and wakes the app. Deadlines come from stored timestamps, so a check that runs late (after a long sleep) still alerts correctly, and the app also runs a check as soon as it starts.
- Alerts are de-duplicated in the database, so overlapping triggers (in-app scheduler, cron, admin button) never send the same alert twice.
- `/health` and `/` answer **HEAD** requests (many uptime monitors use HEAD), and `/health` never touches the database, so a slow database can't make the pinger think the app is down.
- Database connections have a 10 second timeout and are always closed.

Set up two independent triggers so one failing doesn't stop the switch:

1. **cron-job.org** (or UptimeRobot): `GET https://<your-app>.onrender.com/internal/tick` every 10 minutes with header `Authorization: Bearer <CRON_SECRET>`. Set the request timeout as high as it allows (the cold start takes about a minute) and check its settings for what it does after repeated failures.
2. **GitHub Actions**: `.github/workflows/tick.yml` is included. Add repository secrets `APP_URL` and `CRON_SECRET`. It waits up to 2 minutes and retries.

The admin page shows when the last check ran. If that gets stale, a trigger has stopped.

This is the best a free tier can do. For something you really depend on, a paid always-on instance (Render's Starter is about $7/month) removes the sleeping entirely.

## Security notes

- Everything is stored as plain text in the database. Don't keep real passwords on the Important page; use a password manager. Links, numbers, and notes are fine.
- Push subscriptions are endpoint URLs that act like a shared secret. They are stored per device and removed automatically when the browser reports them gone.
- Photos sit in a private bucket. Signed URLs expire after an hour, but anyone holding a live link can view that photo until then.
- Timestamps are stored in UTC, and the browser converts them to local time.
- Uploads are checked by their actual leading bytes (JPEG, PNG or WebP), not just the client-declared `Content-Type`. Images are not re-encoded, so metadata such as GPS tags in photos is kept.
- Login and registration are rate limited. Close registration from the admin page once your people have signed up.
- The admin page shows account metadata only, never entries or photos.

## Known limitations

- Run a single worker. With several (for example `gunicorn -w 4`), every worker starts its own scheduler and keeps its own rate-limit counters. Alerts stay de-duplicated, but limits become per worker.
- Rate-limit counters reset when the app restarts.
- Alerts go only to devices where you enabled them. A user with no device gets nothing, and the admin page flags that account.
- The old `ntfy_topic` column is left in existing databases but is no longer used.

## Roadmap

- [x] Edit and delete entries
- [ ] Calendar and stats views (streak counter, check-in frequency, entries per category)
- [ ] Tabs inside the Important page
- [ ] Full-text search across all entries
- [ ] Export entries as JSON or CSV
- [ ] Recurring checkins or templates (quick-add buttons)
- [ ] Tags or labels on entries for better organization
- [ ] Weekly or monthly digest view
- [ ] Bulk operations (delete multiple, archive old entries)
- [ ] Optional notes on why an entry was edited/deleted
- [ ] Custom deadline per category (different alert times for journal vs. tasks)
- [x] Multiple users (auth per user, private entries)
- [ ] Voice notes or transcription (in addition to photos)
- [ ] Syncing to external services (email digest, webhook, IFTTT)


## License

MIT

## Author

**Adnan** — [@Adnan-Zhaikh](https://github.com/Adnan-Zhaikh)
