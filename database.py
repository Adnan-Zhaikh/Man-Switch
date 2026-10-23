import os
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv()


def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def init_db():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            ntfy_topic TEXT,
            created_at TEXT NOT NULL
        )
        '''
    )

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

    cursor.execute(
        "ALTER TABLE checkins ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_checkins_user_timestamp ON checkins (user_id, timestamp DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_username ON users (username)"
    )

    conn.commit()
    conn.close()


def create_user(username, password_hash, ntfy_topic=None):
    conn = get_conn()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    created_at = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO users (username, password_hash, ntfy_topic, created_at) VALUES (%s, %s, %s, %s) RETURNING id, username, ntfy_topic, created_at",
        (username, password_hash, ntfy_topic, created_at),
    )
    user = cursor.fetchone()
    conn.commit()
    conn.close()
    return user


def get_user_by_username(username):
    conn = get_conn()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        "SELECT id, username, password_hash, ntfy_topic, created_at FROM users WHERE username = %s",
        (username,),
    )
    user = cursor.fetchone()
    conn.close()
    return user


def get_user_by_id(user_id):
    conn = get_conn()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        "SELECT id, username, ntfy_topic, created_at FROM users WHERE id = %s",
        (user_id,),
    )
    user = cursor.fetchone()
    conn.close()
    return user


def get_user_for_deadline(user_id):
    conn = get_conn()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        "SELECT id, username, ntfy_topic FROM users WHERE id = %s",
        (user_id,),
    )
    user = cursor.fetchone()
    conn.close()
    return user


def update_user_ntfy_topic(user_id, ntfy_topic):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET ntfy_topic = %s WHERE id = %s",
        (ntfy_topic, user_id),
    )
    conn.commit()
    conn.close()


def has_users():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM users LIMIT 1")
    result = cursor.fetchone()
    conn.close()
    return result is not None


def get_unowned_checkin_count():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM checkins WHERE user_id IS NULL")
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0


def assign_unowned_checkins(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE checkins SET user_id = %s WHERE user_id IS NULL", (user_id,))
    conn.commit()
    conn.close()


def add_checkin(user_id, note, category="journal", image_url=None):
    conn = get_conn()
    cursor = conn.cursor()
    timestamp = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO checkins (user_id, timestamp, progress_note, category, image_url) VALUES (%s, %s, %s, %s, %s)",
        (user_id, timestamp, note, category, image_url),
    )
    conn.commit()
    conn.close()


def get_last_checkin(user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT timestamp FROM checkins WHERE user_id = %s ORDER BY timestamp DESC LIMIT 1",
        (user_id,),
    )
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None


def get_all_checkins(user_id, category=None):
    conn = get_conn()
    cursor = conn.cursor()
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
    result = cursor.fetchall()
    conn.close()
    return result


def get_checkin_by_id(checkin_id, user_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, timestamp, progress_note, image_url, category FROM checkins WHERE id = %s AND user_id = %s",
        (checkin_id, user_id),
    )
    result = cursor.fetchone()
    conn.close()
    return result


def update_checkin(checkin_id, user_id, note, category, image_url=None):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT image_url FROM checkins WHERE id = %s AND user_id = %s",
        (checkin_id, user_id),
    )
    result = cursor.fetchone()

    if not result:
        conn.close()
        return False

    final_image_url = image_url if image_url is not None else result[0]

    cursor.execute(
        "UPDATE checkins SET progress_note = %s, category = %s, image_url = %s WHERE id = %s AND user_id = %s",
        (note, category, final_image_url, checkin_id, user_id),
    )
    conn.commit()
    conn.close()
    return True


def delete_checkin(checkin_id, user_id):
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT image_url FROM checkins WHERE id = %s AND user_id = %s",
        (checkin_id, user_id),
    )
    result = cursor.fetchone()
    image_url = result[0] if result else None

    if not result:
        conn.close()
        return None, False

    cursor.execute("DELETE FROM checkins WHERE id = %s AND user_id = %s", (checkin_id, user_id))
    conn.commit()
    conn.close()

    return image_url, True


def get_users_for_deadline_check():
    conn = get_conn()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        '''
        SELECT u.id, u.username, u.ntfy_topic, MAX(c.timestamp) AS last_checkin
        FROM users u
        LEFT JOIN checkins c ON c.user_id = u.id
        GROUP BY u.id, u.username, u.ntfy_topic
        '''
    )
    users = cursor.fetchall()
    conn.close()
    return users
