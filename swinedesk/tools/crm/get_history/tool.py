"""Tool: look up a user's full history — notes, orders, conversation count."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


class GetHistory(Tool, name="get_history"):
    TOOL_PATH = "/tools/crm/get_history"
    DESCRIPTION = (
        "Return a summary of a user's history: past orders/deals, saved notes, "
        "and conversation count. Use when asked 'give me the history on X' or "
        "'what do we have on this person'."
    )
    ARGUMENTS = {
        "phone": Arg("Phone number of the user to look up"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        phone = str(arguments.get("phone", "")).strip()
        if not phone:
            return {"error": "Phone number is required."}

        backend = get_backend_client()
        response = await backend.get_user_history(phone)
        if "error" in response:
            return response

        notes = response.get("notes", [])
        orders = response.get("orders", [])
        msg_count = response.get("message_count", 0)

        lines: list[str] = []
        lines.append(f"Phone: {phone} — {msg_count} messages in history")
        if orders:
            lines.append(f"Orders ({len(orders)}):")
            for o in orders[:5]:
                lines.append(f"  {o.get('shortId','?')} {o.get('status','?')} {o.get('market','?')}")
        if notes:
            lines.append(f"Notes ({len(notes)}):")
            for n in notes[:5]:
                lines.append(f"  [{n.get('createdAt','?')}] {n.get('body','')[:80]}")

        return {"result": "\n".join(lines) if lines else "No history found.", **response}
