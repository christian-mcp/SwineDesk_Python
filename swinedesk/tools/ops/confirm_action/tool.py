"""Tool: broker-only — confirm or cancel a staged pending action (blast or reject)."""

from __future__ import annotations

from typing import Any

from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


class ConfirmAction(Tool, name="confirm_action"):
    TOOL_PATH = "/tools/ops/confirm_action"
    DESCRIPTION = (
        "Broker-only. Executes or cancels a pending staged action (blast_message or "
        "reject_order). Call this when the broker replies YES/confirm/send/go ahead "
        "after a staging prompt — pass confirm=true (default). Call with confirm=false "
        "when the broker replies NO/cancel to clear without executing. "
        "Do NOT re-call blast_message or reject_order to confirm — that would re-stage."
    )
    ARGUMENTS = {
        "confirm": Arg(
            "true to execute the pending action, false to cancel it (default: true)",
            optional=True,
        ),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        pending = getattr(state, "pending_action", None)
        if not pending:
            return {"result": "No pending action to confirm or cancel."}

        # Parse confirm flag — treat missing/null/true/"true"/"yes"/"1" as True.
        raw_confirm = arguments.get("confirm", True)
        if isinstance(raw_confirm, bool):
            do_confirm = raw_confirm
        else:
            do_confirm = str(raw_confirm).strip().lower() not in ("false", "no", "0", "cancel")

        if not do_confirm:
            kind = pending.get("kind", "action")
            summary = pending.get("summary", "")
            state.pending_action = None
            return {
                "result": f"Cancelled. The staged {kind} was not executed.",
                "cancelled_summary": summary,
            }

        kind = pending.get("kind")
        args = pending.get("args", {})
        summary = pending.get("summary", "")

        # Clear before executing so a crash doesn't leave a stale pending action.
        state.pending_action = None

        if kind == "blast":
            from swinedesk.tools.ops.blast_message.tool import _execute_blast
            result = await _execute_blast(args, state)
        elif kind == "reject":
            from swinedesk.tools.market.reject_order.tool import _execute_reject
            result = await _execute_reject(args, state)
        else:
            return {"error": f"Unknown pending action kind: {kind!r}"}

        return {"confirmed": True, "executed_summary": summary, **result}
