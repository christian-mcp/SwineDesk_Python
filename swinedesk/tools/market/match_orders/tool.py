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


def _is_yes(raw: Any) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _looks_like_phone(raw: str) -> bool:
    digits = "".join(ch for ch in raw if ch.isdigit())
    return raw.strip().startswith("+") or (digits and len(digits) >= 7 and digits == raw.strip().lstrip("+").replace("-", "").replace(" ", "").replace("(", "").replace(")", ""))


_HONORIFICS = {"dr", "dr.", "doctor", "mr", "mr.", "ms", "ms.", "mrs", "mrs."}


def _name_tokens(text: str) -> set[str]:
    """Lowercase word tokens with punctuation and honorifics stripped."""
    cleaned = text.lower().replace(".", " ").replace(",", " ")
    return {tok for tok in cleaned.split() if tok and tok not in _HONORIFICS}


async def _resolve_contact(backend: Any, raw: Any, role: str) -> tuple[str | None, str | None]:
    """Resolve a free-form 'name or phone' to (actor_id, phone) via the backend phonebook
    for the given role. Returns (None, phone) when the input is itself a phone, and
    (None, None) when nothing matches (caller then surfaces a 'couldn't find' note).

    Matching is token-overlap on the contact's first name (the contacts endpoint exposes
    first_name + company but no last name) or company, with honorifics ignored - so
    'Dr Ana Reyes' matches a contact whose first name is stored as 'Dr Ana'."""
    text = str(raw or "").strip()
    if not text:
        return None, None
    if _looks_like_phone(text):
        return None, text
    want = _name_tokens(text)
    try:
        data = await backend.list_contacts(role=role)
    except Exception:
        logger.exception("Failed to look up %s contacts for '%s'", role, text)
        return None, None
    contacts = data.get("contacts", []) if isinstance(data, dict) else []
    for contact in contacts:
        first_tokens = _name_tokens(str(contact.get("first_name") or ""))
        company = str(contact.get("company") or "").strip().lower()
        if want and first_tokens and (want & first_tokens):
            return contact.get("actor_id"), contact.get("phone")
        if company and (company in text.lower() or any(tok in company for tok in want)):
            return contact.get("actor_id"), contact.get("phone")
    return None, None


async def _send_buyer_addons(phone: str | None, first_name: str | None) -> bool:
    """Text the buyer the optional add-on questions after a deal is paired.
    Returns True if the SMS was sent. Asked by the broker at deal time, never at intake."""
    if not phone:
        return False
    name = (first_name or "").strip()
    greeting = f"Hi {name}, " if name else "Hi, "
    msg = (
        f"{greeting}your deal's set with ELM Pork. A few optional extras we can bundle in: "
        "(1) Do you need barn space? "
        "(2) Want us to line up a feed contract at competitive rates? "
        "(3) Want a packer contract? We can shop our national packer network for you. "
        "Just reply with whichever you'd like, or 'no thanks'."
    )
    try:
        await send_sms_notification(phone, msg)
        return True
    except Exception:
        logger.exception("Failed to send add-on questions to buyer %s", phone)
        return False


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
        "send_buyer_addons": Arg(
            "Set to true ONLY if the broker confirmed they want the buyer texted the "
            "optional add-on questions (barn space, feed contract, packer contract). "
            "Omit otherwise.",
            optional=True,
        ),
        "vet_to_vet_needed": Arg(
            "Whether a vet-to-vet health handoff is needed for this deal (the broker is "
            "asked this when confirming). 'yes' runs vet-to-vet (requires both vets below); "
            "'no' skips it. Omit if the broker has not answered yet.",
            optional=True,
        ),
        "seller_vet": Arg(
            "The seller's vet for this deal - a name (e.g. 'Dr Ana Reyes') or phone number. "
            "Required to run vet-to-vet. Omit otherwise.",
            optional=True,
        ),
        "buyer_vet": Arg(
            "The buyer's vet for this deal - a name or phone number. Required to run "
            "vet-to-vet. Omit otherwise.",
            optional=True,
        ),
        "scheduled_date": Arg(
            "Ship/delivery date for the load, as YYYY-MM-DD. Defaults to the sell listing's "
            "ship date if omitted.",
            optional=True,
        ),
        "freight_company": Arg(
            "Optional freight company for the load - a name or phone. Usually left as TBD at "
            "confirm and assigned a few days before the load; omit unless the broker names it now.",
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

        # Build the deal-confirmation extras: the two vets (only when vet-to-vet is
        # explicitly wanted), the ship date, and an optional freight company. These let
        # the backend create the load and run the vet-to-vet cascade. When the broker
        # hasn't answered the vet-to-vet question we send nothing, so the deal flows
        # exactly as before (vet-to-vet auto-skips).
        extras: dict[str, Any] = {}
        needed_raw = arguments.get("vet_to_vet_needed")
        vet_to_vet_needed = _is_yes(needed_raw) if str(needed_raw or "").strip() else None
        if vet_to_vet_needed:
            sv_guid, sv_phone = await _resolve_contact(backend, arguments.get("seller_vet"), "vet")
            bv_guid, bv_phone = await _resolve_contact(backend, arguments.get("buyer_vet"), "vet")
            if sv_guid:
                extras["seller_vet_guid"] = sv_guid
            elif sv_phone:
                extras["seller_vet_phone"] = sv_phone
            if bv_guid:
                extras["buyer_vet_guid"] = bv_guid
            elif bv_phone:
                extras["buyer_vet_phone"] = bv_phone
            extras["is_vet_to_vet_skipped"] = False
        elif vet_to_vet_needed is False:
            extras["is_vet_to_vet_skipped"] = True

        scheduled_date = str(arguments.get("scheduled_date", "")).strip()
        if scheduled_date:
            extras["scheduled_date"] = scheduled_date

        freight_raw = str(arguments.get("freight_company", "")).strip()
        if freight_raw:
            fg, fp = await _resolve_contact(backend, freight_raw, "freight")
            if fg:
                extras["freight_guid"] = fg
            elif fp:
                extras["freight_phone"] = fp

        response = await backend.match_orders(buy_id, sell_id, regrade=regrade, extras=extras)

        if not response.get("success"):
            return {
                "error": response.get("error", "Match failed."),
                "buy_order_id": buy_id,
                "sell_order_id": sell_id,
            }

        # The backend reports whether the vet-to-vet handoff is actually running for this
        # deal (both vets resolved + not skipped). When it is, hold the broker-confirmation
        # note until the buyer's vet confirms (handled by the vet-to-vet flow); otherwise
        # send it now as before.
        vet_to_vet_running = response.get("vet_to_vet_skipped") is False

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

        # Hold the broker-confirmation PDF/email until vet-to-vet completes. When there's
        # no vet-to-vet (skipped), the deal is done now, so send it immediately as before.
        if not vet_to_vet_running:
            await _send_deal_confirmation_emails(response, regrade=regrade)

        traded_ref = response.get("traded_order_id") or sell_id
        head_count = response.get("head") or "?"
        market_label = response.get("market") or ""
        # When vet-to-vet is running, the backend already texts the two named vets the
        # proper handoff messages, so skip the generic vet heads-up to avoid double-texting.
        if not vet_to_vet_running:
            await _notify_ops_role(settings.vet_notify_phone, "vet", traded_ref, head_count, market_label)
        await _notify_ops_role(settings.freight_notify_phone, "freight", traded_ref, head_count, market_label)

        # Optional: text the buyer the add-on questions, but only if the broker confirmed it
        # at deal time (mirrors how regrade is a broker-set, deal-time choice — not intake).
        addons_sent = False
        if _is_yes(arguments.get("send_buyer_addons")):
            addons_sent = await _send_buyer_addons(
                response.get("buyer_phone"), response.get("buyer_first_name")
            )

        head = response.get("head") or "?"
        market = str(response.get("market") or "").lower().replace("_", " ") or "pigs"
        seller = response.get("seller_company") or "seller"
        buyer = response.get("buyer_company") or "buyer"
        traded = response.get("traded_order_id") or sell_id
        regrade_note = f" Regrade: {regrade}." if regrade else " Regrade: not set."

        # Broker economics: show the expected profit (spread x head) on the match.
        profit = response.get("expected_profit")
        margin = response.get("margin_per_head")
        if profit is not None and margin is not None:
            profit_note = f" Expected profit: ${profit:,.0f} (${margin:,.2f}/head)."
        else:
            profit_note = " Expected profit: n/a (a price is missing on one side)."

        addon_note = " Add-on options texted to the buyer." if addons_sent else ""

        # Vet-to-vet status for the broker.
        if vet_to_vet_running:
            sv = response.get("seller_vet") or "the seller's vet"
            bv = response.get("buyer_vet") or "the buyer's vet"
            vet_note = (
                f" Vet-to-vet kicked off: texted {sv} (seller's vet) and {bv} (buyer's vet). "
                "I'll send the broker note to all parties once the buyer's vet confirms."
            )
        elif response.get("vet_to_vet_skipped") is True and vet_to_vet_needed is False:
            vet_note = " Vet-to-vet skipped; broker note sent."
        else:
            vet_note = ""
        if response.get("vet_warning"):
            vet_note += f" NOTE: {response.get('vet_warning')}"

        # Load schedule for the deal.
        if response.get("load_id"):
            sched = response.get("load_scheduled")
            sched_day = sched.split("T")[0] if isinstance(sched, str) and "T" in sched else sched
            load_note = f" Load {response.get('load_id')} scheduled for {sched_day}." if sched_day else f" Load {response.get('load_id')} created."
        else:
            load_note = ""
        if response.get("load_warning"):
            load_note += f" NOTE: {response.get('load_warning')}"

        summary = (
            f"Done. {seller} -> {buyer}, {head} {market}. "
            f"Deal {traded} is confirmed.{profit_note}{regrade_note}{vet_note}{load_note}{addon_note}"
        )
        return {"result": summary, "buyer_addons_sent": addons_sent, **response}
