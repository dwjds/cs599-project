from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .registry import SkillRecord


RULE_THRESHOLD = 35

EXTENSION_SKILLS = {
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".csv": "xlsx",
    ".tsv": "xlsx",
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
}

CONTENT_TYPE_SKILLS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xlsx",
    "text/csv": "xlsx",
    "text/tab-separated-values": "xlsx",
}

SKILL_HINTS = {
    "weather": {
        "strong": ("天气", "今天天气", "实时天气", "下雨", "降雨", "气温", "温度", "weather", "forecast"),
        "medium": ("风力", "体感", "穿衣", "出行建议", "temperature", "rain"),
        "weak": (),
    },
    "xlsx": {
        "strong": (".xlsx", ".xlsm", ".csv", ".tsv", "excel", "xlsx", "工作簿", "电子表格"),
        "medium": ("工作表", "表格", "公式", "重算", "汇总", "新增行", "新增列", "单元格", "sheet"),
        "weak": ("数据清洗", "行", "列"),
    },
    "pdf": {
        "strong": (".pdf", "pdf", "pdf文件"),
        "medium": ("总结pdf", "读取pdf", "提取pdf", "合并pdf", "拆分pdf", "旋转pdf", "生成pdf", "转pdf"),
        "weak": ("页面", "水印"),
    },
    "docx": {
        "strong": (".docx", ".doc", "word", "word文档", "word 文件", "word文件"),
        "medium": ("生成文档", "修改文档", "读取文档", "接受修订", "批注", "目录", "页码"),
        "weak": ("文档", "报告", "简历", "合同", "memo", "letter"),
    },
    "code_navigation": {
        "strong": (".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml", ".yaml", ".yml", "代码", "函数", "类", "变量"),
        "medium": ("路径", "文件名", "定位", "查找", "搜索代码", "检查文件", "读取文件", "修改文件"),
        "weak": ("文件", "目录"),
    },
}

GENERIC_TRIGGERS = {
    "文件",
    "文档",
    "报告",
    "行",
    "列",
    "页面",
    "目录",
}

PATH_LIKE_RE = re.compile(
    r"(?i)(?:[A-Z]:\\|/|\\|[\w.-]+\.(?:py|js|ts|tsx|jsx|json|toml|ya?ml|md|txt|pdf|docx?|xlsx|xlsm|csv|tsv))"
)


@dataclass
class AttachmentSignal:
    haystack: str
    filename: str = ""
    extension: str = ""
    content_type: str = ""

    def to_prompt(self) -> dict[str, str]:
        return {
            "filename": self.filename,
            "extension": self.extension,
            "content_type": self.content_type,
        }


@dataclass
class SkillRouteDecision:
    skill: SkillRecord
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "rule"

    def add(self, points: int, reason: str) -> None:
        self.score += points
        self.confidence = max(self.confidence, min(max(points / 100, 0.0), 1.0))
        self.reasons.append(reason)

    def merge(self, other: "SkillRouteDecision") -> None:
        self.score = max(self.score, other.score)
        self.confidence = max(self.confidence, other.confidence)
        for reason in other.reasons:
            if reason not in self.reasons:
                self.reasons.append(reason)
        if self.source != other.source:
            self.source = f"{self.source}+{other.source}"

    def to_trace(self) -> dict[str, Any]:
        return {
            "skill": self.skill.name,
            "score": self.score,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "reasons": list(self.reasons),
        }


class RuleSkillRouter:
    """Deterministic fallback router for hard signals and no-LLM environments."""

    def __init__(self, *, threshold: int = RULE_THRESHOLD):
        self.threshold = threshold

    def select_with_scores(
        self,
        skills: list[SkillRecord],
        user_message: str,
        *,
        attachments: list[Any] | None = None,
    ) -> list[SkillRouteDecision]:
        text = normalize_text(user_message)
        attachment_signals = build_attachment_signals(attachments or [])
        if not text and not attachment_signals:
            return []

        decisions = [self._score_skill(skill, text, attachment_signals) for skill in skills]
        selected = [decision for decision in decisions if decision.score >= self.threshold]
        selected.sort(key=lambda item: (-item.score, item.skill.name))
        return self._prune_competing_file_skills(selected)

    def _score_skill(
        self,
        skill: SkillRecord,
        text: str,
        attachment_signals: list[AttachmentSignal],
    ) -> SkillRouteDecision:
        decision = SkillRouteDecision(skill=skill, source="rule")
        skill_key = skill.name.lower().strip() or skill.dir.name.lower().strip()

        self._score_attachment(decision, skill_key, attachment_signals)
        self._score_path_like_text(decision, skill_key, text)
        self._score_route_hints(decision, skill_key, text)
        self._score_metadata_triggers(decision, skill, text, attachment_signals)
        if decision.score:
            decision.confidence = min(0.95, max(0.35, decision.score / 140))
        return decision

    def _score_attachment(
        self,
        decision: SkillRouteDecision,
        skill_key: str,
        signals: list[AttachmentSignal],
    ) -> None:
        for signal in signals:
            if signal.extension and EXTENSION_SKILLS.get(signal.extension) == skill_key:
                decision.add(100, f"attachment extension {signal.extension}")
            mapped_content_type = CONTENT_TYPE_SKILLS.get(signal.content_type)
            if mapped_content_type == skill_key:
                decision.add(80, f"attachment content-type {signal.content_type}")
            if skill_key in signal.haystack:
                decision.add(25, f"attachment text mentions {skill_key}")

    def _score_path_like_text(self, decision: SkillRouteDecision, skill_key: str, text: str) -> None:
        if not text:
            return
        extensions = set(re.findall(r"(?i)\.[a-z0-9]{2,5}\b", text))
        for extension in extensions:
            normalized = extension.lower()
            if EXTENSION_SKILLS.get(normalized) == skill_key:
                decision.add(70, f"text extension {normalized}")
        if skill_key == "code_navigation" and PATH_LIKE_RE.search(text):
            decision.add(55, "text contains path-like code/file reference")

    def _score_route_hints(self, decision: SkillRouteDecision, skill_key: str, text: str) -> None:
        if not text:
            return
        hints = SKILL_HINTS.get(skill_key, {})
        for phrase in hints.get("strong", ()):
            if phrase and phrase.lower() in text:
                decision.add(40, f"strong hint {phrase}")
        for phrase in hints.get("medium", ()):
            if phrase and phrase.lower() in text:
                decision.add(24, f"medium hint {phrase}")
        for phrase in hints.get("weak", ()):
            if phrase and phrase.lower() in text:
                decision.add(8, f"weak hint {phrase}")

    def _score_metadata_triggers(
        self,
        decision: SkillRouteDecision,
        skill: SkillRecord,
        text: str,
        attachment_signals: list[AttachmentSignal],
    ) -> None:
        triggers = sorted({str(item).strip().lower() for item in skill.triggers if str(item).strip()})
        for trigger in triggers:
            weight = metadata_trigger_weight(trigger)
            if text and trigger in text:
                decision.add(weight, f"metadata trigger {trigger}")
            if attachment_signals and any(trigger in signal.haystack for signal in attachment_signals):
                decision.add(min(weight + 12, 45), f"attachment metadata trigger {trigger}")

        skill_name = skill.name.lower().strip()
        if text and skill_name and skill_name in text:
            decision.add(30, f"skill name {skill_name}")

    def _prune_competing_file_skills(
        self,
        decisions: list[SkillRouteDecision],
    ) -> list[SkillRouteDecision]:
        if not decisions:
            return []
        top_file_skill = next(
            (
                decision
                for decision in decisions
                if decision.skill.name.lower() in {"xlsx", "pdf", "docx"} and decision.score >= 70
            ),
            None,
        )
        if top_file_skill is None:
            return decisions

        pruned: list[SkillRouteDecision] = []
        for decision in decisions:
            name = decision.skill.name.lower()
            if name in {"xlsx", "pdf", "docx"} and name != top_file_skill.skill.name.lower():
                if decision.score < top_file_skill.score - 35:
                    continue
            pruned.append(decision)
        return pruned


class LLMSkillRouter:
    """Semantic router that asks the LLM to choose skills from compact metadata."""

    def __init__(self, *, client: Any = None, model: str = "", timeout_hint: str = "short"):
        self.client = client
        self.model = model
        self.timeout_hint = timeout_hint
        self.last_error: str = ""

    def select_with_scores(
        self,
        skills: list[SkillRecord],
        user_message: str,
        *,
        attachments: list[Any] | None = None,
    ) -> list[SkillRouteDecision]:
        self.last_error = ""
        if self.client is None or not self.model:
            self.last_error = "llm router unavailable"
            return []
        if not (user_message or "").strip() and not attachments:
            return []

        skill_map = {skill.name.lower(): skill for skill in skills}
        prompt = build_llm_route_prompt(skills, user_message, build_attachment_signals(attachments or []))
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are MiniAgent's skill router. "
                            "Select only the skills needed for this turn. "
                            "Return strict JSON only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
            )
            content = response.choices[0].message.content or "{}"
            payload = parse_json_object(content)
        except Exception as exc:
            self.last_error = str(exc)
            return []

        raw_selected = payload.get("selected_skills") or []
        if isinstance(raw_selected, str):
            raw_selected = [raw_selected]
        reason = str(payload.get("reason") or "LLM route selected this skill").strip()
        base_confidence = coerce_confidence(payload.get("confidence"), default=0.7)
        decisions: list[SkillRouteDecision] = []
        seen: set[str] = set()
        for raw_name in raw_selected:
            name = str(raw_name or "").strip().lower()
            if not name or name in seen:
                continue
            skill = skill_map.get(name)
            if skill is None:
                continue
            seen.add(name)
            decisions.append(
                SkillRouteDecision(
                    skill=skill,
                    score=int(base_confidence * 100),
                    confidence=base_confidence,
                    reasons=[reason],
                    source="llm",
                )
            )
        return decisions


class SkillRouter:
    """Hybrid router: hard rules for grounding, LLM for semantic ambiguity."""

    def __init__(
        self,
        *,
        mode: str = "hybrid",
        llm_client: Any = None,
        model: str = "",
    ):
        self.mode = normalize_route_mode(mode)
        self.rule_router = RuleSkillRouter()
        self.llm_router = LLMSkillRouter(client=llm_client, model=model)
        self.last_decisions: list[SkillRouteDecision] = []
        self.last_route_status: str = ""
        self._cache: dict[str, tuple[str, list[SkillRouteDecision]]] = {}

    def select(
        self,
        skills: list[SkillRecord],
        user_message: str,
        *,
        attachments: list[Any] | None = None,
    ) -> list[SkillRecord]:
        return [decision.skill for decision in self.select_with_scores(skills, user_message, attachments=attachments)]

    def select_with_scores(
        self,
        skills: list[SkillRecord],
        user_message: str,
        *,
        attachments: list[Any] | None = None,
    ) -> list[SkillRouteDecision]:
        cache_key = build_route_cache_key(self.mode, skills, user_message, attachments or [])
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.last_route_status, decisions = cached
            self.last_decisions = decisions
            return decisions

        if self.mode == "rule":
            decisions = self.rule_router.select_with_scores(skills, user_message, attachments=attachments)
            self.last_decisions = decisions
            self.last_route_status = "rule"
            self._remember(cache_key, self.last_route_status, decisions)
            return decisions

        llm_decisions = self.llm_router.select_with_scores(skills, user_message, attachments=attachments)
        rule_decisions = self.rule_router.select_with_scores(skills, user_message, attachments=attachments)

        if self.mode == "llm":
            decisions = llm_decisions or rule_decisions
            self.last_decisions = decisions
            self.last_route_status = "llm" if llm_decisions else f"llm_fallback_rule:{self.llm_router.last_error}"
            self._remember(cache_key, self.last_route_status, decisions)
            return decisions

        decisions = self._merge_hybrid(llm_decisions, rule_decisions)
        self.last_decisions = decisions
        if llm_decisions:
            self.last_route_status = "hybrid"
        else:
            self.last_route_status = f"hybrid_fallback_rule:{self.llm_router.last_error}"
        self._remember(cache_key, self.last_route_status, decisions)
        return decisions

    def _remember(self, cache_key: str, status: str, decisions: list[SkillRouteDecision]) -> None:
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[cache_key] = (status, decisions)

    def _merge_hybrid(
        self,
        llm_decisions: list[SkillRouteDecision],
        rule_decisions: list[SkillRouteDecision],
    ) -> list[SkillRouteDecision]:
        merged: dict[str, SkillRouteDecision] = {}

        for decision in llm_decisions:
            merged[decision.skill.name.lower()] = decision

        for decision in rule_decisions:
            name = decision.skill.name.lower()
            is_hard_signal = decision.score >= 70 or any("attachment" in reason for reason in decision.reasons)
            if name in merged:
                merged[name].merge(decision)
            elif is_hard_signal or not llm_decisions:
                merged[name] = decision

        selected = list(merged.values())
        selected.sort(key=lambda item: (source_priority(item.source), item.confidence, item.score), reverse=True)
        return selected


def build_llm_route_prompt(
    skills: list[SkillRecord],
    user_message: str,
    attachment_signals: list[AttachmentSignal],
) -> str:
    compact_skills = [
        {
            "name": skill.name,
            "description": skill.description[:500],
            "triggers": list(skill.triggers)[:20],
        }
        for skill in skills
    ]
    payload = {
        "user_message": user_message or "",
        "attachments": [signal.to_prompt() for signal in attachment_signals],
        "available_skills": compact_skills,
        "routing_rules": [
            "Select zero skills when the request is ordinary chat and no skill is useful.",
            "Select one or more skills only when their instructions/tools are needed this turn.",
            "If the user says this file / uploaded file / just uploaded file, use the attachment metadata.",
            "For converting or extracting between file types, select all necessary skills.",
            "Do not select a skill only because of generic words like file, document, report, content.",
        ],
        "response_schema": {
            "selected_skills": ["skill_name"],
            "confidence": 0.0,
            "reason": "short reason",
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def build_route_cache_key(
    mode: str,
    skills: list[SkillRecord],
    user_message: str,
    attachments: list[Any],
) -> str:
    payload = {
        "mode": mode,
        "message": user_message or "",
        "skills": [(skill.name, tuple(skill.triggers)) for skill in skills],
        "attachments": [signal.to_prompt() for signal in build_attachment_signals(attachments)],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def parse_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        return {}
    return parsed


def coerce_confidence(value: Any, *, default: float) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = default
    return min(max(confidence, 0.0), 1.0)


def source_priority(source: str) -> int:
    if "llm" in source and "rule" in source:
        return 3
    if "llm" in source:
        return 2
    return 1


def normalize_route_mode(value: str) -> str:
    mode = str(value or "hybrid").strip().lower()
    if mode not in {"rule", "llm", "hybrid"}:
        return "hybrid"
    return mode


def metadata_trigger_weight(trigger: str) -> int:
    if trigger in GENERIC_TRIGGERS:
        return 8
    if trigger.startswith("."):
        return 45
    if len(trigger) <= 2 and trigger.isascii():
        return 12
    if len(trigger) <= 1:
        return 8
    return 26


def normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def build_attachment_signals(attachments: list[Any]) -> list[AttachmentSignal]:
    signals: list[AttachmentSignal] = []
    for item in attachments:
        values: dict[str, str] = {}
        for attr in ("name", "path", "content_type", "source_url"):
            raw_value = getattr(item, attr, "")
            values[attr] = str(raw_value).strip().lower() if raw_value else ""
        filename = Path(values["name"]).name if values["name"] else Path(values["path"]).name
        extension = Path(filename).suffix.lower()
        haystack = " ".join(value for value in values.values() if value)
        signals.append(
            AttachmentSignal(
                haystack=haystack,
                filename=filename,
                extension=extension,
                content_type=values["content_type"],
            )
        )
    return signals


def build_attachment_haystacks(attachments: list[Any]) -> list[str]:
    return [signal.haystack for signal in build_attachment_signals(attachments)]
