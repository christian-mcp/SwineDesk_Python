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
        "load_id": Arg("Load identifier. Omit to use the load the bot last pinged you about.", optional=True),
        "driver_first": Arg("Driver first name"),
        "driver_last": Arg("Driver last name"),
        "driver_phone": Arg("Driver phone number"),
        "plate": Arg("License plate"),
        "pickup_eta": Arg("Pickup ETA", optional=True),
        "delivery_eta": Arg("Delivery ETA", optional=True),
        "scale_ticket_url": Arg("URL of the scale ticket photo or file", optional=True),
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
            # Fall back to the load the driver-assignment reminder seeded on the session.
            refs = list(getattr(state, "referenced_load_ids", []) or [])
            load_id = refs[-1] if refs else ""
        if not load_id:
            return {"error": "Which load is this for? I don't have one pending for you."}
        arguments["load_id"] = load_id

        merge_workflow_draft(state, "submit_freight_details", arguments)
        backend = get_backend_client()
        response = await backend.submit_freight_details(actor_id, arguments)
        remember_load(state, load_id)
        if getattr(state, "active_workflow", None) == "awaiting_driver_assignment":
            state.active_workflow = None
        return {
            "result": response.get("msg", f"Submitted freight details for {load_id}."),
            **response,
        }
