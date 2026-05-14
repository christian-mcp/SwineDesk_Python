"""Tool: create buyer-side pig request."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import (
    ensure_role,
    merge_workflow_draft,
    remember_request,
    require_actor_id,
    summarize_collection,
)
from swinedesk.tooling import Arg, Tool


class CreateBuyRequest(Tool, name="create_buy_request"):
    TOOL_PATH = "/tools/market/create_buy_request"
    DESCRIPTION = "Create a buyer-side request for wean or feeder pigs."
    ARGUMENTS = {
        "market": Arg("WEAN_PIGS or FEEDER_PIGS"),
        "head_count_needed": Arg("Total number of pigs needed"),
        "health_requirement": Arg("Required health status"),
        "weight_range": Arg("Target weight range", optional=True),
        "num_loads": Arg("Number of loads", optional=True),
        "delivery_start_date": Arg("Delivery start date", optional=True),
        "cadence": Arg("Weekly cadence or schedule notes", optional=True),
        "destination_site": Arg("Known destination site id or name", optional=True),
        "pid": Arg("Destination premises ID", optional=True),
        "budget_target": Arg("Target budget per head", optional=True),
        "vaccine_requirements": Arg("Required vaccines", optional=True),
        "regrade": Arg("Regrade terms", optional=True),
        "notes": Arg("Additional notes", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"buyer"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        payload = {"actorId": actor_id, "phone": getattr(state, "phone", ""), **arguments}
        merge_workflow_draft(state, "buyer_request", arguments)
        backend = get_backend_client()
        response = await backend.create_buy_request(payload)
        request_id = str(
            response.get("requestId")
            or response.get("orderId")
            or response.get("shortId")
            or response.get("guid")
            or ""
        )
        remember_request(state, request_id)
        return {
            "result": response.get(
                "msg",
                summarize_collection("Created buy request", {"request_id": request_id}),
            ),
            "request_id": request_id,
            **response,
        }
