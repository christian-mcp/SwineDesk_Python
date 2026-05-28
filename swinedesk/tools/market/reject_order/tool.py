"""Tool: broker-only - soft-reject an open SMS-submitted order and notify the submitter."""

from __future__ import annotations

import logging
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.notifications import send_sms_notification
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)


class RejectOrder(Tool, name="reject_order"):
    TOOL_PATH = "/tools/market/reject_order"
    DESCRIPTION = (
        "Broker-only. Reject an open SMS-submitted listing or request. Soft-deletes the "
        "order, marks it REJECTED, and texts the submitter. Use when the broker says "
        "'kill that one', 'reject 859253', 'drop 771806', or similar. Optional reason "
        "is included in the message to the submitter."
    )
    ARGUMENTS = {
        "order_id": Arg("Short id of the order to reject"),
        "reason": Arg("Short reason shared with the submitter", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        order_id = str(arguments.get("order_id", "")).strip()
        reason = str(arguments.get("reason", "")).strip()
        if not order_id:
            return {"error": "Need an order_id to reject."}

        backend = get_backend_client()
        response = await backend.reject_order(order_id, reason)

        if not response.get("success"):
            return {"error": response.get("error", "Reject failed."), "order_id": order_id}

        submitter_phone = response.get("submitter_phone")
        submitter_name = (response.get("submitter_first_name") or "").strip()
        side = response.get("side", "")
        label = "sell listing" if side == "SELL" else "buy request"
        if submitter_phone:
            greeting = f"Hey {submitter_name}, " if submitter_name else ""
            tail = f" Reason: {reason}." if reason else ""
            msg = (
                f"{greeting}your {label} {order_id} was passed on for now.{tail} "
                "Reach out if you want to talk it through."
            )
            try:
                await send_sms_notification(submitter_phone, msg)
            except Exception:
                logger.exception("Failed to notify submitter %s about reject", submitter_phone)

        summary = f"Rejected {order_id} ({label}). Submitter notified."
        return {"result": summary, **response}
