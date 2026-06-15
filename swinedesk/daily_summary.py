"""Scheduled daily broker summary, delivered by SMS.

Runs an in-process asyncio loop (same pattern as the session cleanup task) that
wakes once per day at the configured local time, fetches today's desk recap from
the backend, and texts it to the broker.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from swinedesk.backend_client import get_backend_client
from swinedesk.notifications import send_sms_notification
from swinedesk.settings import settings

logger = logging.getLogger(__name__)

_summary_task: asyncio.Task[None] | None = None


def build_recap_message(response: dict[str, Any]) -> str:
    """Format a daily recap payload into SMS-friendly plain text.

    Shared by the on-demand ``get_daily_recap`` tool and the scheduled send so
    both render identically.
    """
    lines = [
        f"Recap for {response.get('date', 'today')}:",
        f"  New listings: {response.get('new_listings', 0)} ({response.get('head_listed', 0)} head)",
        f"  New requests: {response.get('new_requests', 0)} ({response.get('head_requested', 0)} head)",
        f"  Pending tasks: {response.get('pending_tasks', 0)}",
    ]
    return "\n".join(lines)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.daily_summary_timezone)
    except Exception:
        logger.warning(
            "Invalid DAILY_SUMMARY_TIMEZONE=%r; falling back to UTC",
            settings.daily_summary_timezone,
        )
        return ZoneInfo("UTC")


def _seconds_until_next_run(now: datetime) -> float:
    """Seconds from ``now`` until the next configured send time (today or tomorrow)."""
    target = now.replace(
        hour=settings.daily_summary_hour,
        minute=settings.daily_summary_minute,
        second=0,
        microsecond=0,
    )
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def send_daily_summary_once() -> dict[str, Any]:
    """Fetch the recap and text it to the broker. Returns the send result."""
    to_phone = settings.daily_summary_recipient
    if not to_phone:
        logger.warning("Daily summary skipped: no recipient phone configured.")
        return {"success": False, "error": "No recipient phone configured."}

    backend = get_backend_client()
    recap = await backend.get_daily_recap()
    message = build_recap_message(recap)
    result = await send_sms_notification(to_phone, message)
    if result.get("success"):
        logger.info("Daily broker summary sent to %s", to_phone)
    else:
        logger.error("Daily broker summary failed: %s", result.get("error"))
    return result


async def _summary_loop() -> None:
    tz = _tz()
    while True:
        delay = _seconds_until_next_run(datetime.now(tz))
        logger.info(
            "Next daily broker summary in %.0f min (%02d:%02d %s)",
            delay / 60,
            settings.daily_summary_hour,
            settings.daily_summary_minute,
            settings.daily_summary_timezone,
        )
        await asyncio.sleep(delay)
        try:
            await send_daily_summary_once()
        except Exception:
            logger.exception("Daily broker summary send raised an exception")


def start_daily_summary_task() -> None:
    """Start the daily summary background task once, if enabled."""
    global _summary_task
    if not settings.daily_summary_enabled:
        logger.info("Daily broker summary disabled (DAILY_SUMMARY_ENABLED=false).")
        return
    if _summary_task is None or _summary_task.done():
        _summary_task = asyncio.create_task(_summary_loop())
