from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from ..async_compat import run_blocking
from ..config import PROJECT_ROOT
from .base import Tool


class ExecTool(Tool):
    """执行 shell 命令工具"""

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command"},
            },
            "required": ["command"],
        }

    async def execute(self, command: str, **kwargs) -> str:
        for bad in ["rm -rf", "mkfs", "dd if=", "shutdown"]:
            if bad in command.lower():
                return f"Command '{command}' is not allowed for security reasons."
        try:

            def _run_command():
                return subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    timeout=30,
                )

            proc = await run_blocking(_run_command)
            out = proc.stdout or b""
            err = proc.stderr or b""
            result = out.decode(errors="replace")
            if err:
                result += f"\nSTDERR:\n{err.decode(errors='replace')}"
            return (result or "(no output)")[:10000]
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after 30 seconds: {command}"
        except Exception as exc:
            return f"Error: {exc}"


class ReadFileTool(Tool):
    """读取文件工具"""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read file contents."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
            },
            "required": ["path"],
        }

    async def execute(self, path: str, **kwargs) -> str:
        target = Path(path).expanduser().resolve()
        if not target.exists():
            return f"Error: Not found: {path}"
        try:
            return target.read_text(encoding="utf-8")[:50000]
        except Exception as exc:
            return f"Error: {exc}"


class WriteFileTool(Tool):
    """写入文件工具"""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str, content: str, **kwargs) -> str:
        try:
            target = Path(path).expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} bytes to {target}"
        except Exception as exc:
            return f"Error: {exc}"


class FindFilesTool(Tool):
    """按文件名或路径片段搜索文件"""

    @property
    def name(self) -> str:
        return "find_files"

    @property
    def description(self) -> str:
        return "Find files by filename or path fragment under the project root."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Filename or path fragment to search for"},
                "root": {"type": "string", "description": "Optional root directory for the search"},
                "max_results": {"type": "integer", "description": "Maximum number of results to return"},
            },
            "required": ["pattern"],
        }

    async def execute(
        self,
        pattern: str,
        root: str | None = None,
        max_results: int = 20,
        **kwargs,
    ) -> str:
        query = (pattern or "").strip().lower()
        if not query:
            return "Error: pattern is required."

        search_root = Path(root).expanduser().resolve() if root else PROJECT_ROOT
        if not search_root.exists():
            return f"Error: Search root not found: {search_root}"

        results: list[str] = []
        for path in search_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(search_root).as_posix()
            if query in path.name.lower() or query in rel.lower():
                results.append(rel)
                if len(results) >= max(1, max_results):
                    break

        if not results:
            return f"No files found for pattern: {pattern}"
        return "\n".join(results)


class SearchCodeTool(Tool):
    """按文本或正则搜索代码内容"""

    @property
    def name(self) -> str:
        return "search_code"

    @property
    def description(self) -> str:
        return "Search file contents under the project root and return matching lines."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text or regex pattern to search for"},
                "root": {"type": "string", "description": "Optional root directory for the search"},
                "glob": {"type": "string", "description": "Optional file glob such as *.py or *.md"},
                "case_sensitive": {"type": "boolean", "description": "Whether the search is case sensitive"},
                "regex": {"type": "boolean", "description": "Whether to treat pattern as regex"},
                "max_results": {"type": "integer", "description": "Maximum number of matching lines to return"},
            },
            "required": ["pattern"],
        }

    async def execute(
        self,
        pattern: str,
        root: str | None = None,
        glob: str = "*",
        case_sensitive: bool = False,
        regex: bool = False,
        max_results: int = 20,
        **kwargs,
    ) -> str:
        query = pattern or ""
        if not query.strip():
            return "Error: pattern is required."

        search_root = Path(root).expanduser().resolve() if root else PROJECT_ROOT
        if not search_root.exists():
            return f"Error: Search root not found: {search_root}"

        flags = 0 if case_sensitive else re.IGNORECASE
        matcher = re.compile(query if regex else re.escape(query), flags)
        results: list[str] = []

        for path in search_root.rglob(glob or "*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue

            rel = path.relative_to(search_root).as_posix()
            for lineno, line in enumerate(text.splitlines(), start=1):
                if matcher.search(line):
                    results.append(f"{rel}:{lineno}: {line.strip()}")
                    if len(results) >= max(1, max_results):
                        return "\n".join(results)

        if not results:
            return f"No code matches found for pattern: {pattern}"
        return "\n".join(results)
