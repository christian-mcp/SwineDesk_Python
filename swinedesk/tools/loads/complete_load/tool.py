"""Tool: broker-only — mark a load as complete and queue it for invoicing."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


class CompleteLoad(Tool, name="complete_load"):
    TOOL_PATH = "/tools/loads/complete_load"
    DESCRIPTION = (
        "Broker-only. Mark a load as complete after grading is submitted. "
        "Transitions the load from PENDING_ELM to INVOICED and triggers the order "
        "settlement flow. Use when the broker confirms all grading is done and the "
        "load is ready to invoice."
    )
    ARGUMENTS = {
        "load_id": Arg("Short load ID to complete"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        load_id = str(arguments.get("load_id", "")).strip()
        if not load_id:
            return {"error": "load_id is required."}

        backend = get_backend_client()
        response = await backend.complete_load(load_id)

        if not response.get("success"):
            return {"error": response.get("error", "Failed to complete load."), "load_id": load_id}

        return {"result": response.get("msg", f"Load {load_id} completed."), **response}
