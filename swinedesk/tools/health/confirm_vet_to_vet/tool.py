"""Tool: buyer's vet confirms (or defers) the vet-to-vet handoff over SMS.

When the buyer's vet replies that the vet-to-vet went well, this clears the deal's vet
gate in the backend and sends the broker-confirmation note (PDF + email) to both parties,
plus an SMS heads-up to seller, buyer, and broker. When the vet wants the broker to call
first, it flags the deal and pings the broker instead. Counterparty privacy is preserved:
each party's SMS names only ELM Pork, never the other side.
"""

from __future__ import annotations

import logging
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.hellosign import send_deal_confirmation
from swinedesk.notifications import send_sms_notification
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)


def _is_yes(raw: Any) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "y", "on", "good", "all good", "confirm", "confirmed")


def _resolve_order_id(arguments: dict[str, Any], state: Any) -> str:
    """Prefer an explicit order id; otherwise use the one seeded on the session when the
    backend texted this vet (referenced_order_ids), most recent last."""
    explicit = str(arguments.get("order_id", "")).strip()
    if explicit:
        return explicit
    refs = list(getattr(state, "referenced_order_ids", []) or [])
    return refs[-1] if refs else ""


async def _send_broker_note(resp: dict[str, Any]) -> None:
    """Generate + email the deal-confirmation note to both parties (each gets the
    counterparty-masked version). Best-effort; logs on failure."""
    order_id = resp.get("order_id", "")
    head = resp.get("head") or "?"
    market = str(resp.get("market") or "").lower().replace("_", " ") or "pigs"
    try:
        result = await send_deal_confirmation(
            order_id=order_id,
            head=head,
            market=market,
            seller_name=resp.get("seller_first_name") or resp.get("seller_company") or "Seller",
            seller_email=resp.get("seller_email") or "",
            buyer_name=resp.get("buyer_first_name") or resp.get("buyer_company") or "Buyer",
            buyer_email=resp.get("buyer_email") or "",
            seller_company=resp.get("seller_company") or "",
            seller_phone=resp.get("seller_phone") or "",
            buyer_company=resp.get("buyer_company") or "",
            buyer_phone=resp.get("buyer_phone") or "",
            terms={},
        )
        if not result.get("success"):
            logger.warning("Broker-note deal confirmation failed for %s: %s", order_id, result.get("error"))
    except Exception:
        logger.exception("Broker-note deal confirmation raised for %s", order_id)


async def _safe_sms(phone: str | None, message: str) -> None:
    if not phone:
        return
    try:
        await send_sms_notification(phone, message)
    except Exception:
        logger.exception("Failed to send vet-to-vet SMS to %s", phone)


class ConfirmVetToVet(Tool, name="confirm_vet_to_vet"):
    TOOL_PATH = "/tools/health/confirm_vet_to_vet"
    DESCRIPTION = (
        "Vet-only. Record the buyer's-vet decision on a vet-to-vet handoff the bot asked them "
        "to complete. Use when the vet replies that the vet-to-vet went well (confirm=true) or "
        "that the broker should call them first (confirm=false). On confirm, the deal clears its "
        "vet gate and the broker-confirmation note goes to all parties; on a call request, the "
        "broker is pinged to phone the vet."
    )
    ARGUMENTS = {
        "confirm": Arg(
            "true if the vet says the vet-to-vet is good / all set; false if they want the "
            "broker to call them before signing off."
        ),
        "reason": Arg(
            "Short note on what the vet wants to discuss, if they asked for a call. Omit otherwise.",
            optional=True,
        ),
        "order_id": Arg(
            "Order short id. Omit to use the deal the bot last texted this vet about.",
            optional=True,
        ),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"vet"})
        if role_error:
            return role_error

        order_id = _resolve_order_id(arguments, state)
        if not order_id:
            return {"error": "Which order is this about? I don't have a vet-to-vet pending for you right now."}

        backend = get_backend_client()
        confirm = _is_yes(arguments.get("confirm"))

        if confirm:
            resp = await backend.vet_confirm(order_id)
            if not resp.get("success"):
                return {"error": resp.get("error", "Couldn't record the confirmation."), "order_id": order_id}

            # The deal is cleared — send the broker-confirmation note + heads-up SMS to all
            # parties (counterparty-safe: ELM Pork is the only named party to seller/buyer).
            await _send_broker_note(resp)
            head = resp.get("head") or "?"
            market = str(resp.get("market") or "").lower().replace("_", " ") or "pigs"

            seller_first = resp.get("seller_first_name") or ""
            buyer_first = resp.get("buyer_first_name") or ""
            await _safe_sms(
                resp.get("seller_phone"),
                f"Hi {seller_first}, good news - the vet check on your deal {order_id} is all clear. "
                "ELM Pork is sending your broker confirmation now, by text and email. All good to go.",
            )
            await _safe_sms(
                resp.get("buyer_phone"),
                f"Hi {buyer_first}, good news - the vet check on your deal {order_id} is all clear. "
                "ELM Pork is sending your broker confirmation now, by text and email. All good to go.",
            )
            await _safe_sms(
                resp.get("broker_phone"),
                f"Vet-to-vet cleared on {order_id} ({head} {market}). "
                f"{resp.get('seller_company') or 'seller'} -> {resp.get('buyer_company') or 'buyer'}. "
                "Broker note sent to both parties by text and email.",
            )

            if getattr(state, "active_workflow", None) == "awaiting_vet_to_vet_confirm":
                state.active_workflow = None

            return {
                "result": "Thanks doc, appreciate you taking care of that. The broker note is "
                "going out to all parties now by text and email. You're all set.",
                "order_id": order_id,
                "broker_note_sent": True,
            }

        # Vet wants the broker to call before signing off.
        reason = str(arguments.get("reason", "")).strip()
        resp = await backend.vet_reject(order_id, reason)
        if not resp.get("success"):
            return {"error": resp.get("error", "Couldn't flag that for the broker."), "order_id": order_id}

        head = resp.get("head") or "?"
        market = str(resp.get("market") or "").lower().replace("_", " ") or "pigs"
        buyer_vet = resp.get("buyer_vet") or "the buyer's vet"
        reason_note = f' They flagged: "{reason}".' if reason else ""
        await _safe_sms(
            resp.get("broker_phone"),
            f"Heads up - {buyer_vet} on order {order_id} ({head} {market}) wants a call before "
            f"signing off on the vet-to-vet.{reason_note} Can you give them a ring?",
        )

        if getattr(state, "active_workflow", None) == "awaiting_vet_to_vet_confirm":
            state.active_workflow = None

        return {
            "result": "No problem, doc - I'll have the broker give you a call to talk it through. "
            "Thanks for the heads up.",
            "order_id": order_id,
            "broker_call_requested": True,
        }
