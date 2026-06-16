from __future__ import annotations

from pathlib import Path
from typing import Any


class SkillPolicyEngine:
    """Build skill runtime instructions and delivery gates."""

    def build_runtime_lines(
        self,
        documents: list[dict[str, Any]],
        *,
        outbox_dir: Path | None = None,
    ) -> list[str]:
        lines = [
            "你当前命中了文档类 skill。执行时遵守以下运行时协议：",
            "1. 先按需阅读：默认只依据 `SKILL.md` 工作；只有当前任务确实需要更细规则时，才用 `read_file` 读取 reference/forms 等补充文档。",
            "2. 统一脚本执行入口：如果需要 skill 目录下的脚本，使用 `run_skill_script`，不要直接用 `exec` 拼脚本路径。",
            "3. `run_skill_script` 与其他工具一样按字段传参：skill_name、script_path、arguments、timeout_seconds；不要把 arguments 数组当成整个工具参数。",
            "4. 执行前先明确输入输出：在决定运行脚本前，先确认输入文件、输出文件、预期结果，避免盲目改动原件。",
            "5. 结果文件优先写入 outbox：不要直接覆盖 workspace/inbox 中用户上传的原始文件；脚本生成或修改后的交付物应写到当前会话的 outbox 目录。",
            "6. 执行后只依据真实结果汇报：必须依据 `run_skill_script` 的 stdout/stderr 或生成文件结果汇报，失败时要明确说失败了。",
            "7. 结果落盘后可用 `list_outbox_files` 检查当前会话的交付物。",
        ]
        if outbox_dir is not None:
            lines.append(f"当前会话 outbox 目录：{outbox_dir}")

        for doc in documents:
            lines.append(f"Skill: {doc['name']}")
            if doc["references"]:
                lines.append("可按需读取的补充文档：")
                for reference in doc["references"]:
                    lines.append(f"- {reference['path']}")
            if doc["scripts_dir"]:
                lines.append(f"可按需检查的脚本目录：{doc['scripts_dir']}")
                lines.append(
                    '脚本执行参数：skill_name="%s", script_path="scripts/<script>.py", arguments=["<arg1>"], timeout_seconds=60'
                    % doc["name"]
                )
            if str(doc["name"]).strip().lower() == "xlsx" and doc["scripts_dir"]:
                lines.append(
                    "如果当前生成或修改的 .xlsx 包含公式，交付前必须按需执行 "
                    '`run_skill_script(skill_name="xlsx", script_path="scripts/recalc.py", arguments=["<output_xlsx_path>"], timeout_seconds=60)`，'
                    "然后依据返回的 JSON 判断是否存在 `#REF!`、`#DIV/0!`、`#VALUE!`、`#NAME?` 等错误。"
                )
                lines.append(
                    "只有在重算结果 `status=success` 或明确确认没有公式错误时，才能向用户表述为可交付。"
                )
            if str(doc["name"]).strip().lower() == "weather" and doc["scripts_dir"]:
                lines.append(
                    "天气查询必须优先执行 "
                    '`run_skill_script(skill_name="weather", script_path="scripts/query_weather.py", arguments=["<location>"], timeout_seconds=60)`。'
                    "不要直接用 `exec` 拼接 wttr.in、curl 或 PowerShell 命令，除非统一脚本工具不可用。"
                )
        return lines
