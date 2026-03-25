# backend/utils/email_utils.py
"""Email notification utilities for homeowner signup (via SMTP)."""
import os
import logging
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone


def send_signup_notification(street: str, city: str, state: str, zip_code: str, phone: str):
    """
    Send a notification email when a new homeowner signs up via self-service.

    Reads SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SIGNUP_NOTIFICATION_EMAIL,
    and optionally FROM_EMAIL from environment.
    Skips silently if required vars are not set.

    Raises on send failure so the caller can log and continue.
    """
    recipient = os.environ.get('SIGNUP_NOTIFICATION_EMAIL', '').strip()
    if not recipient:
        logging.warning("SIGNUP_NOTIFICATION_EMAIL not set — skipping signup notification")
        return

    smtp_host = os.environ.get('SMTP_HOST', '').strip()
    smtp_user = os.environ.get('SMTP_USER', '').strip()
    smtp_password = os.environ.get('SMTP_PASSWORD', '').strip()
    if not smtp_host or not smtp_user or not smtp_password:
        logging.warning("SMTP credentials not fully set — skipping signup notification")
        return

    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    from_email = os.environ.get('FROM_EMAIL', smtp_user)

    address = f"{street}, {city}, {state} {zip_code}"
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    body = (
        f"A new homeowner signed up for a Free Bill Analysis.\n\n"
        f"Address: {address}\n"
        f"Phone:   {phone}\n"
        f"Time:    {timestamp}\n"
    )

    msg = MIMEText(body)
    msg['Subject'] = f"New Homeowner Signup — {address}"
    msg['From'] = from_email
    msg['To'] = recipient

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)

    logging.info(f"Signup notification sent to {recipient} for {address}")
