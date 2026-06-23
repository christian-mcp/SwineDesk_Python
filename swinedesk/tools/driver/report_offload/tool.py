"""Tool: driver reports the load offloaded; confirm to broker + prompt buyer to grade.

On offload, the broker gets a delivered confirmation and the buyer is prompted to text in
grading numbers as the pigs come off the truck. The buyer's session is seeded with
active_workflow=awaiting_grading so their reply routes straight into grading (Phase 5).
"""

from __future__ import annotations

import logging
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.notifications import send_sms_notification
from swinedesk.session import update_session
from swinedesk.tool_helpers import ensure_role, remember_load
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)


async def _safe_sms(phone: str | None, message: str) -> None:
    if not phone:
        return
    try:
        await send_sms_notification(phone, message)
    except Exception:
        logger.exception("Failed to send driver-offload SMS to %s", phone)


class ReportOffload(Tool, name="report_offload"):
    TOOL_PATH = "/tools/driver/report_offload"
    DESCRIPTION = (
        "Driver-only. Record that the load has been offloaded / delivered. Confirms delivery to the "
        "broker and prompts the buyer to text grading numbers as the pigs come off. The load is "
        "resolved from the driver automatically."
    )
    ARGUMENTS = {
        "load_id": Arg("Load id. Omit to use the driver's current load.", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"driver"})
        if role_error:
            return role_error

        phone = str(getattr(state, "phone", "") or "").strip()
        if not phone:
            return {"error": "I can't tell which driver this is from your number."}

        backend = get_backend_client()
        resp = await backend.driver_offload(phone, {"load_id": str(arguments.get("load_id", "")).strip()})
        if not resp.get("success"):
            return {"error": resp.get("error", "Couldn't record the offload. Which load is this for?")}

        load_id = resp.get("load_id", "")
        order_id = resp.get("order_id", "")
        remember_load(state, load_id)

        head = resp.get("head") or "?"
        market = str(resp.get("market") or "").lower().replace("_", " ") or "pigs"
        seller = resp.get("seller_company") or "seller"
        buyer = resp.get("buyer_company") or "buyer"
        drop = resp.get("dropoff_site")
        drop_clause = f" at {drop}" if drop else ""

        # --- Broker confirmation (internal) ---
        await _safe_sms(
            resp.get("broker_phone"),
            f"Load {load_id} ({head} {market}, {seller} to {buyer}) offloaded{drop_clause}. Delivered.",
        )

        # --- Prompt the buyer to grade in real time + seed their grading session ---
        buyer_phone = resp.get("buyer_phone")
        buyer_first = resp.get("buyer_first_name") or ""
        greeting = f"Hi {buyer_first}, ELM Pork." if buyer_first else "ELM Pork here."
        await _safe_sms(
            buyer_phone,
            f"{greeting} Your {head} {market} are coming off the truck now. Text me each lot's count and "
            "any culls or write-offs as they come off and I'll log the grading live.",
        )

        # An FYI to a distinct barn manager (the grading session still routes through the buyer).
        barn_phone = resp.get("barn_manager_phone")
        if barn_phone and barn_phone != buyer_phone:
            await _safe_sms(
                barn_phone,
                f"ELM Pork here. The {head} {market} are offloading at your barn now.",
            )

        if buyer_phone:
            try:
                seed: dict[str, Any] = {"active_workflow": "awaiting_grading"}
                if load_id:
                    seed["referenced_load_ids"] = [str(load_id)]
                if order_id:
                    seed["referenced_order_ids"] = [str(order_id)]
                await update_session(buyer_phone, seed)
            except Exception:
                logger.exception("Failed to seed buyer grading session for %s", buyer_phone)

        return {
            "result": "Thanks, marked as delivered and let the desk know. Drive safe.",
            "load_id": load_id,
            "broker_confirmed": bool(resp.get("broker_phone")),
            "buyer_grading_prompted": bool(buyer_phone),
        }
