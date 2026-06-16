from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def compare_reports(
    *,
    base: Path,
    head: Path,
    results_dir: Path,
) -> dict[str, Any]:
    base_report = load_report(base)
    head_report = load_report(head)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary = build_regression_summary(base, head, base_report, head_report, run_id=run_id)
    task_changes = compare_task_results(base_report, head_report)
    report = {
        "summary": summary,
        "task_changes": task_changes,
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    json_path = results_dir / f"regression_{run_id}.json"
    md_path = results_dir / "regression_latest.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_regression_markdown(summary, task_changes), encoding="utf-8")
    report["summary"]["json_report"] = str(json_path)
    report["summary"]["markdown_report"] = str(md_path)
    return report


def load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Report not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Report must be a JSON object: {path}")
    return payload


def build_regression_summary(
    base_path: Path,
    head_path: Path,
    base_report: dict[str, Any],
    head_report: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    base_summary = dict(base_report.get("summary") or {})
    head_summary = dict(head_report.get("summary") or {})
    numeric_deltas: dict[str, Any] = {}
    for key in ("total", "passed", "failed", "success_rate", "total_tool_calls", "avg_tool_calls", "avg_steps"):
        base_value = base_summary.get(key)
        head_value = head_summary.get(key)
        if isinstance(base_value, (int, float)) and isinstance(head_value, (int, float)):
            numeric_deltas[key] = round(head_value - base_value, 6)
    return {
        "run_id": run_id,
        "base_report": str(base_path),
        "head_report": str(head_path),
        "base_run_id": base_summary.get("run_id", ""),
        "head_run_id": head_summary.get("run_id", ""),
        "deltas": numeric_deltas,
        "failure_type_deltas": diff_counts(
            dict(base_summary.get("failure_types") or {}),
            dict(head_summary.get("failure_types") or {}),
        ),
    }


def compare_task_results(base_report: dict[str, Any], head_report: dict[str, Any]) -> list[dict[str, Any]]:
    base_by_id = index_results(base_report)
    head_by_id = index_results(head_report)
    changes: list[dict[str, Any]] = []
    for task_id in sorted(set(base_by_id) | set(head_by_id)):
        base_item = base_by_id.get(task_id)
        head_item = head_by_id.get(task_id)
        if base_item is None:
            changes.append({"task_id": task_id, "change": "added"})
            continue
        if head_item is None:
            changes.append({"task_id": task_id, "change": "removed"})
            continue
        base_success = bool(base_item.get("success"))
        head_success = bool(head_item.get("success"))
        base_failures = list(base_item.get("failure_types") or [])
        head_failures = list(head_item.get("failure_types") or [])
        if base_success == head_success and base_failures == head_failures:
            continue
        changes.append(
            {
                "task_id": task_id,
                "change": "status_changed" if base_success != head_success else "failure_changed",
                "base_success": base_success,
                "head_success": head_success,
                "base_failure_types": base_failures,
                "head_failure_types": head_failures,
            }
        )
    return changes


def index_results(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for item in report.get("results") or []:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or item.get("case_id") or "")
        if task_id:
            results[task_id] = item
    return results


def diff_counts(base: dict[str, Any], head: dict[str, Any]) -> dict[str, int]:
    deltas: dict[str, int] = {}
    for key in sorted(set(base) | set(head)):
        delta = int(head.get(key, 0) or 0) - int(base.get(key, 0) or 0)
        if delta:
            deltas[key] = delta
    return deltas


def render_regression_markdown(
    summary: dict[str, Any],
    task_changes: list[dict[str, Any]],
) -> str:
    lines = [
        "# MiniAgent Regression Report",
        "",
        f"- Run ID: `{summary['run_id']}`",
        f"- Base: `{summary['base_report']}`",
        f"- Head: `{summary['head_report']}`",
        f"- Deltas: `{summary['deltas'] or {}}`",
        f"- Failure Type Deltas: `{summary['failure_type_deltas'] or {}}`",
        "",
        "## Task Changes",
        "",
    ]
    if not task_changes:
        lines.append("- No task status or failure-type changes.")
        return "\n".join(lines) + "\n"
    lines.append("| Task | Change | Base | Head |")
    lines.append("| --- | --- | --- | --- |")
    for item in task_changes:
        base_status = format_status(item.get("base_success"), item.get("base_failure_types"))
        head_status = format_status(item.get("head_success"), item.get("head_failure_types"))
        lines.append(f"| `{item['task_id']}` | {item['change']} | {base_status} | {head_status} |")
    return "\n".join(lines) + "\n"


def format_status(success: Any, failures: Any) -> str:
    if success is None:
        return "-"
    label = "PASS" if success else "FAIL"
    failure_text = ", ".join(str(item) for item in failures or [])
    return f"{label} {failure_text}".strip()
