"""Tool: mark a health certificate as received."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import remember_load
from swinedesk.tooling import Arg, Tool


class MarkCertReceived(Tool, name="mark_cert_received"):
    TOOL_PATH = "/tools/health/mark_cert_received"
    DESCRIPTION = "Internal tool to mark a health certificate as received for a load."
    ARGUMENTS = {
        "load_id": Arg("Load identifier"),
        "from_email": Arg("Sender email", optional=True),
        "attachment_url": Arg("Attachment URL", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        load_id = str(arguments.get("load_id", "")).strip()
        if not load_id:
            return {"error": "load_id is required."}

        backend = get_backend_client()
        response = await backend.mark_health_cert_received(arguments)
        remember_load(state, load_id)
        return {
            "result": response.get("msg", f"Marked health cert received for {load_id}."),
            **response,
        }
