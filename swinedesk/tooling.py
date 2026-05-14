"""Local tool abstractions for SwineDesk (self-contained, no ExpertAI deps)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, ClassVar


@dataclass(slots=True)
class Arg:
    """Simple tool argument descriptor used for prompt documentation."""

    description: str
    optional: bool = False
    choices: list[str] | None = None
    validator: Callable[[str, object], bool] | None = None
    error: str | None = None


class Tool(ABC):
    """Base class for SwineDesk tool implementations."""

    NAME: ClassVar[str] = ""
    TOOL_PATH: ClassVar[str] = ""
    DESCRIPTION: ClassVar[str] = ""
    ARGUMENTS: ClassVar[dict[str, Arg | str]] = {}

    def __init_subclass__(cls, *, name: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if name:
            cls.NAME = name

    @classmethod
    def tool_key(cls) -> str:
        return cls.TOOL_PATH or cls.NAME

    @classmethod
    def argument_docs(cls) -> str:
        parts: list[str] = []
        for key, val in cls.ARGUMENTS.items():
            if isinstance(val, Arg):
                opt = "optional" if val.optional else "required"
                choices = f" choices={val.choices}" if val.choices else ""
                parts.append(f"- {key} ({opt}): {val.description}{choices}")
            else:
                parts.append(f"- {key}: {val}")
        return "\n".join(parts)

    @abstractmethod
    async def run(self, arguments: dict[str, Any], state: Any) -> dict[str, Any]:
        """Execute the tool and return a serializable dict result."""

