import os
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv()


def get_conn():
    # connect_timeout: a hung database connection must fail fast, not hang the request
    # (a hung request looks exactly like "the app is down" to an uptime pinger).
    return psycopg2.connect(os.getenv("DATABASE_URL"), connect_timeout=10)


@contextmanager
def _cursor(dict_rows=False):
    """Yield a cursor; commit on success, roll back on error, always close."""
    conn = get_conn()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor) if dict_rows else conn.cursor()
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with _cursor() as cursor:
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
        # Older databases still have a (now unused) ntfy_topic column; it is left alone.
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_alert_at TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS checkins (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                progress_note TEXT NOT NULL,
                image_url TEXT,
                category TEXT NOT NULL DEFAULT 'journal'
            )
            '''
        )
        # Compatibility columns for databases created by older versions.
        cursor.execute("ALTER TABLE checkins ADD COLUMN IF NOT EXISTS image_url TEXT")
        cursor.execute(
            "ALTER TABLE checkins ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'journal'"
        )
        cursor.execute(
            "ALTER TABLE checkins ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_checkins_user_timestamp ON checkins (user_id, timestamp DESC)"
        )
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                user_agent TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscriptions (user_id)"
        )
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'UTC'")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS todo_reminders BOOLEAN NOT NULL DEFAULT TRUE")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quiet_start SMALLINT NOT NULL DEFAULT 22")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS quiet_end SMALLINT NOT NULL DEFAULT 7")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_todo_reminder_at TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS widget_token_hash TEXT")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_widget_token ON users (widget_token_hash) WHERE widget_token_hash IS NOT NULL"
        )
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                day TEXT NOT NULL,
                priority SMALLINT NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                done_at TEXT
            )
            '''
        )
        cursor.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS postponed INTEGER NOT NULL DEFAULT 0")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_day ON tasks (user_id, day)")
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS habits (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                icon TEXT NOT NULL DEFAULT '✅',
                color TEXT NOT NULL DEFAULT '#3fb950',
                days TEXT NOT NULL DEFAULT '1111111',
                created_day TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_habits_user ON habits (user_id)")
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS habit_logs (
                id SERIAL PRIMARY KEY,
                habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                day TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE (habit_id, day)
            )
            '''
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_habit_logs_user_day ON habit_logs (user_id, day)")
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '#2d8cff',
                starts_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reminded_at TEXT
            )
            '''
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_user_start ON events (user_id, starts_at)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_pending ON events (starts_at) WHERE reminded_at IS NULL"
        )
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )


# ---- settings ---------------------------------------------------------------


def get_setting(key, default=None):
    with _cursor() as cursor:
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = cursor.fetchone()
        return row[0] if row else default


def set_setting(key, value):
    with _cursor() as cursor:
        cursor.execute(
            '''
            INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
            ''',
            (key, value, _now()),
        )


# ---- users ------------------------------------------------------------------


def create_user(username, password_hash, is_admin=False, email=None):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at, email) VALUES (%s, %s, %s, %s, %s) RETURNING id, username, is_admin, created_at, email",
            (username, password_hash, is_admin, _now(), email),
        )
        return cursor.fetchone()


def get_user_by_username(username):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            "SELECT id, username, password_hash, is_admin, created_at, email FROM users WHERE username = %s",
            (username,),
        )
        return cursor.fetchone()


def get_user_by_id(user_id):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            "SELECT id, username, is_admin, created_at, email FROM users WHERE id = %s",
            (user_id,),
        )
        return cursor.fetchone()


def set_user_admin(user_id, is_admin):
    with _cursor() as cursor:
        cursor.execute("UPDATE users SET is_admin = %s WHERE id = %s", (is_admin, user_id))


def set_user_email(user_id, email):
    """Store the account's email (None clears it). It is only used for calendar reminders."""
    with _cursor() as cursor:
        cursor.execute("UPDATE users SET email = %s WHERE id = %s", (email, user_id))


def has_users():
    with _cursor() as cursor:
        cursor.execute("SELECT 1 FROM users LIMIT 1")
        return cursor.fetchone() is not None


def get_unowned_checkin_count():
    with _cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM checkins WHERE user_id IS NULL")
        result = cursor.fetchone()
        return result[0] if result else 0


def assign_unowned_checkins(user_id):
    with _cursor() as cursor:
        cursor.execute("UPDATE checkins SET user_id = %s WHERE user_id IS NULL", (user_id,))


def admin_list_users():
    """Account metadata only. Entry text and photos are deliberately never exposed here."""
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            '''
            SELECT u.id, u.username, u.is_admin, u.created_at, u.last_alert_at,
                   (SELECT COUNT(*) FROM checkins c WHERE c.user_id = u.id) AS entries,
                   (SELECT MAX(c.timestamp) FROM checkins c WHERE c.user_id = u.id) AS last_checkin,
                   (SELECT COUNT(*) FROM push_subscriptions p WHERE p.user_id = u.id) AS devices
            FROM users u
            ORDER BY u.created_at
            '''
        )
        return cursor.fetchall()


def admin_delete_user(user_id):
    """Delete a user and (by cascade) their entries and devices.

    Returns (deleted, image_paths) so the caller can remove the photos from storage.
    """
    with _cursor() as cursor:
        cursor.execute(
            "SELECT image_url FROM checkins WHERE user_id = %s AND image_url IS NOT NULL",
            (user_id,),
        )
        paths = [row[0] for row in cursor.fetchall()]
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        return cursor.rowcount > 0, paths


# ---- check-ins --------------------------------------------------------------


def add_checkin(user_id, note, category="journal", image_url=None):
    with _cursor() as cursor:
        cursor.execute(
            "INSERT INTO checkins (user_id, timestamp, progress_note, category, image_url) VALUES (%s, %s, %s, %s, %s)",
            (user_id, _now(), note, category, image_url),
        )


def get_all_checkins(user_id, category=None):
    with _cursor() as cursor:
        if category:
            cursor.execute(
                "SELECT id, timestamp, progress_note, image_url, category FROM checkins WHERE user_id = %s AND category = %s ORDER BY timestamp DESC",
                (user_id, category),
            )
        else:
            cursor.execute(
                "SELECT id, timestamp, progress_note, image_url, category FROM checkins WHERE user_id = %s ORDER BY timestamp DESC",
                (user_id,),
            )
        return cursor.fetchall()


def get_checkin_by_id(checkin_id, user_id):
    with _cursor() as cursor:
        cursor.execute(
            "SELECT id, timestamp, progress_note, image_url, category FROM checkins WHERE id = %s AND user_id = %s",
            (checkin_id, user_id),
        )
        return cursor.fetchone()


def update_checkin(checkin_id, user_id, note, category, image_url=None, remove_image=False):
    """Update a check-in owned by user_id. Returns False if it doesn't exist.

    image_url given -> replace the image; remove_image -> clear it;
    neither -> leave the stored image untouched.
    """
    if image_url is not None:
        image_clause, image_params = ", image_url = %s", (image_url,)
    elif remove_image:
        image_clause, image_params = ", image_url = NULL", ()
    else:
        image_clause, image_params = "", ()

    with _cursor() as cursor:
        cursor.execute(
            f"UPDATE checkins SET progress_note = %s, category = %s{image_clause} WHERE id = %s AND user_id = %s",
            (note, category, *image_params, checkin_id, user_id),
        )
        return cursor.rowcount > 0


def delete_checkin(checkin_id, user_id):
    with _cursor() as cursor:
        cursor.execute(
            "DELETE FROM checkins WHERE id = %s AND user_id = %s RETURNING image_url",
            (checkin_id, user_id),
        )
        row = cursor.fetchone()
    if not row:
        return None, False
    return row[0], True


def list_activity(user_id, since_iso, limit=20000):
    """(timestamp, category) of every post and check-in since a moment, oldest first."""
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            "SELECT timestamp, category FROM checkins WHERE user_id = %s AND timestamp >= %s ORDER BY timestamp LIMIT %s",
            (user_id, since_iso, limit),
        )
        return [dict(r) for r in cursor.fetchall()]


def get_last_checkin_time(user_id):
    """Timestamp of the newest entry of any kind (posts and check-ins both reset the deadline)."""
    with _cursor() as cursor:
        cursor.execute("SELECT MAX(timestamp) FROM checkins WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def list_pulses(user_id, since_iso, limit=500):
    """Timestamps of the user's hold-to-confirm check-ins since a moment, newest first."""
    with _cursor() as cursor:
        cursor.execute(
            "SELECT timestamp FROM checkins WHERE user_id = %s AND category = 'pulse' AND timestamp >= %s ORDER BY timestamp DESC LIMIT %s",
            (user_id, since_iso, limit),
        )
        return [row[0] for row in cursor.fetchall()]


# ---- deadline / alerts ------------------------------------------------------


def get_users_for_deadline_check():
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            '''
            SELECT u.id, u.username, u.last_alert_at, MAX(c.timestamp) AS last_checkin
            FROM users u
            LEFT JOIN checkins c ON c.user_id = u.id
            GROUP BY u.id, u.username, u.last_alert_at
            '''
        )
        return cursor.fetchall()


def claim_alert(user_id, now_iso, cutoff_iso):
    """Atomically reserve the right to alert a user.

    Succeeds only if they haven't been alerted since cutoff_iso. The row lock makes
    this safe when the in-app scheduler and an external cron tick overlap.
    Returns (claimed, previous_last_alert_at).
    """
    with _cursor() as cursor:
        cursor.execute("SELECT last_alert_at FROM users WHERE id = %s FOR UPDATE", (user_id,))
        row = cursor.fetchone()
        if row is None:
            return False, None
        previous = row[0]
        if previous is not None and previous >= cutoff_iso:
            return False, previous
        cursor.execute("UPDATE users SET last_alert_at = %s WHERE id = %s", (now_iso, user_id))
        return True, previous


def release_alert(user_id, previous):
    """Undo claim_alert when nothing could be delivered, so the next tick retries."""
    with _cursor() as cursor:
        cursor.execute("UPDATE users SET last_alert_at = %s WHERE id = %s", (previous, user_id))


# ---- push subscriptions -----------------------------------------------------


def upsert_push_subscription(user_id, endpoint, p256dh, auth, user_agent=None):
    with _cursor() as cursor:
        cursor.execute(
            '''
            INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (endpoint) DO UPDATE
                SET user_id = EXCLUDED.user_id, p256dh = EXCLUDED.p256dh,
                    auth = EXCLUDED.auth, user_agent = EXCLUDED.user_agent
            ''',
            (user_id, endpoint, p256dh, auth, user_agent, _now()),
        )


def get_push_subscriptions(user_id):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = %s ORDER BY id",
            (user_id,),
        )
        return cursor.fetchall()


def count_push_subscriptions(user_id):
    with _cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id = %s", (user_id,))
        return cursor.fetchone()[0]


def delete_push_subscription(endpoint, user_id=None):
    """Remove a subscription; with user_id, only if it belongs to that user."""
    with _cursor() as cursor:
        if user_id is None:
            cursor.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,))
        else:
            cursor.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = %s AND user_id = %s",
                (endpoint, user_id),
            )
        return cursor.rowcount > 0


# ---- to-do tasks ------------------------------------------------------------

_TASK_COLUMNS = "id, title, day, priority, created_at, done_at, postponed"


def add_task(user_id, title, day, priority=0):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            f"INSERT INTO tasks (user_id, title, day, priority, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING {_TASK_COLUMNS}",
            (user_id, title, day, priority, _now()),
        )
        return dict(cursor.fetchone())


def get_task(task_id, user_id):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = %s AND user_id = %s", (task_id, user_id)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_tasks(user_id, start_day=None, end_day=None, only_open=False):
    """Tasks whose planned day is within [start_day, end_day] (either bound optional)."""
    where, params = ["user_id = %s"], [user_id]
    if start_day:
        where.append("day >= %s")
        params.append(start_day)
    if end_day:
        where.append("day <= %s")
        params.append(end_day)
    if only_open:
        where.append("done_at IS NULL")
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE {' AND '.join(where)} ORDER BY day, priority DESC, id",
            params,
        )
        return [dict(r) for r in cursor.fetchall()]


def count_tasks_on_day(user_id, day):
    with _cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM tasks WHERE user_id = %s AND day = %s", (user_id, day))
        return cursor.fetchone()[0]


def update_task(task_id, user_id, title=None, day=None, priority=None, done=None):
    """Change any of the given fields. Returns the updated task, or None if it isn't theirs."""
    sets, params = [], []
    if title is not None:
        sets.append("title = %s")
        params.append(title)
    if day is not None:
        sets.append("day = %s")
        params.append(day)
        # Moving a task to a later day counts as a postponement (the right-hand side sees the old day).
        sets.append("postponed = postponed + (CASE WHEN %s > day THEN 1 ELSE 0 END)")
        params.append(day)
    if priority is not None:
        sets.append("priority = %s")
        params.append(priority)
    if done is True:
        sets.append("done_at = COALESCE(done_at, %s)")
        params.append(_now())
    elif done is False:
        sets.append("done_at = NULL")
    if not sets:
        return get_task(task_id, user_id)
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = %s AND user_id = %s RETURNING {_TASK_COLUMNS}",
            (*params, task_id, user_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def delete_task(task_id, user_id):
    with _cursor() as cursor:
        cursor.execute("DELETE FROM tasks WHERE id = %s AND user_id = %s", (task_id, user_id))
        return cursor.rowcount > 0


def task_day_counts(user_id, start_day, end_day):
    """Per planned day: how many tasks were planned and how many of those got done."""
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            '''
            SELECT day, COUNT(*) AS planned, COUNT(done_at) AS done
            FROM tasks
            WHERE user_id = %s AND day >= %s AND day <= %s
            GROUP BY day ORDER BY day
            ''',
            (user_id, start_day, end_day),
        )
        return [{"day": r["day"], "planned": int(r["planned"]), "done": int(r["done"])} for r in cursor.fetchall()]


# ---- to-do settings, widget token, reminders --------------------------------


def get_todo_settings(user_id):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            "SELECT timezone, todo_reminders, quiet_start, quiet_end, (widget_token_hash IS NOT NULL) AS widget_configured FROM users WHERE id = %s",
            (user_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def update_todo_settings(user_id, timezone_name=None, todo_reminders=None, quiet_start=None, quiet_end=None):
    sets, params = [], []
    for column, value in (
        ("timezone", timezone_name),
        ("todo_reminders", todo_reminders),
        ("quiet_start", quiet_start),
        ("quiet_end", quiet_end),
    ):
        if value is not None:
            sets.append(f"{column} = %s")
            params.append(value)
    if not sets:
        return
    with _cursor() as cursor:
        cursor.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = %s", (*params, user_id))


def set_widget_token_hash(user_id, token_hash):
    with _cursor() as cursor:
        cursor.execute("UPDATE users SET widget_token_hash = %s WHERE id = %s", (token_hash, user_id))


def get_user_by_widget_token_hash(token_hash):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute("SELECT id, timezone FROM users WHERE widget_token_hash = %s", (token_hash,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_users_with_open_tasks():
    """Users who have at least one unfinished task (planned today or earlier is decided by the caller)."""
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            '''
            SELECT u.id, u.timezone, u.todo_reminders, u.quiet_start, u.quiet_end, u.last_todo_reminder_at
            FROM users u
            WHERE EXISTS (SELECT 1 FROM tasks t WHERE t.user_id = u.id AND t.done_at IS NULL)
            '''
        )
        return [dict(r) for r in cursor.fetchall()]


def claim_todo_reminder(user_id, now_iso, cutoff_iso):
    """Like claim_alert, but for the 'tasks left' reminder. Returns (claimed, previous)."""
    with _cursor() as cursor:
        cursor.execute("SELECT last_todo_reminder_at FROM users WHERE id = %s FOR UPDATE", (user_id,))
        row = cursor.fetchone()
        if row is None:
            return False, None
        previous = row[0]
        if previous is not None and previous >= cutoff_iso:
            return False, previous
        cursor.execute("UPDATE users SET last_todo_reminder_at = %s WHERE id = %s", (now_iso, user_id))
        return True, previous


def release_todo_reminder(user_id, previous):
    with _cursor() as cursor:
        cursor.execute("UPDATE users SET last_todo_reminder_at = %s WHERE id = %s", (previous, user_id))


# ---- habits (daily streaks) -------------------------------------------------

_HABIT_COLUMNS = "id, name, icon, color, days, created_day, created_at"


def add_habit(user_id, name, icon, color, days, created_day):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            f"INSERT INTO habits (user_id, name, icon, color, days, created_day, created_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {_HABIT_COLUMNS}",
            (user_id, name, icon, color, days, created_day, _now()),
        )
        return dict(cursor.fetchone())


def get_habit(habit_id, user_id):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            f"SELECT {_HABIT_COLUMNS} FROM habits WHERE id = %s AND user_id = %s", (habit_id, user_id)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_habits(user_id):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(f"SELECT {_HABIT_COLUMNS} FROM habits WHERE user_id = %s ORDER BY id", (user_id,))
        return [dict(r) for r in cursor.fetchall()]


def count_habits(user_id):
    with _cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM habits WHERE user_id = %s", (user_id,))
        return cursor.fetchone()[0]


def update_habit(habit_id, user_id, name=None, icon=None, color=None, days=None):
    """Change any of the given fields. Returns the updated habit, or None if it isn't theirs."""
    sets, params = [], []
    for column, value in (("name", name), ("icon", icon), ("color", color), ("days", days)):
        if value is not None:
            sets.append(f"{column} = %s")
            params.append(value)
    if not sets:
        return get_habit(habit_id, user_id)
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            f"UPDATE habits SET {', '.join(sets)} WHERE id = %s AND user_id = %s RETURNING {_HABIT_COLUMNS}",
            (*params, habit_id, user_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def delete_habit(habit_id, user_id):
    """Deletes the habit; its commits go with it (ON DELETE CASCADE)."""
    with _cursor() as cursor:
        cursor.execute("DELETE FROM habits WHERE id = %s AND user_id = %s", (habit_id, user_id))
        return cursor.rowcount > 0


def set_habit_log(habit_id, user_id, day, note=None):
    """Commit a habit for a day. note=None keeps any existing message; a string replaces it."""
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            '''
            INSERT INTO habit_logs (habit_id, user_id, day, note, created_at) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (habit_id, day) DO UPDATE
                SET note = CASE WHEN %s THEN EXCLUDED.note ELSE habit_logs.note END
            RETURNING day, note
            ''',
            (habit_id, user_id, day, note or "", _now(), note is not None),
        )
        return dict(cursor.fetchone())


def delete_habit_log(habit_id, user_id, day):
    with _cursor() as cursor:
        cursor.execute(
            "DELETE FROM habit_logs WHERE habit_id = %s AND user_id = %s AND day = %s",
            (habit_id, user_id, day),
        )
        return cursor.rowcount > 0


def list_habit_logs(user_id):
    """Every commit the user has made (a few small rows per habit per day)."""
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            "SELECT habit_id, day, note, created_at FROM habit_logs WHERE user_id = %s ORDER BY day", (user_id,)
        )
        return [dict(r) for r in cursor.fetchall()]


# ---- calendar events --------------------------------------------------------

_EVENT_COLUMNS = "id, title, notes, color, starts_at, created_at, reminded_at"


def add_event(user_id, title, notes, color, starts_at):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            f"INSERT INTO events (user_id, title, notes, color, starts_at, created_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_EVENT_COLUMNS}",
            (user_id, title, notes, color, starts_at, _now()),
        )
        return dict(cursor.fetchone())


def get_event(event_id, user_id):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events WHERE id = %s AND user_id = %s", (event_id, user_id)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_events(user_id, start_iso, end_iso):
    """Events starting in [start_iso, end_iso). Times are normalised UTC ISO strings."""
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            f"SELECT {_EVENT_COLUMNS} FROM events WHERE user_id = %s AND starts_at >= %s AND starts_at < %s "
            f"ORDER BY starts_at, id",
            (user_id, start_iso, end_iso),
        )
        return [dict(r) for r in cursor.fetchall()]


def count_events(user_id):
    with _cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM events WHERE user_id = %s", (user_id,))
        return cursor.fetchone()[0]


def update_event(event_id, user_id, title=None, notes=None, color=None, starts_at=None):
    """Change any of the given fields. A new start time re-arms the reminder.

    Returns the updated event, or None if it isn't theirs.
    """
    sets, params = [], []
    for column, value in (("title", title), ("notes", notes), ("color", color), ("starts_at", starts_at)):
        if value is not None:
            sets.append(f"{column} = %s")
            params.append(value)
    if starts_at is not None:
        sets.append("reminded_at = NULL")
    if not sets:
        return get_event(event_id, user_id)
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            f"UPDATE events SET {', '.join(sets)} WHERE id = %s AND user_id = %s RETURNING {_EVENT_COLUMNS}",
            (*params, event_id, user_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def delete_event(event_id, user_id):
    with _cursor() as cursor:
        cursor.execute("DELETE FROM events WHERE id = %s AND user_id = %s", (event_id, user_id))
        return cursor.rowcount > 0


def list_due_event_reminders(now_iso, until_iso, limit=50):
    """Upcoming events whose reminder window has opened and that haven't been emailed yet."""
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            '''
            SELECT e.id, e.title, e.notes, e.starts_at, u.email, u.username, u.timezone
            FROM events e JOIN users u ON u.id = e.user_id
            WHERE e.reminded_at IS NULL AND e.starts_at > %s AND e.starts_at <= %s
              AND u.email IS NOT NULL AND u.email <> ''
            ORDER BY e.starts_at
            LIMIT %s
            ''',
            (now_iso, until_iso, limit),
        )
        return [dict(r) for r in cursor.fetchall()]


def claim_event_reminder(event_id, now_iso):
    """Atomically mark an event as emailed. Only one caller can win, so overlapping ticks never double-send."""
    with _cursor() as cursor:
        cursor.execute(
            "UPDATE events SET reminded_at = %s WHERE id = %s AND reminded_at IS NULL", (now_iso, event_id)
        )
        return cursor.rowcount == 1


def release_event_reminder(event_id):
    """Undo claim_event_reminder after a failed send, so the next tick retries."""
    with _cursor() as cursor:
        cursor.execute("UPDATE events SET reminded_at = NULL WHERE id = %s", (event_id,))
