"""Tool: stage grading details inside the SMS session before final submit."""

from __future__ import annotations

from typing import Any

from swinedesk.tool_helpers import ensure_role, merge_workflow_draft, remember_load
from swinedesk.tooling import Arg, Tool


class UpdateGradingDraft(Tool, name="update_grading_draft"):
    TOOL_PATH = "/tools/grading/update_grading_draft"
    DESCRIPTION = (
        "Capture or update in-progress grading details in session state without submitting them."
    )
    ARGUMENTS = {
        "load_id": Arg("Load identifier", optional=True),
        "grading_stage": Arg(
            "Current grading step: need_load_id, collecting_core, collecting_rare, ready_to_confirm",
            optional=True,
        ),
        "grading_date": Arg("Date grading was completed", optional=True),
        "grader_name": Arg("Person who completed grading", optional=True),
        "head_count_received": Arg("Head count received", optional=True),
        "underweight": Arg("Underweight write-offs", optional=True),
        "unthrifty": Arg("Unthrifty write-offs", optional=True),
        "ruptures": Arg("Ruptures write-offs", optional=True),
        "scrotal_ruptures": Arg("Scrotal ruptures write-offs", optional=True),
        "navel_infections": Arg("Navel infection write-offs", optional=True),
        "greasy_pigs": Arg("Greasy pigs write-offs", optional=True),
        "humpback": Arg("Humpback write-offs", optional=True),
        "swollen_joints": Arg("Swollen joints write-offs", optional=True),
        "abscesses": Arg("Abscesses write-offs", optional=True),
        "severely_crippled": Arg("Severely crippled write-offs", optional=True),
        "swollen_ears": Arg("Swollen ears write-offs", optional=True),
        "bad_feet": Arg("Bad feet write-offs", optional=True),
        "rail_backs": Arg("Rail backs write-offs", optional=True),
        "doa": Arg("Dead on arrival write-offs", optional=True),
        "dead_within_12hrs": Arg("Dead within 12 hours write-offs", optional=True),
        "boars": Arg("Boars write-offs", optional=True),
        "comments": Arg("Additional comments", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"buyer"})
        if role_error:
            return role_error

        payload = {
            key: value
            for key, value in arguments.items()
            if value not in (None, "", [])
        }
        merge_workflow_draft(state, "grading_draft", payload)

        load_id = str(payload.get("load_id") or "").strip()
        if load_id:
            remember_load(state, load_id)

        return {
            "result": f"Updated grading draft for {load_id or 'current load'}.",
            "active_workflow": getattr(state, "active_workflow", None),
            "draft_data": getattr(state, "draft_data", {}),
        }
