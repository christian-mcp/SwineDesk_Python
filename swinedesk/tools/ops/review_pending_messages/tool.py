"""Tool: broker-only — list outbound messages held for approval in Beta Test Mode."""

from __future__ import annotations

from typing import Any

from swinedesk.beta_approvals import list_pending
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Tool


class ReviewPendingMessages(Tool, name="review_pending_messages"):
    TOOL_PATH = "/tools/ops/review_pending_messages"
    DESCRIPTION = (
        "Broker-only. Lists the outbound messages the bot wants to send to other "
        "users that are waiting for your approval (Beta Test Mode). Call this when "
        "the broker asks things like 'any messages to review?', 'what's waiting to "
        "go out?', 'show me the pending drafts', or 'what needs my approval?'. "
        "After reviewing, the broker approves/skips/replaces each with "
        "resolve_pending_message."
    )
    ARGUMENTS: dict[str, Any] = {}

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        pending = await list_pending()
        if not pending:
            return {"result": "No outbound messages are waiting for approval.", "count": 0}

        lines = [f"{len(pending)} message{'s' if len(pending) != 1 else ''} awaiting your approval:"]
        for record in pending:
            lines.append(
                f"#{record['id']} -> {record.get('to_label') or record['to_phone']}: "
                f"\"{record['message']}\""
            )
        lines.append(
            "Reply \"approve <#>\" to send, \"skip <#>\" to drop, "
            "or \"send <#> <new text>\" to replace."
        )
        return {
            "result": "\n".join(lines),
            "count": len(pending),
            "pending": [
                {"id": r["id"], "to_phone": r["to_phone"], "message": r["message"]}
                for r in pending
            ],
        }
