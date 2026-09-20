import sqlite3
from datetime import datetime

def init_db():
    conn = sqlite3.connect('deadman.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            progress_note TEXT NOT NULL
            )
            ''')
    conn.commit()
    conn.close()

def add_checkin(note):
    conn = sqlite3.connect('deadman.db')
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    cursor.execute(
        "INSERT INTO checkins (timestamp, progress_note) VALUES (?, ?)",
        (timestamp, note)
    )
    conn.commit()
    conn.close()

def get_last_checkin():
    conn = sqlite3.connect('deadman.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT timestamp FROM checkins ORDER BY timestamp DESC LIMIT 1")
    result = cursor.fetchone()
    
    conn.close()
    return result[0] if result else None