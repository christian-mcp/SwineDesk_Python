"""Tool stub: create sell order via backend API."""

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tooling import Arg, Tool


class CreateSellOrder(Tool, name="create_sell_order"):
    TOOL_PATH = "/tools/orders/create_sell_order"
    DESCRIPTION = "Create a sell order for wean or feeder pigs."
    ARGUMENTS = {
        "market": Arg("Market type: WEAN_PIGS or FEEDER_PIGS"),
        "head_count": Arg("Total number of pigs to sell"),
        "health": Arg("Health status: CLEAN, PEDV_POSITIVE, PEDV, PRRS_POSITIVE, or PRRS"),
        "price_per_head": Arg("Seller ask price per head"),
        "weight_range": Arg("Weight range, e.g. 10-12 lbs", optional=True),
        "ship_date": Arg("First available ship date in YYYY-MM-DD format", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        phone = getattr(state, "phone", "") if state is not None else ""
        if not phone:
            return {"error": "Missing session phone number for sell order creation."}

        payload = {
            "phone": phone,
            "market": arguments.get("market"),
            "headCount": arguments.get("head_count"),
            "health": arguments.get("health"),
            "pricePerHead": arguments.get("price_per_head"),
            "weightRange": arguments.get("weight_range"),
            "shipDate": arguments.get("ship_date"),
        }

        backend = get_backend_client()
        response = await backend.create_sell_order(payload)
        return {
            "result": f"Created sell order {response.get('shortId', '')}".strip(),
            "order_guid": response.get("guid"),
            "order_short_id": response.get("shortId"),
            "message": response.get("msg"),
        }
