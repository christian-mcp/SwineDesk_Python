"""Tool: broker-only - pair an open buy request with an open sell listing."""

from __future__ import annotations

import logging
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.hellosign import send_deal_confirmation
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
            "ELM Pork will reach out today with shipping details."
        )
    else:
        msg = (
            f"{greeting}your buy request {order_id} is matched. "
            "ELM Pork will reach out today with delivery details."
        )
    try:
        await send_sms_notification(phone, msg)
    except Exception:
        logger.exception("Failed to notify %s about match", phone)


def _normalize_regrade(raw: str) -> str:
    """Coerce a broker-chosen regrade term to a display value. Empty means unset.
    'none' is an explicit broker choice of no regrade, distinct from unset."""
    text = (raw or "").strip()
    if not text:
        return ""
    if text.lower() in ("none", "no", "no regrade", "n/a"):
        return "No regrade"
    return text


def _deal_terms_from_response(response: dict) -> dict[str, Any]:
    """Best-effort deal-term fields for the confirmation email; missing keys are omitted.
    (Backend payload keys may need adjustment once confirmed.)"""
    def first(*keys: str) -> str:
        for k in keys:
            v = response.get(k)
            if v not in (None, "", []):
                return str(v)
        return ""

    return {
        "deal_date": first("deal_date", "traded_at", "date"),
        "price": first("price_per_head", "price", "price_target"),
        "loads": first("loads", "load_count", "num_loads"),
        "health": first("health", "health_status"),
        "vaccine": first("vaccine", "vaccines"),
        "weight_slide": first("weight_slide"),
        "regrade": first("regrade", "re_grade"),
        "source_farms": first("source_farms", "source_farm", "source_site"),
    }


async def _send_deal_confirmation_emails(response: dict, regrade: str = "") -> None:
    order_id = response.get("traded_order_id", "")
    head = response.get("head") or "?"
    market = str(response.get("market") or "").lower().replace("_", " ") or "pigs"
    terms = {k: v for k, v in _deal_terms_from_response(response).items() if v}
    # The broker's confirmed regrade term is authoritative over anything on the orders.
    if regrade:
        terms["regrade"] = regrade
    else:
        terms.pop("regrade", None)
    result = await send_deal_confirmation(
        order_id=order_id,
        head=head,
        market=market,
        seller_name=response.get("seller_first_name") or response.get("seller_company") or "Seller",
        seller_email=response.get("seller_email") or "",
        buyer_name=response.get("buyer_first_name") or response.get("buyer_company") or "Buyer",
        buyer_email=response.get("buyer_email") or "",
        seller_company=response.get("seller_company") or "",
        seller_phone=response.get("seller_phone") or "",
        buyer_company=response.get("buyer_company") or "",
        buyer_phone=response.get("buyer_phone") or "",
        terms=terms,
    )
    if not result.get("success"):
        logger.warning("Deal confirmation HelloSign failed for %s: %s", order_id, result.get("error"))


async def _notify_ops_role(phone: str, role: str, order_id: str, head: object, market: str) -> None:
    """Heads-up SMS to the vet or freight default phone when a new deal pairs."""
    if not phone:
        return
    pretty_market = market.lower().replace("_", " ") if market else "pigs"
    if role == "vet":
        msg = (
            f"Heads up from ELM Pork. New deal {order_id} just paired, "
            f"{head} {pretty_market}. A health cert will be needed in the next couple weeks. "
            "We'll text the load ID once it's scheduled."
        )
    else:
        msg = (
            f"Heads up from ELM Pork. New deal {order_id} just paired, "
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
        "regrade": Arg(
            "Broker-confirmed buyer regrade term for this deal: 'none', '4 weeks', "
            "'8 weeks', or custom text. Omit if the broker did not set one.",
            optional=True,
        ),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        buy_id = str(arguments.get("buy_order_id", "")).strip()
        sell_id = str(arguments.get("sell_order_id", "")).strip()
        if not buy_id or not sell_id:
            return {"error": "Need both a buy_order_id and a sell_order_id."}

        regrade = _normalize_regrade(str(arguments.get("regrade", "")))

        # TODO: persist the broker-confirmed regrade term on the matched deal in the
        # backend. The Regrade enum (NOT_REQUIRED/FOUR_WEEKS/FIVE_WEEKS/SIX_WEEKS) has no
        # 8-weeks or custom slot, and /v1/query/match-orders currently ignores the regrade
        # field. For now regrade lives in the chat flow only: it is sent on the payload
        # (forward-compatible, ignored by the backend today) and surfaced on the deal
        # confirmation email + broker summary below.
        backend = get_backend_client()
        response = await backend.match_orders(buy_id, sell_id, regrade=regrade)

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

        await _send_deal_confirmation_emails(response, regrade=regrade)

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
        regrade_note = f" Regrade: {regrade}." if regrade else " Regrade: not set."
        summary = f"Done. {seller} -> {buyer}, {head} {market}. Deal {traded} is TRADED.{regrade_note}"
        return {"result": summary, **response}
