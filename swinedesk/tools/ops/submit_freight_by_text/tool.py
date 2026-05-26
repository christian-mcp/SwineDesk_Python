"""Tool: extract structured freight details from a free-text message (freight role)."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tooling import Arg, Tool


class SubmitFreightByText(Tool, name="submit_freight_by_text"):
    TOOL_PATH = "/tools/ops/submit_freight_by_text"
    DESCRIPTION = (
        "Parse a freight operator's free-text message into structured delivery details "
        "and submit them. Use when a driver texts natural language like "
        "'I'll be there Tuesday morning, truck #4, 44ft trailer' or provides ETA/truck details "
        "without filling out a form."
    )
    ARGUMENTS = {
        "load_id": Arg("Load short ID this update is for"),
        "text": Arg(
            "The free-text freight message containing truck number, ETA, trailer size, "
            "driver name, or any delivery details"
        ),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        load_id = str(arguments.get("load_id", "")).strip()
        text = str(arguments.get("text", "")).strip()

        if not load_id:
            return {"error": "Load ID is required."}
        if not text:
            return {"error": "Freight message text is required."}

        actor_id = str(getattr(state, "actor_id", "") or "")

        payload = {
            "load_id": load_id,
            "raw_text": text,
            "actor_id": actor_id,
        }

        backend = get_backend_client()
        response = await backend.submit_freight_details(actor_id, payload)
        if "error" in response:
            return response

        return {
            "result": f"Freight details submitted for load {load_id}.",
            **response,
        }
