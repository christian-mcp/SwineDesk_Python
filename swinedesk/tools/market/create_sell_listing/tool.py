"""Tool: create seller-side pig listing."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import (
    ensure_role,
    merge_workflow_draft,
    notify_broker_order_created,
    remember_request,
    require_actor_id,
    summarize_collection,
)
from swinedesk.tooling import Arg, Tool

_VALID_HEALTH = {"CLEAN", "PEDV", "PRRS"}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _normalize_health(raw: str) -> str:
    """Map free-text herd health to the backend enum (CLEAN/PEDV/PRRS)."""
    text = (raw or "").strip().lower()
    if not text:
        return "CLEAN"
    # A herd described as free of disease is CLEAN, even if it names PRRS/PEDV.
    if any(token in text for token in ("neg", "free", "clean", "naive", "healthy", "not ")):
        return "CLEAN"
    if "pedv" in text or "ped " in text or text == "ped":
        return "PEDV"
    if "prrs" in text:
        return "PRRS"
    upper = text.upper()
    return upper if upper in _VALID_HEALTH else "CLEAN"


def _normalize_ship_date(raw: str) -> str | None:
    """Coerce a ship date to ISO YYYY-MM-DD. Returns None if unparseable (field is optional)."""
    text = (raw or "").strip()
    if not text:
        return None
    if _ISO_DATE.match(text):
        return text
    today = datetime.now(timezone.utc).date()
    low = text.lower()
    # "in N days" / "N days from now" / "N days out"
    days_match = re.search(r"(\d+)\s*days?", low)
    if days_match and ("day" in low):
        return (today + timedelta(days=int(days_match.group(1)))).isoformat()
    weeks_match = re.search(r"(\d+)\s*weeks?", low)
    if weeks_match:
        return (today + timedelta(weeks=int(weeks_match.group(1)))).isoformat()
    if "next week" in low:
        return (today + timedelta(days=7)).isoformat()
    if "next month" in low:
        return (today + timedelta(days=30)).isoformat()
    if "tomorrow" in low:
        return (today + timedelta(days=1)).isoformat()
    for idx, day in enumerate(_WEEKDAYS):
        if day in low:
            delta = (idx - today.weekday()) % 7 or 7
            return (today + timedelta(days=delta)).isoformat()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d", "%b %d", "%B %d %Y", "%b %d %Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            if parsed.year == 1900:
                parsed = parsed.replace(year=today.year)
            return parsed.isoformat()
        except ValueError:
            continue
    return None


class CreateSellListing(Tool, name="create_sell_listing"):
    TOOL_PATH = "/tools/market/create_sell_listing"
    DESCRIPTION = "Create a seller-side listing for wean or feeder pigs."
    ARGUMENTS = {
        "market": Arg("WEAN_PIGS or FEEDER_PIGS"),
        "head_count": Arg("Total number of pigs to sell"),
        "health": Arg("Health status — must be one of: CLEAN, PEDV, PRRS"),
        "weight_range": Arg("Weight range", optional=True),
        "num_loads": Arg("Number of loads", optional=True),
        "first_ship_date": Arg("First available ship date", optional=True),
        "cadence": Arg("Weekly cadence or schedule notes", optional=True),
        "source_site": Arg("Known source site id or name", optional=True),
        "pid": Arg("Source premises ID", optional=True),
        "price_target": Arg("Target price per head", optional=True),
        "vaccines_done": Arg("Vaccines already administered", optional=True),
        "regrade": Arg("Regrade terms", optional=True),
        "notes": Arg("Additional notes", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"seller"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        arguments = dict(arguments)
        if "health" in arguments:
            arguments["health"] = _normalize_health(str(arguments.get("health", "")))
        if arguments.get("first_ship_date"):
            normalized_date = _normalize_ship_date(str(arguments["first_ship_date"]))
            if normalized_date:
                arguments["first_ship_date"] = normalized_date
            else:
                arguments.pop("first_ship_date", None)

        payload = {"actorId": actor_id, "phone": getattr(state, "phone", ""), **arguments}
        merge_workflow_draft(state, "seller_listing", arguments)
        backend = get_backend_client()
        response = await backend.create_sell_listing(payload)
        request_id = str(
            response.get("requestId")
            or response.get("orderId")
            or response.get("shortId")
            or response.get("guid")
            or ""
        )
        remember_request(state, request_id)
        await notify_broker_order_created("sell", state, arguments, request_id)
        ref = f" (ref {request_id})" if request_id else ""
        confirmation = (
            f"Ok got it{ref}. Brian will give you a call shortly to talk this through "
            "and find you a buyer."
        )
        return {
            "result": confirmation,
            "request_id": request_id,
            "backend_msg": response.get("msg"),
            **{k: v for k, v in response.items() if k != "msg"},
        }
