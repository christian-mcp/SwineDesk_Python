"""Tool: confirm freight assignment details."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, merge_workflow_draft, remember_load, require_actor_id
from swinedesk.tooling import Arg, Tool


class ConfirmFreightAssignment(Tool, name="confirm_freight_assignment"):
    TOOL_PATH = "/tools/loads/confirm_freight_assignment"
    DESCRIPTION = "Confirm driver, truck, and ETA details for an assigned load."
    ARGUMENTS = {
        "load_id": Arg("Load identifier"),
        "driver_first": Arg("Driver first name", optional=True),
        "driver_last": Arg("Driver last name", optional=True),
        "driver_phone": Arg("Driver phone number", optional=True),
        "plate": Arg("License plate", optional=True),
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

        merge_workflow_draft(state, "confirm_freight_assignment", arguments)
        backend = get_backend_client()
        response = await backend.confirm_freight_assignment(actor_id, arguments)
        remember_load(state, load_id)
        return {
            "result": response.get("msg", f"Confirmed freight details for {load_id}."),
            **response,
        }
