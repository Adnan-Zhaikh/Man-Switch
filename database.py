import os
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def init_db():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS checkins (
            id SERIAL PRIMARY KEY,
            timestamp TEXT NOT NULL,
            progress_note TEXT NOT NULL,
            image_url TEXT,
            category TEXT NOT NULL DEFAULT 'journal'
        )
    ''')
    conn.commit()
    conn.close()

def add_checkin(note, category="journal", image_url=None):
    conn = get_conn()
    cursor = conn.cursor()
    timestamp = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO checkins (timestamp, progress_note, category, image_url) VALUES (%s, %s, %s, %s)",
        (timestamp, note, category, image_url)
    )
    conn.commit()
    conn.close()

def get_last_checkin():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT timestamp FROM checkins ORDER BY timestamp DESC LIMIT 1")
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_all_checkins(category=None):
    conn = get_conn()
    cursor = conn.cursor()
    if category:
        cursor.execute(
            "SELECT * FROM checkins WHERE category = %s ORDER BY timestamp DESC",
            (category,)
        )
    else:
        cursor.execute("SELECT * FROM checkins ORDER BY timestamp DESC")
    result = cursor.fetchall()
    conn.close()
    return result  