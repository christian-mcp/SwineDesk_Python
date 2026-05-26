"""Tool: send an outbound SMS message to any user (broker only)."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


class SendMessageToUser(Tool, name="send_message_to_user"):
    TOOL_PATH = "/tools/ops/send_message_to_user"
    DESCRIPTION = (
        "Send a direct SMS message to a user on behalf of the broker. "
        "Use when Brian says 'text X and tell them Y', 'send a message to this person', "
        "or needs to proactively reach out to a seller, buyer, vet, or freight contact."
    )
    ARGUMENTS = {
        "to_phone": Arg("Phone number to send the message to"),
        "message": Arg("The message text to send"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        to_phone = str(arguments.get("to_phone", "")).strip()
        message = str(arguments.get("message", "")).strip()

        if not to_phone:
            return {"error": "Recipient phone number is required."}
        if not message:
            return {"error": "Message text is required."}

        backend = get_backend_client()
        response = await backend.send_message_to_user(to_phone, message)
        if "error" in response:
            return response

        return {"result": f"Message sent to {to_phone}.", **response}
