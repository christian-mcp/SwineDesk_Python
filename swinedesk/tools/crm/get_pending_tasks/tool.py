"""Tool: get a broker's pending tasks and action items."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


class GetPendingTasks(Tool, name="get_pending_tasks"):
    TOOL_PATH = "/tools/crm/get_pending_tasks"
    DESCRIPTION = (
        "Return the broker's pending action items: loads awaiting health certs, "
        "loads awaiting grade sheets, unmatched orders, and overdue reminders. "
        "Use when asked 'what's outstanding', 'what needs my attention', 'what's on my plate'."
    )
    ARGUMENTS = {
        "phone": Arg("Phone of the broker to query (defaults to current user)", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        phone = str(arguments.get("phone") or getattr(state, "phone", "") or "")
        backend = get_backend_client()
        response = await backend.get_pending_tasks(phone)
        if "error" in response:
            return response

        tasks = response.get("tasks", [])
        if not tasks:
            return {"result": "No pending tasks.", **response}

        lines: list[str] = [f"Pending tasks ({len(tasks)}):"]
        for t in tasks[:10]:
            name = t.get("label") or t.get("actionType") or "Task"
            load = t.get("loadId")
            ref = f" — load #{load}" if load else ""
            lines.append(f"  - {name}{ref}")

        return {"result": "\n".join(lines), **response}
