"""Tool: list upcoming reminders for the current user."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import require_actor_id
from swinedesk.tooling import Arg, Tool


class ListReminders(Tool, name="list_reminders"):
    TOOL_PATH = "/tools/reminders/list_reminders"
    DESCRIPTION = "List scheduled reminders and pending tasks for the current user or a given phone."
    ARGUMENTS = {
        "phone": Arg("Phone number to query (defaults to current user)", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        phone = str(arguments.get("phone") or getattr(state, "phone", "") or "")
        backend = get_backend_client()
        response = await backend.list_reminders(phone)
        items = response.get("reminders", response.get("items", []))
        if not items:
            return {"result": "No upcoming reminders."}
        lines = [f"- {r.get('remind_at', '?')}: {r.get('message', '')}" for r in items[:10]]
        return {"result": "\n".join(lines), "reminders": items}
