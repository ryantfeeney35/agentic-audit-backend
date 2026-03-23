# backend/utils/email_utils.py
"""Email notification utilities for homeowner signup (via Resend)."""
import os
import logging
from datetime import datetime, timezone

import resend


def send_signup_notification(street: str, city: str, state: str, zip_code: str, phone: str):
    """
    Send a notification email when a new homeowner signs up via self-service.

    Reads RESEND_API_KEY, SIGNUP_NOTIFICATION_EMAIL, and optionally FROM_EMAIL
    from environment. Skips silently if SIGNUP_NOTIFICATION_EMAIL or
    RESEND_API_KEY is not set.

    Raises on send failure so the caller can log and continue.
    """
    recipient = os.environ.get('SIGNUP_NOTIFICATION_EMAIL', '').strip()
    if not recipient:
        logging.warning("SIGNUP_NOTIFICATION_EMAIL not set — skipping signup notification")
        return

    api_key = os.environ.get('RESEND_API_KEY', '').strip()
    if not api_key:
        logging.warning("RESEND_API_KEY not set — skipping signup notification")
        return

    resend.api_key = api_key

    from_email = os.environ.get('FROM_EMAIL', 'Sustainrgy <notifications@sustainrgy.com>')
    address = f"{street}, {city}, {state} {zip_code}"
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    body = (
        f"A new homeowner signed up for a Free Bill Analysis.\n\n"
        f"Address: {address}\n"
        f"Phone:   {phone}\n"
        f"Time:    {timestamp}\n"
    )

    resend.Emails.send({
        "from": from_email,
        "to": [recipient],
        "subject": f"New Homeowner Signup — {address}",
        "text": body,
    })

    logging.info(f"Signup notification sent to {recipient} for {address}")
