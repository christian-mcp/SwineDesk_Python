"""Tool: list open actor-side market requests."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, require_actor_id
from swinedesk.tooling import Arg, Tool


class GetMyOpenRequests(Tool, name="get_my_open_requests"):
    TOOL_PATH = "/tools/market/get_my_open_requests"
    DESCRIPTION = "List open seller listings or buyer requests for the current actor."
    ARGUMENTS = {
        "status": Arg("Optional status filter", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"seller", "buyer"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        backend = get_backend_client()
        response = await backend.get_my_open_requests(
            actor_id,
            str(getattr(state, "role", "")),
            arguments,
        )
        if state is not None:
            request_ids = response.get("request_ids") or []
            for request_id in request_ids:
                state.remember_order(str(request_id))
        return {
            "result": "Loaded open requests.",
            **response,
        }
