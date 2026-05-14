"""Expose a single execute_tool bridge for pydantic-ai."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai import Tool as PydanticAITool

from swinedesk.tooling import Tool


class ExecuteToolParams(BaseModel):
    """Unified tool bridge params."""

    tool_name: str = Field(description="Full tool path to execute.")
    model_config = {"extra": "allow"}


def _format_registry_docs(registry: dict[str, type[Tool]]) -> str:
    sections: list[str] = ["Available tools (call via execute_tool):"]
    for path in sorted(registry.keys()):
        tool_cls = registry[path]
        sections.append(f"\n{path}\n{tool_cls.DESCRIPTION}")
        docs = tool_cls.argument_docs()
        if docs:
            sections.append(docs)
    return "\n".join(sections)


def make_documented_prompt(registry: dict[str, type[Tool]]) -> str:
    """Generate prompt section documenting tools for this role."""
    return _format_registry_docs(registry)


def create_execute_tool(registry: dict[str, type[Tool]]) -> PydanticAITool:
    """Create a pydantic-ai tool that dispatches by tool_name."""

    async def execute_tool(ctx: RunContext[Any], params: ExecuteToolParams) -> str:
        tool_name = params.tool_name
        arguments = dict(params.model_extra or {})

        tool_cls = registry.get(tool_name)
        if tool_cls is None:
            available = ", ".join(sorted(registry.keys()))
            return f"Unknown tool: {tool_name}. Available: {available}"

        state = getattr(ctx.deps, "state", None)
        result = await tool_cls().run(arguments, state)

        if not isinstance(result, dict):
            return str(result)
        try:
            return json.dumps(result, ensure_ascii=False)
        except TypeError:
            return str(result)

    return PydanticAITool(execute_tool, takes_ctx=True)

