"""Tool: broker view of loads scheduled to deliver in an upcoming window."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
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


def _format_upcoming(response: dict[str, Any]) -> str:
    """Render upcoming loads into SMS-friendly plain text."""
    items = response.get("items", [])
    days = response.get("window_days", 7)
    if not items:
        return f"No loads scheduled to deliver in the next {days} days."

    head = response.get("total_head", 0)
    count = response.get("load_count", len(items))
    lines = [f"Upcoming loads (next {days} days): {count} load(s), {head} head"]
    for it in items:
        sched = it.get("scheduled") or "TBD"
        date_part = sched.split("T")[0] if isinstance(sched, str) else sched
        pig = it.get("pig_type") or it.get("market") or "pigs"
        lines.append(
            f"  {date_part} | {it.get('load_id', '?')} | {pig} | "
            f"{it.get('head', '?')} head | {it.get('status', '')}"
        )
    return "\n".join(lines)


class GetUpcomingLoads(Tool, name="get_upcoming_loads"):
    TOOL_PATH = "/tools/crm/get_upcoming_loads"
    DESCRIPTION = (
        "Broker-only. List loads scheduled to deliver in an upcoming window (forward-looking). "
        "Use when asked 'what's coming up', 'loads next week', 'deliveries in the next 3 days', "
        "etc. Pass days for the window size (defaults to 7). For PAST activity (new listings/"
        "requests created recently), use get_daily_recap instead."
    )
    ARGUMENTS: dict[str, Arg] = {
        "days": Arg(
            "Size of the forward window in days. 3 = next 3 days, 7 = next week, "
            "14 = next two weeks. Defaults to 7 when omitted.",
            optional=True,
        ),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        days = _opt_int(arguments, "days")

        backend = get_backend_client()
        response = await backend.get_upcoming_loads(days=days)

        return {"result": _format_upcoming(response), **response}
