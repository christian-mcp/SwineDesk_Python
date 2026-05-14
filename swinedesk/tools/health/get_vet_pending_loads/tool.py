"""Tool: list pending health-cert loads for a vet."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, remember_load, require_actor_id
from swinedesk.tooling import Tool


class GetVetPendingLoads(Tool, name="get_vet_pending_loads"):
    TOOL_PATH = "/tools/health/get_vet_pending_loads"
    DESCRIPTION = "List pending health-certificate loads for the current vet."
    ARGUMENTS = {}

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        _ = arguments
        role_error = ensure_role(state, {"vet"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        backend = get_backend_client()
        response = await backend.get_vet_pending_loads(actor_id)
        for load_id in response.get("load_ids", []):
            remember_load(state, str(load_id))
        return {
            "result": "Loaded pending health certificate loads.",
            **response,
        }
