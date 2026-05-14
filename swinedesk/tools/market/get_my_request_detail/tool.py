"""Tool: get one actor-safe request detail."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, remember_request, require_actor_id
from swinedesk.tooling import Arg, Tool


class GetMyRequestDetail(Tool, name="get_my_request_detail"):
    TOOL_PATH = "/tools/market/get_my_request_detail"
    DESCRIPTION = "Fetch actor-safe detail for one open request or listing."
    ARGUMENTS = {
        "request_id": Arg("Request or listing identifier"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"seller", "buyer"})
        if role_error:
            return role_error

        request_id = str(arguments.get("request_id", "")).strip()
        if not request_id:
            return {"error": "request_id is required."}

        actor_id, error = require_actor_id(state)
        if error:
            return error

        backend = get_backend_client()
        response = await backend.get_my_request_detail(actor_id, request_id)
        remember_request(state, request_id)
        return {
            "result": f"Loaded request {request_id}.",
            **response,
        }
