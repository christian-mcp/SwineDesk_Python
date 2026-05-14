"""Tool: send an internal bounded SMS notification."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tooling import Arg, Tool


class SendRoleNotification(Tool, name="send_role_notification"):
    TOOL_PATH = "/tools/ops/send_role_notification"
    DESCRIPTION = "Internal tool to send a bounded SMS notification for a workflow event."
    ARGUMENTS = {
        "to_phone": Arg("Destination phone number"),
        "message": Arg("Notification message"),
        "event_type": Arg("Event type label", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        _ = state
        backend = get_backend_client()
        response = await backend.send_role_notification(arguments)
        return {
            "result": "Sent role notification.",
            **response,
        }
