"""Web Push (the browser's own notification system, e.g. Chrome via FCM).

Needs three env vars, generated once with `python generate_vapid_keys.py`:
VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY and VAPID_SUBJECT (a mailto: or https: URL).
"""
import base64
import json
import logging
import os
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from database import delete_push_subscription, get_push_subscriptions

logger = logging.getLogger(__name__)

# The server POSTs to whatever endpoint a client registers, so only genuine push
# services are accepted (otherwise this would be a server-side request forgery hole).
_PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",         # Chrome, Edge, Brave, Opera, Samsung Internet
    "push.services.mozilla.com",  # Firefox
    "notify.windows.com",         # legacy Edge / Windows
    "push.apple.com",             # Safari / installed iOS web apps
)
MAX_FIELD_LEN = 600


def _allowed_hosts():
    extra = tuple(h.strip().lower() for h in os.getenv("PUSH_EXTRA_HOSTS", "").split(",") if h.strip())
    return _PUSH_HOST_SUFFIXES + extra


def _vapid():
    private = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    if not private:
        return None
    return Vapid.from_string(private)


def is_configured():
    return bool(os.getenv("VAPID_PRIVATE_KEY", "").strip() and os.getenv("VAPID_SUBJECT", "").strip())


def public_key():
    """The VAPID public key the browser needs, derived from the private key."""
    vapid = _vapid()
    if vapid is None:
        return None
    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64_len(value):
    try:
        return len(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    except Exception:
        return -1


def validate_subscription(endpoint, p256dh, auth):
    """Return a clean (endpoint, p256dh, auth) tuple or raise ValueError."""
    endpoint = (endpoint or "").strip()
    if len(endpoint) > MAX_FIELD_LEN or len(p256dh or "") > MAX_FIELD_LEN or len(auth or "") > MAX_FIELD_LEN:
        raise ValueError("Subscription fields are too long")
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Push endpoint must be a plain https URL")
    if not any(host == s or host.endswith("." + s) for s in _allowed_hosts()):
        raise ValueError("Push endpoint is not a recognised browser push service")
    if _b64_len(p256dh) != 65 or _b64_len(auth) != 16:
        raise ValueError("Invalid push keys")
    return endpoint, p256dh, auth


def _send_one(sub, payload, ttl):
    """Returns 'ok', 'gone' (subscription is dead) or 'error'."""
    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
            data=json.dumps(payload),
            vapid_private_key=_vapid(),
            vapid_claims={"sub": os.getenv("VAPID_SUBJECT", "")},  # fresh dict: webpush mutates it
            ttl=ttl,
            timeout=10,
            headers={"Urgency": "high", "Topic": "man-switch"},
        )
        return "ok"
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            return "gone"
        logger.warning("Push failed (HTTP %s): %s", status, exc)
        return "error"
    except Exception:
        logger.exception("Push failed unexpectedly")
        return "error"


def send_to_user(user_id, payload, ttl=24 * 3600):
    """Send a notification to every device of a user. Returns how many were delivered.

    Subscriptions the push service reports as gone are deleted.
    """
    if not is_configured():
        logger.warning("Web Push is not configured (VAPID_* env vars missing)")
        return 0
    delivered = 0
    for sub in get_push_subscriptions(user_id):
        result = _send_one(sub, payload, ttl)
        if result == "ok":
            delivered += 1
        elif result == "gone":
            delete_push_subscription(sub["endpoint"])
    return delivered
