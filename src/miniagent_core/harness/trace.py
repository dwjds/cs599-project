from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

FILE_GROUNDING_REQUEST_HINTS = (
    "文件",
    "附件",
    "上传",
    "pdf",
    "docx",
    "xlsx",
    "论文",
    "内容",
    "全文",
    "章节",
    "第二章",
    "第",
    "表格",
    "总结",
    "提取",
    "读取",
    "查看",
    "file",
    "attachment",
    "uploaded",
    "chapter",
    "page",
    "read",
    "extract",
    "summarize",
)

FILE_GROUNDING_CLAIM_HINTS = (
    "已读取",
    "已查看",
    "已重新查看",
    "已检查",
    "已重新检查",
    "已分析",
    "已重新分析",
    "读取了",
    "查看了",
    "检查了",
    "重新检查",
    "分析了",
    "确认：",
    "确认:",
    "文件内容",
    "全文未提及",
    "全文围绕",
    "未提及",
    "不存在任何",
    "第二章内容",
    "第二章全文",
    "第二章主题",
    "章节主题",
    "该论文内容",
    "该论文第二章",
    "当前文件",
    "无法支持",
    "文中",
    "全文",
    "无自动驾驶",
    "无智能驾驶",
    "无相关术语",
    "does not mention",
    "not mentioned",
    "i read",
    "i reread",
    "i checked",
    "i rechecked",
    "the file says",
)

FILE_GROUNDING_TOOLS = {
    "read_uploaded_file",
    "read_file",
    "run_skill_script",
    "web_fetch",
}

OUTPUT_FILE_REQUEST_HINTS = (
    "保存",
    "导出",
    "输出文件",
    "生成文件",
    "另存",
    "写入文件",
    "以word形式",
    "以 word 形式",
    "以pdf形式",
    "以 pdf 形式",
    "保存在pdf",
    "保存为pdf",
    "保存成pdf",
    "save",
    "export",
    "write to file",
    "save as",
    "as pdf",
    "as word",
)

OUTPUT_FILE_TYPE_HINTS = (
    "pdf",
    "word",
    "docx",
    "excel",
    "xlsx",
    "文件",
    "表格",
)

OUTPUT_FILE_CLAIM_HINTS = (
    "已保存",
    "保存至",
    "保存到",
    "已生成",
    "已输出",
    "输出目录",
    "文件名",
    "路径",
    "workspace/outbox",
    "saved",
    "generated file",
    "path:",
)

OUTPUT_FILE_TOOLS = {
    "save_outbox_file",
    "write_file",
    "run_skill_script",
}


@dataclass
class TraceEvent:
    kind: str
    payload: dict[str, Any]


@dataclass
class ToolEvent:
    tool: str
    params: dict[str, Any]
    duration_seconds: float
    failed: bool
    failure_type: str = ""
    result_preview: str = ""


def classify_tool_failure(tool_name: str, result: str) -> tuple[bool, str]:
    text = str(result or "")
    if text.startswith("Error:"):
        if "Skill script not found" in text:
            return True, "skill_script_not_found"
        if "Tool '" in text and "not found" in text:
            return True, "tool_not_found"
        if "invalid tool arguments" in text:
            return True, "invalid_tool_arguments"
        return True, "tool_error"
    if tool_name == "run_skill_script" and "Return code:" in text and "Return code: 0" not in text:
        if "Skill script not found" in text:
            return True, "skill_script_not_found"
        return True, "skill_script_nonzero"
    return False, ""


def looks_like_file_grounding_request(text: str) -> bool:
    candidate = (text or "").strip().lower()
    if not candidate:
        return False
    return any(hint.lower() in candidate for hint in FILE_GROUNDING_REQUEST_HINTS)


def looks_like_file_grounding_claim(reply: str) -> bool:
    candidate = (reply or "").strip().lower()
    if not candidate:
        return False
    return any(hint.lower() in candidate for hint in FILE_GROUNDING_CLAIM_HINTS)


def has_file_grounding_tool(tool_names: list[str] | tuple[str, ...] | set[str]) -> bool:
    return any(str(name or "") in FILE_GROUNDING_TOOLS for name in tool_names)


def looks_like_output_file_request(text: str) -> bool:
    candidate = (text or "").strip().lower()
    if not candidate:
        return False
    if any(hint.lower() in candidate for hint in OUTPUT_FILE_REQUEST_HINTS):
        return True
    if "生成" in candidate and any(hint.lower() in candidate for hint in OUTPUT_FILE_TYPE_HINTS):
        return True
    if "输出" in candidate and any(hint.lower() in candidate for hint in OUTPUT_FILE_TYPE_HINTS):
        return True
    return False


def looks_like_output_file_claim(reply: str) -> bool:
    candidate = (reply or "").strip().lower()
    if not candidate:
        return False
    if any(hint.lower() in candidate for hint in OUTPUT_FILE_CLAIM_HINTS):
        return True
    return bool(re.search(r"workspace[/\\]+outbox|[^\s`]+\\.(pdf|docx|xlsx|md|txt)", candidate))


def has_output_file_tool(tool_names: list[str] | tuple[str, ...] | set[str]) -> bool:
    return any(str(name or "") in OUTPUT_FILE_TOOLS for name in tool_names)


def detect_grounding_violation(
    *,
    user_text: str,
    reply: str,
    tool_names: list[str] | tuple[str, ...] | set[str],
) -> dict[str, Any] | None:
    """Detect a file-content claim that was not backed by a read/extraction tool."""

    if not looks_like_file_grounding_request(user_text):
        return None
    if not looks_like_file_grounding_claim(reply):
        return None
    if has_file_grounding_tool(tool_names):
        return None
    return {
        "violation_type": "file_grounding_without_tool",
        "user_preview": str(user_text or "")[:500],
        "reply_preview": str(reply or "")[:500],
        "tool_names": list(tool_names),
    }


def detect_output_violation(
    *,
    user_text: str,
    reply: str,
    tool_names: list[str] | tuple[str, ...] | set[str],
) -> dict[str, Any] | None:
    """Detect a saved-file claim that was not backed by an output tool."""

    if not looks_like_output_file_request(user_text):
        return None
    if not looks_like_output_file_claim(reply):
        return None
    if has_output_file_tool(tool_names):
        return None
    return {
        "violation_type": "output_file_claim_without_tool",
        "user_preview": str(user_text or "")[:500],
        "reply_preview": str(reply or "")[:500],
        "tool_names": list(tool_names),
    }


class TraceSink:
    """Append runtime trace events as JSONL."""

    def __init__(
        self,
        *,
        workspace: Path,
        run_id: str = "",
        session_key: str = "",
        mode: str = "",
        filename: str = "runtime_trace.jsonl",
    ):
        self.workspace = workspace
        self.run_id = run_id
        self.session_key = session_key
        self.mode = mode
        self.trace_file = workspace / "traces" / filename
        self.trace_file.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, **payload: Any) -> None:
        event = {
            "timestamp": datetime.now().isoformat(),
            "kind": kind,
            "run_id": self.run_id,
            "session_key": self.session_key,
            "mode": self.mode,
            **payload,
        }
        with open(self.trace_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


class InstrumentedToolRegistry:
    """Wrap a ToolRegistry-compatible object and collect tool-call metrics."""

    def __init__(self, base: Any):
        self.base = base
        self.events: list[ToolEvent] = []

    def get_definitions(self) -> list[dict[str, Any]]:
        return self.base.get_definitions()

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        start = time.perf_counter()
        result = await self.base.execute(name, params)
        duration = time.perf_counter() - start
        failed, failure_type = classify_tool_failure(name, result)
        self.events.append(
            ToolEvent(
                tool=name,
                params=params,
                duration_seconds=round(duration, 4),
                failed=failed,
                failure_type=failure_type,
                result_preview=str(result or "")[:500],
            )
        )
        return result
