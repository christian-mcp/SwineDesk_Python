"""Tool: broker-only — send a message to multiple contacts filtered by role and/or state."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)


def _normalize_phone(phone: str) -> str:
    """Strip spaces and leading + for comparison purposes."""
    return re.sub(r"[\s+]", "", phone)


async def _execute_blast(args: dict[str, Any], state: Any) -> dict[str, Any]:
    """Actually send the blast. Called from confirm_action or (in tests) directly.

    If args contains a pre-resolved '_resolved_contacts' list (set during staging),
    that list is used directly so we don't re-fetch from the backend.
    """
    message = str(args.get("message") or "").strip()
    backend = get_backend_client()

    # Use pre-resolved list when available (avoids a duplicate backend round-trip).
    if "_resolved_contacts" in args:
        contacts: list[dict[str, Any]] = list(args["_resolved_contacts"])
    else:
        role = str(args.get("role") or "").strip() or None
        contact_state = str(args.get("state") or "").strip().upper() or None
        exclude_phone = str(args.get("exclude_phone") or "").strip() or None

        contacts_response = await backend.list_contacts(role=role, state=contact_state)
        contacts = contacts_response.get("contacts", [])

        if exclude_phone:
            normalized_exclude = _normalize_phone(exclude_phone)
            contacts = [
                c for c in contacts
                if _normalize_phone(str(c.get("phone") or "")) != normalized_exclude
            ]

    if not contacts:
        return {"result": "No contacts found matching that filter. Nothing sent.", "sent": 0}

    async def send_one(contact: dict[str, Any]) -> dict[str, Any]:
        phone = str(contact.get("phone") or "").strip()
        if not phone:
            return {"skipped": True, "contact": contact}
        try:
            await backend.send_message_to_user(phone, message)
            return {
                "sent": True,
                "phone": phone,
                "name": contact.get("first_name"),
                "company": contact.get("company"),
            }
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


class BlastMessage(Tool, name="blast_message"):
    TOOL_PATH = "/tools/ops/blast_message"
    DESCRIPTION = (
        "Broker-only. STAGES a blast SMS to all contacts matching a role and/or state filter. "
        "The message is NOT sent immediately — the tool returns a confirmation prompt and the "
        "broker must reply YES (triggering confirm_action) before anything is sent. "
        "Use when Brian says things like 'text all Iowa buyers that I have 5000 pigs at $85', "
        "'blast my Texas sellers', or 'send this to all vets in MN'. "
        "Pass exclude_phone (E.164) when blasting about a specific seller's listing so the "
        "seller is not texted about their own pigs."
    )
    ARGUMENTS = {
        "message": Arg("The message text to send to each contact"),
        "role": Arg("Filter by role: seller, buyer, vet, or freight", optional=True),
        "state": Arg("Filter by US state code, e.g. IA, TX, MN", optional=True),
        "exclude_phone": Arg(
            "E.164 phone to exclude from recipients (e.g. the listing seller's phone)",
            optional=True,
        ),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        message = str(arguments.get("message") or "").strip()
        if not message:
            return {"error": "Message text is required."}

        role_filter = str(arguments.get("role") or "").strip() or None
        contact_state = str(arguments.get("state") or "").strip().upper() or None
        exclude_phone = str(arguments.get("exclude_phone") or "").strip() or None

        # Resolve the recipient list so we can show a meaningful count in the summary.
        backend = get_backend_client()
        contacts_response = await backend.list_contacts(role=role_filter, state=contact_state)
        contacts = contacts_response.get("contacts", [])

        if exclude_phone:
            normalized_exclude = _normalize_phone(exclude_phone)
            contacts = [
                c for c in contacts
                if _normalize_phone(str(c.get("phone") or "")) != normalized_exclude
            ]

        if not contacts:
            return {"result": "No contacts found matching that filter. Nothing to stage.", "staged": 0}

        # Build the human-readable summary shown to the broker before they confirm.
        filter_desc = " ".join(filter(None, [
            contact_state,
            role_filter,
            "contacts" if (role_filter or contact_state) else "all contacts",
        ]))
        exclude_note = f", excluding {exclude_phone}" if exclude_phone else ""
        human_summary = (
            f"This will text {len(contacts)} {filter_desc}{exclude_note}: \"{message}\""
        )

        # Store args for execution — include the already-resolved exclusion so
        # _execute_blast skips the re-fetch-and-filter step cleanly.
        stored_args = dict(arguments)
        stored_args["_resolved_contacts"] = contacts  # pass pre-filtered list through

        state.pending_action = {
            "kind": "blast",
            "args": stored_args,
            "summary": human_summary,
        }

        return {
            "staged": True,
            "recipient_count": len(contacts),
            "confirmation_prompt": (
                f"{human_summary}. Reply YES to send, or NO to cancel."
            ),
        }
