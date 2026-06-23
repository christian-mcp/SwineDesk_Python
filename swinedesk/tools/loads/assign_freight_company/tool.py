"""Tool: broker assigns a freight company to a deal's load(s) over SMS.

Used when the broker replies to a freight-assignment reminder ("give it to Midwest Freight").
Resolves the company from the phonebook, sets it on the deal's load(s), and the backend then
schedules the freight admin a reminder to assign a driver.
"""

from __future__ import annotations

import logging
from typing import Any

from swinedesk.backend_client import get_backend_client
from swinedesk.tool_helpers import ensure_role
from swinedesk.tooling import Arg, Tool

logger = logging.getLogger(__name__)

# Generic company words to ignore when matching a freight company name.
_NOISE = {"the", "co", "co.", "inc", "inc.", "llc", "freight", "trucking", "transport", "logistics"}


def _looks_like_phone(raw: str) -> bool:
    digits = "".join(ch for ch in raw if ch.isdigit())
    return raw.strip().startswith("+") or (
        len(digits) >= 7
        and digits == raw.strip().lstrip("+").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
    )


def _tokens(text: str) -> set[str]:
    return {t for t in text.lower().replace(".", " ").split() if t and t not in _NOISE}


async def _resolve_freight(backend: Any, raw: Any) -> tuple[str | None, str | None]:
    """Resolve a free-form freight company name/phone to (actor_id, phone) via the phonebook."""
    text = str(raw or "").strip()
    if not text:
        return None, None
    if _looks_like_phone(text):
        return None, text
    want = _tokens(text)
    try:
        data = await backend.list_contacts(role="freight")
    except Exception:
        logger.exception("Failed to look up freight contacts for '%s'", text)
        return None, None
    contacts = data.get("contacts", []) if isinstance(data, dict) else []
    for contact in contacts:
        company_tokens = _tokens(str(contact.get("company") or ""))
        first_tokens = _tokens(str(contact.get("first_name") or ""))
        if want and ((want & company_tokens) or (want & first_tokens)):
            return contact.get("actor_id"), contact.get("phone")
    return None, None


class AssignFreightCompany(Tool, name="assign_freight_company"):
    TOOL_PATH = "/tools/loads/assign_freight_company"
    DESCRIPTION = (
        "Broker-only. Assign a freight company to a deal's load(s) - e.g. when the broker says "
        "'give it to Midwest Freight' in reply to a freight-assignment reminder. The freight admin "
        "is then automatically reminded to assign a driver."
    )
    ARGUMENTS = {
        "freight_company": Arg(
            "The freight company to assign - a name (e.g. 'Midwest Freight') or a phone number."
        ),
        "order_id": Arg(
            "Order short id. Omit to use the deal the bot last pinged you about.", optional=True
        ),
    }

    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        role_error = ensure_role(state, {"broker"})
        if role_error:
            return role_error

        order_id = str(arguments.get("order_id", "")).strip()
        if not order_id:
            refs = list(getattr(state, "referenced_order_ids", []) or [])
            order_id = refs[-1] if refs else ""
        if not order_id:
            return {"error": "Which deal should I assign freight on? Give me the order id."}

        backend = get_backend_client()
        fg, fp = await _resolve_freight(backend, arguments.get("freight_company"))
        if not fg and not fp:
            return {
                "error": f"I couldn't find a freight company matching "
                f"'{arguments.get('freight_company')}'. Who should I assign it to?",
                "order_id": order_id,
            }

        extras: dict[str, Any] = {}
        if fg:
            extras["freight_guid"] = fg
        elif fp:
            extras["freight_phone"] = fp

        resp = await backend.assign_freight_company(order_id, extras)
        if not resp.get("success"):
            return {"error": resp.get("error", "Couldn't assign freight."), "order_id": order_id}

        if getattr(state, "active_workflow", None) == "awaiting_freight_assignment":
            state.active_workflow = None

        company = resp.get("freight_company") or "the freight company"
        return {
            "result": f"Done - {company} is now on deal {order_id}. I've pinged them to assign a "
            "driver, and I'll get the driver's details to the seller before pickup.",
            "order_id": order_id,
            **resp,
        }
