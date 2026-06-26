"""Tool: broker recap of recent desk activity over a chosen look-back window."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.daily_summary import build_recap_message
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


def _opt_int(arguments: dict[str, Any], key: str) -> int | None:
    raw = arguments.get(key)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class GetDailyRecap(Tool, name="get_daily_recap"):
    TOOL_PATH = "/tools/crm/get_daily_recap"
    DESCRIPTION = (
        "Broker-only. Recap of recent desk activity over a look-back window: new sell "
        "listings and buy requests created in the window, head counts, and a breakdown of "
        "outstanding tasks (assign freight, vet checks, health certs, etc.). Use for "
        "'today's recap' / 'end of day summary' (default, last 24h), "
        "'weekly recap' (days=7), or any custom window the broker asks for. "
        "For loads/deliveries coming UP in the future, use get_upcoming_loads instead."
    )
    ARGUMENTS: dict[str, Arg] = {
        "days": Arg(
            "Look-back window in days. 1 = daily recap, 7 = weekly recap, 30 = monthly. "
            "Defaults to 1 (last 24 hours) when omitted.",
            optional=True,
        ),
        "hours": Arg(
            "Look-back window in hours, for a sub-day window. Ignored when days is given.",
            optional=True,
        ),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        days = _opt_int(arguments, "days")
        hours = _opt_int(arguments, "hours")

        backend = get_backend_client()
        response = await backend.get_daily_recap(days=days, hours=hours)

        return {"result": build_recap_message(response), **response}
