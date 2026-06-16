from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .async_compat import run_blocking
from .config import PROJECT_ROOT
from .skills import SkillLoader


@dataclass
class Session:
    """会话类，管理对话历史"""

    key: str
    messages: list[dict] = field(default_factory=list)

    def get_history(self, max_messages: int = 50) -> list[dict]:
        recent = self.messages[-max_messages:]
        for index, message in enumerate(recent):
            if message.get("role") == "user":
                return recent[index:]
        return recent


class SessionManager:
    """会话管理器，负责创建和加载会话"""

    def __init__(self, workspace: Path):
        self.dir = workspace / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Session] = {}

    def get_or_create(self, key: str) -> Session:
        if key in self._cache:
            return self._cache[key]
        session = self._load(key) or Session(key=key)
        self._cache[key] = session
        return session

    def save(self, session: Session):
        path = self.dir / f"{session.key.replace(':', '__')}.jsonl"
        with open(path, "w", encoding="utf-8") as handle:
            for message in session.messages:
                handle.write(json.dumps(message, ensure_ascii=False) + "\n")

    def reset(self, key: str) -> Session:
        session = Session(key=key)
        self._cache[key] = session
        self.save(session)
        return session

    def _load(self, key: str) -> Session | None:
        path = self.dir / f"{key.replace(':', '__')}.jsonl"
        if not path.exists():
            return None
        messages = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return Session(key=key, messages=messages)


@dataclass
class HistoryRecord:
    timestamp: str
    session_key: str
    summary: str
    topic: str = ""
    keywords: list[str] = field(default_factory=list)
    message_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "session_key": self.session_key,
            "summary": self.summary,
            "topic": self.topic,
            "keywords": list(self.keywords),
            "message_count": self.message_count,
        }


@dataclass
class MemoryItem:
    id: str
    timestamp: str
    updated_at: str
    source: str
    type: str
    topic: str
    summary: str
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.8
    active: bool = True
    embedding: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "updated_at": self.updated_at or self.timestamp,
            "source": self.source,
            "type": self.type,
            "topic": self.topic,
            "summary": self.summary,
            "keywords": list(self.keywords),
            "tags": list(self.tags),
            "confidence": float(self.confidence),
            "active": bool(self.active),
            "embedding": list(self.embedding) if self.embedding else [],
        }


def _normalize_keywords(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_values = re.split(r"[,\n;|、，]+", values)
    else:
        raw_values = list(values)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def _tokenize_text(value: str) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    tokens = re.findall(r"[\u4e00-\u9fff]{1,8}|[a-z0-9_./:+-]+", text)
    return {token for token in tokens if token}


def _keyword_overlap_score(query_tokens: set[str], item: MemoryItem) -> float:
    if not query_tokens:
        return 0.0
    item_tokens = set()
    item_tokens.update(_tokenize_text(item.topic))
    item_tokens.update(_tokenize_text(item.summary))
    for value in item.keywords + item.tags:
        item_tokens.update(_tokenize_text(value))
    if not item_tokens:
        return 0.0
    overlap = query_tokens & item_tokens
    if not overlap:
        return 0.0
    return len(overlap) / max(len(query_tokens), 1)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _memory_item_id(item_type: str, topic: str, summary: str) -> str:
    payload = f"{item_type.strip().lower()}|{topic.strip().lower()}|{summary.strip().lower()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


MEMORY_ITEM_ALLOWED_TYPES = {
    "profile",
    "preference",
    "project",
    "fact",
    "workflow",
    "tooling",
}
MEMORY_ITEM_SCHEMA_FIELDS = (
    "id",
    "timestamp",
    "updated_at",
    "source",
    "type",
    "topic",
    "summary",
    "keywords",
    "tags",
    "confidence",
    "active",
    "embedding",
)
TRANSIENT_MEMORY_HINTS = (
    "刚刚",
    "这一轮",
    "这轮对话",
    "这次对话",
    "用户问了",
    "随后",
    "接着",
    "临时",
    "中间状态",
)
UNRESOLVED_MEMORY_HINTS = (
    "未解决",
    "无法执行",
    "执行失败",
    "工具失败",
    "报错",
    "error:",
    "traceback",
    "not found",
)
REDUNDANT_MEMORY_HINTS = (
    "文件列表",
    "上传了以下",
    "完整内容如下",
    "原文如下",
    "逐段内容",
)


def _normalize_timestamp(value: str, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback or datetime.now().isoformat()


def _is_transient_summary(summary: str) -> bool:
    lowered = str(summary or "").strip().lower()
    if not lowered:
        return True
    if any(hint in lowered for hint in UNRESOLVED_MEMORY_HINTS):
        return True
    if any(hint in lowered for hint in TRANSIENT_MEMORY_HINTS):
        return True
    if any(hint in lowered for hint in REDUNDANT_MEMORY_HINTS):
        return True
    return False


def _is_allowed_memory_item(item: MemoryItem) -> bool:
    if item.type not in MEMORY_ITEM_ALLOWED_TYPES:
        return False
    summary = str(item.summary or "").strip()
    topic = str(item.topic or "").strip()
    if not summary or len(summary) < 8:
        return False
    if len(summary) > 240:
        return False
    if _is_transient_summary(summary):
        return False
    if topic and len(topic) > 80:
        return False
    if item.confidence <= 0:
        return False
    return True


def _memory_exact_key(item: MemoryItem) -> tuple[str, str, str]:
    return (
        item.type.strip().lower(),
        item.topic.strip().lower(),
        item.summary.strip().lower(),
    )


def _memory_topic_key(item: MemoryItem) -> tuple[str, str]:
    return (
        item.type.strip().lower(),
        item.topic.strip().lower(),
    )


def _prefer_memory_item(left: MemoryItem, right: MemoryItem) -> MemoryItem:
    left_score = (max(0.0, min(left.confidence, 1.0)), _normalize_timestamp(left.updated_at, left.timestamp))
    right_score = (max(0.0, min(right.confidence, 1.0)), _normalize_timestamp(right.updated_at, right.timestamp))
    if right_score > left_score:
        return right
    return left


class ContextBuilder:
    """上下文构建器，生成系统提示词"""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
    FILE_EXTENSIONS = (
        ".py",".js",".ts",".tsx",".jsx",".java",".go",".rs",".cpp",".c",".h",".hpp",
        ".cs",".json",".yaml",".yml",".toml",".md",".txt",".html",".css",".sql",".sh",
    )
    FILE_KEYWORDS = (
        "文件","代码","源码","路径","目录","函数", "方法","类","模块","脚本","配置","readme", 
        "file", "code","path","folder","directory","function","class","module",
    )
    FILE_ACTION_KEYWORDS = (
        "检查","查看","阅读","分析","修改","定位","搜索","查找","打开","inspect","read",
        "analyze","modify","edit","search","find","open",
    )
    WEB_SEARCH_KEYWORDS = (
        "搜索","搜一下","查一下","查查","查找","最新","官网","文档","新闻","资料","教程",
        "recommend","search","look up","latest","official","documentation","docs","news",
    )
    WEB_FETCH_KEYWORDS = (
        "网页正文","页面文本","网页内容","页面内容","网页源码","html","抓取网页","读取网页",
        "fetch","scrape","page text","raw html",
    )
    BROWSER_KEYWORDS = (
        "打开网页","浏览网站","点击","填写","表单","输入","截图","动态页面","浏览器",
        "页面操作","open website","browse","click","fill","form","type into","screenshot",
    )
    TOOL_USE_POLICY_SECTIONS = {
        "file": (
            "当用户提到文件名、代码文件、路径，且意图是检查、阅读、分析、修改、定位代码时：\n"
            "- 如果用户给的是完整路径，直接使用 `read_file`\n"
            "- 如果用户只给了文件名或模糊路径，必须先调用 `find_files`\n"
            "- 找到候选文件后，再调用 `read_file`\n"
            "- 如果要查找代码内容，使用 `search_code`\n"
            "- 没有实际调用工具时，不要声称“我已经搜索过”“我执行了 find_files”\n"
            "- 不要把推测当成工具结果返回给用户"
        ),
        "web_search": (
            "当用户需要联网搜索、查最新资料、寻找官网/文档/新闻链接，但还不知道目标网址时：\n"
            "- 优先考虑调用 `web_search`\n"
            "- 不要把你自己的常识记忆说成已经联网搜索过"
        ),
        "web_fetch": (
            "当用户已经给出网址，或者明确要抓取某个网页正文、页面文本、HTML 内容时：\n"
            "- 优先考虑调用 `web_fetch`\n"
            "- 如果只是读取页面内容，不要先绕到浏览器自动化"
        ),
        "browser": (
            "当用户希望你打开网页、浏览网站、点击网页元素、填写表单、抓取动态页面文本、网页截图时：\n"
            "- 优先考虑调用 `browser_automation`\n"
            "- 网页交互任务优先用浏览器自动化，不要改用桌面鼠标点击浏览器界面，除非浏览器工具做不到"
        ),
        "file_browser": (
            "当一个任务既涉及本地文件又涉及浏览器操作时：\n"
            "- 先用文件工具定位或生成文件，再用 `browser_automation` 完成后续交互"
        ),
    }
    TOOL_GROUNDING_POLICY = (
        "## Tool Grounding Policy\n\n"
        "- 工具返回结果是最高优先级事实来源\n"
        "- 如果工具返回 `Error:`，必须明确告诉用户该工具失败了，不能继续声称执行成功\n"
        "- 如果工具结果包含 `\"confirmed\": false` 或明显写着仅尝试/未验证，就不能把结果表述成“已成功完成”，必须明确说只是尝试执行，尚未独立验证\n"
        "- 不要编造未被工具结果明确支持的结论\n"
        "- 当结果不确定时，要直接说不确定，并建议下一步实际工具动作"
    )
    BASE_HISTORY_NOTE = (
        "你会收到当前会话最近的历史消息，这些消息来自本地持久化 session 文件。"
        "必须优先依据这些历史消息作答，不要否认自己看到了会话历史。"
        "另外要明确区分：项目根目录与 workspace 目录不是同一个位置。"
        "当用户只说“当前目录”“项目目录”“根目录”时，优先理解为项目根目录；"
        "只有用户明确说 workspace 时，才理解为 workspace 目录。"
    )
    HISTORY_POLICY_SNIPPETS = {
        "file": "当用户只给出文件名（如 `app.py`）并要求检查代码时，先 `find_files`，再 `read_file`。",
        "web_search": "当用户要你联网搜资料但没有给定网址时，优先调用 `web_search`。",
        "web_fetch": "当用户已经给出网址并要读取网页内容时，优先调用 `web_fetch`。",
        "browser": (
            "当用户请求真实执行网页操作时，优先实际调用 `browser_automation`，"
            "不要默认只给步骤说明。"
        ),
        "grounding": (
            "如果历史消息里曾经出现过助手自称“已执行”“已截图”“已保存文件”等说法，但当前回合没有对应工具结果，不能复述这些旧结论。"
            "如果工具失败或没有返回足够证据，不要把推测写成已经验证的事实。"
        ),
    }

    def __init__(
        self,
        workspace: Path,
        skill_loader: SkillLoader | None = None,
        bootstrap_workspace: Path | None = None,
    ):
        self.workspace = workspace
        self.skill_loader = skill_loader
        self.bootstrap_workspace = bootstrap_workspace or workspace

    def build_system_prompt(
        self,
        user_message: str | None = None,
        attachments: list[Any] | None = None,
    ) -> str:
        parts = [
            f"# Mini Agent\n\n你是一个有帮助的AI助手。\n\n"
            f"项目根目录：{PROJECT_ROOT}\n"
            f"工作区目录：{self.workspace}\n"
            f"长期记忆总览视图：{self.workspace}/memory/MEMORY.md\n"
            f"结构化长期记忆主库：{self.workspace}/memory/memory_store.jsonl\n"
            f"结构化历史摘要日志：{self.workspace}/memory/history.jsonl\n"
            "记忆检索只以 `memory_store.jsonl` 为事实源，不直接整份注入 `MEMORY.md`。\n"
            "默认回复要像聊天，不要写成报告或 Markdown 文档。"
            "少用 `###`、`---`、过度加粗和装饰符号；除非用户要求表格、代码或正式文档，"
            "否则用自然段和很短的列表即可。"
        ]
        policy_sections = self._build_policy_sections(user_message or "")
        if policy_sections:
            parts.append("\n\n".join(policy_sections))
        for filename in self.BOOTSTRAP_FILES:
            path = self.bootstrap_workspace / filename
            if path.exists():
                parts.append(f"## {filename}\n\n{path.read_text(encoding='utf-8')}")

        if self.skill_loader is not None:
            skills_section = self.skill_loader.build_prompt_section(
                user_message=user_message,
                attachments=attachments,
            ).strip()
            if skills_section:
                parts.append(skills_section)
        return "\n\n".join(parts)

    def build_messages(
        self,
        history: list[dict],
        user_message: str,
        attachments: list[Any] | None = None,
        extra_system_notes: list[str] | None = None,
    ) -> list[dict]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        history_note = self.build_history_note(user_message)
        attachment_note = ""
        if attachments:
            attachment_note = (
                "当前回合包含上传文件。需要查看文件内容时，优先使用 "
                "`list_uploaded_files` 和 `read_uploaded_file`；"
                "需要生成交付物时，使用 `save_outbox_file` 保存到 workspace/outbox。"
                "除非用户明确要求全文、原文或完整表格，否则默认只给简短摘要和关键结论，"
                "不要大段复述文件内容。回复风格保持聊天化，不要使用报告式标题、分隔线或大量加粗。"
            )
        system_notes = [
            {"role": "system", "content": self.build_system_prompt(user_message, attachments=attachments)},
            {"role": "system", "content": history_note},
        ]
        if attachment_note:
            system_notes.append({"role": "system", "content": attachment_note})
        for note in extra_system_notes or []:
            if str(note or "").strip():
                system_notes.append({"role": "system", "content": str(note)})
        return [
            *system_notes,
            *history,
            {"role": "user", "content": f"[Time: {now}]\n\n{user_message}"},
        ]

    def build_history_note(self, user_message: str | None = None) -> str:
        active_policies = self._detect_active_policies(user_message or "")
        parts = [self.BASE_HISTORY_NOTE]
        for policy_name in ("file", "web_search", "web_fetch", "browser"):
            if policy_name in active_policies:
                parts.append(self.HISTORY_POLICY_SNIPPETS[policy_name])
        if active_policies:
            parts.append(self.HISTORY_POLICY_SNIPPETS["grounding"])
        return "".join(parts)

    def _build_policy_sections(self, user_message: str) -> list[str]:
        active_policies = self._detect_active_policies(user_message)
        if not active_policies:
            return []

        sections = ["## Tool Use Policy\n"]
        for policy_name in ("file", "web_search", "web_fetch", "browser", "file_browser"):
            if policy_name in active_policies:
                sections.append(self.TOOL_USE_POLICY_SECTIONS[policy_name])
        sections.append(self.TOOL_GROUNDING_POLICY)
        return ["\n\n".join(sections)]

    def _detect_active_policies(self, user_message: str) -> set[str]:
        text = (user_message or "").strip()
        lowered = text.lower()
        active: set[str] = set()

        has_url = bool(re.search(r"https?://", text, flags=re.IGNORECASE))
        file_like = self._looks_like_file_request(lowered)
        web_search_like = self._looks_like_web_search_request(lowered, has_url)
        web_fetch_like = self._looks_like_web_fetch_request(lowered, has_url)
        browser_like = self._looks_like_browser_request(lowered)

        if file_like:
            active.add("file")
        if web_search_like:
            active.add("web_search")
        if web_fetch_like:
            active.add("web_fetch")
        if browser_like:
            active.add("browser")
        if file_like and browser_like:
            active.add("file_browser")
        return active

    def _looks_like_file_request(self, lowered: str) -> bool:
        has_path = "/" in lowered or "\\" in lowered
        has_extension = any(ext in lowered for ext in self.FILE_EXTENSIONS)
        has_file_keyword = any(keyword in lowered for keyword in self.FILE_KEYWORDS)
        has_file_action = any(keyword in lowered for keyword in self.FILE_ACTION_KEYWORDS)
        return has_path or has_extension or (has_file_keyword and has_file_action)

    def _looks_like_web_search_request(self, lowered: str, has_url: bool) -> bool:
        if has_url:
            return False
        return any(keyword in lowered for keyword in self.WEB_SEARCH_KEYWORDS)

    def _looks_like_web_fetch_request(self, lowered: str, has_url: bool) -> bool:
        if has_url:
            return True
        return any(keyword in lowered for keyword in self.WEB_FETCH_KEYWORDS)

    def _looks_like_browser_request(self, lowered: str) -> bool:
        return any(keyword in lowered for keyword in self.BROWSER_KEYWORDS)


class MemoryStore:
    """记忆存储。

    文件职责固定如下：
    - `sessions/*.jsonl`：短期会话原始记录
    - `history.jsonl`：每次 consolidation 的结构化历史摘要日志
    - `memory_store.jsonl`：长期记忆主库，也是检索唯一事实源
    - `MEMORY.md`：由 `memory_store.jsonl` 派生的人类可读汇总视图，不作为检索主源
    """

    def __init__(self, workspace: Path):
        mem_dir = workspace / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = mem_dir / "MEMORY.md"
        self.history_file = mem_dir / "HISTORY.md"
        self.history_index_file = mem_dir / "history.jsonl"
        self.memory_store_file = mem_dir / "memory_store.jsonl"
        self.trace_file = mem_dir / "consolidation_trace.jsonl"
        self._migrate_legacy_trace_file()

    def read_memory(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_memory(self, content: str):
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str):
        with open(self.history_file, "a", encoding="utf-8") as handle:
            handle.write(entry.rstrip() + "\n\n")

    def append_history_record(self, record: HistoryRecord):
        with open(self.history_index_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def read_history_records(self, limit: int | None = None) -> list[HistoryRecord]:
        if not self.history_index_file.exists():
            return []
        records: list[HistoryRecord] = []
        for line in self.history_index_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "summary" not in payload:
                continue
            records.append(
                HistoryRecord(
                    timestamp=str(payload.get("timestamp", "")),
                    session_key=str(payload.get("session_key", "")),
                    summary=str(payload.get("summary", "")),
                    topic=str(payload.get("topic", "")),
                    keywords=_normalize_keywords(payload.get("keywords") or []),
                    message_count=int(payload.get("message_count", 0) or 0),
                )
            )
        if limit is not None and limit > 0:
            return records[-limit:]
        return records

    def read_memory_items(self, active_only: bool = True) -> list[MemoryItem]:
        if not self.memory_store_file.exists():
            return []
        items: list[MemoryItem] = []
        for line in self.memory_store_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = MemoryItem(
                id=str(payload.get("id") or ""),
                timestamp=str(payload.get("timestamp", "")),
                updated_at=_normalize_timestamp(
                    str(payload.get("updated_at", "") or ""),
                    str(payload.get("timestamp", "")),
                ),
                source=str(payload.get("source", "")),
                type=str(payload.get("type", "fact") or "fact"),
                topic=str(payload.get("topic", "")),
                summary=str(payload.get("summary", "")),
                keywords=_normalize_keywords(payload.get("keywords") or []),
                tags=_normalize_keywords(payload.get("tags") or []),
                confidence=float(payload.get("confidence", 0.8) or 0.8),
                active=bool(payload.get("active", True)),
                embedding=list(payload.get("embedding") or []),
            )
            if not item.id:
                item.id = _memory_item_id(item.type, item.topic, item.summary)
            if active_only and not item.active:
                continue
            if not item.summary.strip():
                continue
            items.append(item)
        return items

    def upsert_memory_items(self, items: list[MemoryItem]):
        now = datetime.now().isoformat()
        existing = {item.id: item for item in self.read_memory_items(active_only=False)}
        for item in items:
            item.timestamp = _normalize_timestamp(item.timestamp, now)
            item.updated_at = _normalize_timestamp(item.updated_at, item.timestamp)
            item.keywords = _normalize_keywords(item.keywords)
            item.tags = _normalize_keywords(item.tags)
            item.type = str(item.type or "fact").strip() or "fact"
            item.topic = str(item.topic or "").strip()
            item.summary = str(item.summary or "").strip()
            item.confidence = max(0.0, min(float(item.confidence or 0.0), 1.0))
            if not _is_allowed_memory_item(item):
                continue

            exact_match_id = next(
                (
                    existing_id
                    for existing_id, existing_item in existing.items()
                    if _memory_exact_key(existing_item) == _memory_exact_key(item)
                ),
                None,
            )
            if exact_match_id is not None:
                merged = _prefer_memory_item(existing[exact_match_id], item)
                merged.active = True
                merged.updated_at = now
                if merged is item and not merged.embedding and existing[exact_match_id].embedding:
                    merged.embedding = list(existing[exact_match_id].embedding or [])
                existing[exact_match_id] = merged
                continue

            conflicting_ids = [
                existing_id
                for existing_id, existing_item in existing.items()
                if existing_item.active
                and _memory_topic_key(existing_item) == _memory_topic_key(item)
                and _memory_exact_key(existing_item) != _memory_exact_key(item)
            ]
            if conflicting_ids:
                winner = item
                for existing_id in conflicting_ids:
                    winner = _prefer_memory_item(existing[existing_id], winner)
                if winner is item:
                    for existing_id in conflicting_ids:
                        existing_item = existing[existing_id]
                        existing_item.active = False
                        existing_item.updated_at = now
                        existing[existing_id] = existing_item
                    item.active = True
                    item.updated_at = now
                    existing[item.id] = item
                else:
                    winner_id = next(
                        (
                            existing_id
                            for existing_id in conflicting_ids
                            if existing[existing_id] is winner
                        ),
                        None,
                    )
                    for existing_id in conflicting_ids:
                        existing_item = existing[existing_id]
                        existing_item.active = existing_id == winner_id
                        existing_item.updated_at = now
                        existing[existing_id] = existing_item
                    item.active = False
                    item.updated_at = now
                    existing[item.id] = item
                continue

            item.active = True
            item.updated_at = now
            existing[item.id] = item

        ordered = sorted(existing.values(), key=lambda item: (_normalize_timestamp(item.updated_at, item.timestamp), item.id))
        with open(self.memory_store_file, "w", encoding="utf-8") as handle:
            for item in ordered:
                handle.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        self.write_memory(self.render_memory_overview([item for item in ordered if item.active]))

    def render_memory_overview(self, items: list[MemoryItem]) -> str:
        if not items:
            return ""
        order = ["profile", "preference", "project", "workflow", "tooling", "fact"]
        titles = {
            "profile": "Profile",
            "preference": "Preferences",
            "project": "Project Context",
            "workflow": "Workflow Results",
            "tooling": "Tooling / Environment",
            "fact": "Facts",
        }
        grouped: dict[str, list[MemoryItem]] = {key: [] for key in order}
        extra_keys: list[str] = []
        for item in items:
            bucket = item.type if item.type in grouped else item.type
            if bucket not in grouped:
                grouped[bucket] = []
                extra_keys.append(bucket)
            grouped[bucket].append(item)

        lines = ["# Long-Term Memory Overview"]
        for bucket in [*order, *extra_keys]:
            bucket_items = grouped.get(bucket) or []
            if not bucket_items:
                continue
            lines.append(f"\n## {titles.get(bucket, bucket.title())}")
            for item in bucket_items:
                topic = f" [{item.topic}]" if item.topic else ""
                lines.append(f"-{topic} {item.summary}")
        return "\n".join(lines).strip()

    async def build_relevant_memory_note(
        self,
        query: str,
        *,
        client: Any,
        embedding_model: str,
        top_k: int,
        candidate_pool: int,
        trace_sink: Any | None = None,
    ) -> str:
        items = await self.retrieve_relevant_memory(
            query,
            client=client,
            embedding_model=embedding_model,
            top_k=top_k,
            candidate_pool=candidate_pool,
        )
        if trace_sink is not None:
            try:
                trace_sink.write(
                    "memory_retrieval",
                    query=query,
                    top_k=top_k,
                    candidate_pool=candidate_pool,
                    retrieved_ids=[item.id for item in items],
                    hit=bool(items),
                )
            except Exception as exc:
                print(f"[Trace] Failed to write memory_retrieval: {exc}")
        if not items:
            return ""
        lines = ["# Relevant Memory"]
        for item in items:
            label = item.type or "memory"
            suffix = f" ({item.topic})" if item.topic else ""
            lines.append(f"- [{label}]{suffix} {item.summary}")
        return "\n".join(lines)

    async def retrieve_relevant_memory(
        self,
        query: str,
        *,
        client: Any,
        embedding_model: str,
        top_k: int = 4,
        candidate_pool: int = 8,
    ) -> list[MemoryItem]:
        memory_items = self.read_memory_items(active_only=True)
        if not memory_items:
            return []

        query_text = str(query or "").strip()
        if not query_text:
            return []
        query_tokens = _tokenize_text(query_text)
        query_embedding: list[float] = []
        if client is not None and embedding_model.strip():
            try:
                query_embedding = await self._embed_text(client, embedding_model, query_text)
            except Exception as exc:
                print(f"  [Memory] Embedding retrieval failed, fallback to lexical rerank: {exc}")

        scored: list[tuple[float, float, MemoryItem]] = []
        for item in memory_items:
            embedding_score = 0.0
            if query_embedding and item.embedding:
                embedding_score = _cosine_similarity(query_embedding, item.embedding)
            lexical_score = _keyword_overlap_score(query_tokens, item)
            base_score = embedding_score if query_embedding else lexical_score
            scored.append((base_score, lexical_score, item))

        scored.sort(key=lambda triple: (triple[0], triple[1], triple[2].confidence), reverse=True)
        pool = scored[: max(top_k, candidate_pool)]

        reranked: list[tuple[float, MemoryItem]] = []
        for base_score, lexical_score, item in pool:
            topic_tokens = _tokenize_text(item.topic)
            topic_bonus = 0.08 if query_tokens and topic_tokens and (query_tokens & topic_tokens) else 0.0
            confidence_bonus = max(0.0, min(item.confidence, 1.0)) * 0.05
            rerank_score = (base_score * 0.8) + (lexical_score * 0.15) + topic_bonus + confidence_bonus
            reranked.append((rerank_score, item))

        reranked.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in reranked[: max(1, top_k)]]

    def log_consolidation_event(
        self,
        *,
        session_key: str,
        status: str,
        message_count: int,
        trigger_messages: int,
        keep_recent: int,
        old_message_count: int = 0,
        details: str = "",
        raw_response: str = "",
    ):
        event = {
            "timestamp": datetime.now().isoformat(),
            "kind": "memory_consolidation",
            "session_key": session_key,
            "status": status,
            "message_count": message_count,
            "trigger_messages": trigger_messages,
            "keep_recent": keep_recent,
            "old_message_count": old_message_count,
        }
        if details.strip():
            event["details"] = details.strip()
        if raw_response.strip():
            event["raw_response_preview"] = raw_response.strip()[:1000]
        with open(self.trace_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def _embed_text(self, client: Any, model: str, text: str) -> list[float]:
        payload = str(text or "").strip()
        if not payload:
            return []

        def _call_embeddings():
            return client.embeddings.create(model=model, input=payload)

        response = await run_blocking(_call_embeddings)
        if not getattr(response, "data", None):
            return []
        embedding = getattr(response.data[0], "embedding", None)
        return list(embedding or [])

    def _migrate_legacy_trace_file(self):
        if self.trace_file.exists() or not self.history_index_file.exists():
            return
        lines = self.history_index_file.read_text(encoding="utf-8").splitlines()
        if not lines:
            return
        legacy_trace_lines: list[str] = []
        retained_lines: list[str] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                retained_lines.append(line)
                continue
            if payload.get("kind") == "memory_consolidation":
                legacy_trace_lines.append(json.dumps(payload, ensure_ascii=False))
            else:
                retained_lines.append(json.dumps(payload, ensure_ascii=False))
        if legacy_trace_lines:
            self.trace_file.write_text("\n".join(legacy_trace_lines) + "\n", encoding="utf-8")
            self.history_index_file.write_text(
                ("\n".join(retained_lines) + "\n") if retained_lines else "",
                encoding="utf-8",
            )


def _parse_consolidation_payload(raw_content: str) -> tuple[dict[str, Any] | None, str, str]:
    content = str(raw_content or "").strip()
    if not content:
        return None, "empty", ""

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = None
    else:
        if isinstance(parsed, dict):
            return parsed, "direct_json", content

    fenced_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", content, flags=re.DOTALL | re.IGNORECASE)
    if fenced_match:
        candidate = fenced_match.group(1).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        else:
            if isinstance(parsed, dict):
                return parsed, "fenced_json", candidate

    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = content[start : end + 1].strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        else:
            if isinstance(parsed, dict):
                return parsed, "embedded_json", candidate

    return None, "unparsed", content


def _build_memory_item(
    *,
    timestamp: str,
    session_key: str,
    item_type: str,
    topic: str,
    summary: str,
    keywords: Any = None,
    tags: Any = None,
    confidence: float = 0.8,
    embedding: list[float] | None = None,
) -> MemoryItem:
    normalized_summary = str(summary or "").strip()
    normalized_topic = str(topic or "").strip()
    normalized_type = str(item_type or "fact").strip() or "fact"
    normalized_keywords = _normalize_keywords(keywords or [])
    normalized_tags = _normalize_keywords(tags or [])
    normalized_timestamp = _normalize_timestamp(timestamp)
    return MemoryItem(
        id=_memory_item_id(normalized_type, normalized_topic, normalized_summary),
        timestamp=normalized_timestamp,
        updated_at=normalized_timestamp,
        source=session_key,
        type=normalized_type,
        topic=normalized_topic,
        summary=normalized_summary,
        keywords=normalized_keywords,
        tags=normalized_tags,
        confidence=max(0.0, min(float(confidence or 0.8), 1.0)),
        active=True,
        embedding=list(embedding or []),
    )


def _coerce_memory_items(
    payload: dict[str, Any],
    *,
    timestamp: str,
    session_key: str,
) -> list[MemoryItem]:
    raw_items = payload.get("memory_items") or []
    items: list[MemoryItem] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary", "") or "").strip()
        if not summary:
            continue
        items.append(
            _build_memory_item(
                timestamp=timestamp,
                session_key=session_key,
                item_type=str(item.get("type", "fact") or "fact"),
                topic=str(item.get("topic", "") or ""),
                summary=summary,
                keywords=item.get("keywords") or [],
                tags=item.get("tags") or [],
                confidence=float(item.get("confidence", 0.8) or 0.8),
            )
        )
    return items


async def consolidate_memory(
    client: Any,
    model: str,
    session: Session,
    memory: MemoryStore,
    trigger_messages: int = 30,
    keep_recent: int = 25,
    embedding_model: str = "",
):
    """把旧消息整合进记忆"""
    if trigger_messages <= keep_recent:
        raise ValueError("trigger_messages must be greater than keep_recent")

    message_count = len(session.messages)
    if message_count <= trigger_messages:
        return

    old = session.messages[:-keep_recent]
    memory.log_consolidation_event(
        session_key=session.key,
        status="started",
        message_count=message_count,
        trigger_messages=trigger_messages,
        keep_recent=keep_recent,
        old_message_count=len(old),
    )
    current_memory = memory.read_memory()
    current_store_items = memory.read_memory_items(active_only=True)
    current_store_preview = json.dumps(
        [item.to_dict() for item in current_store_items[-20:]],
        ensure_ascii=False,
        indent=2,
    )[:6000]
    prompt = f"""Summarize this conversation and update the memory.

## Current Memory
{current_memory or "(empty)"}

## Current Structured Memory Items
{current_store_preview or "[]"}

## Conversation
{json.dumps(old, ensure_ascii=False, indent=2)[:8000]}

Respond in JSON with this schema:
{{
  "history_summary": "one concise summary for history log",
  "history_topic": "main topic",
  "history_keywords": ["keyword1", "keyword2"],
  "memory_markdown": "updated MEMORY.md markdown",
  "memory_items": [
    {{
      "type": "preference|project|fact|workflow|profile|tooling",
      "topic": "stable topic label",
      "summary": "standalone reusable memory sentence",
      "keywords": ["keyword1", "keyword2"],
      "tags": ["optional-tag"],
      "confidence": 0.0
    }}
  ]
}}

Rules:
- Only keep durable, reusable memories in memory_items.
- Allowed memory categories: user identity/profile, stable preferences, default locations, stable project context, verified long-term task conclusions, and high-value workflow results.
- Do NOT write one-off temporary dialogue details, unresolved tool failures, speculative claims, long raw excerpts, or repeated file lists into memory_items.
- Each memory_item should be atomic: one item should capture one reusable fact.
- Prefer concise standalone summaries that are still meaningful when retrieved without surrounding conversation.
- memory_markdown is only a human-readable overview; the authoritative retrieval source is memory_items / memory_store.jsonl.
"""

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You consolidate conversations into memory."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
    except Exception as exc:
        memory.log_consolidation_event(
            session_key=session.key,
            status="llm_error",
            message_count=message_count,
            trigger_messages=trigger_messages,
            keep_recent=keep_recent,
            old_message_count=len(old),
            details=str(exc),
        )
        print(f"  [Memory] Consolidation LLM call failed for {session.key}: {exc}")
        raise

    content = resp.choices[0].message.content or "{}"
    result, parse_mode, parsed_source = _parse_consolidation_payload(content)
    if result is None:
        try:
            json.loads(str(content or "").strip())
        except json.JSONDecodeError as exc:
            details = f"{exc}; parse_mode={parse_mode}"
        else:
            details = f"JSON payload was not a dict; parse_mode={parse_mode}"
        memory.log_consolidation_event(
            session_key=session.key,
            status="parse_error",
            message_count=message_count,
            trigger_messages=trigger_messages,
            keep_recent=keep_recent,
            old_message_count=len(old),
            details=details,
            raw_response=content,
        )
        print(
            f"  [Memory] Consolidation JSON parse failed for {session.key}: "
            f"{details}"
        )
        return

    consolidation_timestamp = datetime.now().isoformat()
    history_summary = str(result.get("history_summary") or result.get("history") or "").strip()
    updated_memory = str(result.get("memory_markdown") or result.get("memory") or "").strip()
    history_topic = str(result.get("history_topic", "") or "").strip()
    history_keywords = _normalize_keywords(result.get("history_keywords") or [])
    memory_items = _coerce_memory_items(
        result,
        timestamp=consolidation_timestamp,
        session_key=session.key,
    )
    if not history_summary and not updated_memory:
        memory.log_consolidation_event(
            session_key=session.key,
            status="empty_result",
            message_count=message_count,
            trigger_messages=trigger_messages,
            keep_recent=keep_recent,
            old_message_count=len(old),
            details="JSON parsed successfully but did not contain history or memory fields.",
            raw_response=content,
        )
        print(f"  [Memory] Consolidation returned empty result for {session.key}")
        return

    if history_summary:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        memory.append_history(f"{ts} {history_summary}")
        memory.append_history_record(
            HistoryRecord(
                timestamp=consolidation_timestamp,
                session_key=session.key,
                summary=history_summary,
                topic=history_topic,
                keywords=history_keywords,
                message_count=len(old),
            )
        )
    if memory_items and client is not None and embedding_model.strip():
        try:
            for item in memory_items:
                text_for_embedding = " | ".join(
                    part
                    for part in [
                        item.topic,
                        item.summary,
                        ", ".join(item.keywords),
                    ]
                    if part
                )
                item.embedding = await memory._embed_text(client, embedding_model, text_for_embedding)
        except Exception as exc:
            memory.log_consolidation_event(
                session_key=session.key,
                status="embedding_error",
                message_count=message_count,
                trigger_messages=trigger_messages,
                keep_recent=keep_recent,
                old_message_count=len(old),
                details=str(exc),
            )
            print(f"  [Memory] Consolidation embedding failed for {session.key}: {exc}")
    if memory_items:
        memory.upsert_memory_items(memory_items)
    elif updated_memory:
        memory.write_memory(updated_memory)
    session.messages = session.messages[-keep_recent:]
    memory.log_consolidation_event(
        session_key=session.key,
        status="success",
        message_count=message_count,
        trigger_messages=trigger_messages,
        keep_recent=keep_recent,
        old_message_count=len(old),
        details=(
            f"parse_mode={parse_mode} "
            f"history_written={bool(history_summary)} "
            f"memory_written={bool(updated_memory)} "
            f"memory_items={len(memory_items)} "
            f"remaining_messages={len(session.messages)}"
        ),
    )
    print(f"  [Memory] Consolidated old messages for {session.key}")
