from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .async_compat import run_blocking
from .attachments import Attachment, AttachmentStore
from .channels import BaseChannel, CLIChannel, QQChannel
from .config import (
    EMBEDDING_MODEL,
    MAX_HISTORY_MESSAGES,
    MEMORY_CONSOLIDATE_TRIGGER,
    MEMORY_KEEP_RECENT,
    MEMORY_RETRIEVAL_CANDIDATES,
    MEMORY_RETRIEVAL_TOP_K,
    MODEL,
    WORKSPACE,
    client,
)
from .intent import infer_turn_intent
from .memory import ContextBuilder, MemoryStore, SessionManager, consolidate_memory
from .message import InboundMessage, MessageBus, OutboundMessage
from .runtime_guards import wants_full_attachment_output as _wants_full_attachment_output
from .runtime_recovery import RuntimeRecoveryController
from .runtime_verifier import RuntimeVerificationState, verify_final_reply
from .skills import SkillLoader
from .skills.actions import SkillActionPlan, plan_skill_action
from .tools import (
    BrowserAutomationTool,
    ExecTool,
    FindFilesTool,
    ListOutboxFilesTool,
    ListUploadedFilesTool,
    ReadFileTool,
    ReadUploadedFileTool,
    RunSkillScriptTool,
    SaveOutboxFileTool,
    SearchCodeTool,
    ToolRegistry,
    WebFetchTool,
    WebSearchTool,
    WriteFileTool,
)

try:
    from .harness.trace import (
        detect_grounding_violation,
        has_file_grounding_tool,
        looks_like_file_grounding_request,
    )
except ImportError:  # pragma: no cover
    detect_grounding_violation = None  # type: ignore[assignment]
    has_file_grounding_tool = None  # type: ignore[assignment]
    looks_like_file_grounding_request = None  # type: ignore[assignment]

OUTPUT_FILE_TOOLS = {
    "save_outbox_file",
    "write_file",
    "run_skill_script",
}


def build_default_tools(
    llm_client: Any | None = None,
    model: str | None = None,
    attachment_store: AttachmentStore | None = None,
    session_key: str | None = None,
    inbound_attachments: list[Any] | None = None,
    session_attachments: list[Any] | None = None,
    skill_loader: SkillLoader | None = None,
    workspace: Path = WORKSPACE,
) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(ExecTool())
    tools.register(ReadFileTool())
    tools.register(WriteFileTool())
    tools.register(FindFilesTool())
    tools.register(SearchCodeTool())
    tools.register(WebSearchTool())
    tools.register(WebFetchTool())
    tools.register(BrowserAutomationTool())
    tools.register(RunSkillScriptTool(skill_loader or SkillLoader(workspace)))
    if attachment_store is not None and session_key:
        tools.register(ListOutboxFilesTool(attachment_store, session_key))
        tools.register(SaveOutboxFileTool(attachment_store, session_key))
        attachments = list(inbound_attachments or [])
        for item in list(session_attachments or []):
            if all(str(existing.path) != str(item.path) for existing in attachments):
                attachments.append(item)
        if attachments:
            tools.register(ListUploadedFilesTool(attachments))
            tools.register(ReadUploadedFileTool(attachment_store, attachments))
    return tools


async def agent_loop(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    tools: ToolRegistry,
    max_iterations: int = 10,
    metrics: dict[str, Any] | None = None,
    trace_sink: Any | None = None,
    requires_file_grounding: bool = False,
    initial_file_grounding_evidence: bool = False,
    requires_output_file: bool = False,
    force_skill_script_tool: bool = False,
) -> str:
    if client is None:
        reply = "LLM client is unavailable in this Python environment. Please activate the correct environment and configure the API key."
        _trace(
            trace_sink,
            "turn_completed",
            finish_reason="llm_unavailable",
            reply=reply,
            reply_preview=reply[:1000],
            tool_calls=0,
        )
        return reply

    recovery = RuntimeRecoveryController(max_retries=2)
    tools_executed_in_turn = 0
    tool_names_executed_in_turn: list[str] = []
    file_grounding_evidence_collected = initial_file_grounding_evidence
    output_file_created = False
    skill_script_succeeded = False

    if metrics is not None:
        metrics.setdefault("iterations", 0)
        metrics.setdefault("tool_calls", 0)
        metrics.setdefault("tool_call_batches", 0)
        metrics.setdefault("tool_names", [])
        metrics.setdefault("tool_errors", [])
        metrics.setdefault("requires_file_grounding", requires_file_grounding)
        metrics.setdefault("initial_file_grounding_evidence", initial_file_grounding_evidence)
        metrics.setdefault("requires_output_file", requires_output_file)
        metrics.setdefault("force_skill_script_tool", force_skill_script_tool)
        metrics.setdefault("finish_reason", "")

    for iteration_index in range(max_iterations):
        iteration = iteration_index + 1
        if metrics is not None:
            metrics["iterations"] = int(metrics.get("iterations", 0)) + 1
        definitions = tools.get_definitions() or None
        forced_tool_name = _forced_tool_choice_for_turn(
            definitions,
            force_skill_script_tool=force_skill_script_tool,
            iteration=iteration,
            forced_output_retries=recovery.state.forced_output_retries,
            forced_script_retries=recovery.state.forced_script_retries,
            output_file_created=output_file_created,
            skill_script_succeeded=skill_script_succeeded,
            recovery_forced_tools=recovery.consume_forced_tools(),
        )
        tool_choice = _tool_choice_for_function(forced_tool_name)
        _trace(
            trace_sink,
            "llm_request",
            iteration=iteration,
            model=model,
            message_count=len(messages),
            tool_count=len(definitions or []),
            last_user_preview=_latest_user_text(messages)[:500],
            requires_file_grounding=requires_file_grounding,
            file_grounding_evidence_collected=file_grounding_evidence_collected,
            requires_output_file=requires_output_file,
            output_file_created=output_file_created,
            forced_tool=forced_tool_name or "",
            force_skill_script_tool=force_skill_script_tool,
            skill_script_succeeded=skill_script_succeeded,
        )

        def _create_completion(use_tool_choice: bool = True):
            kwargs = {
                "model": model,
                "messages": messages,
                "tools": definitions,
                "temperature": 0.1,
            }
            if use_tool_choice and tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            return client.chat.completions.create(**kwargs)

        try:
            resp = await run_blocking(_create_completion)
        except Exception as exc:
            if tool_choice is None:
                raise
            _trace(
                trace_sink,
                "tool_choice_fallback",
                iteration=iteration,
                forced_tool=forced_tool_name or "",
                error=str(exc),
            )
            resp = await run_blocking(lambda: _create_completion(use_tool_choice=False))
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        _trace(
            trace_sink,
            "llm_response",
            iteration=iteration,
            content=str(msg.content or "")[:20000],
            content_preview=str(msg.content or "")[:1000],
            tool_calls=[
                {
                    "id": getattr(tc, "id", ""),
                    "name": getattr(getattr(tc, "function", None), "name", ""),
                    "arguments": str(getattr(getattr(tc, "function", None), "arguments", "") or "")[:20000],
                    "arguments_preview": str(getattr(getattr(tc, "function", None), "arguments", "") or "")[:500],
                }
                for tc in tool_calls
            ],
        )
        if tool_calls:
            if metrics is not None:
                metrics["tool_call_batches"] = int(metrics.get("tool_call_batches", 0)) + 1
            prepared_tool_calls: list[dict[str, Any]] = []
            prepared_results: list[tuple[Any, dict[str, Any] | None, str | None]] = []
            for tc in tool_calls:
                raw_arguments = tc.function.arguments or "{}"
                normalized_arguments = raw_arguments
                argument_error: str | None = None
                params: dict[str, Any] | None = None
                try:
                    parsed_arguments, repaired_arguments = _parse_tool_arguments(raw_arguments)
                    if repaired_arguments:
                        normalized_arguments = json.dumps(parsed_arguments, ensure_ascii=False)
                        print(
                            "  [ToolArgs] Recovered first JSON object from malformed "
                            f"arguments for {tc.function.name}."
                        )
                except json.JSONDecodeError as exc:
                    argument_error = f"Error: invalid tool arguments JSON: {exc}"
                    normalized_arguments = "{}"
                else:
                    if not isinstance(parsed_arguments, dict):
                        argument_error = (
                            "Error: invalid tool arguments: expected a JSON object "
                            f"but got {type(parsed_arguments).__name__}."
                        )
                        normalized_arguments = "{}"
                    else:
                        params = parsed_arguments
                        normalized_arguments = json.dumps(params, ensure_ascii=False)

                prepared_tool_calls.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": normalized_arguments,
                        },
                    }
                )
                prepared_results.append((tc, params, argument_error))

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": prepared_tool_calls,
                }
            )
            for tc, params, argument_error in prepared_results:
                _trace(
                    trace_sink,
                    "tool_call",
                    iteration=iteration,
                    tool=getattr(tc.function, "name", ""),
                    params=params or {},
                    argument_error=argument_error or "",
                )
                if argument_error:
                    result = argument_error
                else:
                    print(f"  [Tool] {tc.function.name}({(tc.function.arguments or '')[:80]})")
                    result = await tools.execute(tc.function.name, params or {})
                    tools_executed_in_turn += 1
                    tool_names_executed_in_turn.append(tc.function.name)
                failed, failure_type = _classify_runtime_tool_failure(tc.function.name, result)
                if not failed and _is_file_grounding_tool(tc.function.name):
                    file_grounding_evidence_collected = True
                    _trace(
                        trace_sink,
                        "evidence_collected",
                        iteration=iteration,
                        evidence_type="file_content",
                        source_tool=tc.function.name,
                    )
                if not failed and _is_output_file_tool(tc.function.name, result):
                    output_file_created = True
                    _trace(
                        trace_sink,
                        "output_artifact",
                        iteration=iteration,
                        source_tool=tc.function.name,
                        result_preview=str(result or "")[:1000],
                    )
                if (
                    not failed
                    and tc.function.name == "run_skill_script"
                    and "Return code: 0" in str(result or "")
                ):
                    skill_script_succeeded = True
                _trace(
                    trace_sink,
                    "tool_result",
                    iteration=iteration,
                    tool=tc.function.name,
                    failed=failed,
                    failure_type=failure_type,
                    result=str(result or "")[:20000],
                    result_preview=str(result or "")[:1000],
                )
                if metrics is not None:
                    metrics["tool_calls"] = int(metrics.get("tool_calls", 0)) + 1
                    metrics.setdefault("tool_names", []).append(tc.function.name)
                preview = result.replace("\n", " ")[:160]
                print(f"  [ToolResult] {preview}")
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result}
                )
                if result.startswith("Error:") or _is_skill_script_failure(tc.function.name, result):
                    if failure_type == "skill_script_not_found" and requires_output_file:
                        recovery.state.next_forced_tools = ["save_outbox_file", "write_file"]
                        _trace(
                            trace_sink,
                            "recovery_plan",
                            iteration=iteration,
                            violation_type="skill_script_not_found",
                            recovery_kind="fallback_to_output_file_tool",
                            action="retry",
                            forced_tools=list(recovery.state.next_forced_tools),
                            retry_count=0,
                            max_retries=2,
                            finish_reason="",
                        )
                    if metrics is not None:
                        metrics.setdefault("tool_errors", []).append(
                            {
                                "tool": tc.function.name,
                                "preview": result[:300],
                            }
                        )
                    messages.append(
                        {
                            "role": "system",
                            "content": _build_tool_failure_recovery_note(tc.function.name, result),
                        }
                    )
                elif (
                    tc.function.name == "run_skill_script"
                    and "Return code: 0" in result
                    and not result.startswith("Error:")
                ):
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "The skill script just completed successfully. "
                                "If this result is sufficient for the user's request, stop calling tools and "
                                "reply with a concise summary of the actual result. "
                                "Only call more tools if a required output file still has not been created or verified."
                            ),
                        }
                    )
            continue
        reply = msg.content or ""
        latest_user = _latest_user_text(messages)
        verification = verify_final_reply(
            latest_user=latest_user,
            reply=reply,
            state=RuntimeVerificationState(
                requires_file_grounding=requires_file_grounding,
                file_grounding_evidence_collected=file_grounding_evidence_collected,
                requires_output_file=requires_output_file,
                output_file_created=output_file_created,
                force_skill_script_tool=force_skill_script_tool,
                skill_script_succeeded=skill_script_succeeded,
                tools_executed_in_turn=tools_executed_in_turn,
                tool_names_executed_in_turn=list(tool_names_executed_in_turn),
            ),
            grounding_detector=_detect_grounding_violation,
        )
        if not verification.ok:
            _trace(
                trace_sink,
                verification.trace_kind,
                iteration=iteration,
                **verification.payload,
            )
            plan = recovery.plan(verification)
            _trace(
                trace_sink,
                "recovery_plan",
                iteration=iteration,
                violation_type=verification.violation_type,
                recovery_kind=plan.recovery_kind,
                action=plan.action,
                forced_tools=plan.forced_tools or [],
                retry_count=plan.retry_count,
                max_retries=plan.max_retries,
                finish_reason=plan.finish_reason,
            )
            if plan.should_fail:
                if metrics is not None:
                    metrics["finish_reason"] = plan.finish_reason
                _trace(
                    trace_sink,
                    "turn_completed",
                    finish_reason=plan.finish_reason,
                    reply=plan.error_message,
                    reply_preview=plan.error_message[:1000],
                    tool_calls=tools_executed_in_turn,
                )
                return plan.error_message
            if plan.should_retry:
                recovery.apply_plan(plan)
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "system", "content": plan.system_message})
                continue
        if metrics is not None:
            metrics["finish_reason"] = "completed"
        _trace(
            trace_sink,
            "turn_completed",
            finish_reason="completed",
            reply=str(reply or "")[:20000],
            reply_preview=str(reply or "")[:1000],
            tool_calls=tools_executed_in_turn,
        )
        return reply

    if metrics is not None:
        metrics["finish_reason"] = "max_iterations"
    _trace(
        trace_sink,
        "turn_completed",
        finish_reason="max_iterations",
        reply="Max iterations reached.",
        reply_preview="Max iterations reached.",
        tool_calls=tools_executed_in_turn,
    )
    return "Max iterations reached."


def _parse_tool_arguments(raw_arguments: str) -> tuple[Any, bool]:
    text = raw_arguments or "{}"
    try:
        return json.loads(text), False
    except json.JSONDecodeError as exc:
        if exc.msg != "Extra data":
            raise
        decoder = json.JSONDecoder()
        parsed, index = decoder.raw_decode(text)
        if not isinstance(parsed, dict):
            raise
        rest = text[index:].strip()
        if not rest:
            return parsed, False
        return parsed, True


def _build_tool_failure_recovery_note(tool_name: str, result: str) -> str:
    base = (
        "The immediately preceding tool call failed. "
        "You must explicitly report that failure to the user if it blocks the task. "
        "Do not claim the action succeeded, do not invent screenshots, window states, "
        "contact lists, button text, output files, or any other observations that were not returned by tools. "
        "If the user's task is still achievable by a different available tool or by writing a small script into "
        "outbox/workspace, continue with that real tool-backed action now. "
        "Do not reply with a progress-only promise such as 'I will do it next'."
    )
    if tool_name == "run_skill_script" and "Skill script not found" in str(result or ""):
        return (
            f"{base} The requested skill script does not exist. "
            "Do not call that missing script again, and do not guess alternate script names. "
            "Use only scripts that are documented in the loaded SKILL.md or confirmed by reading/listing the skill directory. "
            "For simple generated `.md`, `.docx`, or `.pdf` deliverables, prefer `save_outbox_file` with the final content. "
            "For xlsx row filtering tasks, prefer `scripts/filter_workbook.py`. "
            "If no suitable skill script exists, create a temporary script with `write_file` and execute it with `exec`, "
            "saving outputs to workspace/outbox."
        )
    if tool_name == "run_skill_script" and _is_skill_script_failure(tool_name, result):
        return (
            f"{base} The skill script returned a non-zero exit code. "
            "Inspect stderr/stdout, correct the arguments or choose another available workflow, then continue if possible. "
            "If the failure cannot be fixed with available information, stop and provide the exact tool failure."
        )
    return base


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = str(message.get("content", "") or "")
        if content.startswith("[Time:") and "\n\n" in content:
            return content.split("\n\n", 1)[1].strip()
        return content.strip()
    return ""


def _forced_tool_choice_for_turn(
    definitions: list[dict[str, Any]] | None,
    *,
    force_skill_script_tool: bool,
    iteration: int,
    forced_output_retries: int,
    forced_script_retries: int,
    output_file_created: bool,
    skill_script_succeeded: bool,
    recovery_forced_tools: list[str] | None = None,
) -> str:
    for tool_name in recovery_forced_tools or []:
        if _has_tool_definition(definitions, tool_name):
            return tool_name
    if not force_skill_script_tool or skill_script_succeeded:
        return ""
    if not _has_tool_definition(definitions, "run_skill_script"):
        return ""
    if iteration == 1 or forced_output_retries > 0 or forced_script_retries > 0:
        return "run_skill_script"
    return ""


def _has_tool_definition(definitions: list[dict[str, Any]] | None, tool_name: str) -> bool:
    for definition in definitions or []:
        function = definition.get("function") if isinstance(definition, dict) else None
        if isinstance(function, dict) and function.get("name") == tool_name:
            return True
    return False


def _tool_choice_for_function(tool_name: str) -> dict[str, Any] | None:
    if not tool_name:
        return None
    return {"type": "function", "function": {"name": tool_name}}


def _should_force_skill_script_tool(
    user_text: str,
    *,
    has_visible_attachments: bool,
    script_skill_names: list[str],
) -> bool:
    return infer_turn_intent(
        user_text,
        has_visible_attachments=has_visible_attachments,
        script_skill_names=script_skill_names,
    ).requires_script


def requires_output_file_for_turn(user_text: str, has_visible_attachments: bool = False) -> bool:
    return infer_turn_intent(
        user_text,
        has_visible_attachments=has_visible_attachments,
    ).requires_output_file


def _looks_like_outbox_listing_request(candidate: str) -> bool:
    return infer_turn_intent(candidate).is_outbox_listing


def _looks_like_chat_output_request(candidate: str) -> bool:
    intent = infer_turn_intent(candidate)
    return intent.operation in {"answer", "answer_from_file"} and not intent.requires_output_file


def _is_output_file_tool(tool_name: str, result: str) -> bool:
    if tool_name not in OUTPUT_FILE_TOOLS:
        return False
    text = str(result or "")
    if text.startswith("Error:") or _is_skill_script_failure(tool_name, text):
        return False
    if tool_name == "save_outbox_file":
        return "Saved generated file" in text and "Path:" in text
    if tool_name == "write_file":
        return text.startswith("Wrote ")
    if tool_name == "run_skill_script" and "Return code: 0" not in text:
        return False
    return bool(
        re.search(r"workspace[/\\]+outbox", text, re.IGNORECASE)
        or re.search(
            r'"(?:output|output_path|output_file|path)"\s*:\s*".+?\.(?:pdf|docx|xlsx|md|txt)"',
            text,
            re.IGNORECASE,
        )
        or re.search(r"\b(saved|generated|created|wrote)\b", text, re.IGNORECASE)
    )


def _format_skill_action_reply(plan: SkillActionPlan, result: str) -> str:
    output_path = _extract_output_path_from_result(result) or plan.output_path
    output_name = Path(output_path).name
    if output_name.lower().endswith(".pdf"):
        return f"已导出 PDF：{output_name}\n路径：{output_path}"
    return f"已生成文件：{output_name}\n路径：{output_path}"


def _extract_output_path_from_result(result: str) -> str:
    text = str(result or "")
    match = re.search(
        r'"(?:output|output_path|output_file|path)"\s*:\s*"([^"]+)"',
        text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    match = re.search(r"([A-Za-z]:\\[^\n\r]+?\.(?:pdf|docx|xlsx|md|txt))", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def requires_file_grounding_for_turn(user_text: str, has_visible_attachments: bool) -> bool:
    if not has_visible_attachments:
        return False
    structured = infer_turn_intent(
        user_text,
        has_visible_attachments=has_visible_attachments,
    )
    if structured.requires_file_grounding:
        return True
    if looks_like_file_grounding_request is None:
        return False
    return bool(looks_like_file_grounding_request(user_text))


def _is_file_grounding_tool(tool_name: str) -> bool:
    if has_file_grounding_tool is None:
        return tool_name in {"read_uploaded_file", "read_file", "run_skill_script"}
    return bool(has_file_grounding_tool([tool_name]))


def _detect_grounding_violation(
    latest_user: str,
    reply: str,
    tool_names: list[str],
) -> dict[str, Any] | None:
    if detect_grounding_violation is None:
        return None
    return detect_grounding_violation(
        user_text=latest_user,
        reply=reply,
        tool_names=tool_names,
    )


def _is_skill_script_failure(tool_name: str, result: str) -> bool:
    if tool_name != "run_skill_script":
        return False
    text = str(result or "")
    return "Return code:" in text and "Return code: 0" not in text


def _classify_runtime_tool_failure(tool_name: str, result: str) -> tuple[bool, str]:
    text = str(result or "")
    if text.startswith("Error:"):
        if "Skill script not found" in text:
            return True, "skill_script_not_found"
        if "Tool '" in text and "not found" in text:
            return True, "tool_not_found"
        if "invalid tool arguments" in text:
            return True, "invalid_tool_arguments"
        return True, "tool_error"
    if _is_skill_script_failure(tool_name, result):
        return True, "skill_script_nonzero"
    return False, ""


def _trace(trace_sink: Any | None, kind: str, **payload: Any) -> None:
    if trace_sink is None:
        return
    try:
        trace_sink.write(kind, **payload)
    except Exception as exc:
        print(f"[Trace] Failed to write {kind}: {exc}")


def _list_outbox_paths(store: AttachmentStore, session_key: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for item in store.list_session_outbox(session_key):
        normalized = str(Path(item.path).expanduser().resolve())
        if normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)
    session_dir = store.outbox_session_dir(session_key)
    if session_dir.exists():
        for path in session_dir.rglob("*"):
            if not path.is_file() or path.name == "manifest.json":
                continue
            normalized = str(path.expanduser().resolve())
            if normalized in seen:
                continue
            seen.add(normalized)
            paths.append(normalized)
    return paths


def _collect_outbox_attachments(store: AttachmentStore, session_key: str, max_items: int = 10) -> list[Attachment]:
    items: list[Attachment] = []
    for raw_path in _list_outbox_paths(store, session_key):
        path = Path(raw_path).expanduser().resolve()
        if not path.exists() or not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        items.append(
            Attachment(
                name=path.name,
                path=str(path),
                size=size,
                origin="outbox",
            )
        )
    items.sort(key=lambda item: Path(item.path).stat().st_mtime if Path(item.path).exists() else 0, reverse=True)
    return items[:max_items]


def build_file_grounding_note(
    *,
    user_text: str,
    attachments: list[Attachment],
    store: AttachmentStore,
    trace_sink: Any | None = None,
    max_files: int = 2,
    max_chars_per_file: int = 16000,
) -> tuple[str, bool]:
    selected = _select_file_grounding_attachments(user_text, attachments, max_files=max_files)
    if not selected:
        return "", False

    sections: list[str] = []
    failures: list[str] = []
    for attachment in selected:
        try:
            content = store.read_text(attachment.path, max_chars=max_chars_per_file).strip()
        except Exception as exc:
            failures.append(f"{attachment.name}: {exc}")
            _trace(
                trace_sink,
                "file_grounding_preload",
                status="failed",
                file_name=attachment.name,
                path=attachment.path,
                error=str(exc),
            )
            continue
        if not content:
            failures.append(f"{attachment.name}: extracted text is empty")
            _trace(
                trace_sink,
                "file_grounding_preload",
                status="empty",
                file_name=attachment.name,
                path=attachment.path,
            )
            continue
        sections.append(
            "\n".join(
                [
                    f"[File] {attachment.name}",
                    f"[Path] {attachment.path}",
                    "[Extracted text]",
                    content,
                ]
            )
        )
        _trace(
            trace_sink,
            "evidence_collected",
            evidence_type="file_content",
            source_tool="runtime_preload",
            file_name=attachment.name,
            path=attachment.path,
            char_count=len(content),
        )
        _trace(
            trace_sink,
            "file_grounding_preload",
            status="ok",
            file_name=attachment.name,
            path=attachment.path,
            char_count=len(content),
        )

    if not sections:
        return "", False

    note = (
        "Runtime has already read the visible file content for this turn. "
        "Treat the extracted text below as authoritative evidence. "
        "本轮 runtime 已预先读取可见文件内容；下面的提取文本是回答文件内容问题的权威依据。"
        "不要根据文件名、历史回复或记忆推断文件内容；如果它们与此证据冲突，以此证据为准。"
        "If the user asks to create an output file, use this evidence and then save the result with the appropriate tool.\n\n"
        + "\n\n---\n\n".join(sections)
    )
    if failures:
        note += "\n\nFiles that could not be preloaded:\n" + "\n".join(f"- {item}" for item in failures)
    return note, True


def _select_file_grounding_attachments(
    user_text: str,
    attachments: list[Attachment],
    *,
    max_files: int,
) -> list[Attachment]:
    if not attachments:
        return []
    text = (user_text or "").lower()
    mentioned = [
        item
        for item in attachments
        if item.name and item.name.lower() in text
    ]
    if mentioned:
        return mentioned[:max_files]

    stem_matches = [
        item
        for item in attachments
        if item.name and Path(item.name).stem.lower() and Path(item.name).stem.lower() in text
    ]
    if stem_matches:
        return stem_matches[:max_files]

    type_matches = [
        item
        for item in attachments
        if _attachment_type_matches_request(text, item)
    ]
    if type_matches:
        return type_matches[:max_files]

    # Visible files are ordered newest-first by the caller. The newest one is usually
    # what "这个文件/这个 excel/文件里" refers to when no filename is mentioned.
    return attachments[:max_files]


def _attachment_type_matches_request(text: str, attachment: Attachment) -> bool:
    suffix = Path(attachment.name or attachment.path).suffix.lower()
    if any(hint in text for hint in ("excel", "xlsx", "xlsm", "表格", "工作簿", "电子表格")):
        return suffix in {".xlsx", ".xlsm", ".csv", ".tsv"}
    if any(hint in text for hint in ("pdf", "论文")):
        return suffix == ".pdf"
    if any(hint in text for hint in ("word", "docx", "文档")):
        return suffix in {".docx", ".doc"}
    if any(hint in text for hint in ("markdown", ".md", "md文件")):
        return suffix == ".md"
    return False


class MiniAgentApp:
    """把消息总线、记忆、工具和 Channel 装配成可运行的 Agent 应用。"""

    def __init__(
        self,
        workspace: Path = WORKSPACE,
        model: str = MODEL,
        llm_client: Any = client,
        tools: ToolRegistry | None = None,
        bus: MessageBus | None = None,
        skill_loader: SkillLoader | None = None,
        context_builder: ContextBuilder | None = None,
        attachment_store: AttachmentStore | None = None,
        memory: MemoryStore | None = None,
        sessions: SessionManager | None = None,
        trace_sink: Any | None = None,
    ):
        self.workspace = workspace
        self.model = model
        self.client = llm_client
        self.tools = tools
        self.bus = bus or MessageBus()
        self.skill_loader = skill_loader or SkillLoader(workspace)
        self.ctx = context_builder or ContextBuilder(workspace, skill_loader=self.skill_loader)
        self.attachment_store = attachment_store or AttachmentStore(workspace)
        self.memory = memory or MemoryStore(workspace)
        self.sessions = sessions or SessionManager(workspace)
        self.trace_sink = trace_sink
        self.channels: dict[str, BaseChannel] = {}

    def register_channel(self, channel: BaseChannel):
        self.channels[channel.name] = channel

    async def handle_inbound(self, inbound: InboundMessage):
        content = (inbound.content or "").strip()
        if not content and not inbound.media:
            return

        print(f"[App] Handling inbound from {inbound.channel}/{inbound.chat_id}: {content}")

        if content == "/new":
            self.sessions.reset(inbound.session_key)
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    content="New session started.",
                )
            )
            return

        session = self.sessions.get_or_create(inbound.session_key)

        if inbound.attachments and not content:
            timestamp = datetime.now().isoformat()
            session.messages.append(
                {
                    "role": "user",
                    "content": "",
                    "timestamp": timestamp,
                    "attachments": [item.to_dict() for item in inbound.attachments],
                    "media": inbound.media,
                    "metadata": inbound.metadata,
                }
            )
            names = "、".join(item.name for item in inbound.attachments if item.name)
            if names:
                reply = f"已收到您上传的文件：{names}。"
            else:
                reply = f"已收到您上传的 {len(inbound.attachments)} 个文件。"
            session.messages.append(
                {
                    "role": "assistant",
                    "content": reply,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            self.sessions.save(session)
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    content=reply,
                )
            )
            print(
                f"[App] Attachment-only message acknowledged for "
                f"{inbound.channel}/{inbound.chat_id}: {reply[:120]}"
            )
            return

        if self.client is not None:
            try:
                await consolidate_memory(
                    client=self.client,
                    model=self.model,
                    session=session,
                    memory=self.memory,
                    trigger_messages=MEMORY_CONSOLIDATE_TRIGGER,
                    keep_recent=MEMORY_KEEP_RECENT,
                    embedding_model=EMBEDDING_MODEL,
                )
            except Exception as exc:
                print(f"[Memory] Consolidation skipped: {exc}")

        self.sessions.save(session)

        history = session.get_history(max_messages=MAX_HISTORY_MESSAGES)
        session_attachments = self.attachment_store.collect_session_attachments(
            session.messages
        )
        outbox_attachments = _collect_outbox_attachments(
            self.attachment_store,
            inbound.session_key,
        )
        all_visible_attachments = list(inbound.attachments)
        for item in [*outbox_attachments, *session_attachments]:
            if all(str(existing.path) != str(item.path) for existing in all_visible_attachments):
                all_visible_attachments.append(item)
        script_skill_names = self.skill_loader.select_script_skill_names(
            content,
            attachments=all_visible_attachments,
        )
        turn_intent = infer_turn_intent(
            content,
            attachments=all_visible_attachments,
            has_visible_attachments=bool(all_visible_attachments),
            script_skill_names=script_skill_names,
        )
        _trace(self.trace_sink, "turn_intent", **turn_intent.to_trace_dict())
        requires_file_grounding = turn_intent.requires_file_grounding
        requires_output_file = turn_intent.requires_output_file
        file_grounding_note = ""
        initial_file_grounding_evidence = False
        if requires_file_grounding:
            file_grounding_note, initial_file_grounding_evidence = build_file_grounding_note(
                user_text=content,
                attachments=all_visible_attachments,
                store=self.attachment_store,
                trace_sink=self.trace_sink,
            )
        prompt_content = content or "请处理我刚上传的文件。"
        current_attachment_note = self.attachment_store.describe_attachments(inbound.attachments)
        summary_first_instruction = (
            "默认像聊天一样简洁回复，只说结论、关键依据和下一步。"
            "不要写成 Markdown 报告，不要使用 `###`、`---`、大量加粗或装饰符号。"
            "不要复述文件全文，不要逐段粘贴内容，除非用户明确要求全文、原文、完整表格或正式文档。"
        )
        if current_attachment_note:
            prompt_content = (
                f"{current_attachment_note}\n\n"
                "如需查看上传文件内容，请使用 `list_uploaded_files` 或 `read_uploaded_file`。"
                f"{summary_first_instruction}"
                "如果需要输出结果文件，请使用 `save_outbox_file` 保存到 workspace/outbox。\n\n"
                f"用户要求：{prompt_content}"
            )
        elif all_visible_attachments:
            prompt_content = (
                "你可以访问这个会话里最近上传或生成过的文件。"
                "如需查看文件内容，请使用 `list_uploaded_files` 或 `read_uploaded_file`。"
                f"{summary_first_instruction}"
                "如果需要输出结果文件，请使用 `save_outbox_file` 保存到 workspace/outbox。\n\n"
                f"用户要求：{prompt_content}"
            )
        if all_visible_attachments and not _wants_full_attachment_output(content):
            prompt_content += (
                "\n\n回复要求：像聊天一样回复。除非用户明确要求全文、完整展开、表格或正式文档，"
                "否则不要使用报告式 Markdown；优先用 2-5 句自然语言说明结论、依据和下一步。"
            )
        retrieval_query = content.strip()
        if not retrieval_query and all_visible_attachments:
            retrieval_query = " ".join(item.name for item in all_visible_attachments if item.name).strip()
        relevant_memory_note = await self.memory.build_relevant_memory_note(
            retrieval_query,
            client=self.client,
            embedding_model=EMBEDDING_MODEL,
            top_k=MEMORY_RETRIEVAL_TOP_K,
            candidate_pool=MEMORY_RETRIEVAL_CANDIDATES,
            trace_sink=self.trace_sink,
        )
        runtime_skill_note = self.skill_loader.build_runtime_note(
            prompt_content,
            outbox_dir=self.attachment_store.outbox_session_dir(inbound.session_key),
            attachments=all_visible_attachments,
        )
        force_skill_script_tool = turn_intent.requires_script
        messages = self.ctx.build_messages(
            history,
            prompt_content,
            attachments=all_visible_attachments,
            extra_system_notes=[
                note
                for note in (relevant_memory_note, file_grounding_note, runtime_skill_note)
                if str(note or "").strip()
            ] or None,
        )
        runtime_tools = self.tools or build_default_tools(
            llm_client=self.client,
            model=self.model,
            attachment_store=self.attachment_store,
            session_key=inbound.session_key,
            inbound_attachments=inbound.attachments,
            session_attachments=[*outbox_attachments, *session_attachments],
            skill_loader=self.skill_loader,
            workspace=self.workspace,
        )
        before_outbox = set(_list_outbox_paths(self.attachment_store, inbound.session_key))

        reply = ""
        action_plan = None
        if requires_output_file:
            action_plan = plan_skill_action(
                skill_loader=self.skill_loader,
                user_text=content,
                attachments=all_visible_attachments,
                outbox_dir=self.attachment_store.outbox_session_dir(inbound.session_key),
                requires_output_file=requires_output_file,
            )
        if action_plan is not None:
            _trace(
                self.trace_sink,
                "skill_action_plan",
                skill_name=action_plan.skill_name,
                action_name=action_plan.action_name,
                tool=action_plan.tool_name,
                params=action_plan.params,
                input_path=action_plan.input_path,
                output_path=action_plan.output_path,
                description=action_plan.description,
            )
            _trace(
                self.trace_sink,
                "tool_call",
                iteration=0,
                tool=action_plan.tool_name,
                params=action_plan.params,
                source="skill_action_plan",
            )
            result = await runtime_tools.execute(action_plan.tool_name, action_plan.params)
            failed, failure_type = _classify_runtime_tool_failure(action_plan.tool_name, result)
            _trace(
                self.trace_sink,
                "tool_result",
                iteration=0,
                tool=action_plan.tool_name,
                failed=failed,
                failure_type=failure_type,
                result=str(result or "")[:20000],
                result_preview=str(result or "")[:1000],
                source="skill_action_plan",
            )
            if not failed and _is_output_file_tool(action_plan.tool_name, result):
                _trace(
                    self.trace_sink,
                    "output_artifact",
                    iteration=0,
                    source_tool=action_plan.tool_name,
                    result_preview=str(result or "")[:1000],
                    source="skill_action_plan",
                )
                reply = _format_skill_action_reply(action_plan, result)
                _trace(
                    self.trace_sink,
                    "turn_completed",
                    finish_reason="skill_action_completed",
                    reply=reply,
                    reply_preview=reply[:1000],
                    tool_calls=1,
                    source="skill_action_plan",
                )
            else:
                _trace(
                    self.trace_sink,
                    "skill_action_failed",
                    skill_name=action_plan.skill_name,
                    action_name=action_plan.action_name,
                    tool=action_plan.tool_name,
                    failure_type=failure_type,
                    result_preview=str(result or "")[:1000],
                )

        if not reply:
            try:
                reply = await agent_loop(
                    client=self.client,
                    model=self.model,
                    messages=messages,
                    tools=runtime_tools,
                    trace_sink=self.trace_sink,
                    requires_file_grounding=requires_file_grounding,
                    initial_file_grounding_evidence=initial_file_grounding_evidence,
                    requires_output_file=requires_output_file,
                    force_skill_script_tool=force_skill_script_tool,
                )
            except Exception as exc:
                reply = f"Error while generating reply: {exc}"
        after_outbox = _list_outbox_paths(self.attachment_store, inbound.session_key)
        for path in [path for path in after_outbox if path not in before_outbox]:
            _trace(self.trace_sink, "file_created", path=path, session_key=inbound.session_key)

        timestamp = datetime.now().isoformat()
        session.messages.append(
            {
                "role": "user",
                "content": content,
                "timestamp": timestamp,
                "attachments": [item.to_dict() for item in inbound.attachments],
                "media": inbound.media,
                "metadata": inbound.metadata,
            }
        )
        session.messages.append(
            {
                "role": "assistant",
                "content": reply,
                "timestamp": datetime.now().isoformat(),
            }
        )
        self.sessions.save(session)

        await self.bus.publish_outbound(
            OutboundMessage(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                content=reply,
            )
        )
        preview = reply.splitlines()[0] if reply else ""
        print(
            f"[App] Reply queued for {inbound.channel}/{inbound.chat_id}: "
            f"{preview[:120]}"
        )

    async def inbound_worker(self):
        while True:
            inbound = await self.bus.consume_inbound()
            await self.handle_inbound(inbound)

    async def outbound_worker(self):
        while True:
            outbound = await self.bus.consume_outbound()
            channel = self.channels.get(outbound.channel)
            if channel is None:
                print(f"[Bus] No channel registered for outbound message: {outbound.channel}")
                continue
            try:
                await channel.send(outbound)
            except Exception as exc:
                print(
                    f"[Bus] Outbound send failed via {outbound.channel} "
                    f"to {outbound.chat_id}: {exc}"
                )

    async def run(self, channels: list[BaseChannel]):
        for channel in channels:
            self.register_channel(channel)

        inbound_task = asyncio.create_task(self.inbound_worker(), name="miniagent-inbound")
        outbound_task = asyncio.create_task(self.outbound_worker(), name="miniagent-outbound")
        channel_tasks = [asyncio.create_task(channel.start(), name=f"channel-{channel.name}") for channel in channels]

        try:
            done, pending = await asyncio.wait(channel_tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
            for task in pending:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            for channel in channels:
                try:
                    await channel.stop()
                except Exception as exc:
                    print(f"[Channel] Stop failed for {channel.name}: {exc}")

            inbound_task.cancel()
            outbound_task.cancel()
            await asyncio.gather(inbound_task, outbound_task, return_exceptions=True)


async def run_terminal_app():
    print(f"Mini Agent (workspace: {WORKSPACE})")
    print("输入 exit 退出 | 输入 /new 清空会话\n")
    app = MiniAgentApp()
    cli = CLIChannel(app.bus)
    await app.run([cli])


def build_channels(app: MiniAgentApp, channel_names: list[str]) -> list[BaseChannel]:
    channels: list[BaseChannel] = []
    for name in channel_names:
        channel_name = name.strip().lower()
        if channel_name == "cli":
            channels.append(CLIChannel(app.bus))
        elif channel_name == "qq":
            channels.append(
                QQChannel(
                    app.bus,
                    workspace=app.workspace,
                    attachment_store=app.attachment_store,
                )
            )
        else:
            raise ValueError(f"Unsupported channel: {name}")
    return channels


async def run_selected_channels(channel_names: list[str]):
    app = MiniAgentApp()
    channels = build_channels(app, channel_names)
    print(f"Mini Agent (workspace: {WORKSPACE})")
    print(f"Channels: {', '.join(channel_names)}")
    if "cli" in [name.lower() for name in channel_names]:
        print("输入 exit 退出 | 输入 /new 清空会话\n")
    else:
        print("QQ 服务模式已启动，终端不会接收输入；按 Ctrl+C 退出。\n")
    await app.run(channels)


def parse_channel_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Mini Agent with selected channels.")
    parser.add_argument(
        "--channels",
        default="cli",
        help="Comma-separated channels to enable, e.g. cli or qq or cli,qq",
    )
    return parser.parse_args(argv)
