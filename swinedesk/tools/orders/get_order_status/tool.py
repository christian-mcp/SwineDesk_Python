"""Tool stub: fetch order status."""

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tooling import Arg, Tool


class GetOrderStatus(Tool, name="get_order_status"):
    TOOL_PATH = "/tools/orders/get_order_status"
    DESCRIPTION = "Look up order status by Order ID."
    ARGUMENTS = {
        "order_id": Arg("Order identifier, e.g. ELM-001"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        order_id = str(arguments.get("order_id", "")).upper()
        if not order_id:
            return {"error": "Order ID is required."}

        backend = get_backend_client()
        response = await backend.get_order_status(order_id)
        return {
            "order_id": response.get("id"),
            "status": response.get("status"),
            "market": response.get("market"),
            "head_count": response.get("headCount"),
            "price_per_head": response.get("pricePerHead"),
            "health_status": response.get("health"),
            "weight_range": response.get("weightRange"),
            "created_date": response.get("createdDate"),
            "ship_date": response.get("shipDate"),
            "delivery_date": response.get("deliveryDate"),
        }

