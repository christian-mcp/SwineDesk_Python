"""Tool: broker-only — record purchase order details and email buyer/seller."""

from __future__ import annotations

import logging
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.hellosign import send_purchase_order
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)


class SubmitPurchaseOrder(Tool, name="submit_purchase_order"):
    TOOL_PATH = "/tools/orders/submit_purchase_order"
    DESCRIPTION = (
        "Broker-only. Record the final purchase order for a load — freight cost, "
        "actual head count, weight slide adjustments — and email the confirmation "
        "to both buyer and seller. Use after the load is delivered and graded."
    )
    ARGUMENTS = {
        "load_id": Arg("Short load ID"),
        "head_count_final": Arg("Actual head count off the truck"),
        "freight_cost": Arg("Total freight invoice cost", optional=True),
        "weight_slide_count": Arg("Number of pigs impacted by weight slide", optional=True),
        "weight_slide_discount": Arg("Dollar discount per pound per pig for weight slide", optional=True),
        "comments": Arg("Additional comments", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        load_id = str(arguments.get("load_id", "")).strip()
        if not load_id:
            return {"error": "load_id is required."}

        backend = get_backend_client()
        response = await backend.record_purchase_order(load_id, arguments)

        if not response.get("success"):
            return {"error": response.get("error", "Failed to record purchase order."), "load_id": load_id}

        await _send_po_emails(load_id, arguments, response)

        return {"result": response.get("msg", f"Purchase order recorded for load {load_id}."), **response}


async def _send_po_emails(load_id: str, arguments: dict[str, Any], response: dict[str, Any]) -> None:
    market = str(response.get("market") or "pigs").lower().replace("_", " ")
    result = await send_purchase_order(
        load_id=load_id,
        head_final=arguments.get("head_count_final", "?"),
        market=market,
        freight_cost=arguments.get("freight_cost"),
        weight_slide_count=arguments.get("weight_slide_count"),
        weight_slide_discount=arguments.get("weight_slide_discount"),
        comments=str(arguments.get("comments") or ""),
        buyer_name=str(response.get("buyer_first_name") or response.get("buyer_email") or "Buyer"),
        buyer_email=str(response.get("buyer_email") or ""),
        seller_name=str(response.get("seller_first_name") or response.get("seller_email") or "Seller"),
        seller_email=str(response.get("seller_email") or ""),
        seller_company=str(response.get("seller_company") or ""),
        seller_phone=str(response.get("seller_phone") or ""),
        buyer_company=str(response.get("buyer_company") or ""),
        buyer_phone=str(response.get("buyer_phone") or ""),
    )
    if not result.get("success"):
        logger.warning("HelloSign PO failed for load %s: %s", load_id, result.get("error"))
