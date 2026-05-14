"""Tool: fetch role-safe actor profile."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import require_actor_id
from swinedesk.tooling import Arg, Tool


class GetActorProfile(Tool, name="get_actor_profile"):
    TOOL_PATH = "/tools/actors/get_actor_profile"
    DESCRIPTION = "Fetch the current actor profile for the authenticated SMS role."
    ARGUMENTS = {
        "actor_id": Arg("Actor identifier override", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        actor_id = str(arguments.get("actor_id") or "")
        if not actor_id:
            actor_id, error = require_actor_id(state)
            if error:
                return error

        role = str(getattr(state, "role", "unknown"))
        backend = get_backend_client()
        response = await backend.get_actor_profile(actor_id, role)
        if state is not None:
            state.actor_profile = dict(response)
        return {
            "result": f"Loaded profile for {role}.",
            **response,
        }
