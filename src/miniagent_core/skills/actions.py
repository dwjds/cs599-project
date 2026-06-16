from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from miniagent_core.attachments import Attachment

from .loader import SkillLoader


@dataclass
class SkillActionPlan:
    skill_name: str
    action_name: str
    tool_name: str
    params: dict[str, Any]
    input_path: str
    output_path: str
    description: str = ""
    input_paths: list[str] | None = None


def plan_skill_action(
    *,
    skill_loader: SkillLoader,
    user_text: str,
    attachments: list[Attachment],
    outbox_dir: Path,
    requires_output_file: bool,
) -> SkillActionPlan | None:
    text = (user_text or "").strip().lower()
    if not text or not attachments:
        return None

    for skill in skill_loader.registry.list_skills():
        manifest = _read_actions_manifest(skill.dir)
        for action in manifest.get("actions") or []:
            if not isinstance(action, dict):
                continue
            if not action.get("auto_execute", False):
                continue
            if action.get("requires_output_file", False) and not requires_output_file:
                continue
            if not _matches_hints(text, action):
                continue
            input_files = _select_input_attachments(
                text,
                attachments,
                extensions=[str(item).lower() for item in action.get("input_extensions") or []],
                input_mode=str(action.get("input_mode") or "single"),
                min_inputs=int(action.get("min_inputs") or 1),
                max_inputs=int(action.get("max_inputs") or 20),
            )
            if not input_files:
                continue
            plan = _build_plan(
                skill_name=skill.name,
                action=action,
                input_files=input_files,
                text=text,
                outbox_dir=outbox_dir,
            )
            if plan is not None:
                return plan
    return None


def _read_actions_manifest(skill_dir: Path) -> dict[str, Any]:
    path = skill_dir / "actions.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _matches_hints(text: str, action: dict[str, Any]) -> bool:
    for key in ("intent_hints_any", "output_hints_any"):
        hints = [str(item).lower() for item in action.get(key) or []]
        if hints and not any(hint in text for hint in hints):
            return False
    for group in action.get("hint_groups_any") or []:
        hints = [str(item).lower() for item in group or []]
        if hints and not any(hint in text for hint in hints):
            return False
    return True


def _select_input_attachments(
    text: str,
    attachments: list[Attachment],
    *,
    extensions: list[str],
    input_mode: str,
    min_inputs: int,
    max_inputs: int,
) -> list[Attachment]:
    candidates = [
        item
        for item in attachments
        if not extensions or Path(item.name or item.path).suffix.lower() in extensions
    ]
    if not candidates:
        return []

    def score(item: Attachment) -> int:
        value = _filename_reference_score(text, item.name or Path(item.path).name)
        if getattr(item, "origin", "") == "outbox":
            value += 5
        return value

    ranked = sorted(candidates, key=score, reverse=True)
    if input_mode == "all_matching":
        selected = ranked[: max(1, max_inputs)]
        if len(selected) < max(1, min_inputs):
            return []
        return selected
    return ranked[:1]


def _filename_reference_score(text: str, filename: str) -> int:
    """Score how strongly the user text points at this generic filename."""
    name = (filename or "").lower()
    stem = Path(name).stem
    value = 0
    if name and name in text:
        value += 100
    if stem and stem in text:
        value += 80
    for token in _name_tokens(stem):
        if token and token in text:
            value += 20
    return value


def _name_tokens(stem: str) -> list[str]:
    tokens = [
        item
        for item in re.split(r"[_\-\s.]+", stem)
        if len(item) >= 2
    ]
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", stem):
        for size in range(2, min(5, len(chunk)) + 1):
            for index in range(0, len(chunk) - size + 1):
                tokens.append(chunk[index : index + size])
    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped


def _build_plan(
    *,
    skill_name: str,
    action: dict[str, Any],
    input_files: list[Attachment],
    text: str,
    outbox_dir: Path,
) -> SkillActionPlan | None:
    action_name = str(action.get("name") or "action")
    output_extension = str(action.get("output_extension") or "").strip()
    output_template = str(action.get("output_filename_template") or "{input_stem}{output_extension}")
    input_file = input_files[0]
    input_path = str(Path(input_file.path).expanduser().resolve())
    input_paths = [str(Path(item.path).expanduser().resolve()) for item in input_files]
    input_name = input_file.name or Path(input_path).name
    input_stem = Path(input_name).stem or "output"
    extracted = _extract_variables(text, action)
    if extracted is None:
        return None
    output_name = output_template.format(
        input_name=input_name,
        input_stem=input_stem,
        output_extension=output_extension,
        **extracted,
    )
    output_name = _safe_filename(output_name)
    output_path = str((outbox_dir / output_name).expanduser().resolve())
    variables = {
        "input_path": input_path,
        "input_paths": input_paths,
        "input_name": input_name,
        "input_stem": input_stem,
        "output_path": output_path,
        "output_name": output_name,
        "output_extension": output_extension,
        **extracted,
    }
    arguments = _format_arguments(action.get("arguments") or [], variables)
    tool_name = str(action.get("tool") or "run_skill_script")
    params: dict[str, Any] = {
        "skill_name": str(action.get("skill_name") or skill_name),
        "script_path": str(action.get("script_path") or ""),
        "arguments": arguments,
        "timeout_seconds": int(action.get("timeout_seconds") or 60),
    }
    return SkillActionPlan(
        skill_name=skill_name,
        action_name=action_name,
        tool_name=tool_name,
        params=params,
        input_path=input_path,
        output_path=output_path,
        description=str(action.get("description") or ""),
        input_paths=input_paths,
    )


def _extract_variables(text: str, action: dict[str, Any]) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for name, config in (action.get("variables") or {}).items():
        if not isinstance(config, dict):
            continue
        value = ""
        pattern = str(config.get("regex") or "")
        if pattern:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = _select_regex_value(match)
        if not value:
            value = str(config.get("default") or "")
        if value:
            value = _normalize_variable(value, str(config.get("normalizer") or ""))
        if not value and config.get("required", False):
            return None
        values[str(name)] = value
        values[f"{name}_slug"] = _safe_filename(value.replace(",", "_").replace("-", "_"))
    return values


def _normalize_variable(value: str, normalizer: str) -> str:
    text = str(value or "").strip()
    if normalizer == "pages":
        if text.lower() == "all":
            return "all"
        text = text.replace("，", ",").replace("、", ",")
        text = re.sub(r"\s*(?:到|至|—|–|~)\s*", "-", text)
        text = re.sub(r"\s+", "", text)
        text = re.sub(r"[^0-9,\-]", "", text)
        return text.strip(",-")
    if normalizer == "angle":
        match = re.search(r"-?\d+", text)
        return match.group(0) if match else ""
    return text


def _select_regex_value(match: re.Match[str]) -> str:
    groups = [str(item) for item in match.groups() if item]
    for group in groups:
        if re.search(r"\d", group):
            return group
    if groups:
        return groups[0]
    return str(match.group(0))


def _format_arguments(raw_arguments: list[Any], variables: dict[str, Any]) -> list[str]:
    arguments: list[str] = []
    for raw in raw_arguments:
        template = str(raw)
        if template == "{input_paths}":
            arguments.extend(str(item) for item in variables.get("input_paths") or [])
            continue
        arguments.append(template.format(**variables))
    return arguments


def _safe_filename(filename: str) -> str:
    name = Path(str(filename or "").strip()).name
    name = re.sub(r'[<>:"/\\\\|?*\x00-\x1f]+', "_", name)
    name = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff.-]+", "_", name)
    return name.strip("._") or "output"
