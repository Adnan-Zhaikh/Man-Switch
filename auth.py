import hashlib
import hmac
import os
import secrets

from fastapi import HTTPException, Request, status

SESSION_USER_ID_KEY = "user_id"
SESSION_ID_KEY = "session_id"

PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 600000
SALT_BYTES = 16

USERNAME_MIN_LEN = 3
USERNAME_MAX_LEN = 32
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 128


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return f"{PASSWORD_HASH_ALGORITHM}${PASSWORD_HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected_hex = password_hash.split("$", 3)
    except ValueError:
        return False

    if algorithm != PASSWORD_HASH_ALGORITHM:
        return False

    try:
        iterations_value = int(iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
    except (ValueError, TypeError):
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations_value,
    )
    return hmac.compare_digest(candidate, expected)


# Verified against when a login names an unknown user, so that response time
# doesn't reveal which usernames exist.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


def validate_username(username: str) -> str:
    if username is None:
        raise HTTPException(status_code=422, detail="Username is required")

    normalized = username.strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="Username is required")
    if len(normalized) < USERNAME_MIN_LEN or len(normalized) > USERNAME_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Username must be between {USERNAME_MIN_LEN} and {USERNAME_MAX_LEN} characters",
        )
    if not all(ch.isalnum() or ch in {"_", "-", "."} for ch in normalized):
        raise HTTPException(
            status_code=422,
            detail="Username can contain letters, numbers, '.', '_' and '-' only",
        )
    return normalized


def validate_password(password: str) -> str:
    if password is None:
        raise HTTPException(status_code=422, detail="Password is required")

    if len(password) < PASSWORD_MIN_LEN or len(password) > PASSWORD_MAX_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Password must be between {PASSWORD_MIN_LEN} and {PASSWORD_MAX_LEN} characters",
        )
    return password


def get_required_session_secret() -> str:
    secret = os.getenv("SESSION_SECRET")
    if not secret:
        raise RuntimeError("SESSION_SECRET must be set")
    return secret


def start_user_session(request: Request, user_id: int) -> None:
    request.session.clear()
    request.session[SESSION_USER_ID_KEY] = user_id
    request.session[SESSION_ID_KEY] = secrets.token_urlsafe(24)


def clear_user_session(request: Request) -> None:
    request.session.clear()


def get_session_user_id(request: Request):
    user_id = request.session.get(SESSION_USER_ID_KEY)
    if not isinstance(user_id, int):
        return None
    return user_id


def require_authenticated_user_id(request: Request) -> int:
    user_id = get_session_user_id(request)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user_id
