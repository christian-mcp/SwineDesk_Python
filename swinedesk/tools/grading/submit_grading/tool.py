"""Tool: submit delivery grading."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, merge_workflow_draft, remember_load, require_actor_id
from swinedesk.tooling import Arg, Tool


class SubmitGrading(Tool, name="submit_grading"):
    TOOL_PATH = "/tools/grading/submit_grading"
    DESCRIPTION = "Submit grading results after a buyer receives a load."
    ARGUMENTS = {
        "load_id": Arg("Load identifier"),
        "grading_date": Arg("Date grading was completed", optional=True),
        "grader_name": Arg("Person who completed grading", optional=True),
        "head_count_received": Arg("Head count received"),
        "underweight": Arg("Underweight write-offs", optional=True),
        "unthrifty": Arg("Unthrifty write-offs", optional=True),
        "ruptures": Arg("Ruptures write-offs", optional=True),
        "navel_infections": Arg("Navel infection write-offs", optional=True),
        "doa": Arg("Dead on arrival write-offs", optional=True),
        "dead_within_12hrs": Arg("Dead within 12 hours write-offs", optional=True),
        "other_count": Arg("Other write-offs count", optional=True),
        "other_desc": Arg("Other write-offs description", optional=True),
        "comments": Arg("Additional comments", optional=True),
        "photo_urls": Arg("List of photo URLs", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"buyer"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        load_id = str(arguments.get("load_id", "")).strip()
        if not load_id:
            return {"error": "load_id is required."}

        merge_workflow_draft(state, "submit_grading", arguments)
        backend = get_backend_client()
        response = await backend.submit_grading(actor_id, arguments)
        remember_load(state, load_id)

        writeoff_fields = (
            "underweight",
            "unthrifty",
            "ruptures",
            "navel_infections",
            "doa",
            "dead_within_12hrs",
            "other_count",
        )
        for field in writeoff_fields:
            value = arguments.get(field, 0)
            if str(value).strip() not in {"", "0", "0.0", "None"}:
                if state is not None:
                    state.add_escalation_flag(f"grading-review:{load_id}")
                break

        return {
            "result": response.get("msg", f"Submitted grading for {load_id}."),
            **response,
        }
