"""Tool: submit freight details for a load."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, merge_workflow_draft, remember_load, require_actor_id
from swinedesk.tooling import Arg, Tool


class SubmitFreightDetails(Tool, name="submit_freight_details"):
    TOOL_PATH = "/tools/loads/submit_freight_details"
    DESCRIPTION = "Submit driver, truck, and ETA details for a freight load."
    ARGUMENTS = {
        "load_id": Arg("Load identifier"),
        "driver_first": Arg("Driver first name"),
        "driver_last": Arg("Driver last name"),
        "driver_phone": Arg("Driver phone number"),
        "plate": Arg("License plate"),
        "pickup_eta": Arg("Pickup ETA", optional=True),
        "delivery_eta": Arg("Delivery ETA", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"freight_operator"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        load_id = str(arguments.get("load_id", "")).strip()
        if not load_id:
            return {"error": "load_id is required."}

        merge_workflow_draft(state, "submit_freight_details", arguments)
        backend = get_backend_client()
        response = await backend.submit_freight_details(actor_id, arguments)
        remember_load(state, load_id)
        return {
            "result": response.get("msg", f"Submitted freight details for {load_id}."),
            **response,
        }
