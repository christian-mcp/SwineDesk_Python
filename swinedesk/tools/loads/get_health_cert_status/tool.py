"""Tool: fetch health certificate status for a load."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, remember_load, require_actor_id
from swinedesk.tooling import Arg, Tool


class GetHealthCertStatus(Tool, name="get_health_cert_status"):
    TOOL_PATH = "/tools/loads/get_health_cert_status"
    DESCRIPTION = "Check whether a load's health certificate is pending or received."
    ARGUMENTS = {
        "load_id": Arg("Load identifier"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"seller", "buyer", "vet"})
        if role_error:
            return role_error

        load_id = str(arguments.get("load_id", "")).strip()
        if not load_id:
            return {"error": "load_id is required."}

        actor_id, error = require_actor_id(state)
        if error:
            return error

        backend = get_backend_client()
        response = await backend.get_health_cert_status(actor_id, str(getattr(state, "role", "")), load_id)
        remember_load(state, load_id)
        return {
            "result": f"Loaded health cert status for {load_id}.",
            **response,
        }
