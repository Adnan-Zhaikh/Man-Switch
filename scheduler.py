from database import get_last_checkin
from datetime import datetime

DEADLINE_HOURS = 0.01

def check_deadline():
    last = get_last_checkin()

    if last is None:
        return False

    last_dt = datetime.fromisoformat(last)

    elapsed_hours = (datetime.now() - last_dt).total_seconds() / 3600

    return elapsed_hours > DEADLINE_HOURS
