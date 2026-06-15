"""Helpers shared across SwineDesk tool implementations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from swinedesk.notifications import send_sms_notification
from swinedesk.phone_region import infer_region_from_phone
from swinedesk.settings import settings
from swinedesk.state import SwineDeskState

logger = logging.getLogger(__name__)


def require_state(state: Any) -> SwineDeskState | None:
    """Ensure the tool received a SwineDesk state object."""
    if isinstance(state, SwineDeskState):
        return state
    return None


def ensure_role(state: Any, allowed_roles: set[str]) -> dict[str, str] | None:
    """Return a standard error payload when the caller has the wrong role."""
    current = getattr(state, "role", "unknown")
    if current not in allowed_roles:
        allowed = ", ".join(sorted(allowed_roles))
        return {"error": f"This action is only available for: {allowed}."}
    return None


def require_actor_id(state: Any) -> tuple[str, dict[str, str] | None]:
    """Return actor ID or a standard error payload."""
    actor_id = str(getattr(state, "actor_id", "") or "")
    if actor_id:
        return actor_id, None
    return "", {"error": "Missing actor context for this phone number."}


def remember_request(state: Any, request_id: str | None) -> None:
    """Track a request reference in state when supported."""
    if state is None or not request_id:
        return
    if hasattr(state, "remember_order"):
        state.remember_order(request_id)


def remember_load(state: Any, load_id: str | None) -> None:
    """Track a load reference in state when supported."""
    if state is None or not load_id:
        return
    if hasattr(state, "remember_load"):
        state.remember_load(load_id)


def merge_workflow_draft(state: Any, workflow: str, payload: dict[str, Any]) -> None:
    """Merge draft data and active workflow into state when supported."""
    if state is None:
        return
    if hasattr(state, "active_workflow"):
        state.active_workflow = workflow
    if hasattr(state, "merge_draft"):
        state.merge_draft(payload)


async def notify_broker_order_created(
    side: str,
    state: Any,
    arguments: dict[str, Any],
    request_id: str | None,
) -> None:
    """Send a one-line SMS to the broker that a new listing/request was posted.

    Failures are logged and swallowed — broker alerts must never break the user reply.
    """
    broker_phone = settings.effective_broker_alert_phone
    if not broker_phone:
        return
    sender_phone = str(getattr(state, "phone", "") or "")
    region = infer_region_from_phone(sender_phone) or ""
    role = str(getattr(state, "role", "") or "user")
    market = str(arguments.get("market", "") or "").strip() or "pigs"
    head = (
        arguments.get("head_count")
        or arguments.get("head_count_needed")
        or arguments.get("quantity")
        or "?"
    )
    health = str(
        arguments.get("health") or arguments.get("health_requirement") or ""
    ).strip()
    target = str(
        arguments.get("price_target") or arguments.get("budget_target") or ""
    ).strip()
    # Source site for a sell listing, destination site for a buy request.
    site = str(
        arguments.get("source_site") or arguments.get("destination_site") or ""
    ).strip()
    pid = str(arguments.get("pid") or "").strip()
    notes = str(arguments.get("notes") or "").strip()
    # Buyer add-on services (buy requests only). Surface what the buyer asked for.
    addon_labels = {
        "barn_space": "barn space",
        "feed_contract": "feed contract",
        "packer_contract": "packer contract",
    }
    addons: list[str] = []
    for key, addon_label in addon_labels.items():
        answer = str(arguments.get(key) or "").strip()
        low = answer.lower()
        if not low or low.startswith(("no", "none", "n/a", "not ")):
            continue
        # Plain "yes" -> just name the add-on; anything richer -> include the detail.
        addons.append(addon_label if low in ("yes", "y", "true") else f"{addon_label} ({answer})")
    label = "SELL listing" if side.lower() == "sell" else "BUY request"
    ref = f" (ref {request_id})" if request_id else ""
    parts = [
        f"New {label}{ref}",
        f"From: {role} {sender_phone}",
        f"{head} {market}" + (f", {health}" if health else ""),
    ]
    if target:
        parts.append(f"Target: {target if target.startswith('$') else '$' + target}")
    if site or pid:
        site_line = site or ""
        if pid:
            site_line = f"{site_line} (PID {pid})".strip() if site_line else f"PID {pid}"
        parts.append(f"Site: {site_line}")
    if region:
        parts.append(f"Location: {region}")
    if addons:
        parts.append(f"Add-ons: {', '.join(addons)}")
    if notes:
        parts.append(f"Notes: {notes}")
    parts.append(f"At: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    message = "\n".join(parts)
    try:
        await send_sms_notification(broker_phone, message)
    except Exception:
        logger.exception("Failed to notify broker about new %s", label)


def summarize_collection(prefix: str, values: dict[str, Any]) -> str:
    """Build a compact result message from a payload dict."""
    compact = ", ".join(f"{key}={value}" for key, value in values.items() if value not in ("", None, []))
    return f"{prefix}: {compact}" if compact else prefix
