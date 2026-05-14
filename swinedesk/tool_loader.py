"""Filesystem tool discovery for SwineDesk."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from swinedesk.tooling import Tool


class ToolLoader:
    """Discover tool classes under <category>/<tool_name>/tool.py."""

    def __init__(self, tools_path: str | Path):
        self.tools_path = Path(tools_path).resolve()
        if not self.tools_path.is_dir():
            raise NotADirectoryError(f"tools_path must be a directory: {self.tools_path}")

    def discover(self) -> dict[str, type[Tool]]:
        registry: dict[str, type[Tool]] = {}
        for subdir in sorted(d for d in self.tools_path.iterdir() if d.is_dir()):
            tool_cls = self.load_tool(subdir.name)
            if tool_cls is None:
                continue
            key = tool_cls.tool_key()
            if key:
                registry[key] = tool_cls
        return registry

    def load_tool(self, subdir: str) -> type[Tool] | None:
        tool_file = self.tools_path / subdir / "tool.py"
        if not tool_file.exists():
            return None
        spec = importlib.util.spec_from_file_location(f"swinedesk_tool_{subdir}", tool_file)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, Tool)
                and obj is not Tool
                and not getattr(obj, "__abstractmethods__", set())
            ):
                return obj
        return None

