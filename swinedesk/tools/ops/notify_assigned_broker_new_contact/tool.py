"""Tool: notify broker about a new unknown SMS contact."""

from __future__ import annotations

from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.settings import settings
from swinedesk.tooling import Arg, Tool


class NotifyAssignedBrokerNewContact(Tool, name="notify_assigned_broker_new_contact"):
    TOOL_PATH = "/tools/ops/notify_assigned_broker_new_contact"
    DESCRIPTION = "Internal tool to alert the assigned broker about a new unknown SMS contact."
    ARGUMENTS = {
        "phone": Arg("Unknown phone number"),
        "message": Arg("Broker alert text"),
        "timestamp": Arg("Received timestamp"),
        "inferred_intent": Arg("seller, buyer, freight, vet, or unknown", optional=True),
        "broker_phone": Arg("Broker phone override", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        _ = state
        payload = dict(arguments)
        payload.setdefault("broker_phone", settings.effective_broker_alert_phone)
        backend = get_backend_client()
        response = await backend.notify_assigned_broker(payload)
        return {
            "result": "Sent broker alert.",
            **response,
        }
