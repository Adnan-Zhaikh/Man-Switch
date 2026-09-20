import os
import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# Load .env into os.environ (call once at the top of main.py)
from dotenv import load_dotenv
load_dotenv()

security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    current_username_bytes = credentials.username.encode("utf-8")
    correct_username_bytes = os.getenv("BASIC_AUTH_USER").encode("utf-8")

    current_password_bytes = credentials.password.encode("utf-8")
    correct_password_bytes = os.getenv("BASIC_AUTH_PASS").encode("utf-8")

    is_correct_username = secrets.compare_digest(current_username_bytes, correct_username_bytes)
    is_correct_password = secrets.compare_digest(current_password_bytes, correct_password_bytes)

    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username   