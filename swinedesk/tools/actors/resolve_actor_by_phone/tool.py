"""Tool: resolve actor profile by phone number."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import merge_workflow_draft
from swinedesk.tooling import Arg, Tool


class ResolveActorByPhone(Tool, name="resolve_actor_by_phone"):
    TOOL_PATH = "/tools/actors/resolve_actor_by_phone"
    DESCRIPTION = "Resolve actor role and profile context from a phone number."
    ARGUMENTS = {
        "phone": Arg("Phone number to resolve", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        phone = str(arguments.get("phone") or getattr(state, "phone", "")).strip()
        if not phone:
            return {"error": "Phone number is required."}

        backend = get_backend_client()
        response = await backend.resolve_actor_by_phone(phone)
        merge_workflow_draft(state, "resolve_actor", {"phone": phone})
        if state is not None:
            state.role = str(response.get("role", "unknown"))
            state.actor_profile = dict(response)
            state.actor_id = str(response.get("actor_id", response.get("id", "")))
            state.contact_id = str(response.get("contact_id", response.get("contactId", "")))
            state.company_id = str(response.get("company_id", response.get("companyId", "")))
        return {
            "result": f"Resolved role {response.get('role', 'unknown')} for {phone}.",
            **response,
        }
