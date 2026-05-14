"""Tool: list actor-safe upcoming loads."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, remember_load, require_actor_id
from swinedesk.tooling import Arg, Tool


class ListMyLoads(Tool, name="list_my_loads"):
    TOOL_PATH = "/tools/loads/list_my_loads"
    DESCRIPTION = "List upcoming actor-safe loads for sellers or buyers."
    ARGUMENTS = {
        "window": Arg("today, week, month, or custom", optional=True),
        "date_from": Arg("Start date for custom range", optional=True),
        "date_to": Arg("End date for custom range", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"seller", "buyer"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        backend = get_backend_client()
        response = await backend.list_my_loads(actor_id, str(getattr(state, "role", "")), arguments)
        for load_id in response.get("load_ids", []):
            remember_load(state, str(load_id))
        return {
            "result": "Loaded upcoming loads.",
            **response,
        }
