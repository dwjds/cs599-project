from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .registry import SkillRecord


class SkillScanner:
    """Scan workspace and builtin skill directories."""

    def __init__(self, workspace_skills: Path, builtin_skills: Path | None = None):
        self.workspace_skills = workspace_skills
        self.builtin_skills = builtin_skills

    def scan(self) -> list[SkillRecord]:
        records: list[SkillRecord] = []
        records.extend(self._scan_dir(self.workspace_skills))
        if self.builtin_skills:
            existing = {record.name for record in records}
            for record in self._scan_dir(self.builtin_skills):
                if record.name not in existing and record.dir.name not in existing:
                    records.append(record)
        return records

    def _scan_dir(self, root: Path) -> list[SkillRecord]:
        if not root.exists():
            return []
        records: list[SkillRecord] = []
        for skill_dir in root.iterdir():
            skill_file = skill_dir / "SKILL.md"
            if not skill_dir.is_dir() or not skill_file.exists():
                continue
            metadata = parse_skill_metadata(skill_file)
            records.append(
                SkillRecord(
                    name=str(metadata.get("name") or get_skill_name(skill_file)),
                    path=skill_file,
                    dir=skill_dir,
                    description=str(metadata.get("description") or get_skill_description(skill_file)),
                    triggers=list(metadata.get("triggers") or []),
                    metadata=metadata,
                )
            )
        return records


def parse_skill_metadata(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    metadata: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in match.group(1).split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and current_list_key:
            metadata.setdefault(current_list_key, []).append(
                stripped[2:].strip().strip("\"'")
            )
            continue
        current_list_key = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            metadata[key] = []
            current_list_key = key
            continue
        metadata[key] = value.strip("\"'")
    return metadata


def get_skill_description(path: Path) -> str:
    metadata = parse_skill_metadata(path)
    if metadata.get("description"):
        return str(metadata["description"])
    return path.parent.name


def get_skill_name(path: Path) -> str:
    metadata = parse_skill_metadata(path)
    if metadata.get("name"):
        return str(metadata["name"])
    return path.parent.name
