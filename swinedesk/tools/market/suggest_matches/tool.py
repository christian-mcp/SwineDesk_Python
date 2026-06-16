"""Tool: broker-only — find the set of buy/sell pairings that maximizes total profit.

This is the assignment problem (a.k.a. max-weight bipartite matching / an LP):
given every open sell listing and every open buy request, choose which sells to
pair with which buys so the *total* expected profit across the whole board is as
high as possible, with each order used at most once.

Profit for a pairing is computed exactly the way /v1/query/match-orders books it:
ELM buys from the seller at the sell listing's price (cost in) and sells to the
buyer at the buy request's price (revenue out), on the sell listing's head count.

    profit = (buy_out_price - sell_in_price) * sell_head

Only pairings with a positive margin and a compatible market (same pig type) are
eligible. The optimum is solved with the Hungarian algorithm — no external solver
dependency.
"""
from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool

_NO_EDGE = 0.0  # profit contributed by an ineligible / unmatched pairing


def _solve_assignment(profit: list[list[float]]) -> list[int]:
    """Max-weight assignment on a rectangular profit matrix (rows=sells, cols=buys).

    Returns assign[row] = col (or -1 if a row is left unmatched). Solved by running
    the O(n^3) Hungarian algorithm (minimization) on a padded square matrix of
    negated profits; ineligible/dummy cells cost 0 so they read as "leave unmatched".
    """
    n_rows = len(profit)
    n_cols = len(profit[0]) if n_rows else 0
    if n_rows == 0 or n_cols == 0:
        return [-1] * n_rows

    n = max(n_rows, n_cols)
    # Square cost matrix for minimization: cost = -profit (0 outside the real grid).
    cost = [[0.0] * n for _ in range(n)]
    for i in range(n_rows):
        for j in range(n_cols):
            cost[i][j] = -profit[i][j]

    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)      # p[j] = row (1-indexed) assigned to column j
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = -1
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    assign = [-1] * n_rows
    for j in range(1, n + 1):
        row = p[j] - 1
        col = j - 1
        if 0 <= row < n_rows and 0 <= col < n_cols:
            assign[row] = col
    return assign


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _markets_compatible(a: Any, b: Any) -> bool:
    # Mirror /v1/query/match-orders: a mismatch only blocks when BOTH markets are set.
    return not (a and b and a != b)


class SuggestMatches(Tool, name="suggest_matches"):
    TOOL_PATH = "/tools/market/suggest_matches"
    DESCRIPTION = (
        "Broker-only. Look across ALL open buy requests and sell listings and compute the "
        "set of pairings that maximizes TOTAL expected profit for the desk (an assignment / "
        "linear-programming optimization, each order used at most once). Use when the broker "
        "asks things like 'which orders should I match to make the most money', 'what's the "
        "most profitable way to pair the board', 'optimize my open orders', or 'where's the "
        "biggest margin right now'. Returns the recommended pairings with per-deal and total "
        "profit and the exact pair commands. It only recommends — it does NOT book anything; "
        "the broker still confirms each pairing with match_orders."
    )
    ARGUMENTS = {
        "market": Arg(
            "Optional pig-type filter, e.g. WEAN_PIGS or FEEDER_PIGS. Omit to optimize across "
            "all markets (pairings are always kept within the same market).",
            optional=True,
        ),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        market_filter = str(arguments.get("market") or "").strip().upper() or None

        backend = get_backend_client()
        board = await backend.get_open_market()
        sells = list(board.get("supply", []))
        buys = list(board.get("demand", []))

        if market_filter:
            sells = [s for s in sells if str(s.get("market") or "").upper() == market_filter]
            buys = [b for b in buys if str(b.get("market") or "").upper() == market_filter]

        if not sells or not buys:
            return {
                "result": "Nothing to optimize — the board needs at least one open sell "
                "listing and one open buy request"
                + (f" in {market_filter}." if market_filter else "."),
                "pairings": [],
                "total_profit": 0,
            }

        # Build the profit matrix: rows = sells, cols = buys.
        profit = [[_NO_EDGE] * len(buys) for _ in range(len(sells))]
        for i, s in enumerate(sells):
            in_price = _num(s.get("target_price"))
            head = s.get("head")
            head = int(head) if head is not None else None
            for j, b in enumerate(buys):
                out_price = _num(b.get("target_price"))
                if in_price is None or out_price is None or head is None:
                    continue
                if not _markets_compatible(s.get("market"), b.get("market")):
                    continue
                margin = out_price - in_price
                if margin <= 0:
                    continue
                profit[i][j] = margin * head

        assign = _solve_assignment(profit)

        pairings = []
        matched_sells: set[int] = set()
        matched_buys: set[int] = set()
        for i, j in enumerate(assign):
            if j < 0 or profit[i][j] <= 0:
                continue
            s, b = sells[i], buys[j]
            in_price = _num(s.get("target_price"))
            out_price = _num(b.get("target_price"))
            head = int(s.get("head"))
            margin = out_price - in_price
            matched_sells.add(i)
            matched_buys.add(j)
            pairings.append({
                "sell_order_id": s.get("order_id"),
                "sell_company": s.get("company"),
                "buy_order_id": b.get("order_id"),
                "buy_company": b.get("company"),
                "market": s.get("market"),
                "pig_type": s.get("pig_type"),
                "head": head,
                "buy_head_requested": b.get("head"),
                "sell_price_in": in_price,
                "buy_price_out": out_price,
                "margin_per_head": round(margin, 2),
                "expected_profit": round(margin * head, 2),
            })

        pairings.sort(key=lambda p: p["expected_profit"], reverse=True)
        total_profit = round(sum(p["expected_profit"] for p in pairings), 2)

        unmatched_sells = [
            {"order_id": s.get("order_id"), "company": s.get("company"),
             "pig_type": s.get("pig_type"), "head": s.get("head"), "price_in": s.get("target_price")}
            for i, s in enumerate(sells) if i not in matched_sells
        ]
        unmatched_buys = [
            {"order_id": b.get("order_id"), "company": b.get("company"),
             "pig_type": b.get("pig_type"), "head": b.get("head"), "price_out": b.get("target_price")}
            for j, b in enumerate(buys) if j not in matched_buys
        ]

        if not pairings:
            return {
                "result": "No profitable pairings on the board right now — every "
                "compatible buy/sell overlap has a non-positive margin.",
                "pairings": [],
                "total_profit": 0,
                "unmatched_supply": unmatched_sells,
                "unmatched_demand": unmatched_buys,
            }

        lines = [
            f"Most profitable way to pair the board: {len(pairings)} deal"
            f"{'s' if len(pairings) != 1 else ''}, total expected profit ${total_profit:,.0f}."
        ]
        for n, p in enumerate(pairings, 1):
            lines.append(
                f"{n}. {p['sell_company']} (sell {p['sell_order_id']}, {p['head']:,} "
                f"{p['pig_type']} @ ${p['sell_price_in']:,.2f}) -> {p['buy_company']} "
                f"(buy {p['buy_order_id']} @ ${p['buy_price_out']:,.2f}) = "
                f"${p['expected_profit']:,.0f} (${p['margin_per_head']:,.2f}/head)"
            )
        commands = [f"pair {p['buy_order_id']} with sell {p['sell_order_id']}" for p in pairings]
        lines.append("To book: " + "; ".join(commands) + ".")

        return {
            "result": "\n".join(lines),
            "pairings": pairings,
            "total_profit": total_profit,
            "pair_commands": commands,
            "unmatched_supply": unmatched_sells,
            "unmatched_demand": unmatched_buys,
        }
