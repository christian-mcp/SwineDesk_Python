"""Tool: list upcoming reminders for the current user."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from swinedesk.reminders import list_reminders
from swinedesk.settings import settings
from swinedesk.tooling import Arg, Tool


class ListReminders(Tool, name="list_reminders"):
    TOOL_PATH = "/tools/reminders/list_reminders"
    DESCRIPTION = "List scheduled (pending) reminders for the current user or a given phone."
    ARGUMENTS = {
        "phone": Arg("Phone number to query (defaults to current user)", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        phone = str(arguments.get("phone") or getattr(state, "phone", "") or "")
        items = await list_reminders(phone)
        if not items:
            return {"result": "No upcoming reminders."}

        try:
            local = ZoneInfo(settings.daily_summary_timezone)
        except Exception:
            local = timezone.utc

        lines: list[str] = []
        for r in items[:10]:
            try:
                when = datetime.fromisoformat(r["fire_at"]).astimezone(local).strftime("%Y-%m-%d %H:%M")
            except (ValueError, KeyError):
                when = r.get("fire_at", "?")
            lines.append(f"- {when}: {r.get('message', '')}")
        return {"result": "\n".join(lines), "reminders": items}
