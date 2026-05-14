"""Internal SMS notification helpers."""

from __future__ import annotations

import logging

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
