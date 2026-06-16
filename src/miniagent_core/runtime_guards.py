"""
剩余词表只用于兜底检查“假装已保存/已执行/还在处理中”，不再作为核心任务意图来源。
"""

from __future__ import annotations

import re

ACTION_REQUEST_HINTS = (
    "打开","关闭","最小化","恢复","激活","切到","点击","发送","截图","保存","检查","列出",
    "查找","输入","拖拽","切换","处理","生成","筛选","提取","汇总","计算","转换","修改","新增",
    "删除","导出","总结","restore","activate","click","send","screenshot","save","inspect","list",
    "process","generate","filter","extract","summarize","calculate","convert","modify","edit","export",
)

TOOL_CLAIM_HINTS = (
    "browser_automation(",
    "exec(",
    "web_search(",
    "web_fetch(",
    "已执行",
    "已完成",
    "成功捕获",
    "验证结果",
)

INCOMPLETE_PROGRESS_HINTS = (
    "正在处理",
    "正在生成",
    "正在执行",
    "正在筛选",
    "正在读取",
    "正在保存",
    "正在计算",
    "下一步将",
    "接下来将",
    "将继续",
    "我将",
    "稍等",
    "请稍候",
    "处理中",
    "正在处理……",
    "processing",
    "working on it",
    "next i will",
    "i will now",
)

FULL_ATTACHMENT_OUTPUT_HINTS = (
    "全文",
    "完整内容",
    "原文",
    "逐字",
    "逐段",
    "完整表格",
    "所有内容",
    "全文输出",
    "详细展开",
    "完整展开",
    "full text",
    "verbatim",
    "raw content",
    "print all",
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


def looks_like_action_request(text: str) -> bool:
    candidate = (text or "").strip().lower()
    return bool(candidate and any(hint in candidate for hint in ACTION_REQUEST_HINTS))


def looks_like_tool_claim(reply: str) -> bool:
    candidate = (reply or "").strip().lower()
    return bool(candidate and any(hint in candidate for hint in TOOL_CLAIM_HINTS))


def looks_like_output_file_claim(reply: str) -> bool:
    candidate = (reply or "").strip().lower()
    if not candidate:
        return False
    if re.search(
        r"no generated files|no file (?:has been )?saved|没有(?:生成|保存)|未(?:生成|保存)|尚未(?:生成|保存)",
        candidate,
        re.IGNORECASE,
    ):
        return False
    if any(hint in candidate for hint in OUTPUT_FILE_CLAIM_HINTS):
        return True
    return bool(re.search(r"workspace[/\\]+outbox|[^\s`]+\\.(pdf|docx|xlsx|md|txt)", candidate))


def looks_like_incomplete_progress(reply: str) -> bool:
    candidate = (reply or "").strip().lower()
    return bool(candidate and any(hint in candidate for hint in INCOMPLETE_PROGRESS_HINTS))


def wants_full_attachment_output(text: str) -> bool:
    candidate = (text or "").strip().lower()
    return bool(candidate and any(hint in candidate for hint in FULL_ATTACHMENT_OUTPUT_HINTS))
