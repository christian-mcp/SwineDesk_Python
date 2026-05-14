"""Tool: resolve a known or candidate site."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, merge_workflow_draft, require_actor_id
from swinedesk.tooling import Arg, Tool


class ResolveSite(Tool, name="resolve_site"):
    TOOL_PATH = "/tools/sites/resolve_site"
    DESCRIPTION = "Resolve a site by PID, known site id, or address fragment."
    ARGUMENTS = {
        "site_id": Arg("Known site identifier", optional=True),
        "pid": Arg("Premises ID", optional=True),
        "address_fragment": Arg("Partial address or site name", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"seller", "buyer"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        payload = {"actorId": actor_id, **arguments}
        merge_workflow_draft(state, "resolve_site", arguments)
        backend = get_backend_client()
        response = await backend.resolve_site(payload)
        site_id = str(response.get("site_id", response.get("siteId", "")))
        if site_id and state is not None and site_id not in state.known_site_ids:
            state.known_site_ids.append(site_id)
        return {
            "result": "Resolved site details.",
            **response,
        }
