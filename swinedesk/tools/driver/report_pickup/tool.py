"""Tool: driver reports pickup + ETA; notifies buyer, barn manager, and broker.

Counterparty privacy is preserved: the buyer's barn is told only the origin STATE plus the
driver's details and ETA, never the seller's name or company. The broker is internal and is
always updated, with the on-track / flagged status and (for the broker only) both companies.
"""

from __future__ import annotations

import logging
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.notifications import send_sms_notification
from swinedesk.tool_helpers import ensure_role, remember_load
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)


def _is_true(raw: Any) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "y", "on")


async def _safe_sms(phone: str | None, message: str) -> None:
    if not phone:
        return
    try:
        await send_sms_notification(phone, message)
    except Exception:
        logger.exception("Failed to send driver-pickup SMS to %s", phone)


def _eta_phrase(arguments: dict[str, Any]) -> str:
    """Human ETA from the driver's words or hour count."""
    text = str(arguments.get("eta_text") or "").strip()
    if text:
        return text
    hours = arguments.get("eta_hours")
    if hours not in (None, ""):
        return f"about {hours} hrs out"
    return ""


def _driver_bits(resp: dict[str, Any]) -> str:
    bits = f"Driver {resp.get('driver_name') or 'your driver'}."
    if resp.get("plate"):
        bits += f" Plate {resp.get('plate')}."
    if resp.get("driver_phone"):
        bits += f" Cell {resp.get('driver_phone')}."
    return bits


class ReportPickup(Tool, name="report_pickup"):
    TOOL_PATH = "/tools/driver/report_pickup"
    DESCRIPTION = (
        "Driver-only. Record that the driver has loaded / picked up and is en route, with an ETA. "
        "Texts the buyer's barn (origin state + driver + ETA, never the seller's name) and the barn "
        "manager, and always updates the broker. The load is resolved from the driver automatically."
    )
    ARGUMENTS = {
        "eta_hours": Arg("Hours until arrival, if the driver gave a number.", optional=True),
        "eta_text": Arg("The driver's ETA in their own words, if not a clean hour count.", optional=True),
        "delayed": Arg(
            "true if the driver mentioned a delay or problem (late, breakdown, weather, reroute).",
            optional=True,
        ),
        "note": Arg("Short note on any problem the driver mentioned.", optional=True),
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
        resp = await backend.driver_pickup(
            phone,
            {
                "load_id": str(arguments.get("load_id", "")).strip(),
                "eta_hours": arguments.get("eta_hours"),
                "eta_text": arguments.get("eta_text"),
                "note": arguments.get("note"),
            },
        )
        if not resp.get("success"):
            return {"error": resp.get("error", "Couldn't record the pickup. Which load is this for?")}

        load_id = resp.get("load_id", "")
        remember_load(state, load_id)

        head = resp.get("head") or "?"
        market = str(resp.get("market") or "").lower().replace("_", " ") or "pigs"
        origin_state = resp.get("origin_state") or "the farm"
        eta = _eta_phrase(arguments)

        # --- Buyer-facing: origin STATE only, never the seller's identity ---
        buyer_first = resp.get("buyer_first_name") or ""
        greeting = f"Hi {buyer_first}, ELM Pork." if buyer_first else "ELM Pork here."
        buyer_parts = [f"{greeting} Your {head} {market} are loaded and on the way from {origin_state}."]
        if eta:
            buyer_parts.append(f"ETA {eta}.")
        buyer_parts.append(_driver_bits(resp))
        await _safe_sms(resp.get("buyer_phone"), " ".join(buyer_parts))

        # --- Barn manager (destination site contact), if distinct from the buyer ---
        barn_phone = resp.get("barn_manager_phone")
        if barn_phone and barn_phone != resp.get("buyer_phone"):
            barn_name = resp.get("barn_manager_name") or ""
            barn_greeting = f"Hi {barn_name}, ELM Pork." if barn_name else "ELM Pork here."
            barn_parts = [f"{barn_greeting} A load of {head} {market} is inbound to your barn."]
            if eta:
                barn_parts.append(f"ETA {eta}.")
            barn_parts.append(_driver_bits(resp))
            await _safe_sms(barn_phone, " ".join(barn_parts))

        # --- Broker: always updated, on-track or flagged (internal, may name both sides) ---
        delayed = _is_true(arguments.get("delayed"))
        note = str(arguments.get("note") or "").strip()
        seller = resp.get("seller_company") or "seller"
        buyer = resp.get("buyer_company") or "buyer"
        eta_tail = f" ETA {eta}." if eta else ""
        if delayed:
            flag = f"FLAGGED: {note}" if note else "FLAGGED a delay/problem"
            broker_msg = (
                f"Heads up, load {load_id} ({head} {market}, {seller} to {buyer}) picked up but the "
                f"driver {flag}.{eta_tail} May want to check in."
            )
        else:
            broker_msg = f"Load {load_id} ({head} {market}, {seller} to {buyer}) picked up, on track.{eta_tail}"
        await _safe_sms(resp.get("broker_phone"), broker_msg)

        ack_eta = eta or "on the way"
        return {
            "result": (
                f"Got it, thanks. I've let the barn know you're {ack_eta} and updated the desk. "
                "Text me when you've offloaded."
            ),
            "load_id": load_id,
            "notified_buyer": bool(resp.get("buyer_phone")),
            "notified_barn": bool(barn_phone and barn_phone != resp.get("buyer_phone")),
            "notified_broker": bool(resp.get("broker_phone")),
            "delayed": delayed,
        }
