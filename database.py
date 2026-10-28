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


def create_user(username, password_hash, is_admin=False):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (%s, %s, %s, %s) RETURNING id, username, is_admin, created_at",
            (username, password_hash, is_admin, _now()),
        )
        return cursor.fetchone()


def get_user_by_username(username):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            "SELECT id, username, password_hash, is_admin, created_at FROM users WHERE username = %s",
            (username,),
        )
        return cursor.fetchone()


def get_user_by_id(user_id):
    with _cursor(dict_rows=True) as cursor:
        cursor.execute(
            "SELECT id, username, is_admin, created_at FROM users WHERE id = %s",
            (user_id,),
        )
        return cursor.fetchone()


def set_user_admin(user_id, is_admin):
    with _cursor() as cursor:
        cursor.execute("UPDATE users SET is_admin = %s WHERE id = %s", (is_admin, user_id))


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
