"""Tool: create seller-side pig listing."""

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


class CreateSellListing(Tool, name="create_sell_listing"):
    TOOL_PATH = "/tools/market/create_sell_listing"
    DESCRIPTION = "Create a seller-side listing for wean or feeder pigs."
    ARGUMENTS = {
        "market": Arg("WEAN_PIGS or FEEDER_PIGS"),
        "head_count": Arg("Total number of pigs to sell"),
        "health": Arg("Health status — must be one of: CLEAN, PEDV, PRRS"),
        "weight_range": Arg("Weight range", optional=True),
        "num_loads": Arg("Number of loads", optional=True),
        "first_ship_date": Arg("First available ship date", optional=True),
        "cadence": Arg("Weekly cadence or schedule notes", optional=True),
        "source_site": Arg("Known source site id or name", optional=True),
        "pid": Arg("Source premises ID", optional=True),
        "price_target": Arg("Target price per head", optional=True),
        "vaccines_done": Arg("Vaccines already administered", optional=True),
        "regrade": Arg("Regrade terms", optional=True),
        "notes": Arg("Additional notes", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"seller"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        payload = {"actorId": actor_id, "phone": getattr(state, "phone", ""), **arguments}
        merge_workflow_draft(state, "seller_listing", arguments)
        backend = get_backend_client()
        response = await backend.create_sell_listing(payload)
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
                summarize_collection("Created sell listing", {"request_id": request_id}),
            ),
            "request_id": request_id,
            **response,
        }
