from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from .attachments import Attachment

"""
目前不是“智能理解器”，而是一个集中式规则分类器。

统一输出 TurnIntent,
包含 requires_file_grounding、requires_output_file、requires_script、
source_format、target_format 等字段。
"""

@dataclass
class TurnIntent:
    """Structured runtime intent used by gates instead of scattered prompt hints."""

    operation: str = "answer"
    requires_file_grounding: bool = False
    requires_output_file: bool = False
    requires_script: bool = False
    target_format: str = ""
    source_format: str = ""
    is_outbox_listing: bool = False
    confidence: float = 0.5
    reasons: list[str] = field(default_factory=list)

    def to_trace_dict(self) -> dict[str, object]:
        return asdict(self)


FILE_FORMATS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".csv": "csv",
    ".md": "markdown",
    ".txt": "text",
}

FORMAT_ALIASES = {
    "pdf": "pdf",
    ".pdf": "pdf",
    "word": "docx",
    "docx": "docx",
    ".docx": "docx",
    "excel": "xlsx",
    "xlsx": "xlsx",
    ".xlsx": "xlsx",
    "markdown": "markdown",
    "md": "markdown",
    ".md": "markdown",
    "txt": "text",
    ".txt": "text",
}


def infer_turn_intent(
    user_text: str,
    *,
    attachments: Iterable[Attachment] | None = None,
    has_visible_attachments: bool = False,
    script_skill_names: Iterable[str] | None = None,
) -> TurnIntent:
    text = _normalize(_extract_user_request(user_text))
    attachment_list = list(attachments or [])
    has_files = bool(has_visible_attachments or attachment_list)
    script_skills = {str(name).lower() for name in script_skill_names or []}
    target_format = _target_format(text)
    source_format = _source_format(text, attachment_list, target_format)

    intent = TurnIntent(source_format=source_format, target_format=target_format)
    if not text:
        intent.confidence = 0.1
        return intent

    if _is_outbox_listing(text):
        intent.operation = "list_outputs"
        intent.is_outbox_listing = True
        intent.confidence = 0.95
        intent.reasons.append("outbox listing request")
        return intent

    if has_files and _requires_file_evidence(text):
        intent.requires_file_grounding = True
        intent.reasons.append("request refers to visible file content or file operation")

    if _is_chat_only_answer(text):
        intent.operation = "answer_from_file" if intent.requires_file_grounding else "answer"
        intent.confidence = 0.9
        intent.reasons.append("chat answer requested, not a saved artifact")
        return intent

    if _is_saved_artifact_request(text, target_format):
        intent.operation = "create_output"
        intent.requires_output_file = True
        intent.confidence = 0.95
        intent.reasons.append("explicit saved/exported artifact request")
    elif has_files and _is_file_mutation_request(text):
        intent.operation = "modify_file"
        intent.requires_output_file = True
        intent.confidence = 0.9
        intent.reasons.append("visible file mutation should produce an outbox copy")
    elif has_files and _is_file_transform_request(text, target_format):
        intent.operation = "transform_file"
        intent.requires_output_file = True
        intent.confidence = 0.9
        intent.reasons.append("visible file transform should produce an outbox artifact")
    else:
        intent.operation = "answer_from_file" if intent.requires_file_grounding else "answer"
        intent.confidence = 0.65

    if _requires_script(text, source_format, target_format, script_skills, intent):
        intent.requires_script = True
        intent.reasons.append("operation maps to a script-backed skill")

    return intent


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _extract_user_request(text: str) -> str:
    raw = str(text or "")
    match = re.search(r"用户要求[:：]\s*(.+?)(?:\n\n|回复要求[:：]|$)", raw, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw


def _source_format(text: str, attachments: list[Attachment], target_format: str = "") -> str:
    for attachment in attachments:
        suffix = Path(attachment.name or attachment.path).suffix.lower()
        if suffix in FILE_FORMATS:
            return FILE_FORMATS[suffix]
    if re.search(r"excel|xlsx|工作簿|电子表格", text, re.IGNORECASE) and target_format != "xlsx":
        return "xlsx"
    if re.search(r"pdf", text, re.IGNORECASE) and target_format != "pdf":
        return "pdf"
    if re.search(r"word|docx", text, re.IGNORECASE) and target_format != "docx":
        return "docx"
    if re.search(r"文本文件|txt", text, re.IGNORECASE) and target_format != "text":
        return "text"
    if re.search(r"markdown|\\.md|md文件", text, re.IGNORECASE) and target_format != "markdown":
        return "markdown"
    return ""


def _target_format(text: str) -> str:
    # Prefer formats appearing after explicit conversion/output words.
    match = re.search(
        r"(?:保存为|保存成|导出为|转成|转为|生成(?:一份|一个)?|输出(?:为)?|\bas\b|\bto\b)\s*([a-z.]+|pdf|word|excel|markdown|md|txt)",
        text,
        re.IGNORECASE,
    )
    if match:
        raw = match.group(1).lower()
        return FORMAT_ALIASES.get(raw, raw)
    for token, fmt in FORMAT_ALIASES.items():
        if token in text:
            return fmt
    return ""


def _is_outbox_listing(text: str) -> bool:
    if not re.search(r"\boutbox\b|输出目录|生成的文件|结果文件", text, re.IGNORECASE):
        return False
    return bool(re.search(r"列出|列一下|查看|看看|有哪些|有没有|是否有|\blist\b|\bshow\b", text, re.IGNORECASE))


def _is_chat_only_answer(text: str) -> bool:
    if _has_artifact_save_verb(text):
        return False
    if re.search(r"输出\s*(?:\d+|[一二三四五六七八九十几])\s*条|输出(?:要点|结论|建议|摘要|列表)", text):
        return True
    return bool(
        re.search(
            r"总结|摘要|说明|回答|有没有|是否|是什么|为什么|怎么看|几类|点名|summari[sz]e|explain",
            text,
            re.IGNORECASE,
        )
    )


def _requires_file_evidence(text: str) -> bool:
    if re.search(r"文件|附件|上传|文档|论文|表格|excel|xlsx|pdf|docx|word|里面|内容|sheet|工作簿", text, re.IGNORECASE):
        return True
    return _is_file_mutation_request(text) or _is_file_transform_request(text, _target_format(text))


def _has_artifact_save_verb(text: str) -> bool:
    return bool(
        re.search(
            r"保存|导出|另存|写入文件|生成文件|保存到|保存为|保存成|另存为|保存在|\bsave\b|\bexport\b|write to file|save as",
            text,
            re.IGNORECASE,
        )
    )


def _is_saved_artifact_request(text: str, target_format: str) -> bool:
    if _has_artifact_save_verb(text):
        return True
    if target_format and re.search(r"生成(?:一份|一个)?|输出为|作为", text):
        return True
    return False


def _is_file_mutation_request(text: str) -> bool:
    return bool(
        re.search(
            r"新增|添加|修改|编辑|删除|修复|修正|改正|更正|帮我修|直接修|计算|汇总|重算|校验|公式|筛选|过滤|接受修订|清除修订|edit|modify|add|delete|fix|repair|correct|calculate|recalc|filter",
            text,
            re.IGNORECASE,
        )
    )


def _is_file_transform_request(text: str, target_format: str) -> bool:
    if target_format and re.search(r"导出|转换|转成|转为|合并|拆分|分割|提取|抽取|截取|旋转|裁剪|export|convert|merge|split|extract|rotate", text, re.IGNORECASE):
        return True
    return bool(re.search(r"合并|拆分|分割|提取|抽取|截取|旋转|裁剪|merge|split|extract|rotate", text, re.IGNORECASE))


def _requires_script(
    text: str,
    source_format: str,
    target_format: str,
    script_skills: set[str],
    intent: TurnIntent,
) -> bool:
    if "weather" in script_skills and re.search(r"天气|温度|气温|降雨|下雨|风力|预报|weather|temperature|forecast|rain", text, re.IGNORECASE):
        return True
    if not script_skills:
        return False
    if intent.operation in {"modify_file", "transform_file"}:
        return True
    if source_format == "xlsx" and target_format == "pdf":
        return True
    if source_format == "pdf" and re.search(r"合并|抽页|提取|旋转|merge|extract|rotate", text, re.IGNORECASE):
        return True
    if source_format == "docx" and re.search(r"接受修订|清除修订|tracked changes|accept changes", text, re.IGNORECASE):
        return True
    return False
