from __future__ import annotations

from .loader import SkillLoader
from .policy import SkillPolicyEngine
from .registry import SkillRecord, SkillRegistry
from .router import SkillRouter
from .runtime import SkillRuntime, SkillTraceLogger
from .scanner import SkillScanner

__all__ = [
    "SkillLoader",
    "SkillPolicyEngine",
    "SkillRecord",
    "SkillRegistry",
    "SkillRouter",
    "SkillRuntime",
    "SkillScanner",
    "SkillTraceLogger",
    "DoctorCheck",
    "DoctorReport",
    "SkillDoctor",
    "run_skill_doctor",
]


def __getattr__(name: str):
    if name in {"DoctorCheck", "DoctorReport", "SkillDoctor", "run_skill_doctor"}:
        from .doctor import DoctorCheck, DoctorReport, SkillDoctor, run_skill_doctor

        exports = {
            "DoctorCheck": DoctorCheck,
            "DoctorReport": DoctorReport,
            "SkillDoctor": SkillDoctor,
            "run_skill_doctor": run_skill_doctor,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
