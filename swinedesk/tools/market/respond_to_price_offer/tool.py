"""Tool: a seller/buyer responds to a broker price offer (accept / counter / decline)."""

from __future__ import annotations

import re
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.negotiations import get_pending_offer_for_phone, resolve_offer
from swinedesk.notifications import send_sms_notification
from swinedesk.tooling import Arg, Tool


def _parse_price(raw: Any) -> float | None:
    text = str(raw or "").strip().replace("$", "").replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


class RespondToPriceOffer(Tool, name="respond_to_price_offer"):
    TOOL_PATH = "/tools/market/respond_to_price_offer"
    DESCRIPTION = (
        "Record this user's answer to a price ELM offered them (shown as pending_offer in "
        "session context). decision='accept' updates their order price and confirms the "
        "deal; 'counter' relays a new number to the team without changing the price; "
        "'decline' passes. Only call this when the user is responding to that offer."
    )
    ARGUMENTS = {
        "decision": Arg("One of: accept, counter, decline"),
        "counter_price": Arg("The price they'll do instead (required for counter)", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        phone = str(getattr(state, "phone", "") or "")
        offer = await get_pending_offer_for_phone(phone)
        if offer is None:
            return {"error": "No pending price offer for this user."}

        decision = str(arguments.get("decision", "")).strip().lower()
        order_id = offer["order_id"]
        proposed = offer["proposed_price"]
        broker_phone = offer.get("broker_phone", "")
        price_txt = f"${proposed:g}"

        async def notify_broker(text: str) -> None:
            if broker_phone:
                await send_sms_notification(broker_phone, text)

        if decision == "accept":
            backend = get_backend_client()
            update = await backend.update_order_price(order_id, proposed, offer.get("side", ""))
            if not update.get("success", False):
                # Don't tell the user it's locked if the price didn't actually change.
                return {
                    "error": (
                        f"Couldn't update the price on {order_id}: "
                        f"{update.get('error', 'backend error')}. Tell them you'll confirm shortly."
                    )
                }
            await resolve_offer(offer["id"], "accepted")
            await notify_broker(
                f"{phone} accepted {price_txt} on ref {order_id}. Price updated."
            )
            return {"result": f"Confirmed at {price_txt} on ref {order_id}. Tell them it's locked in."}

        if decision == "counter":
            counter = _parse_price(arguments.get("counter_price"))
            if counter is None:
                return {"error": "What price are they countering with?"}
            await resolve_offer(offer["id"], "countered", counter_price=counter)
            await notify_broker(
                f"{phone} countered at ${counter:g} on ref {order_id} (you offered {price_txt}). "
                "Price not changed."
            )
            return {
                "result": (
                    f"Passed their counter of ${counter:g} to the team. Tell them ELM will "
                    "be in touch, nothing is locked yet."
                )
            }

        if decision == "decline":
            await resolve_offer(offer["id"], "declined")
            await notify_broker(f"{phone} passed on {price_txt} for ref {order_id}.")
            return {"result": "Noted as a pass. Thank them and let them know the team has it."}

        return {"error": "decision must be one of: accept, counter, decline."}
