from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .trace import detect_grounding_violation, detect_output_violation, has_output_file_tool


@dataclass
class ReplayedToolCall:
    tool: str
    params: dict[str, Any]
    failed: bool
    failure_type: str = ""
    result: str = ""


@dataclass
class ReplayedIteration:
    iteration: int
    model: str
    last_user_preview: str
    response: str
    tool_calls: list[ReplayedToolCall] = field(default_factory=list)


@dataclass
class DeterministicReplayResult:
    status: str
    issues: list[str]
    iterations: list[ReplayedIteration]
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""
    final_reply: str = ""
    judge: dict[str, Any] | None = None


def run_replay_report(
    *,
    source: Path,
    results_dir: Path,
) -> dict[str, Any]:
    events = load_jsonl(source)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    replay = replay_events(events)
    summary = build_replay_summary(source, events, replay=replay, run_id=run_id)
    report = {
        "summary": summary,
        "deterministic_replay": render_replay_payload(replay),
        "events": events,
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    json_path = results_dir / f"replay_{run_id}.json"
    md_path = results_dir / "replay_latest.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_replay_markdown(summary, events), encoding="utf-8")
    report["summary"]["json_report"] = str(json_path)
    report["summary"]["markdown_report"] = str(md_path)
    return report


def replay_events(events: list[dict[str, Any]]) -> DeterministicReplayResult:
    issues: list[str] = []
    iterations: list[ReplayedIteration] = []
    diagnostics = analyze_trace_diagnostics(events)
    index = 0
    final_reply = ""
    finish_reason = ""
    judge: dict[str, Any] | None = None

    while index < len(events):
        event = events[index]
        kind = str(event.get("kind") or "")
        if kind == "llm_request":
            iteration = int(event.get("iteration") or len(iterations) + 1)
            replayed, index = replay_iteration(events, index, issues)
            iterations.append(replayed)
            continue
        if kind == "turn_completed":
            finish_reason = str(event.get("finish_reason") or "")
            final_reply = str(event.get("reply") or event.get("reply_preview") or "")
        elif kind == "judge_result":
            judge = {
                "task_id": event.get("task_id", ""),
                "success": bool(event.get("success")),
                "failure_types": list(event.get("failure_types") or []),
            }
        elif kind in {"tool_call", "tool_result", "llm_response"}:
            issues.append(f"Unexpected {kind} without matching llm_request at event {index + 1}.")
        index += 1

    if not finish_reason and iterations:
        issues.append("Missing turn_completed event.")
    status = "passed" if not issues else "failed"
    return DeterministicReplayResult(
        status=status,
        issues=issues,
        iterations=iterations,
        diagnostics=diagnostics,
        finish_reason=finish_reason,
        final_reply=final_reply,
        judge=judge,
    )


def replay_iteration(
    events: list[dict[str, Any]],
    request_index: int,
    issues: list[str],
) -> tuple[ReplayedIteration, int]:
    request = events[request_index]
    iteration = int(request.get("iteration") or 0)
    response_index = request_index + 1
    if response_index >= len(events) or str(events[response_index].get("kind") or "") != "llm_response":
        issues.append(f"Missing llm_response after llm_request iteration {iteration}.")
        return (
            ReplayedIteration(
                iteration=iteration,
                model=str(request.get("model") or ""),
                last_user_preview=str(request.get("last_user_preview") or ""),
                response="",
            ),
            response_index,
        )

    response = events[response_index]
    expected_calls = list(response.get("tool_calls") or [])
    next_index = response_index + 1
    replayed_calls: list[ReplayedToolCall] = []

    for expected in expected_calls:
        if next_index >= len(events) or str(events[next_index].get("kind") or "") != "tool_call":
            issues.append(f"Missing tool_call after llm_response iteration {iteration}.")
            break
        call_event = events[next_index]
        expected_name = str(expected.get("name") or "")
        actual_name = str(call_event.get("tool") or "")
        if expected_name and actual_name and expected_name != actual_name:
            issues.append(
                f"Tool call mismatch in iteration {iteration}: expected {expected_name}, got {actual_name}."
            )
        next_index += 1
        if next_index >= len(events) or str(events[next_index].get("kind") or "") != "tool_result":
            issues.append(f"Missing tool_result for {actual_name or expected_name} in iteration {iteration}.")
            replayed_calls.append(
                ReplayedToolCall(
                    tool=actual_name or expected_name,
                    params=dict(call_event.get("params") or {}),
                    failed=True,
                    failure_type="missing_tool_result",
                )
            )
            break
        result_event = events[next_index]
        result_name = str(result_event.get("tool") or "")
        if actual_name and result_name and actual_name != result_name:
            issues.append(
                f"Tool result mismatch in iteration {iteration}: call {actual_name}, result {result_name}."
            )
        replayed_calls.append(
            ReplayedToolCall(
                tool=result_name or actual_name or expected_name,
                params=dict(call_event.get("params") or {}),
                failed=bool(result_event.get("failed")),
                failure_type=str(result_event.get("failure_type") or ""),
                result=str(result_event.get("result") or result_event.get("result_preview") or ""),
            )
        )
        next_index += 1

    return (
        ReplayedIteration(
            iteration=iteration,
            model=str(request.get("model") or ""),
            last_user_preview=str(request.get("last_user_preview") or ""),
            response=str(response.get("content") or response.get("content_preview") or ""),
            tool_calls=replayed_calls,
        ),
        next_index,
    )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Replay source not found: {path}")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
        if isinstance(item, dict):
            events.append(item)
        else:
            events.append({"value": item})
    return events


def analyze_trace_diagnostics(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    grounded_tools_this_turn: list[str] = []
    output_tools_this_turn: list[str] = []
    latest_user = ""
    seen: set[tuple[str, str, str]] = set()

    for index, event in enumerate(events, start=1):
        kind = str(event.get("kind") or "")
        if kind == "llm_request":
            latest_user = str(event.get("last_user_preview") or latest_user)
            continue
        if kind == "tool_result":
            tool_name = str(event.get("tool") or "")
            if not bool(event.get("failed")):
                grounded_tools_this_turn.append(tool_name)
                if has_output_file_tool([tool_name]):
                    output_tools_this_turn.append(tool_name)
            continue
        if kind not in {"llm_response", "turn_completed"}:
            continue

        reply = str(
            event.get("content")
            or event.get("reply")
            or event.get("content_preview")
            or event.get("reply_preview")
            or ""
        )
        if kind == "llm_response" and event.get("tool_calls"):
            continue
        violation = detect_grounding_violation(
            user_text=latest_user,
            reply=reply,
            tool_names=grounded_tools_this_turn,
        )
        if violation is not None:
            key = (
                str(violation.get("violation_type") or ""),
                str(violation.get("reply_preview") or "")[:120],
            )
            if key not in seen:
                seen.add(key)
                diagnostics.append(
                    {
                        "event_index": index,
                        "kind": kind,
                        **violation,
                    }
                )
        output_violation = detect_output_violation(
            user_text=latest_user,
            reply=reply,
            tool_names=output_tools_this_turn,
        )
        if output_violation is not None:
            key = (
                str(output_violation.get("violation_type") or ""),
                str(output_violation.get("reply_preview") or "")[:120],
            )
            if key not in seen:
                seen.add(key)
                diagnostics.append(
                    {
                        "event_index": index,
                        "kind": kind,
                        **output_violation,
                    }
                )
        if kind == "turn_completed":
            grounded_tools_this_turn = []
            output_tools_this_turn = []
            latest_user = ""
    return diagnostics


def build_replay_summary(
    source: Path,
    events: list[dict[str, Any]],
    *,
    replay: DeterministicReplayResult,
    run_id: str,
) -> dict[str, Any]:
    roles: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for event in events:
        role = str(event.get("role") or "")
        kind = str(event.get("kind") or "")
        if role:
            roles[role] = roles.get(role, 0) + 1
        if kind:
            kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "run_id": run_id,
        "source": str(source),
        "events": len(events),
        "roles": roles,
        "kinds": kinds,
        "replay_status": replay.status,
        "replay_issues": list(replay.issues),
        "diagnostics": list(replay.diagnostics),
        "diagnostic_count": len(replay.diagnostics),
        "replayed_iterations": len(replay.iterations),
        "replayed_tool_calls": sum(len(item.tool_calls) for item in replay.iterations),
        "finish_reason": replay.finish_reason,
        "final_reply_preview": replay.final_reply[:500],
    }


def render_replay_markdown(summary: dict[str, Any], events: list[dict[str, Any]]) -> str:
    lines = [
        "# MiniAgent Replay Report",
        "",
        f"- Run ID: `{summary['run_id']}`",
        f"- Source: `{summary['source']}`",
        f"- Events: {summary['events']}",
        f"- Roles: `{summary['roles'] or {}}`",
        f"- Kinds: `{summary['kinds'] or {}}`",
        f"- Replay Status: `{summary['replay_status']}`",
        f"- Replayed Iterations: {summary['replayed_iterations']}",
        f"- Replayed Tool Calls: {summary['replayed_tool_calls']}",
        f"- Finish Reason: `{summary['finish_reason']}`",
        "",
        "## Deterministic Replay",
        "",
    ]
    issues = list(summary.get("replay_issues") or [])
    if issues:
        lines.extend(f"- {issue}" for issue in issues)
    else:
        lines.append("- Trace replay completed without ordering or matching issues.")
    lines.extend([
        "",
        "## Diagnostics",
        "",
    ])
    diagnostics = list(summary.get("diagnostics") or [])
    if diagnostics:
        for item in diagnostics:
            lines.append(
                "- "
                f"`{item.get('violation_type', 'diagnostic')}` at event {item.get('event_index')}: "
                f"{str(item.get('reply_preview') or '')[:160]}"
            )
    else:
        lines.append("- No runtime diagnostics were detected.")
    lines.extend([
        "",
        "## Timeline Preview",
        "",
    ])
    lines.append("| # | Role | Kind | Preview |")
    lines.append("| ---: | --- | --- | --- |")
    for index, event in enumerate(events[:50], start=1):
        role = str(event.get("role") or "-")
        kind = str(event.get("kind") or "-")
        preview = event_preview(event)
        lines.append(f"| {index} | {role} | {kind} | {preview} |")
    if len(events) > 50:
        lines.append(f"| ... | ... | ... | {len(events) - 50} more events omitted |")
    return "\n".join(lines) + "\n"


def event_preview(event: dict[str, Any]) -> str:
    content = event.get("content")
    if content is None:
        content = event.get("summary") or event.get("user_message_preview") or event.get("details") or event
    text = str(content).replace("\n", " ").replace("|", "\\|")
    return text[:160] or "-"


def render_replay_payload(replay: DeterministicReplayResult) -> dict[str, Any]:
    return {
        "status": replay.status,
        "issues": list(replay.issues),
        "diagnostics": list(replay.diagnostics),
        "finish_reason": replay.finish_reason,
        "final_reply": replay.final_reply,
        "judge": replay.judge or {},
        "iterations": [
            {
                "iteration": item.iteration,
                "model": item.model,
                "last_user_preview": item.last_user_preview,
                "response": item.response,
                "tool_calls": [
                    {
                        "tool": call.tool,
                        "params": call.params,
                        "failed": call.failed,
                        "failure_type": call.failure_type,
                        "result": call.result,
                    }
                    for call in item.tool_calls
                ],
            }
            for item in replay.iterations
        ],
    }
