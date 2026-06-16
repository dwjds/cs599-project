from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SkillRecord:
    name: str
    path: Path
    dir: Path
    description: str
    triggers: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "dir": str(self.dir),
            "description": self.description,
            "triggers": list(self.triggers),
        }


class SkillRegistry:
    """Register skill metadata and resolve skills by name."""

    def __init__(self, scanner: Any):
        self.scanner = scanner

    def list_skills(self) -> list[SkillRecord]:
        return self.scanner.scan()

    def get(self, name: str) -> SkillRecord | None:
        target = str(name or "").strip().lower()
        if not target:
            return None
        for skill in self.list_skills():
            if skill.name.lower() == target or skill.dir.name.lower() == target:
                return skill
        return None
