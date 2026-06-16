from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from miniagent_core.config import WORKSPACE

from .scanner import SkillScanner


SCRIPT_REF_RE = re.compile(r"scripts/[A-Za-z0-9_./-]+\.py")
STUB_MARKERS = (
    "NotImplementedError",
    "TODO",
    "FIXME",
    "placeholder",
    "pass  # TODO",
    "待实现",
)

REQUIRED_PACKAGES = {
    "xlsx": ("openpyxl", "pandas"),
    "pdf": ("pypdf", "pdfplumber", "reportlab"),
    "docx": ("docx", "defusedxml", "lxml"),
}

OPTIONAL_COMMANDS = {
    "soffice": "LibreOffice formula recalculation and Office conversion",
    "pandoc": "DOCX conversion workflows that need track-changes aware conversion",
}

CORE_SCRIPTS = {
    "xlsx": (
        "scripts/edit_workbook.py",
        "scripts/filter_workbook.py",
        "scripts/recalc.py",
    ),
    "pdf": (
        "scripts/extract_text.py",
        "scripts/extract_tables.py",
        "scripts/pdf_ops.py",
        "scripts/create_report.py",
    ),
    "docx": (
        "scripts/accept_changes.py",
        "scripts/comment.py",
        "scripts/office/unpack.py",
        "scripts/office/pack.py",
        "scripts/office/validate.py",
        "scripts/office/soffice.py",
    ),
    "weather": (
        "scripts/query_weather.py",
    ),
}


@dataclass
class DoctorCheck:
    status: str
    area: str
    name: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass
class DoctorReport:
    workspace: str
    checks: list[DoctorCheck]

    @property
    def ok_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "OK")

    @property
    def warn_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "WARN")

    @property
    def error_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "ERROR")

    @property
    def exit_code(self) -> int:
        return 1 if self.error_count else 0

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace": self.workspace,
            "summary": {
                "ok": self.ok_count,
                "warn": self.warn_count,
                "error": self.error_count,
            },
            "checks": [asdict(check) for check in self.checks],
        }


class SkillDoctor:
    """Validate that the workspace skill system is structurally runnable."""

    def __init__(self, workspace: Path = WORKSPACE):
        self.workspace = workspace
        self.skills_dir = workspace / "skills"
        self.checks: list[DoctorCheck] = []

    def run(self, *, deep: bool = False) -> DoctorReport:
        self.checks = []
        self._check_workspace()
        self._check_dependencies()
        self._check_commands()
        self._check_skills()
        if deep:
            self._record(
                "WARN",
                "deep",
                "smoke-tests",
                "Deep runtime smoke tests are reserved for explicit script-level fixtures and are not enabled yet.",
            )
        return DoctorReport(str(self.workspace), self.checks)

    def _record(
        self,
        status: str,
        area: str,
        name: str,
        message: str,
        **details: object,
    ) -> None:
        self.checks.append(DoctorCheck(status, area, name, message, details))

    def _check_workspace(self) -> None:
        if not self.workspace.exists():
            self._record("ERROR", "workspace", "root", "Workspace directory does not exist.")
            return
        self._record("OK", "workspace", "root", "Workspace directory exists.")

        if not self.skills_dir.exists():
            self._record("ERROR", "workspace", "skills", "workspace/skills directory does not exist.")
        else:
            self._record("OK", "workspace", "skills", "workspace/skills directory exists.")

        outbox = self.workspace / "outbox"
        try:
            outbox.mkdir(parents=True, exist_ok=True)
            probe = outbox / ".skill_doctor_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            self._record("OK", "workspace", "outbox", "workspace/outbox is writable.")
        except OSError as exc:
            self._record("ERROR", "workspace", "outbox", "workspace/outbox is not writable.", error=str(exc))

        trace = self.skills_dir / "skill_trace.jsonl"
        try:
            trace.parent.mkdir(parents=True, exist_ok=True)
            with trace.open("a", encoding="utf-8"):
                pass
            self._record("OK", "workspace", "skill_trace", "workspace/skills/skill_trace.jsonl is writable.")
        except OSError as exc:
            self._record("ERROR", "workspace", "skill_trace", "Skill trace file is not writable.", error=str(exc))

    def _check_dependencies(self) -> None:
        for skill_name, packages in REQUIRED_PACKAGES.items():
            for package in packages:
                if importlib.util.find_spec(package) is None:
                    self._record(
                        "ERROR",
                        "dependency",
                        package,
                        f"Missing Python package required by {skill_name} skill.",
                        skill=skill_name,
                    )
                else:
                    self._record("OK", "dependency", package, f"Python package is importable for {skill_name}.")

    def _check_commands(self) -> None:
        for command, purpose in OPTIONAL_COMMANDS.items():
            resolved = shutil.which(command)
            if resolved:
                self._record("OK", "command", command, f"Command is available: {purpose}.", path=resolved)
            else:
                self._record("WARN", "command", command, f"Command is not on PATH: {purpose}.")

    def _check_skills(self) -> None:
        if not self.skills_dir.exists():
            return

        skills = SkillScanner(self.skills_dir).scan()
        if not skills:
            self._record("ERROR", "registry", "skills", "No skills were discovered.")
            return
        self._record("OK", "registry", "skills", f"Discovered {len(skills)} skills.")

        seen_names: set[str] = set()
        for skill in skills:
            skill_key = skill.name.lower()
            if skill_key in seen_names:
                self._record("ERROR", "registry", skill.name, "Duplicate skill name detected.")
            else:
                seen_names.add(skill_key)
                self._record("OK", "registry", skill.name, "Skill name is unique.")

            self._check_skill_metadata(skill.path, skill.name, skill.description, skill.triggers)
            content = self._read_skill(skill.path)
            if content is None:
                continue
            self._check_skill_references(skill.name, skill.dir, content)
            self._check_skill_scripts(skill.name, skill.dir, content)
            self._check_core_scripts(skill.name, skill.dir)

    def _check_skill_metadata(
        self,
        path: Path,
        name: str,
        description: str,
        triggers: Iterable[str],
    ) -> None:
        if not path.exists():
            self._record("ERROR", "skill", name, "SKILL.md is missing.", path=str(path))
            return
        self._record("OK", "skill", name, "SKILL.md exists.", path=str(path))

        if not description or description == name:
            self._record("WARN", "skill", name, "Skill description is empty or too generic.")
        else:
            self._record("OK", "skill", name, "Skill description is available.")

        if list(triggers):
            self._record("OK", "skill", name, "Skill trigger metadata is available.")
        else:
            self._record("WARN", "skill", name, "Skill has no trigger metadata; routing may rely on description only.")

    def _read_skill(self, path: Path) -> str | None:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            self._record("ERROR", "skill", path.parent.name, "Failed to read SKILL.md.", error=str(exc))
            return None
        if not content.strip():
            self._record("ERROR", "skill", path.parent.name, "SKILL.md is empty.")
            return None
        return content

    def _check_skill_references(self, skill_name: str, skill_dir: Path, content: str) -> None:
        for ref_name in ("reference.md", "references.md", "forms.md"):
            ref_path = skill_dir / ref_name
            mentioned = ref_name in content
            exists = ref_path.exists()
            if mentioned and exists:
                self._record("OK", "reference", f"{skill_name}/{ref_name}", "Referenced document exists.")
            elif mentioned and not exists:
                self._record("WARN", "reference", f"{skill_name}/{ref_name}", "SKILL.md mentions a missing document.")

    def _check_skill_scripts(self, skill_name: str, skill_dir: Path, content: str) -> None:
        script_dir = skill_dir / "scripts"
        referenced_scripts = sorted(set(SCRIPT_REF_RE.findall(content)))

        for script_ref in referenced_scripts:
            script_path = skill_dir / script_ref
            if script_path.exists():
                self._record("OK", "script-ref", f"{skill_name}/{script_ref}", "Referenced script exists.")
            else:
                self._record("ERROR", "script-ref", f"{skill_name}/{script_ref}", "Referenced script is missing.")

        if not script_dir.exists():
            if referenced_scripts:
                self._record("ERROR", "script", skill_name, "SKILL.md references scripts but scripts/ is missing.")
            return

        scripts = sorted(path for path in script_dir.rglob("*.py") if "__pycache__" not in path.parts)
        if scripts:
            self._record("OK", "script", skill_name, f"Found {len(scripts)} Python scripts.")
        else:
            self._record("WARN", "script", skill_name, "scripts/ exists but contains no Python scripts.")

        for script_path in scripts:
            self._check_python_script(skill_name, script_path)

    def _check_core_scripts(self, skill_name: str, skill_dir: Path) -> None:
        expected = CORE_SCRIPTS.get(skill_name.lower())
        if not expected:
            return
        for script_ref in expected:
            script_path = skill_dir / script_ref
            if script_path.exists():
                self._record("OK", "core-script", f"{skill_name}/{script_ref}", "Core workflow script exists.")
            else:
                self._record("ERROR", "core-script", f"{skill_name}/{script_ref}", "Core workflow script is missing.")

    def _check_python_script(self, skill_name: str, script_path: Path) -> None:
        rel_path = self._safe_relative(script_path)
        try:
            source = script_path.read_text(encoding="utf-8")
            ast.parse(source, filename=str(script_path))
        except SyntaxError as exc:
            self._record(
                "ERROR",
                "script",
                rel_path,
                "Python script has a syntax error.",
                skill=skill_name,
                line=exc.lineno,
                error=exc.msg,
            )
            return
        except OSError as exc:
            self._record("ERROR", "script", rel_path, "Failed to read Python script.", skill=skill_name, error=str(exc))
            return

        self._record("OK", "script", rel_path, "Python script syntax is valid.", skill=skill_name)
        if any(marker in source for marker in STUB_MARKERS):
            self._record(
                "WARN",
                "script",
                rel_path,
                "Script contains stub-like markers; verify it is implemented before relying on it.",
                skill=skill_name,
            )

    def _safe_relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace))
        except ValueError:
            return str(path)


def run_skill_doctor(workspace: Path = WORKSPACE, *, deep: bool = False) -> DoctorReport:
    return SkillDoctor(workspace).run(deep=deep)


def print_text_report(report: DoctorReport) -> None:
    print("Skill Doctor")
    print(f"Workspace: {report.workspace}")
    print(f"Summary: OK={report.ok_count} WARN={report.warn_count} ERROR={report.error_count}")
    print()
    for check in report.checks:
        print(f"[{check.status}] {check.area}/{check.name}: {check.message}")
        if check.details:
            detail = " ".join(f"{key}={value}" for key, value in check.details.items())
            print(f"  {detail}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check whether the MiniAgent skill system is runnable.")
    parser.add_argument("--workspace", type=Path, default=WORKSPACE, help="Workspace directory. Default: ./workspace")
    parser.add_argument("--deep", action="store_true", help="Reserve flag for deeper runtime smoke tests.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_skill_doctor(args.workspace, deep=args.deep)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print_text_report(report)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
