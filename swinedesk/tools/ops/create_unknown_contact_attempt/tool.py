"""Tool: create an unknown contact attempt record."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tooling import Arg, Tool


class CreateUnknownContactAttempt(Tool, name="create_unknown_contact_attempt"):
    TOOL_PATH = "/tools/ops/create_unknown_contact_attempt"
    DESCRIPTION = "Internal tool to record a new unknown-phone contact attempt."
    ARGUMENTS = {
        "phone": Arg("Unknown phone number"),
        "first_message": Arg("First inbound SMS text"),
        "timestamp": Arg("Received timestamp"),
        "inferred_intent": Arg("seller, buyer, freight, vet, or unknown", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        _ = state
        backend = get_backend_client()
        response = await backend.create_unknown_contact_attempt(arguments)
        return {
            "result": "Stored unknown contact attempt.",
            **response,
        }
