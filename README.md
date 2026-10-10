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

If the deadline passes (24 hours by default), ntfy sends a push alert to your phone. The alert repeats on every check until you post again.

**Edit & Delete:** Click the Edit or Delete button on any card to modify or remove an entry. Editing opens a modal where you can change the text, category, and optionally replace the photo. Deleting removes the entry and cleans up its image from storage.

## How it works

- **FastAPI** serves the API and the web UI.
- **Postgres (Supabase)** stores every post with a timestamp and category.
- **Supabase Storage** holds photos in a **private bucket**. The database stores only the file path, and the API creates short-lived signed URLs when a page loads.
- **APScheduler** runs inside the app and checks every 15 minutes how long it has been since the last check-in.
- **ntfy** delivers the push notification when the deadline is missed.
- **HTTP Basic Auth** protects every route except `/health`.
- **Frontend:** a single HTML/CSS/JS file, no framework and no build step.

## Stack

Python, FastAPI, Uvicorn, psycopg2, APScheduler, Supabase (Postgres + Storage), ntfy.sh, vanilla HTML/CSS/JS. Deployed on Render, kept awake by an external cron pinger.

## What I learned

- **FastAPI:** routes, form and file uploads, and serving a frontend from the same app.
- **APScheduler:** running background jobs inside a web service. The scheduler and the web server share one process, so if the host sleeps, the scheduler sleeps too.
- **Keeping a free host awake:** Render's free tier spins down after about 15 minutes without traffic. I fixed it with a public `/health` endpoint pinged every 10 minutes by cron-job.org.
- **Private file storage:** photos go in a private Supabase bucket, and the app hands out signed URLs that expire after an hour instead of public links.
- **Validating uploads on the server:** checking the category, the image type (JPEG, PNG, WebP), and the size limit (5 MB) before accepting anything.
- **Environment-based config:** database URLs, passwords, keys, and the notification topic all live in `.env` and are never committed.
- **Authentication basics:** Basic Auth, with credentials compared using `secrets.compare_digest` to avoid timing attacks.
- **Postgres through Supabase:** using the connection pooler, because the direct connection is IPv6-only and many networks can't reach it.
- **Deployment:** a real running service, not a toy script.
- **Full CRUD in FastAPI:** building PUT and DELETE endpoints with proper validation and cascading deletes for related files.

## Run it yourself

### 1. Clone and install

```bash
git clone https://github.com/Adnan-Zhaikh/Man-Switch
cd Man-Switch
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` needs at least: `fastapi`, `uvicorn`, `psycopg2-binary`, `python-dotenv`, `apscheduler`, `requests`, `supabase`, `python-multipart`.

### 2. ntfy

1. Install the [ntfy app](https://ntfy.sh/) on your phone.
2. Pick a private, hard-to-guess topic name and subscribe to it.
3. Your `NTFY_TOPIC` value is the full URL, for example `https://ntfy.sh/your-topic-name`.

### 3. Supabase

1. Create a free project at [supabase.com](https://supabase.com).
2. **Database:** copy the **Transaction pooler** connection string (Project Settings → Database). Don't use the direct connection.
3. **Storage:** create a bucket named `post-images` with **Public bucket turned off**. Optionally limit it to `image/jpeg`, `image/png`, `image/webp` and 5 MB.
4. **API keys:** copy your Project URL (just `https://<ref>.supabase.co`, nothing after `.co`) and a **secret key** (Project Settings → API Keys).
5. The app can create the table on first start. If your table is from an older version, add the new columns:

```sql
ALTER TABLE checkins ADD COLUMN IF NOT EXISTS image_url TEXT;
ALTER TABLE checkins ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'journal';
```

### 4. Environment variables

Create a `.env` file in the project root:

```
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
NTFY_TOPIC=https://ntfy.sh/your-private-topic
BASIC_AUTH_USER=choose-a-username
BASIC_AUTH_PASS=choose-a-strong-password
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_KEY=sb_secret_...
```

`.env` must stay out of version control. The Supabase secret key bypasses all access rules, so treat it like a database password.

### 5. Run locally

```bash
python -m uvicorn main:app --reload
```

Open `http://127.0.0.1:8000/` and log in with your Basic Auth credentials.

## Configuration

- `scheduler.py`: `DEADLINE_HOURS` is how long you can go without checking in (24 by default).
- `main.py`: the scheduler interval (15 minutes by default) controls how often the check runs.

## Endpoints

| Route | Method | Auth | Purpose |
| --- | --- | --- | --- |
| `/` | GET | Yes | Serves the web UI |
| `/checkin` | POST | Yes | Creates a post. Multipart form: `note`, `category` (`journal`, `important` or `task`), optional `image` |
| `/checkin/{id}` | PUT | Yes | Updates a post. Multipart form: `note`, `category`, optional `image`. If `image` is not provided, the old image is preserved. |
| `/checkin/{id}` | DELETE | Yes | Deletes a post and its associated image from storage |
| `/history?category=` | GET | Yes | Returns posts, newest first, as JSON. Optional category filter. Image paths are replaced with signed URLs |
| `/health` | GET | No | Used by the keepalive pinger |

## Deployment (Render)

1. Push the repo to GitHub, with `.env` ignored.
2. Create a **Web Service** on [Render](https://render.com) from the repo.
3. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add all six environment variables in Render's Environment tab.
5. Set up a free external cron service such as [cron-job.org](https://cron-job.org) to request `/health` every 10 minutes, so the free tier doesn't sleep and stop the scheduler.

## Security notes

- Everything is stored as plain text in the database. Don't keep real passwords on the Important page; use a password manager. Links, numbers, and notes are fine.
- The ntfy topic URL works like a shared secret. Anyone who has it can read your alerts or send fake ones.
- Photos sit in a private bucket. Signed URLs expire after an hour, but anyone holding a live link can view that photo until then.
- Timestamps are stored in UTC, and the browser converts them to local time.
- The `Content-Type` of an uploaded file is declared by the client. It is checked, but the file contents are not inspected.

## Known limitations

- Single user only.

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
- [ ] Multiple users (auth per user, private entries)
- [ ] Voice notes or transcription (in addition to photos)
- [ ] Syncing to external services (email digest, webhook, IFTTT)

## Feature Ideas

Here are additional features you could add to make Man-Switch even more powerful:

### Analytics & Insights
- **Streak counter:** Display how many days in a row you've checked in
- **Statistics dashboard:** posts per category, busiest time of day, check-in frequency
- **Heat map calendar:** visual representation of check-in patterns
- **Word cloud:** most-used words in your entries

### Organization
- **Full-text search:** find entries by keyword
- **Tags/labels:** attach tags to entries and filter by them
- **Archived entries:** soft-delete old entries without losing data
- **Bulk operations:** delete/archive multiple entries at once

### Reminders & Motivation
- **Custom deadlines per category:** e.g., journal every 24h, tasks every 12h
- **Motivational tips:** random encouraging messages on the page
- **Check-in streak badges:** visual reward for consistency
- **Email digest:** weekly summary of your entries

### Export & Sync
- **Export to JSON/CSV:** backup or migrate your data
- **Email digest:** weekly or monthly summary sent to your inbox
- **Webhook integration:** send new entries to Discord, Slack, or other services
- **IFTTT support:** trigger actions based on check-ins

### Content
- **Templates/quick-add buttons:** pre-fill entry forms for common check-ins
- **Voice notes:** record audio instead of typing (with optional transcription)
- **File attachments:** beyond just images (PDFs, documents)
- **Markdown support:** format entries with bold, lists, code blocks

### Collaboration (Future)
- **Multi-user support:** separate accounts with private entries
- **Shared goals:** invite friends to check in together
- **Comment/react on entries:** like and comment on past posts

### Customization
- **Custom colors and themes:** more theme options beyond light/dark
- **Custom categories:** create your own page names
- **Notification customization:** choose alert frequency and channels
- **Data retention policy:** auto-delete old entries after X days

Start with **statistics** or **search** if you want quick wins. **Export** is useful for data portability. **Multi-user support** is the biggest architectural change but opens the app up to teams.

## License

MIT

## Author

**Adnan** — [@Adnan-Zhaikh](https://github.com/Adnan-Zhaikh)
