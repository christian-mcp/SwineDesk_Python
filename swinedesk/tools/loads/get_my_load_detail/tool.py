"""Tool: fetch actor-safe load detail."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, remember_load, require_actor_id
from swinedesk.tooling import Arg, Tool


class GetMyLoadDetail(Tool, name="get_my_load_detail"):
    TOOL_PATH = "/tools/loads/get_my_load_detail"
    DESCRIPTION = "Get actor-safe detail for a specific load."
    ARGUMENTS = {
        "load_id": Arg("Load identifier"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"seller", "buyer"})
        if role_error:
            return role_error

        load_id = str(arguments.get("load_id", "")).strip()
        if not load_id:
            return {"error": "load_id is required."}

        actor_id, error = require_actor_id(state)
        if error:
            return error

        backend = get_backend_client()
        response = await backend.get_my_load_detail(actor_id, str(getattr(state, "role", "")), load_id)
        remember_load(state, load_id)
        return {
            "result": f"Loaded load {load_id}.",
            **response,
        }
