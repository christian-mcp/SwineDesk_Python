"""Tool: report a delivery or logistics issue for broker review."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, merge_workflow_draft, remember_load, require_actor_id
from swinedesk.tooling import Arg, Tool


class ReportDeliveryIssue(Tool, name="report_delivery_issue"):
    TOOL_PATH = "/tools/issues/report_delivery_issue"
    DESCRIPTION = "Report a delivery, logistics, or quality issue for broker review."
    ARGUMENTS = {
        "load_id": Arg("Load identifier", optional=True),
        "issue_type": Arg("Issue category"),
        "description": Arg("What happened"),
        "severity": Arg("low, medium, or high", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"seller", "buyer", "freight_operator"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        load_id = str(arguments.get("load_id", "")).strip()
        if load_id:
            remember_load(state, load_id)

        merge_workflow_draft(state, "report_issue", arguments)
        backend = get_backend_client()
        response = await backend.report_delivery_issue(
            actor_id,
            str(getattr(state, "role", "")),
            arguments,
        )
        if load_id and state is not None:
            state.add_escalation_flag(f"delivery-issue:{load_id}")
        return {
            "result": response.get("msg", "Reported issue for broker review."),
            **response,
        }
