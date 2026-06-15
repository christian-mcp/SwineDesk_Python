"""Tool: broker view of open supply and demand across the desk."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


class GetOpenMarket(Tool, name="get_open_market"):
    TOOL_PATH = "/tools/market/get_open_market"
    DESCRIPTION = (
        "Broker-only. Return all open supply (sell listings) and demand (buy requests) "
        "across the whole desk, with head counts. Use when asked about 'open supply and "
        "demand', 'what's on the board', 'open listings and requests', or market position."
    )
    ARGUMENTS: dict[str, Arg] = {}

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        _ = arguments
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        backend = get_backend_client()
        response = await backend.get_open_market()

        supply = response.get("supply", [])
        demand = response.get("demand", [])
        lines: list[str] = [
            f"Supply: {response.get('supply_count', len(supply))} listings, "
            f"{response.get('supply_head', 0)} head",
            f"Demand: {response.get('demand_count', len(demand))} requests, "
            f"{response.get('demand_head', 0)} head",
        ]
        def _line(side: str, item: dict[str, Any]) -> str:
            line = (
                f"  {side} {item.get('order_id','?')} - {item.get('head','?')} "
                f"{item.get('market','')} {item.get('health','')}".rstrip()
            )
            vaccine = str(item.get("vaccine") or "").strip()
            if vaccine:
                line += f" - Vaccine: {vaccine}"
            note = str(item.get("notes") or "").strip()
            if note:
                line += f" - Notes: {note}"
            return line

        # Show the whole board, not a silent top-5 — the broker asks for "all orders".
        for s in supply:
            lines.append(_line("SELL", s))
        for d in demand:
            lines.append(_line("BUY", d))

        return {"result": "\n".join(lines), **response}
