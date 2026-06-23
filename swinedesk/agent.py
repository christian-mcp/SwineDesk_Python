"""SwineDesk role-routed agent wiring for external SMS actors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import RunContext
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from swinedesk.prompts import prompt_for_role
from swinedesk.settings import settings
from swinedesk.state import SwineDeskState
from swinedesk.tool_dispatcher import create_execute_tool, make_documented_prompt
from swinedesk.tool_loader import ToolLoader
from swinedesk.tooling import Tool

TOOLS_PATH = Path(__file__).resolve().parent / "tools"


class SwineDeskDeps(BaseModel):
    """Dependencies passed to each agent run."""

    state: SwineDeskState = Field(default_factory=SwineDeskState)


def _discover_swinedesk_tools() -> dict[str, type[Tool]]:
    """Discover all custom tools under swinedesk/tools/<category>/<tool>/tool.py."""
    registry: dict[str, type[Tool]] = {}
    for category_dir in sorted(TOOLS_PATH.iterdir()):
        if not category_dir.is_dir() or category_dir.name.startswith("_"):
            continue
        registry.update(ToolLoader(category_dir).discover())
    return registry


def _filter_registry(
    full_registry: dict[str, type[Tool]], allowed_paths: set[str]
) -> dict[str, type[Tool]]:
    """Return only the tools explicitly allowed for an agent role."""
    return {path: tool_cls for path, tool_cls in full_registry.items() if path in allowed_paths}


ALL_CUSTOM_TOOLS = _discover_swinedesk_tools()

COMMON_TOOL_PATHS = {
    "/tools/actors/get_actor_profile",
    "/tools/sites/resolve_site",
    "/tools/sites/create_site_for_known_actor",
    # Lets the bot text the caller content they ask for during a voice call.
    "/tools/ops/text_caller",
}

SELLER_TOOL_PATHS = COMMON_TOOL_PATHS | {
    "/tools/market/create_sell_listing",
    "/tools/market/get_my_open_requests",
    "/tools/market/get_my_request_detail",
    "/tools/market/respond_to_price_offer",
    "/tools/loads/list_my_loads",
    "/tools/loads/get_my_load_detail",
    "/tools/loads/get_driver_details",
    "/tools/loads/get_health_cert_status",
    "/tools/health/get_cert_instructions",
    "/tools/issues/report_delivery_issue",
    "/tools/reminders/set_reminder",
    "/tools/reminders/list_reminders",
}

BUYER_TOOL_PATHS = COMMON_TOOL_PATHS | {
    "/tools/market/create_buy_request",
    "/tools/market/get_my_open_requests",
    "/tools/market/get_my_request_detail",
    "/tools/market/respond_to_price_offer",
    "/tools/market/submit_bid",
    "/tools/loads/list_my_loads",
    "/tools/loads/get_my_load_detail",
    "/tools/loads/get_driver_details",
    "/tools/grading/submit_grading",
    "/tools/grading/update_grading_draft",
    "/tools/grading/get_submission_status",
    "/tools/issues/report_delivery_issue",
    "/tools/reminders/set_reminder",
    "/tools/reminders/list_reminders",
}

FREIGHT_TOOL_PATHS = {
    "/tools/actors/get_actor_profile",
    "/tools/loads/get_freight_loads",
    "/tools/loads/get_freight_load_detail",
    "/tools/loads/confirm_freight_assignment",
    "/tools/loads/submit_freight_details",
    "/tools/ops/submit_freight_by_text",
    "/tools/ops/text_caller",
    "/tools/issues/report_delivery_issue",
}

VET_TOOL_PATHS = {
    "/tools/actors/get_actor_profile",
    "/tools/health/get_cert_instructions",
    "/tools/loads/get_health_cert_status",
    "/tools/health/get_vet_pending_loads",
    "/tools/health/confirm_vet_to_vet",
    "/tools/ops/text_caller",
}

DRIVER_TOOL_PATHS = {
    "/tools/actors/get_actor_profile",
    "/tools/driver/get_my_loads",
    "/tools/driver/report_pickup",
    "/tools/driver/report_offload",
    "/tools/issues/report_delivery_issue",
    "/tools/ops/text_caller",
}

BROKER_TOOL_PATHS = COMMON_TOOL_PATHS | {
    # Market
    "/tools/market/create_sell_listing",
    "/tools/market/create_buy_request",
    "/tools/market/get_my_open_requests",
    "/tools/market/get_my_request_detail",
    "/tools/market/get_open_market",
    "/tools/market/match_orders",
    "/tools/market/suggest_matches",
    "/tools/market/reject_order",
    "/tools/market/propose_price",
    "/tools/market/open_auction",
    "/tools/market/close_auction_now",
    # Orders
    "/tools/orders/submit_purchase_order",
    "/tools/crm/get_daily_recap",
    "/tools/crm/get_upcoming_loads",
    # Loads
    "/tools/loads/list_my_loads",
    "/tools/loads/get_my_load_detail",
    "/tools/loads/get_driver_details",
    "/tools/loads/get_health_cert_status",
    "/tools/loads/get_freight_loads",
    "/tools/loads/get_freight_load_detail",
    "/tools/loads/confirm_freight_assignment",
    "/tools/loads/submit_freight_details",
    "/tools/loads/assign_freight_company",
    # Health
    "/tools/health/get_cert_instructions",
    "/tools/health/get_vet_pending_loads",
    # Grading
    "/tools/grading/submit_grading",
    "/tools/grading/update_grading_draft",
    "/tools/grading/get_submission_status",
    # Issues
    "/tools/issues/report_delivery_issue",
    # CRM
    "/tools/crm/add_note",
    "/tools/crm/get_history",
    "/tools/crm/get_pending_tasks",
    "/tools/crm/find_contacts",
    # Reminders
    "/tools/reminders/set_reminder",
    "/tools/reminders/list_reminders",
    # Ops
    "/tools/ops/send_message_to_user",
    "/tools/ops/send_role_notification",
    "/tools/ops/blast_message",
    "/tools/ops/confirm_action",
}

ROLE_REGISTRIES = {
    "seller": _filter_registry(ALL_CUSTOM_TOOLS, SELLER_TOOL_PATHS),
    "buyer": _filter_registry(ALL_CUSTOM_TOOLS, BUYER_TOOL_PATHS),
    "freight_operator": _filter_registry(ALL_CUSTOM_TOOLS, FREIGHT_TOOL_PATHS),
    "driver": _filter_registry(ALL_CUSTOM_TOOLS, DRIVER_TOOL_PATHS),
    "vet": _filter_registry(ALL_CUSTOM_TOOLS, VET_TOOL_PATHS),
    "broker": _filter_registry(ALL_CUSTOM_TOOLS, BROKER_TOOL_PATHS),
}


def _build_agent(role: str, registry: dict[str, type[Tool]]) -> PydanticAgent[SwineDeskDeps, str]:
    model_name = settings.model_name.removeprefix("anthropic:")
    anthropic_model = AnthropicModel(
        model_name,
        provider=AnthropicProvider(api_key=settings.anthropic_api_key),
    )

    agent = PydanticAgent[SwineDeskDeps, str](
        anthropic_model,
        tools=[create_execute_tool(registry)],
        deps_type=SwineDeskDeps,
        output_type=str,
        name=f"swinedesk_{role}_agent",
        model_settings={"temperature": 0, "max_tokens": 800},
        retries=3,
    )

    @agent.system_prompt
    async def role_prompt(ctx: RunContext[SwineDeskDeps]) -> str:
        return (
            f"{prompt_for_role(role, ctx.deps.state)}\n\n"
            f"{make_documented_prompt(registry)}"
        )

    return agent


_role_agents: dict[str, PydanticAgent[SwineDeskDeps, str]] | None = None


def _get_role_agents() -> dict[str, PydanticAgent[SwineDeskDeps, str]]:
    global _role_agents
    if _role_agents is None:
        _role_agents = {
            role: _build_agent(role, registry) for role, registry in ROLE_REGISTRIES.items()
        }
    return _role_agents


def _format_message_history(message_history: list[dict[str, Any]] | None) -> str:
    """Convert stored SMS history into a compact transcript for the model."""
    if not message_history:
        return ""

    lines: list[str] = []
    for entry in message_history:
        role = str(entry.get("role", "")).strip().lower()
        content = str(entry.get("content", "")).strip()
        if not content:
            continue
        speaker = "Assistant" if role == "assistant" else "User"
        lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


def _build_agent_input(
    user_prompt: str,
    message_history: list[dict[str, Any]] | None = None,
) -> str:
    """Build the model input, including prior SMS turns when available."""
    transcript = _format_message_history(message_history)
    if not transcript:
        return user_prompt

    return (
        "Continue this SMS conversation using the prior transcript below. "
        "Do not say that you lack conversation history unless the transcript is empty.\n\n"
        "Prior transcript (oldest first):\n"
        f"{transcript}\n\n"
        "Latest user message:\n"
        f"{user_prompt}"
    )


async def run_swinedesk_agent(
    user_prompt: str,
    state: SwineDeskState,
    *,
    message_history: list[dict[str, Any]] | None = None,
) -> AgentRunResult[str]:
    """Route to the correct external-role agent based on state.role."""
    deps = SwineDeskDeps(state=state)
    selected_agent = _get_role_agents().get(state.role)
    if selected_agent is None:
        raise ValueError(f"Unsupported SwineDesk SMS role: {state.role}")
    return await selected_agent.run(
        _build_agent_input(user_prompt, message_history=message_history),
        deps=deps,
    )
