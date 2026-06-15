"""Durable reminder store + in-process firing scheduler.

The Java backend has no reminder support, and SMS delivery (Twilio) lives in this
service, so reminders are owned here. Reminders are persisted to a JSON file (same
durable pattern as the session store) and fired by a background poll loop that
texts the recipient at the due time — mirroring the session-cleanup and
daily-summary background tasks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from swinedesk.notifications import send_sms_notification
from swinedesk.settings import settings

logger = logging.getLogger(__name__)

_reminders: dict[str, dict[str, Any]] = {}
_loaded = False
_lock = asyncio.Lock()
_scheduler_task: asyncio.Task[None] | None = None

# "in 2 minutes", "in 3 hrs", "in 1 day", "in 2 weeks"
_RELATIVE_RE = re.compile(
    r"\bin\s+(\d+)\s*(min|minute|minutes|hr|hrs|hour|hours|day|days|week|weeks)\b",
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    "min": 60, "minute": 60, "minutes": 60,
    "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "day": 86400, "days": 86400,
    "week": 604800, "weeks": 604800,
}


def _format_reminder_sms(message: str) -> str:
    """Prefix the outbound reminder with 'Reminder:' unless it already leads with it."""
    text = (message or "").strip()
    if text.lower().startswith("reminder"):
        return text
    return f"Reminder: {text}"


def _local_tz() -> ZoneInfo:
    """Broker-facing timezone, shared with the daily summary."""
    try:
        return ZoneInfo(settings.daily_summary_timezone)
    except Exception:
        return ZoneInfo("UTC")


def resolve_remind_at(text: str, now: datetime | None = None) -> datetime | None:
    """Resolve a reminder time into an absolute aware UTC datetime.

    Handles, in order: relative phrases ("in 2 minutes", "in 3 hours"),
    ISO-8601 datetimes (naive treated as broker-local), and plain ISO dates
    (fired at 9:00 broker-local on that day). Returns None if unparseable.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    now = now or datetime.now(timezone.utc)

    match = _RELATIVE_RE.search(raw)
    if match:
        seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()]
        return now + timedelta(seconds=seconds)

    iso = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            # Date-only input has no time component; fire at 9am broker-local.
            if len(raw) <= 10:
                parsed = parsed.replace(hour=9, minute=0)
            parsed = parsed.replace(tzinfo=_local_tz())
        return parsed.astimezone(timezone.utc)

    return None


def _store_path() -> Path:
    path = settings.reminder_store_path
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _save() -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(_reminders, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _load() -> None:
    global _loaded
    if _loaded:
        return
    path = _store_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _reminders.update(data)
        except (OSError, json.JSONDecodeError):
            pass
    _loaded = True


async def create_reminder(
    *,
    to_phone: str,
    message: str,
    fire_at: datetime,
    created_by_phone: str = "",
    linked_order_id: str = "",
) -> dict[str, Any]:
    """Persist a reminder. fire_at must be an aware datetime."""
    reminder_id = uuid.uuid4().hex[:12]
    record = {
        "id": reminder_id,
        "to_phone": to_phone,
        "message": message,
        "fire_at": fire_at.astimezone(timezone.utc).isoformat(),
        "created_by_phone": created_by_phone,
        "linked_order_id": linked_order_id,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    async with _lock:
        _load()
        _reminders[reminder_id] = record
        _save()
    return record


async def list_reminders(phone: str, *, include_sent: bool = False) -> list[dict[str, Any]]:
    """Return pending (and optionally sent) reminders for a phone, soonest first."""
    async with _lock:
        _load()
        items = [
            r for r in _reminders.values()
            if r.get("to_phone") == phone
            and (include_sent or r.get("status") == "pending")
        ]
    return sorted(items, key=lambda r: r.get("fire_at", ""))


async def _fire_due_reminders() -> int:
    """Send any pending reminders whose time has passed. Returns count sent."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with _lock:
        _load()
        due = [
            r for r in _reminders.values()
            if r.get("status") == "pending" and r.get("fire_at", "") <= now_iso
        ]
    sent = 0
    for record in due:
        result = await send_sms_notification(record["to_phone"], _format_reminder_sms(record["message"]))
        async with _lock:
            stored = _reminders.get(record["id"])
            if stored is None:
                continue
            if result.get("success"):
                stored["status"] = "sent"
                stored["sent_at"] = datetime.now(timezone.utc).isoformat()
                sent += 1
            else:
                # Leave pending to retry next tick, but record the last error.
                stored["last_error"] = str(result.get("error", "send failed"))
            _save()
        if result.get("success"):
            logger.info("Fired reminder %s to %s", record["id"], record["to_phone"])
        else:
            logger.warning(
                "Reminder %s send failed, will retry: %s",
                record["id"], result.get("error"),
            )
    return sent


def _poll_seconds() -> int:
    return max(10, settings.reminder_poll_seconds)


async def _scheduler_loop() -> None:
    while True:
        try:
            await _fire_due_reminders()
        except Exception:
            logger.exception("Reminder scheduler tick failed")
        await asyncio.sleep(_poll_seconds())


def start_reminder_scheduler() -> None:
    """Start the reminder firing loop once."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())


async def purge_sent_reminders(retention_days: int | None = None) -> int:
    """Drop sent reminders older than the retention window. Returns count removed."""
    days = retention_days if retention_days is not None else settings.reminder_retention_days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with _lock:
        _load()
        stale = [
            rid for rid, r in _reminders.items()
            if r.get("status") == "sent" and r.get("sent_at", "") < cutoff
        ]
        for rid in stale:
            _reminders.pop(rid, None)
        if stale:
            _save()
    if stale:
        logger.info("Purged %d sent reminders older than %d days", len(stale), days)
    return len(stale)
