"""Helpers shared across SwineDesk tool implementations."""

from __future__ import annotations

from typing import Any

from swinedesk.state import SwineDeskState


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


def summarize_collection(prefix: str, values: dict[str, Any]) -> str:
    """Build a compact result message from a payload dict."""
    compact = ", ".join(f"{key}={value}" for key, value in values.items() if value not in ("", None, []))
    return f"{prefix}: {compact}" if compact else prefix
