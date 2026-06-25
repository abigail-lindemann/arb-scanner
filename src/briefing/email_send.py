"""Send HTML email via Gmail SMTP (STARTTLS). §11.

Credentials come from GitHub Actions Secrets: GMAIL_ADDRESS,
GMAIL_APP_PASSWORD (a Google App Password, not the account password), and
BRIEFING_TO. Reused by the scanner's drastic-move alerts (§8) and the daily
briefing (§11).
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_email(subject: str, html_body: str, to: str | None = None) -> None:
    """Send one HTML email. Raises on failure so callers can fail-soft."""
    sender = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = to or os.environ["BRIEFING_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())
    log.info("sent email %r to %s", subject, recipient)
