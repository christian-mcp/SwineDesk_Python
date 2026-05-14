"""Tool: return standard health certificate submission instructions."""

from __future__ import annotations

from typing import Any

from swinedesk.settings import settings
from swinedesk.tool_helpers import ensure_role, remember_load
from swinedesk.tooling import Arg, Tool


class GetCertInstructions(Tool, name="get_cert_instructions"):
    TOOL_PATH = "/tools/health/get_cert_instructions"
    DESCRIPTION = "Return health certificate submission instructions for a load."
    ARGUMENTS = {
        "load_id": Arg("Load identifier", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"seller", "vet"})
        if role_error:
            return role_error

        load_id = str(arguments.get("load_id", "")).strip()
        if load_id:
            remember_load(state, load_id)

        subject_hint = f"HEALTH CERT {load_id}" if load_id else "HEALTH CERT [LOAD ID]"
        instructions = (
            f"Email the health cert PDF or photo to {settings.docs_email} "
            f"with subject: {subject_hint}. Reply here once sent and we'll confirm receipt."
        )
        return {"result": instructions, "load_id": load_id}
