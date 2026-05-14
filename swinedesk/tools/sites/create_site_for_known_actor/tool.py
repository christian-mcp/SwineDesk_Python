"""Tool: create a site for a known actor."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role, merge_workflow_draft, require_actor_id
from swinedesk.tooling import Arg, Tool


class CreateSiteForKnownActor(Tool, name="create_site_for_known_actor"):
    TOOL_PATH = "/tools/sites/create_site_for_known_actor"
    DESCRIPTION = "Create a site for a known seller or buyer account."
    ARGUMENTS = {
        "site_name": Arg("Site name"),
        "address_line_1": Arg("Address line 1"),
        "address_line_2": Arg("Address line 2", optional=True),
        "city": Arg("City"),
        "state_code": Arg("State code"),
        "zip_code": Arg("ZIP code"),
        "pid": Arg("Premises ID", optional=True),
        "contact_name": Arg("Site contact name", optional=True),
        "contact_phone": Arg("Site contact phone", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"seller", "buyer"})
        if role_error:
            return role_error

        actor_id, error = require_actor_id(state)
        if error:
            return error

        payload = {"actorId": actor_id, **arguments}
        merge_workflow_draft(state, "create_site", arguments)
        backend = get_backend_client()
        response = await backend.create_site_for_known_actor(payload)
        site_id = str(response.get("site_id", response.get("siteId", "")))
        if site_id and state is not None and site_id not in state.known_site_ids:
            state.known_site_ids.append(site_id)
        return {
            "result": response.get("msg", f"Created site {site_id}".strip()),
            **response,
        }
