import os
import psycopg2
from datetime import datetime
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
            progress_note TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def add_checkin(note):
    conn = get_conn()
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    cursor.execute(
        "INSERT INTO checkins (timestamp, progress_note) VALUES (%s, %s)",
        (timestamp, note)
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

def get_all_checkins():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM checkins ORDER BY timestamp DESC")
    result = cursor.fetchall()
    conn.close()
    return result   