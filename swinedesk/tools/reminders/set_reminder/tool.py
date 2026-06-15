"""Tool: schedule a follow-up reminder for a user or deal."""

from __future__ import annotations

from datetime import timezone
from typing import Any
from zoneinfo import ZoneInfo

from swinedesk.reminders import create_reminder, resolve_remind_at
from swinedesk.settings import settings
from swinedesk.tooling import Arg, Tool


class SetReminder(Tool, name="set_reminder"):
    TOOL_PATH = "/tools/reminders/set_reminder"
    DESCRIPTION = (
        "Schedule a follow-up reminder. The system sends an SMS to the current user "
        "(or a specified phone) at the requested time. Use for: 'remind me to call JP "
        "in 2 minutes', 'remind me in 3 weeks', 'follow up next Monday', or any "
        "time-based follow-up."
    )
    ARGUMENTS = {
        "message": Arg("What the reminder should say when it fires"),
        "remind_at": Arg(
            "When to send it. Pass the user's phrasing for short relative times "
            "('in 2 minutes', 'in 3 hours', 'in 2 days') directly, or an ISO-8601 "
            "datetime / YYYY-MM-DD date for absolute times."
        ),
        "to_phone": Arg("Phone number to remind (defaults to current user)", optional=True),
        "linked_order_id": Arg("Order short ID this reminder relates to", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        to_phone = str(arguments.get("to_phone") or getattr(state, "phone", "") or "")
        if not to_phone:
            return {"error": "No phone number available to schedule reminder."}

        message = str(arguments.get("message", "")).strip()
        if not message:
            return {"error": "What should the reminder say?"}

        raw_when = str(arguments.get("remind_at", ""))
        fire_at = resolve_remind_at(raw_when)
        if fire_at is None:
            return {
                "error": f"Couldn't understand the time '{raw_when}'. "
                "Try 'in 30 minutes' or a date."
            }

        record = await create_reminder(
            to_phone=to_phone,
            message=message,
            fire_at=fire_at,
            created_by_phone=str(getattr(state, "phone", "")),
            linked_order_id=str(arguments.get("linked_order_id") or ""),
        )

        try:
            local = ZoneInfo(settings.daily_summary_timezone)
        except Exception:
            local = timezone.utc
        when_label = fire_at.astimezone(local).strftime("%Y-%m-%d %H:%M %Z")
        return {
            "result": f'Reminder set for {when_label}: "{message}"',
            "id": record["id"],
            "fire_at": record["fire_at"],
        }
