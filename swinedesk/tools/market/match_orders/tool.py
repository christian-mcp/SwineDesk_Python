"""Tool: broker-only - pair an open buy request with an open sell listing."""

from __future__ import annotations

import logging
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.notifications import send_sms_notification
from swinedesk.settings import settings
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)


async def _notify_party(phone: str | None, first_name: str | None, side: str, order_id: str) -> None:
    if not phone:
        return
    name = (first_name or "").strip()
    greeting = f"Hey {name}, " if name else ""
    if side == "SELL":
        msg = (
            f"{greeting}your sell listing {order_id} is matched. "
            "Elmport will reach out today with shipping details."
        )
    else:
        msg = (
            f"{greeting}your buy request {order_id} is matched. "
            "Elmport will reach out today with delivery details."
        )
    try:
        await send_sms_notification(phone, msg)
    except Exception:
        logger.exception("Failed to notify %s about match", phone)


async def _notify_ops_role(phone: str, role: str, order_id: str, head: object, market: str) -> None:
    """Heads-up SMS to the vet or freight default phone when a new deal pairs."""
    if not phone:
        return
    pretty_market = market.lower().replace("_", " ") if market else "pigs"
    if role == "vet":
        msg = (
            f"Heads up from Elmport. New deal {order_id} just paired, "
            f"{head} {pretty_market}. A health cert will be needed in the next couple weeks. "
            "We'll text the load ID once it's scheduled."
        )
    else:
        msg = (
            f"Heads up from Elmport. New deal {order_id} just paired, "
            f"{head} {pretty_market}. Expect pickup and delivery details once the load is scheduled."
        )
    try:
        await send_sms_notification(phone, msg)
    except Exception:
        logger.exception("Failed to notify %s phone %s about match", role, phone)


class MatchOrders(Tool, name="match_orders"):
    TOOL_PATH = "/tools/market/match_orders"
    DESCRIPTION = (
        "Broker-only. Pair an open buy request with an open sell listing and mark the "
        "deal TRADED. Use when the broker says things like 'pair 859253 with 771806', "
        "'put 771806 on 859253', or 'fill that buy with the Iowa sell'. Both ids are the "
        "short numeric order ids shown on the open market board."
    )
    ARGUMENTS = {
        "buy_order_id": Arg("Short id of the open BUY request"),
        "sell_order_id": Arg("Short id of the open SELL listing"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        buy_id = str(arguments.get("buy_order_id", "")).strip()
        sell_id = str(arguments.get("sell_order_id", "")).strip()
        if not buy_id or not sell_id:
            return {"error": "Need both a buy_order_id and a sell_order_id."}

        backend = get_backend_client()
        response = await backend.match_orders(buy_id, sell_id)

        if not response.get("success"):
            return {
                "error": response.get("error", "Match failed."),
                "buy_order_id": buy_id,
                "sell_order_id": sell_id,
            }

        await _notify_party(
            response.get("seller_phone"),
            response.get("seller_first_name"),
            "SELL",
            response.get("traded_order_id") or sell_id,
        )
        await _notify_party(
            response.get("buyer_phone"),
            response.get("buyer_first_name"),
            "BUY",
            response.get("retired_order_id") or buy_id,
        )

        traded_ref = response.get("traded_order_id") or sell_id
        head_count = response.get("head") or "?"
        market_label = response.get("market") or ""
        await _notify_ops_role(settings.vet_notify_phone, "vet", traded_ref, head_count, market_label)
        await _notify_ops_role(settings.freight_notify_phone, "freight", traded_ref, head_count, market_label)

        head = response.get("head") or "?"
        market = str(response.get("market") or "").lower().replace("_", " ") or "pigs"
        seller = response.get("seller_company") or "seller"
        buyer = response.get("buyer_company") or "buyer"
        traded = response.get("traded_order_id") or sell_id
        summary = f"Done. {seller} -> {buyer}, {head} {market}. Deal {traded} is TRADED."
        return {"result": summary, **response}
