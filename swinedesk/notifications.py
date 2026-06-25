"""Internal SMS and email notification helpers."""

from __future__ import annotations

import asyncio
import logging
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from twilio.base.exceptions import TwilioException
from twilio.rest import Client as TwilioClient

from swinedesk.settings import settings

logger = logging.getLogger(__name__)

_twilio_client: TwilioClient | None = None


def _to_e164(phone: str | None) -> str | None:
    """Normalize a phone number to E.164 (leading +). Twilio rejects bare numbers.

    Prepends a '+' when the value is otherwise all digits (e.g. a stored number
    like '525519535147' that lost its '+'). Numbers already starting with '+'
    and anything we can't confidently normalize are returned unchanged.
    """
    if not phone:
        return phone
    trimmed = phone.strip()
    if trimmed.startswith("+"):
        return trimmed
    digits = re.sub(r"[\s\-().]", "", trimmed)
    if digits.isdigit():
        return "+" + digits
    return trimmed


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


async def send_sms_raw(to_phone: str, message: str) -> dict[str, str | bool]:
    """Send an SMS if Twilio is configured, with NO Beta Test Mode gating.

    Use this for sends that must never be held for broker approval: the message
    the broker already dictated/confirmed (blast, direct message, price offers),
    a text back to the same caller, and broker-facing alerts. Autonomous bot
    notifications to other users should go through send_sms_notification instead.
    """
    client = _get_twilio_client()
    from_phone = settings.twilio_phone_number
    if client is None or not from_phone or not to_phone:
        return {"success": False, "error": "Twilio is not configured."}

    to_phone = _to_e164(to_phone)
    try:
        client.messages.create(body=message, from_=from_phone, to=to_phone)
        return {"success": True, "to_phone": to_phone}
    except TwilioException as exc:
        logger.exception("Failed to send SMS notification to %s", to_phone)
        return {"success": False, "error": str(exc), "to_phone": to_phone}


async def send_sms_notification(to_phone: str, message: str) -> dict[str, str | bool]:
    """Send an SMS to another user, gated by Beta Test Mode.

    When Beta Test Mode is on and the recipient is not the broker, the message is
    held in the approval queue and the broker is asked to confirm it instead of
    sending right away. Otherwise it sends immediately via send_sms_raw.
    """
    # Lazy import breaks the notifications <-> beta_approvals import cycle.
    from swinedesk.beta_approvals import maybe_queue_outbound

    queued = await maybe_queue_outbound(to_phone, message)
    if queued is not None:
        return queued
    return await send_sms_raw(to_phone, message)
