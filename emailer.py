"""Plain-text email for calendar reminders.

Render's free tier blocks outbound SMTP (ports 25, 465, 587), so the primary options
talk to an email provider over HTTPS. Pick one by setting its key:

  BREVO_API_KEY    Brevo (free, 300 emails/day, sends from one verified address, no domain needed)
  RESEND_API_KEY   Resend (needs a verified domain to email anyone but yourself)
  SMTP_HOST        plain SMTP, for local use or a paid host (SMTP_PORT, SMTP_USER, SMTP_PASSWORD)

All of them also need EMAIL_FROM (an address the provider has verified). SMTP falls back
to SMTP_USER when EMAIL_FROM is empty. EMAIL_FROM_NAME is the display name.
"""
import json
import logging
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10
USER_AGENT = "man-switch/1.0"
BREVO_URL = "https://api.brevo.com/v3/smtp/email"
RESEND_URL = "https://api.resend.com/emails"


class EmailError(Exception):
    """Raised when a message could not be handed to the provider."""


def _env(name):
    return os.getenv(name, "").strip()


def _sender():
    return _env("EMAIL_FROM") or _env("SMTP_USER")


def provider():
    """Name of the configured provider, or None."""
    if not _sender():
        return None
    if _env("BREVO_API_KEY"):
        return "brevo"
    if _env("RESEND_API_KEY"):
        return "resend"
    if _env("SMTP_HOST"):
        return "smtp"
    return None


def is_configured():
    return provider() is not None


def one_line(text, limit=150):
    """Header-safe single line: no CR/LF, so a title can never inject extra headers."""
    return " ".join(str(text or "").split())[:limit]


def _post_json(url, headers, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT, **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        # Only the status code is kept: the body can echo the recipient's address.
        raise EmailError(f"Email provider rejected the message (HTTP {exc.code})") from None
    except (urllib.error.URLError, OSError):
        raise EmailError("Could not reach the email provider") from None


def _send_brevo(to, subject, text):
    _post_json(
        BREVO_URL,
        {"api-key": _env("BREVO_API_KEY")},
        {
            "sender": {"name": _env("EMAIL_FROM_NAME") or "Man Switch", "email": _sender()},
            "to": [{"email": to}],
            "subject": subject,
            "textContent": text,
        },
    )


def _send_resend(to, subject, text):
    name = _env("EMAIL_FROM_NAME") or "Man Switch"
    _post_json(
        RESEND_URL,
        {"Authorization": f"Bearer {_env('RESEND_API_KEY')}"},
        {"from": f"{name} <{_sender()}>", "to": [to], "subject": subject, "text": text},
    )


def _send_smtp(to, subject, text):
    message = EmailMessage()
    message["From"] = f"{one_line(_env('EMAIL_FROM_NAME') or 'Man Switch', 60)} <{_sender()}>"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT") or "587")
    user, password = _env("SMTP_USER"), os.getenv("SMTP_PASSWORD", "")
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS)
        with server:
            if port != 465:
                server.starttls(context=ssl.create_default_context())
            if user:
                server.login(user, password)
            server.send_message(message)
    except (smtplib.SMTPException, OSError):
        raise EmailError("Could not send through the SMTP server") from None


def send_email(to, subject, text):
    """Send one plain-text email. Raises EmailError if it can't be sent."""
    chosen = provider()
    if chosen is None:
        raise EmailError("Email is not configured on the server")
    subject = one_line(subject)
    if not to or "\n" in to or "\r" in to:
        raise EmailError("Invalid recipient")
    {"brevo": _send_brevo, "resend": _send_resend, "smtp": _send_smtp}[chosen](to, subject, text)
