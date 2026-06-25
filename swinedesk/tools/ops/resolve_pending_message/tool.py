"""Tool: broker-only — approve, skip, or replace a held outbound message (Beta Test Mode)."""

from __future__ import annotations

from typing import Any

from swinedesk.beta_approvals import resolve_approval
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool


class ResolvePendingMessage(Tool, name="resolve_pending_message"):
    TOOL_PATH = "/tools/ops/resolve_pending_message"
    DESCRIPTION = (
        "Broker-only. Acts on one outbound message held for approval in Beta Test Mode. "
        "Pass the draft's approval_id (the # shown in review_pending_messages or the "
        "broker notification) and a decision: "
        "'approve' to send it as drafted, 'skip' to drop it (nothing is sent), or "
        "'revise' to send a replacement (put the new wording in message). "
        "If the broker dictates new wording ('no, tell them ...', 'send this instead: ...'), "
        "pass that text in message — it is sent verbatim instead of the draft. "
        "If the broker gives feedback but not the exact words, rewrite the draft yourself "
        "and pass your revised text in message."
    )
    ARGUMENTS = {
        "approval_id": Arg("The draft's id (the number shown after #, e.g. '3')"),
        "decision": Arg(
            "approve, skip, or revise",
            optional=True,
            choices=["approve", "skip", "revise"],
        ),
        "message": Arg(
            "Replacement text to send instead of the draft (required for revise)",
            optional=True,
        ),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        approval_id = arguments.get("approval_id")
        if approval_id in (None, ""):
            return {"error": "Need the draft's approval_id (the # shown in the review list)."}

        return await resolve_approval(
            approval_id,
            decision=str(arguments.get("decision", "")),
            message=str(arguments.get("message", "")),
        )
