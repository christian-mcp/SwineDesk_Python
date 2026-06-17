"""Tool: buyer submits a bid price on an open auction."""

from __future__ import annotations

import re
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


def _parse_price(raw: Any) -> float | None:
    text = str(raw or "").strip().replace("$", "").replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


class SubmitBid(Tool, name="submit_bid"):
    TOOL_PATH = "/tools/market/submit_bid"
    DESCRIPTION = (
        "Buyer-only. Submit a bid price per head on an open auction order. "
        "Use when a buyer replies with a price in the context of an auction they were "
        "notified about, e.g. 'I'll bid 52', 'my bid is 84/head', 'put me in at 76'. "
        "Resolve the order_id from the auction notification context."
    )
    ARGUMENTS = {
        "order_id": Arg("Short order ID (shortId) of the auction to bid on"),
        "bid_price": Arg("Bid price per head (numeric)"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"buyer"})
        if role_error:
            return role_error

        order_id = str(arguments.get("order_id") or "").strip()
        if not order_id:
            return {"error": "order_id is required."}

        bid_price = _parse_price(arguments.get("bid_price"))
        if bid_price is None:
            return {"error": "A numeric bid price is required."}

        buyer_phone = str(getattr(state, "phone", "") or "").strip()
        if not buyer_phone:
            return {"error": "Could not determine your phone number from the session."}

        backend = get_backend_client()
        result = await backend.submit_bid(order_id, bid_price, buyer_phone)

        if not result.get("success"):
            return {
                "error": f"Bid submission failed for order {order_id}.",
                "detail": result,
            }

        bid_id = result.get("bid_id", "")
        price_txt = f"${bid_price:g}"
        confirmation = (
            f"Your bid of {price_txt}/hd on order {order_id} has been received"
            + (f" (bid ref {bid_id})" if bid_id else "")
            + ". ELM will be in touch once the auction closes."
        )

        return {
            "result": confirmation,
            "bid_id": bid_id,
            "order_id": result.get("order_id", order_id),
            "bid_price": result.get("bid_price", bid_price),
        }
