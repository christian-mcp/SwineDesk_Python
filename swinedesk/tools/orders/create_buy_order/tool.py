"""Tool stub: create buy order via backend API."""

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tooling import Arg, Tool


class CreateBuyOrder(Tool, name="create_buy_order"):
    TOOL_PATH = "/tools/orders/create_buy_order"
    DESCRIPTION = "Create a buy order for wean or feeder pigs."
    ARGUMENTS = {
        "market": Arg("Market type: WEAN_PIGS or FEEDER_PIGS"),
        "head_count": Arg("Total number of pigs to buy"),
        "health_req": Arg("Required health status"),
        "price_per_head": Arg("Buyer bid price per head"),
        "weight_range": Arg("Target weight range", optional=True),
        "delivery_date": Arg("Delivery start date", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        phone = getattr(state, "phone", "") if state is not None else ""
        if not phone:
            return {"error": "Missing session phone number for buy order creation."}

        payload = {
            "phone": phone,
            "market": arguments.get("market"),
            "headCount": arguments.get("head_count"),
            "healthReq": arguments.get("health_req"),
            "pricePerHead": arguments.get("price_per_head"),
            "weightRange": arguments.get("weight_range"),
            "deliveryDate": arguments.get("delivery_date"),
        }

        backend = get_backend_client()
        response = await backend.create_buy_order(payload)
        return {
            "result": f"Created buy order {response.get('shortId', '')}".strip(),
            "order_guid": response.get("guid"),
            "order_short_id": response.get("shortId"),
            "message": response.get("msg"),
        }

