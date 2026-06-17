"""Tool: broker closes an open auction immediately, booking the winning bid."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


class CloseAuctionNow(Tool, name="close_auction_now"):
    TOOL_PATH = "/tools/market/close_auction_now"
    DESCRIPTION = (
        "Broker-only. Close an open auction immediately and book the best bid. "
        "Use when Brian says 'close the auction on <order>', 'take the best bid now', "
        "'book it', or 'end the auction'. The Java side fires cascade SMS to both parties; "
        "this tool just confirms the result to the broker."
    )
    ARGUMENTS = {
        "order_id": Arg("Short order ID (shortId) of the auction to close"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        order_id = str(arguments.get("order_id") or "").strip()
        if not order_id:
            return {"error": "order_id is required."}

        backend = get_backend_client()
        result = await backend.close_auction(order_id)

        if not result.get("success"):
            reason = result.get("reason", "unknown reason")
            if reason == "no bids":
                return {"result": f"Auction on order {order_id} closed with no bids. Nothing booked."}
            return {
                "error": f"Could not close auction for order {order_id}: {reason}",
                "detail": result,
            }

        buyer_phone = result.get("buyer_phone", "unknown")
        seller_phone = result.get("seller_phone", "")
        head = result.get("head", "")
        traded_order_id = result.get("traded_order_id", order_id)
        winning_bid_price = result.get("winning_bid_price")
        winner_name = result.get("winner_first_name") or ""

        head_txt = f"{head} head" if head else ""
        winner_txt = winner_name if winner_name else f"buyer {buyer_phone}"
        parts = [f"Auction closed. Order {traded_order_id} booked"]
        if head_txt:
            parts.append(head_txt)
        if winning_bid_price is not None:
            parts.append(f"won by {winner_txt} at ${winning_bid_price}/head")
        else:
            parts.append(f"buyer {buyer_phone}")
        if seller_phone:
            parts.append(f"seller {seller_phone}")

        summary = " | ".join(parts) + ". Both parties have been notified."

        return {
            "result": summary,
            "traded_order_id": traded_order_id,
            "buyer_phone": buyer_phone,
            "seller_phone": seller_phone,
            "head": head,
            "winning_bid_price": winning_bid_price,
        }
