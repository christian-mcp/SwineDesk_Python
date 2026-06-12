"""Tool: broker-only — find contacts filtered by role and/or state."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


class FindContacts(Tool, name="find_contacts"):
    TOOL_PATH = "/tools/crm/find_contacts"
    DESCRIPTION = (
        "Broker-only. Find contacts filtered by role (seller, buyer, vet, freight) "
        "and/or US state code. Returns name, company, phone, and state for each match. "
        "Use this to identify who to reach out to or to build a blast list."
    )
    ARGUMENTS = {
        "role": Arg("seller, buyer, vet, or freight", optional=True),
        "state": Arg("US state code, e.g. IA, TX, MN", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        role = str(arguments.get("role") or "").strip() or None
        contact_state = str(arguments.get("state") or "").strip().upper() or None

        backend = get_backend_client()
        return await backend.list_contacts(role=role, state=contact_state)
