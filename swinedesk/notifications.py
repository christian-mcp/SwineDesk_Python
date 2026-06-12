"""Internal SMS and email notification helpers."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from twilio.base.exceptions import TwilioException
from twilio.rest import Client as TwilioClient

from swinedesk.settings import settings

logger = logging.getLogger(__name__)

_twilio_client: TwilioClient | None = None


def _get_twilio_client() -> TwilioClient | None:
    global _twilio_client
    if _twilio_client is not None:
        return _twilio_client
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        return None
    _twilio_client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
    return _twilio_client


def _send_email_sync(to: str, subject: str, body: str) -> None:
    """Blocking SMTP send — run via asyncio.to_thread."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = to
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_pass)
        smtp.sendmail(settings.smtp_user, [to], msg.as_string())


async def send_email(to: str, subject: str, body: str) -> dict[str, str | bool]:
    """Send an email if SMTP is configured. Failures are logged and swallowed."""
    if not all([settings.smtp_host, settings.smtp_user, settings.smtp_pass, to]):
        return {"success": False, "error": "SMTP not configured."}
    try:
        await asyncio.to_thread(_send_email_sync, to, subject, body)
        return {"success": True, "to": to}
    except Exception as exc:
        logger.exception("Failed to send email to %s", to)
        return {"success": False, "error": str(exc), "to": to}


def _send_email_with_pdf_sync(to: str, subject: str, body: str,
                              pdf_bytes: bytes, filename: str) -> None:
    """Blocking SMTP send with a PDF attachment — run via asyncio.to_thread."""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = to
    msg.attach(MIMEText(body))
    part = MIMEApplication(pdf_bytes, _subtype="pdf")
    part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(part)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_pass)
        smtp.sendmail(settings.smtp_user, [to], msg.as_string())


async def send_email_with_pdf(to: str, subject: str, body: str,
                              pdf_bytes: bytes, filename: str) -> dict[str, str | bool]:
    """Email a PDF attachment if SMTP is configured. Failures are logged and swallowed."""
    if not all([settings.smtp_host, settings.smtp_user, settings.smtp_pass, to]):
        return {"success": False, "error": "SMTP not configured.", "to": to}
    try:
        await asyncio.to_thread(_send_email_with_pdf_sync, to, subject, body, pdf_bytes, filename)
        return {"success": True, "to": to}
    except Exception as exc:
        logger.exception("Failed to send PDF email to %s", to)
        return {"success": False, "error": str(exc), "to": to}


async def send_sms_notification(to_phone: str, message: str) -> dict[str, str | bool]:
    """Send an SMS if Twilio is configured."""
    client = _get_twilio_client()
    from_phone = settings.twilio_phone_number
    if client is None or not from_phone or not to_phone:
        return {"success": False, "error": "Twilio is not configured."}

    try:
        client.messages.create(body=message, from_=from_phone, to=to_phone)
        return {"success": True, "to_phone": to_phone}
    except TwilioException as exc:
        logger.exception("Failed to send SMS notification to %s", to_phone)
        return {"success": False, "error": str(exc), "to_phone": to_phone}
