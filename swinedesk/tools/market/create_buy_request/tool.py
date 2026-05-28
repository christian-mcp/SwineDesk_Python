"""Tool: create buyer-side pig request."""

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
    text = (raw or "").strip().lower()
    if not text:
        return "CLEAN"
    if any(token in text for token in ("neg", "free", "clean", "naive", "healthy", "not ")):
        return "CLEAN"
    if "pedv" in text or "ped " in text or text == "ped":
        return "PEDV"
    if "prrs" in text:
        return "PRRS"
    upper = text.upper()
    return upper if upper in _VALID_HEALTH else "CLEAN"


def _normalize_date(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    if _ISO_DATE.match(text):
        return text
    today = datetime.now(timezone.utc).date()
    low = text.lower()
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


class CreateBuyRequest(Tool, name="create_buy_request"):
    TOOL_PATH = "/tools/market/create_buy_request"
    DESCRIPTION = "Create a buyer-side request for wean or feeder pigs."
    ARGUMENTS = {
        "market": Arg("WEAN_PIGS or FEEDER_PIGS"),
        "head_count_needed": Arg("Total number of pigs needed"),
        "health_requirement": Arg("Required health status — must be one of: CLEAN, PEDV, PRRS"),
        "weight_range": Arg("Target weight range", optional=True),
        "num_loads": Arg("Number of loads", optional=True),
        "delivery_start_date": Arg("Delivery start date", optional=True),
        "cadence": Arg("Weekly cadence or schedule notes", optional=True),
        "destination_site": Arg("Known destination site id or name", optional=True),
        "pid": Arg("Destination premises ID", optional=True),
        "budget_target": Arg("Target budget per head", optional=True),
        "vaccine_requirements": Arg("Required vaccines", optional=True),
        "regrade": Arg("Regrade terms", optional=True),
        "notes": Arg("Additional notes", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"buyer"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        arguments = dict(arguments)
        if "health_requirement" in arguments:
            arguments["health_requirement"] = _normalize_health(str(arguments.get("health_requirement", "")))
        if arguments.get("delivery_start_date"):
            normalized_date = _normalize_date(str(arguments["delivery_start_date"]))
            if normalized_date:
                arguments["delivery_start_date"] = normalized_date
            else:
                arguments.pop("delivery_start_date", None)

        payload = {"actorId": actor_id, "phone": getattr(state, "phone", ""), **arguments}
        merge_workflow_draft(state, "buyer_request", arguments)
        backend = get_backend_client()
        response = await backend.create_buy_request(payload)
        request_id = str(
            response.get("requestId")
            or response.get("orderId")
            or response.get("shortId")
            or response.get("guid")
            or ""
        )
        remember_request(state, request_id)
        await notify_broker_order_created("buy", state, arguments, request_id)
        confirmation = (
            f"Buy request is in (ref {request_id}). Elmport will be in touch today to talk this through."
            if request_id
            else "Buy request is in. Elmport will be in touch today to talk this through."
        )
        return {
            "result": confirmation,
            "request_id": request_id,
            "backend_msg": response.get("msg"),
            **{k: v for k, v in response.items() if k != "msg"},
        }
