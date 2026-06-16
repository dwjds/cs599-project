from __future__ import annotations

from pathlib import Path
from typing import Any

from miniagent_core.config import MODEL, SKILL_ROUTE_MODE, client

from .policy import SkillPolicyEngine
from .registry import SkillRecord, SkillRegistry
from .router import SkillRouter, build_attachment_haystacks
from .runtime import SkillRuntime, SkillTraceLogger
from .scanner import (
    SkillScanner,
    get_skill_description,
    get_skill_name,
    parse_skill_metadata,
)


class SkillLoader:
    """Facade for the project skill runtime."""

    def __init__(
        self,
        workspace: Path,
        builtin_dir: Path | None = None,
        *,
        runtime_workspace: Path | None = None,
        trace_sink: Any | None = None,
        route_mode: str = SKILL_ROUTE_MODE,
        llm_client: Any = client,
        model: str = MODEL,
    ):
        self.workspace = workspace
        self.runtime_workspace = runtime_workspace or workspace
        self.trace_sink = trace_sink
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_dir
        self.scanner = SkillScanner(self.workspace_skills, self.builtin_skills)
        self.registry = SkillRegistry(self.scanner)
        self.router = SkillRouter(mode=route_mode, llm_client=llm_client, model=model)
        self.policy = SkillPolicyEngine()
        self.runtime = SkillRuntime(self.registry, self.runtime_workspace)
        self.trace = SkillTraceLogger(self.runtime_workspace)

    def list_skills(self) -> list[dict[str, Any]]:
        return [skill.to_dict() for skill in self.registry.list_skills()]

    def get_skill(self, name: str) -> dict[str, Any] | None:
        skill = self.registry.get(name)
        return skill.to_dict() if skill else None

    async def run_skill_script(
        self,
        *,
        skill_name: str,
        script_path: str,
        arguments: list[str] | None = None,
        timeout_seconds: int = 60,
        cwd: str | None = None,
    ) -> str:
        return await self.runtime.run_script(
            skill_name=skill_name,
            script_path=script_path,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
        )

    def load_skill_documents(
        self,
        user_message: str | None = None,
        attachments: list[Any] | None = None,
        *,
        trace_activation: bool = True,
    ) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        decisions = self.router.select_with_scores(
            self.registry.list_skills(),
            user_message or "",
            attachments=attachments,
        )
        skills = [decision.skill for decision in decisions]
        if trace_activation and skills:
            event = {
                "kind": "skill_activation",
                "status": "selected",
                "route_status": self.router.last_route_status,
                "skills": [skill.name for skill in skills],
                "route": [decision.to_trace() for decision in decisions],
                "user_message_preview": (user_message or "")[:300],
            }
            self.trace.log(event)
            if self.trace_sink is not None:
                self.trace_sink.write(
                    "skill_activation",
                    status=event["status"],
                    route_status=event["route_status"],
                    skills=event["skills"],
                    route=event["route"],
                    user_message_preview=event["user_message_preview"],
                )
        for skill in skills:
            path = skill.path
            try:
                content = path.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if not content:
                continue
            documents.append(
                {
                    "name": str(skill.name),
                    "description": str(skill.description),
                    "content": content,
                    "triggers": list(skill.triggers),
                    "references": self._list_supplemental_markdown(skill.dir),
                    "scripts_dir": self._get_scripts_dir(skill.dir),
                }
            )
        return documents

    def build_prompt_section(
        self,
        user_message: str | None = None,
        attachments: list[Any] | None = None,
    ) -> str:
        documents = self.load_skill_documents(
            user_message=user_message,
            attachments=attachments,
            trace_activation=False,
        )
        if not documents:
            return ""

        parts = ["# Skills"]
        for doc in documents:
            skill_parts = [
                f"## {doc['name']}\n"
                f"Description: {doc['description']}\n\n"
                f"{doc['content']}"
            ]
            if doc["references"]:
                reference_lines = [
                    "### Supplemental References",
                    "这些补充文档不要立即阅读；只有当前任务确实需要更细的格式说明、边界条件或表单规则时，再用基础文件工具读取它们。",
                    "推荐先用 `read_file` 按路径读取相关补充文档，再决定是否继续执行。",
                ]
                for reference in doc["references"]:
                    reference_lines.append(
                        f"- {reference['title']}: {reference['path']}"
                    )
                skill_parts.append("\n".join(reference_lines))
            if doc["scripts_dir"]:
                script_lines = [
                    "### Script Workspace",
                    "这个 skill 目录下存在可复用脚本，但不要立即查看或执行全部脚本。",
                    "只有当前任务确实需要脚本能力时，再先用基础文件工具查看相关脚本或说明文档，然后再决定是否执行。",
                    "脚本目录："
                    f" `{doc['scripts_dir']}`",
                    "如果确认需要执行，请使用统一 `run_skill_script` 工具运行目标脚本。",
                    f"参数填写方式与其他工具一致：skill_name=\"{doc['name']}\"，script_path=\"scripts/<script>.py\"，arguments=[...]，timeout_seconds=60。",
                    "不要把 arguments 数组当成整个工具参数；它只是 `arguments` 字段的值。",
                ]
                skill_parts.append("\n".join(script_lines))
            parts.append("\n\n".join(skill_parts))
        return "\n\n".join(parts)

    def build_runtime_note(
        self,
        user_message: str | None = None,
        *,
        outbox_dir: Path | None = None,
        attachments: list[Any] | None = None,
    ) -> str:
        documents = self.load_skill_documents(
            user_message=user_message,
            attachments=attachments,
            trace_activation=True,
        )
        if not documents:
            return ""

        return "\n".join(self.policy.build_runtime_lines(documents, outbox_dir=outbox_dir))

    def select_script_skill_names(
        self,
        user_message: str | None = None,
        attachments: list[Any] | None = None,
    ) -> list[str]:
        documents = self.load_skill_documents(
            user_message=user_message,
            attachments=attachments,
            trace_activation=False,
        )
        return [
            str(doc["name"])
            for doc in documents
            if str(doc.get("scripts_dir") or "").strip()
        ]

    def _select_skills(
        self,
        skills: list[dict[str, Any]],
        user_message: str,
        *,
        attachments: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        records = [
            SkillRecord(
                name=str(skill.get("name", "")),
                path=Path(str(skill.get("path", ""))),
                dir=Path(str(skill.get("dir", ""))),
                description=str(skill.get("description", "")),
                triggers=list(skill.get("triggers") or []),
            )
            for skill in skills
        ]
        return [skill.to_dict() for skill in self.router.select(records, user_message, attachments=attachments)]

    def _build_attachment_haystacks(self, attachments: list[Any]) -> list[str]:
        return build_attachment_haystacks(attachments)

    def _get_metadata(self, path: Path) -> dict[str, Any]:
        return parse_skill_metadata(path)

    def _list_supplemental_markdown(self, skill_dir: Path) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        for name in ("reference.md", "references.md", "forms.md"):
            path = skill_dir / name
            if not path.exists():
                continue
            results.append(
                {
                    "title": path.name,
                    "path": str(path),
                }
            )
        return results

    def _get_scripts_dir(self, skill_dir: Path) -> str | None:
        scripts_dir = skill_dir / "scripts"
        if not scripts_dir.exists():
            return None
        return str(scripts_dir)

    def _get_description(self, path: Path) -> str:
        return get_skill_description(path)

    def _get_name(self, path: Path) -> str:
        return get_skill_name(path)
