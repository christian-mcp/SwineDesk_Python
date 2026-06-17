"""Tool: text the current caller's own phone with a message.

Mainly for voice calls: the caller can't read on the phone, so they ask the bot
to text them something (their open orders, a load's details, a summary). This
sends that content to the same number that is talking to the bot.
"""

from __future__ import annotations

from typing import Any

from swinedesk.notifications import send_sms_notification
from swinedesk.tooling import Arg, Tool


class TextCaller(Tool, name="text_caller"):
    TOOL_PATH = "/tools/ops/text_caller"
    DESCRIPTION = (
        "Text the current caller's own phone with the given message. Use on a voice "
        "call when the caller asks you to text them something, their open orders, a "
        "load's details, driver info, a summary, so they have it in writing. The "
        "message is sent to the number already on the call; do not ask for a phone "
        "number. Put the full content in the message argument."
    )
    ARGUMENTS = {
        "message": Arg("The full message text to send to the caller"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        message = str(arguments.get("message", "")).strip()
        if not message:
            return {"error": "Message text is required."}

        phone = str(getattr(state, "phone", "") or "").strip() if state else ""
        if not phone:
            return {"error": "No caller phone on file to text."}

        response = await send_sms_notification(phone, message)
        if not response.get("success"):
            return {"error": response.get("error", "Failed to send the text."), **response}
        return {"result": f"Texted to {phone}."}
