from __future__ import annotations

from typing import Any

from .base import Tool


class ToolRegistry:
    """工具注册表，管理可用的工具"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get_definitions(self) -> list[dict[str, Any]]:
        return [tool.to_schema() for tool in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found."
        if not isinstance(params, dict):
            return (
                "Error: invalid tool arguments: expected a JSON object "
                f"but got {type(params).__name__}."
            )
        try:
            return await tool.execute(**params)
        except Exception as exc:
            return f"Error: {exc}"
