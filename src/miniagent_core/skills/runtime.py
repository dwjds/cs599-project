from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ..async_compat import run_blocking
from .registry import SkillRegistry


class SkillTraceLogger:
    """Log skill activation and skill script execution."""

    def __init__(self, workspace: Path):
        self.trace_file = workspace / "skills" / "skill_trace.jsonl"
        self.trace_file.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: dict[str, Any]):
        payload = {
            "timestamp": datetime.now().isoformat(),
            **event,
        }
        with open(self.trace_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


class SkillRuntime:
    """Run scripts inside a skill's scripts/ directory with path checks."""

    def __init__(self, registry: SkillRegistry, workspace: Path):
        self.registry = registry
        self.workspace = workspace
        self.trace = SkillTraceLogger(workspace)

    def resolve_script(self, skill_name: str, script_path: str) -> Path:
        skill = self.registry.get(skill_name)
        if skill is None:
            raise ValueError(f"Skill not found: {skill_name}")
        requested = str(script_path or "").strip().replace("\\", "/")
        if not requested:
            raise ValueError("script_path is required.")
        if requested.startswith("/"):
            raise ValueError("script_path must be relative to the skill directory.")
        skill_root = skill.dir.resolve()
        candidate = (skill_root / requested).resolve()
        scripts_root = (skill.dir / "scripts").resolve()
        if not _is_relative_to(candidate, skill_root):
            raise ValueError("script_path escapes the skill directory.")
        if not _is_relative_to(candidate, scripts_root):
            raise ValueError("Only scripts under the skill scripts/ directory may be executed.")
        if candidate.suffix.lower() != ".py":
            raise ValueError("Only Python skill scripts can be executed.")
        if not candidate.exists() or not candidate.is_file():
            raise ValueError(f"Skill script not found: {script_path}")
        return candidate

    async def run_script(
        self,
        *,
        skill_name: str,
        script_path: str,
        arguments: list[str] | None = None,
        timeout_seconds: int = 60,
        cwd: str | None = None,
    ) -> str:
        skill = self.registry.get(skill_name)
        if skill is None:
            raise ValueError(f"Skill not found: {skill_name}")
        script = self.resolve_script(skill_name, script_path)
        args = [str(item) for item in (arguments or [])]
        timeout = max(1, min(int(timeout_seconds or 60), 300))
        working_dir = self._resolve_cwd(cwd, script.parent)
        command = [sys.executable, str(script), *args]
        self.trace.log(
            {
                "kind": "skill_script",
                "status": "started",
                "skill_name": skill_name,
                "script_path": str(script),
                "arguments": args,
                "cwd": str(working_dir),
                "timeout_seconds": timeout,
            }
        )

        try:
            def _run():
                return subprocess.run(
                    command,
                    cwd=str(working_dir),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )

            proc = await run_blocking(_run)
        except subprocess.TimeoutExpired:
            self.trace.log(
                {
                    "kind": "skill_script",
                    "status": "timeout",
                    "skill_name": skill_name,
                    "script_path": str(script),
                    "arguments": args,
                    "timeout_seconds": timeout,
                }
            )
            return f"Error: skill script timed out after {timeout} seconds: {script}"
        except Exception as exc:
            self.trace.log(
                {
                    "kind": "skill_script",
                    "status": "error",
                    "skill_name": skill_name,
                    "script_path": str(script),
                    "arguments": args,
                    "details": str(exc),
                }
            )
            return f"Error: {exc}"

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        status = "success" if proc.returncode == 0 else "error"
        self.trace.log(
            {
                "kind": "skill_script",
                "status": status,
                "skill_name": skill_name,
                "script_path": str(script),
                "arguments": args,
                "returncode": proc.returncode,
                "stdout_preview": stdout[:1000],
                "stderr_preview": stderr[:1000],
            }
        )
        result = [
            f"Skill script: {skill_name}/{script.relative_to(skill.dir.resolve()).as_posix()}",
            f"Return code: {proc.returncode}",
        ]
        if stdout:
            result.append(f"STDOUT:\n{stdout}")
        if stderr:
            result.append(f"STDERR:\n{stderr}")
        if proc.returncode != 0 and not stderr:
            result.append("Error: skill script exited with a non-zero status.")
        return "\n".join(result)[:20000]

    def _resolve_cwd(self, cwd: str | None, default: Path) -> Path:
        if not cwd:
            return default
        candidate = Path(cwd).expanduser().resolve()
        allowed_roots = [self.workspace.resolve(), Path.cwd().resolve()]
        if not any(_is_relative_to(candidate, root) for root in allowed_roots):
            raise ValueError("cwd must stay within the project/workspace.")
        return candidate


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
