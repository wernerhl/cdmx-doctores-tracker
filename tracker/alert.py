# Portal ToS may prohibit scraping. Output is for private research only.

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def check_and_send_alerts(
    regular: list,
    changes: dict,
    config: dict,
):
    alert_score = config.get("alert_value_score", 2.0)
    alert_drop_bp = config.get("alert_drop_bp", 500)

    alerts = []

    for sp in regular:
        if sp.composite_score >= alert_score:
            alerts.append(
                f"🏠 High-value listing: {sp.listing.colonia} — "
                f"${sp.listing.price_mxn:,} — Score {sp.composite_score:.2f} — "
                f"Yield {sp.indicators.gross_yield*100:.1f}% — {sp.listing.url}"
            )

    for listing, change_mxn, change_bp in changes.get("price_drop", []):
        if abs(change_bp) >= alert_drop_bp:
            alerts.append(
                f"📉 Price drop: {listing.colonia} — "
                f"${listing.price_mxn:,} ({change_bp:+,} bp) — {listing.url}"
            )

    if not alerts:
        return

    message = "\n\n".join(alerts)
    logger.info(f"Alerts to send: {len(alerts)}")

    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    telegram_chat = os.environ.get("TELEGRAM_CHAT_ID")
    if telegram_token and telegram_chat:
        _send_telegram(telegram_token, telegram_chat, message)
        return

    smtp_host = os.environ.get("SMTP_HOST")
    if smtp_host:
        _send_email(message)
        return

    logger.info(f"No alert channel configured. Alerts:\n{message}")


def _send_telegram(token: str, chat_id: str, message: str):
    try:
        import urllib.request
        import urllib.parse

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }).encode()

        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Telegram alert sent")
            else:
                logger.warning(f"Telegram response: {resp.status}")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


def _send_email(message: str):
    try:
        import smtplib
        from email.mime.text import MIMEText

        smtp_host = os.environ["SMTP_HOST"]
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")
        to_addr = os.environ.get("ALERT_EMAIL", smtp_user)
        from_addr = os.environ.get("SMTP_FROM", smtp_user)

        msg = MIMEText(message)
        msg["Subject"] = "CDMX Listing Alert"
        msg["From"] = from_addr
        msg["To"] = to_addr

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        logger.info("Email alert sent")
    except Exception as e:
        logger.error(f"Email send failed: {e}")
