import os
import secrets

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

load_dotenv()

security = HTTPBasic()

# Load users from .env
USERS = {}

i = 1
while True:
    username = os.getenv(f"BASIC_AUTH_USER_{i}")
    password = os.getenv(f"BASIC_AUTH_PASS_{i}")

    if username is None and password is None:
        break

    if not username or not password:
        raise RuntimeError(f"Missing username or password for user {i}")

    if username in USERS:
        raise RuntimeError(f"Duplicate username: {username}")

    USERS[username] = password
    i += 1

if not USERS:
    raise RuntimeError("No users configured in .env")


def verify_credentials(
    credentials: HTTPBasicCredentials = Depends(security),
):
    authenticated = False

    for username, password in USERS.items():
        is_correct_username = secrets.compare_digest(
            credentials.username.encode("utf-8"),
            username.encode("utf-8"),
        )

        is_correct_password = secrets.compare_digest(
            credentials.password.encode("utf-8"),
            password.encode("utf-8"),
        )

        if is_correct_username and is_correct_password:
            authenticated = True

    if not authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username