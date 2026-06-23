"""Tool: broker records a post-grading price adjustment negotiated with the buyer.

Used when the broker replies to a grading-variance heads-up ("all good, negotiated down to
48"). Updates the buyer-facing settlement price on the deal, logs a note, and confirms the
new price to the buyer. The order is resolved from the deal the bot last flagged.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.notifications import send_sms_notification
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)


def _parse_price(raw: Any) -> float | None:
    text = str(raw or "").replace("$", "").replace(",", "").strip()
    match = re.search(r"\d+(\.\d+)?", text)
    return float(match.group()) if match else None


class RecordGradingAdjustment(Tool, name="record_grading_adjustment"):
    TOOL_PATH = "/tools/grading/record_grading_adjustment"
    DESCRIPTION = (
        "Broker-only. Record the settlement price the broker negotiated with the buyer after a "
        "grading variance ('all good, negotiated down to 48'). Updates the buyer-facing price on "
        "the deal and logs a note. Uses the deal the bot last flagged for grading if no order id "
        "is given."
    )
    ARGUMENTS = {
        "price": Arg("The settled per-head price the broker agreed with the buyer."),
        "reason": Arg("Short note on the adjustment, e.g. the write-off issue.", optional=True),
        "order_id": Arg(
            "Order short id. Omit to use the deal the bot last flagged for grading.", optional=True
        ),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        order_id = str(arguments.get("order_id", "")).strip()
        if not order_id:
            refs = list(getattr(state, "referenced_order_ids", []) or [])
            order_id = refs[-1] if refs else ""
        if not order_id:
            return {"error": "Which deal is this adjustment for? Give me the order id."}

        price = _parse_price(arguments.get("price"))
        if price is None:
            return {"error": "What price did you settle on?", "order_id": order_id}

        backend = get_backend_client()
        resp = await backend.record_grading_adjustment(
            order_id, price, str(arguments.get("reason", "")).strip()
        )
        if not resp.get("success"):
            return {"error": resp.get("error", "Couldn't record the adjustment."), "order_id": order_id}

        if getattr(state, "active_workflow", None) == "awaiting_grading_adjustment":
            state.active_workflow = None

        # Confirm the new price to the buyer (counterparty-safe: only the price + ELM Pork).
        buyer_phone = resp.get("buyer_phone")
        if buyer_phone:
            buyer_first = resp.get("buyer_first_name") or ""
            greeting = f"Hi {buyer_first}, ELM Pork." if buyer_first else "ELM Pork here."
            try:
                await send_sms_notification(
                    buyer_phone,
                    f"{greeting} All squared on deal {order_id}, we've got you at ${price:g} a head after "
                    "grading. Thanks for working it through.",
                )
            except Exception:
                logger.exception("Failed to send grading-adjustment confirmation for %s", order_id)

        return {
            "result": (
                f"Done, recorded ${price:g} a head on {order_id} and let the buyer know. "
                "The note's on the deal."
            ),
            "order_id": order_id,
            **resp,
        }
