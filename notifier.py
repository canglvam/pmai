"""
Notification module — send analysis results to your phone/email
Supports: Telegram Bot (recommended), Email
"""

import logging
import os
import smtplib
from email.mime.text import MIMEText
import requests

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")


def notify(message: str, urgent: bool = False) -> None:
    """Send notification. Telegram if configured, else log only."""
    logger.info(f"\n{'='*60}\n{message}\n{'='*60}")

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        _send_telegram(message, urgent)
    elif EMAIL_FROM and EMAIL_TO:
        _send_email("PMAI Notification", message)
    else:
        logger.warning("No notification channel configured (Telegram/Email). Results in log only.")


def _send_telegram(message: str, urgent: bool = False) -> None:
    """Send Telegram message"""
    prefix = "[!!!] " if urgent else "[==] "
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": prefix + message,
                "parse_mode": "HTML",
            },
            timeout=10
        )
        if resp.ok:
            logger.info("Telegram notification sent")
        else:
            logger.error(f"Telegram send failed: {resp.text}")
    except Exception as e:
        logger.error(f"Telegram notification error: {e}")


def _send_email(subject: str, body: str) -> None:
    """Send email notification (Gmail)"""
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
        logger.info("Email notification sent")
    except Exception as e:
        logger.error(f"Email send failed: {e}")
