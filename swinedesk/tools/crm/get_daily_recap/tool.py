"""Tool: broker recap of today's desk activity."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.daily_summary import build_recap_message
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


class GetDailyRecap(Tool, name="get_daily_recap"):
    TOOL_PATH = "/tools/crm/get_daily_recap"
    DESCRIPTION = (
        "Broker-only. Return a recap of today's activity: new sell listings and buy "
        "requests created today, head counts, and outstanding task count. Use when asked "
        "for 'today's recap', 'what happened today', or 'end of day summary'."
    )
    ARGUMENTS: dict[str, Arg] = {}

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        _ = arguments
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        backend = get_backend_client()
        response = await backend.get_daily_recap()

        return {"result": build_recap_message(response), **response}
