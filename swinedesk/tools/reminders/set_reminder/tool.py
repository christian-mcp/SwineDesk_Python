"""Tool: schedule a follow-up reminder for a user or deal."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tooling import Arg, Tool


class SetReminder(Tool, name="set_reminder"):
    TOOL_PATH = "/tools/reminders/set_reminder"
    DESCRIPTION = (
        "Schedule a follow-up reminder. The system will send an SMS to the current user "
        "(or a specified phone) at the requested time. Use for: 'remind me in 3 weeks', "
        "'follow up with this person next month', or any time-based follow-up request."
    )
    ARGUMENTS = {
        "message": Arg("What the reminder should say when it fires"),
        "remind_at": Arg(
            "When to send it — ISO date (YYYY-MM-DD) or natural description like '2 weeks from now', "
            "'next Monday', 'July 15'"
        ),
        "to_phone": Arg("Phone number to remind (defaults to current user)", optional=True),
        "linked_order_id": Arg("Order short ID this reminder relates to", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        to_phone = str(arguments.get("to_phone") or getattr(state, "phone", "") or "")
        if not to_phone:
            return {"error": "No phone number available to schedule reminder."}

        payload = {
            "to_phone": to_phone,
            "message": str(arguments.get("message", "")),
            "remind_at": str(arguments.get("remind_at", "")),
            "linked_order_id": str(arguments.get("linked_order_id") or ""),
            "created_by_phone": str(getattr(state, "phone", "")),
        }

        backend = get_backend_client()
        response = await backend.create_reminder(payload)
        if "error" in response:
            return response

        return {
            "result": f"Reminder set for {payload['remind_at']}: \"{payload['message']}\"",
            **response,
        }
