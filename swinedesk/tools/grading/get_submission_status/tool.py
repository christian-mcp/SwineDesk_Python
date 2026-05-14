"""Tool: fetch grading submission status."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, remember_load, require_actor_id
from swinedesk.tooling import Arg, Tool


class GetSubmissionStatus(Tool, name="get_submission_status"):
    TOOL_PATH = "/tools/grading/get_submission_status"
    DESCRIPTION = "Check whether grading for a load was received and is under review."
    ARGUMENTS = {
        "load_id": Arg("Load identifier"),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"buyer"})
        if role_error:
            return role_error

        load_id = str(arguments.get("load_id", "")).strip()
        if not load_id:
            return {"error": "load_id is required."}

        actor_id, error = require_actor_id(state)
        if error:
            return error

        backend = get_backend_client()
        response = await backend.get_grading_submission_status(actor_id, load_id)
        remember_load(state, load_id)
        return {
            "result": f"Loaded grading status for {load_id}.",
            **response,
        }
