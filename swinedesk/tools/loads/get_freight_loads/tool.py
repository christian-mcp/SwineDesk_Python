"""Tool: list freight-assigned loads."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, remember_load, require_actor_id
from swinedesk.tooling import Arg, Tool


class GetFreightLoads(Tool, name="get_freight_loads"):
    TOOL_PATH = "/tools/loads/get_freight_loads"
    DESCRIPTION = "List loads assigned to the freight operator."
    ARGUMENTS = {
        "window": Arg("today, week, month, or custom", optional=True),
        "date_from": Arg("Start date for custom range", optional=True),
        "date_to": Arg("End date for custom range", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"freight_operator"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        backend = get_backend_client()
        response = await backend.get_freight_loads(actor_id, arguments)
        for load_id in response.get("load_ids", []):
            remember_load(state, str(load_id))
        return {
            "result": "Loaded freight assignments.",
            **response,
        }
