"""Tool: add a note to a user, order, or company (broker only)."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


class AddNote(Tool, name="add_note"):
    TOOL_PATH = "/tools/crm/add_note"
    DESCRIPTION = (
        "Save a note about a user, order, or deal. Notes are stored permanently and "
        "surface in history lookups. Use whenever Brian says 'note that', 'remember that', "
        "'log this', or dictates information about a customer."
    )
    ARGUMENTS = {
        "body": Arg("The note content to save"),
        "linked_user_phone": Arg("Phone number of the user this note is about", optional=True),
        "linked_order_id": Arg("Order short ID this note relates to", optional=True),
        "linked_company_name": Arg("Company name this note relates to", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        body = str(arguments.get("body", "")).strip()
        if not body:
            return {"error": "Note body is required."}

        payload = {
            "body": body,
            "created_by_phone": str(getattr(state, "phone", "")),
            "linked_user_phone": str(arguments.get("linked_user_phone") or ""),
            "linked_order_id": str(arguments.get("linked_order_id") or ""),
            "linked_company_name": str(arguments.get("linked_company_name") or ""),
        }

        backend = get_backend_client()
        response = await backend.create_note(payload)
        if "error" in response:
            return response

        return {"result": "Note saved.", **response}
