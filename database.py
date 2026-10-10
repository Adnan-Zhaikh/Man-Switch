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

def get_checkin_by_id(checkin_id):
    """Fetch a single checkin by ID."""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM checkins WHERE id = %s", (checkin_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def update_checkin(checkin_id, note, category, image_url=None):
    """Update an existing checkin. If image_url is None, keep the old one."""
    conn = get_conn()
    cursor = conn.cursor()
    
    # Fetch the current entry to preserve the image if not replaced
    cursor.execute("SELECT image_url FROM checkins WHERE id = %s", (checkin_id,))
    result = cursor.fetchone()
    
    if not result:
        conn.close()
        return False
    
    # Use new image_url if provided, otherwise keep the old one
    final_image_url = image_url if image_url is not None else result[0]
    
    cursor.execute(
        "UPDATE checkins SET progress_note = %s, category = %s, image_url = %s WHERE id = %s",
        (note, category, final_image_url, checkin_id)
    )
    conn.commit()
    conn.close()
    return True

def delete_checkin(checkin_id):
    """Delete a checkin by ID. Returns the image_url if one exists."""
    conn = get_conn()
    cursor = conn.cursor()
    
    # Fetch the image_url before deleting
    cursor.execute("SELECT image_url FROM checkins WHERE id = %s", (checkin_id,))
    result = cursor.fetchone()
    image_url = result[0] if result else None
    
    cursor.execute("DELETE FROM checkins WHERE id = %s", (checkin_id,))
    conn.commit()
    conn.close()
    
    return image_url
