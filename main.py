from fastapi import FastAPI
from database import init_db, add_checkin

app = FastAPI()


@app.post("/checkin")
def checkin(note: str):
    add_checkin(note)
    return {"status": "checked in"}