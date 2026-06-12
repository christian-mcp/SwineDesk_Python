"""Tool: broker-only — send a message to multiple contacts filtered by role and/or state."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)


class BlastMessage(Tool, name="blast_message"):
    TOOL_PATH = "/tools/ops/blast_message"
    DESCRIPTION = (
        "Broker-only. Send the same SMS to all contacts matching a role and/or state filter. "
        "Use when Brian says things like 'text all Iowa buyers that I have 5000 pigs at $85', "
        "'blast my Texas sellers', or 'send this to all vets in MN'. "
        "Always confirm the message text and recipient filter with the broker before sending."
    )
    ARGUMENTS = {
        "message": Arg("The message text to send to each contact"),
        "role": Arg("Filter by role: seller, buyer, vet, or freight", optional=True),
        "state": Arg("Filter by US state code, e.g. IA, TX, MN", optional=True),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        message = str(arguments.get("message") or "").strip()
        if not message:
            return {"error": "Message text is required."}

        role = str(arguments.get("role") or "").strip() or None
        contact_state = str(arguments.get("state") or "").strip().upper() or None

        backend = get_backend_client()
        contacts_response = await backend.list_contacts(role=role, state=contact_state)
        contacts = contacts_response.get("contacts", [])

        if not contacts:
            return {"result": "No contacts found matching that filter. Nothing sent.", "sent": 0}

        async def send_one(contact: dict[str, Any]) -> dict[str, Any]:
            phone = str(contact.get("phone") or "").strip()
            if not phone:
                return {"skipped": True, "contact": contact}
            try:
                await backend.send_message_to_user(phone, message)
                return {"sent": True, "phone": phone, "name": contact.get("first_name"), "company": contact.get("company")}
            except Exception as exc:
                logger.warning("blast_message failed for %s: %s", phone, exc)
                return {"sent": False, "phone": phone, "error": str(exc)}

        results = await asyncio.gather(*[send_one(c) for c in contacts])

        sent = [r for r in results if r.get("sent")]
        failed = [r for r in results if not r.get("sent") and not r.get("skipped")]

        sent_names = ", ".join(
            f"{r.get('name') or r.get('company') or r['phone']}" for r in sent
        )
        summary = f"Sent to {len(sent)} contact{'s' if len(sent) != 1 else ''}"
        if sent_names:
            summary += f": {sent_names}"
        if failed:
            summary += f". {len(failed)} failed."

        return {
            "result": summary,
            "sent_count": len(sent),
            "failed_count": len(failed),
            "sent": sent,
            "failed": failed,
        }
