"""Tool: broker opens a Dutch-auction window on an order and notifies buyers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)


class OpenAuction(Tool, name="open_auction"):
    TOOL_PATH = "/tools/market/open_auction"
    DESCRIPTION = (
        "Broker-only. Open a Dutch-auction bidding window on an existing order and "
        "broadcast the opportunity to all matching buyers. "
        "Use when Brian says 'open an auction on <order>', 'take bids on <order>', "
        "'auction off <listing>', or 'let buyers bid on <order>'. "
        "Resolve the order_id first via get_open_market if needed."
    )
    ARGUMENTS = {
        "order_id": Arg("Short order ID (shortId) to put up for auction"),
        "duration_hours": Arg("How long the auction stays open, in hours (default 24)", optional=True),
        "state": Arg("Optional US state filter for buyer notification, e.g. IA, TX", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        order_id = str(arguments.get("order_id") or "").strip()
        if not order_id:
            return {"error": "order_id is required."}

        raw_hours = arguments.get("duration_hours")
        try:
            duration_hours = int(raw_hours) if raw_hours is not None else 24
        except (TypeError, ValueError):
            duration_hours = 24

        contact_state = str(arguments.get("state") or "").strip().upper() or None

        backend = get_backend_client()

        # Start the auction on the backend.
        auction_result = await backend.start_auction(order_id, duration_hours)
        if not auction_result.get("success"):
            return {
                "error": f"Backend could not start auction for order {order_id}.",
                "detail": auction_result,
            }

        auction_ends_at = auction_result.get("auction_ends_at", "")

        # Broadcast to buyers.
        contacts_response = await backend.list_contacts(role="buyer", state=contact_state)
        contacts = contacts_response.get("contacts", [])

        notice = (
            f"ELM Pork is opening an auction on order {order_id}. "
            f"Reply with your best bid price per head before the deadline"
            + (f" ({auction_ends_at})" if auction_ends_at else "")
            + "."
        )

        async def notify_one(contact: dict[str, Any]) -> dict[str, Any]:
            phone = str(contact.get("phone") or "").strip()
            if not phone:
                return {"skipped": True}
            try:
                await backend.send_message_to_user(phone, notice)
                return {"sent": True, "phone": phone}
            except Exception as exc:
                logger.warning("open_auction notify failed for %s: %s", phone, exc)
                return {"sent": False, "phone": phone, "error": str(exc)}

        results = await asyncio.gather(*[notify_one(c) for c in contacts])
        sent_count = sum(1 for r in results if r.get("sent"))
        failed_count = sum(1 for r in results if not r.get("sent") and not r.get("skipped"))

        summary = (
            f"Auction opened on order {order_id} for {duration_hours}h"
            + (f", closes {auction_ends_at}" if auction_ends_at else "")
            + f". Notified {sent_count} buyer{'s' if sent_count != 1 else ''}"
            + (f" in {contact_state}" if contact_state else "")
            + ("." if not failed_count else f"; {failed_count} notification(s) failed.")
        )

        return {
            "result": summary,
            "order_id": auction_result.get("order_id", order_id),
            "auction_ends_at": auction_ends_at,
            "buyers_notified": sent_count,
            "buyers_failed": failed_count,
        }
