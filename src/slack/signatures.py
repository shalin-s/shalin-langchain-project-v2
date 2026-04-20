"""Slack request signature verification.

Slack signs every HTTP webhook request with HMAC-SHA256 over the body and
timestamp, using the app's signing secret. This module verifies that signature.
Only relevant for HTTP Events API mode; Socket Mode authenticates the
WebSocket connection itself with the App-Level Token instead.

Spec: https://api.slack.com/authentication/verifying-requests-from-slack
"""
import hashlib
import hmac
import time

SIGNATURE_VERSION = "v0"
MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5  # Slack's recommended window: 5 minutes


def verify_slack_signature(
    signing_secret: str,
    body: bytes,
    timestamp: str,
    signature: str,
    *,
    now: float | None = None,
) -> bool:
    """Return True if the request signature is valid and recent.

    Args:
        signing_secret: The app's signing secret (from app config).
        body: The raw request body bytes (must be exactly what Slack sent;
            do not re-serialize JSON).
        timestamp: Value of the X-Slack-Request-Timestamp header.
        signature: Value of the X-Slack-Signature header (e.g., 'v0=abc...').
        now: Optional override for the current unix time, for testing.
    """
    if not signing_secret or not timestamp or not signature:
        return False

    try:
        ts_int = int(timestamp)
    except ValueError:
        return False

    current = now if now is not None else time.time()
    if abs(current - ts_int) > MAX_TIMESTAMP_SKEW_SECONDS:
        return False

    basestring = f"{SIGNATURE_VERSION}:{timestamp}:".encode() + body
    expected = (
        SIGNATURE_VERSION
        + "="
        + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature)
