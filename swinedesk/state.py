"""State model for the SwineDesk agent runtime."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ActorRole = Literal["seller", "buyer", "freight_operator", "vet", "unknown"]


class SwineDeskState(BaseModel):
    """State carried across a single SMS session."""

    phone: str = ""
    role: ActorRole = "unknown"
    session_id: str = ""
    actor_id: str = ""
    contact_id: str = ""
    company_id: str = ""
    active_workflow: str | None = None
    complete: bool = False
    actor_profile: dict[str, Any] = Field(default_factory=dict)
    draft_data: dict[str, Any] = Field(default_factory=dict)
    referenced_order_ids: list[str] = Field(default_factory=list)
    referenced_load_ids: list[str] = Field(default_factory=list)
    known_site_ids: list[str] = Field(default_factory=list)
    last_broker_alert_at: str | None = None
    escalation_flags: list[str] = Field(default_factory=list)

    def merge_draft(self, values: dict[str, Any]) -> None:
        """Merge draft workflow data into state."""
        self.draft_data = {**self.draft_data, **values}

    def remember_order(self, order_id: str | None) -> None:
        """Track a referenced order ID once."""
        if order_id and order_id not in self.referenced_order_ids:
            self.referenced_order_ids.append(order_id)

    def remember_load(self, load_id: str | None) -> None:
        """Track a referenced load ID once."""
        if load_id and load_id not in self.referenced_load_ids:
            self.referenced_load_ids.append(load_id)

    def add_escalation_flag(self, flag: str | None) -> None:
        """Track a review or escalation flag once."""
        if flag and flag not in self.escalation_flags:
            self.escalation_flags.append(flag)
