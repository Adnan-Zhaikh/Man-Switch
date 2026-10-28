"""Generate the VAPID key pair used to send browser push notifications.

Run once on your own computer:   python generate_vapid_keys.py
Then add the printed values to your environment (Render > Environment, and .env locally).
Keep VAPID_PRIVATE_KEY secret. If you ever change it, every device must tap "Enable alerts" again.
"""
import base64

from cryptography.hazmat.primitives.asymmetric import ec


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


key = ec.generate_private_key(ec.SECP256R1())
private_raw = key.private_numbers().private_value.to_bytes(32, "big")

print("VAPID_PRIVATE_KEY=" + b64url(private_raw))
print("VAPID_SUBJECT=mailto:you@example.com")
print("(replace you@example.com with your own email address)")
print()
print("(The matching public key is derived automatically by the app.)")
