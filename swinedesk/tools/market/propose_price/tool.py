"""Tool: broker asks a seller/buyer whether they'll accept a price."""

from __future__ import annotations

import re
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.negotiations import create_offer
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


def _parse_price(raw: Any) -> float | None:
    text = str(raw or "").strip().replace("$", "").replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


class ProposePrice(Tool, name="propose_price"):
    TOOL_PATH = "/tools/market/propose_price"
    DESCRIPTION = (
        "Broker-only. Ask a seller or buyer whether they'll accept a specific price on "
        "their order. Texts the question to them; the price is updated automatically ONLY "
        "if they accept. Use when Brian says 'ask JP if he'd take 85', 'see if Hector will "
        "do 60', 'offer the Iowa seller 88'. Resolve the person and their order first "
        "(get_open_market gives order_id, phone, side, and current target_price)."
    )
    ARGUMENTS = {
        "to_phone": Arg("Phone number of the seller/buyer to ask"),
        "order_id": Arg("Short order ID the price applies to"),
        "proposed_price": Arg("The price per head to propose"),
        "side": Arg("SELL or BUY (from the open-market row)", optional=True),
        "current_price": Arg("Their current target price, for context in the text", optional=True),
        "label": Arg("Short description of the pigs, e.g. '2,400 feeder pigs'", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        to_phone = str(arguments.get("to_phone", "")).strip()
        order_id = str(arguments.get("order_id", "")).strip()
        proposed = _parse_price(arguments.get("proposed_price"))
        if not to_phone:
            return {"error": "Recipient phone number is required."}
        if not order_id:
            return {"error": "Order ID is required to propose a price."}
        if proposed is None:
            return {"error": "A numeric price is required."}

        current = _parse_price(arguments.get("current_price"))
        label = str(arguments.get("label", "")).strip()
        broker_phone = str(getattr(state, "phone", "") or "")

        offer = await create_offer(
            to_phone=to_phone,
            order_id=order_id,
            proposed_price=proposed,
            broker_phone=broker_phone,
            side=str(arguments.get("side", "")),
            current_price=current,
            label=label,
        )

        price_txt = f"${proposed:g}"
        pigs = label or f"order {order_id}"
        question = (
            f"ELM Pork here. Would you take {price_txt}/hd on your {pigs} (ref {order_id})? "
            "Reply YES to lock it in, or reply with a number to counter."
        )

        backend = get_backend_client()
        send_result = await backend.send_message_to_user(to_phone, question)
        if isinstance(send_result, dict) and send_result.get("error"):
            return {"error": f"Couldn't text {to_phone}: {send_result['error']}"}

        return {
            "result": (
                f"Asked {to_phone} whether they'll take {price_txt} on ref {order_id}. "
                "I'll text you their answer."
            ),
            "offer_id": offer["id"],
        }
