from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .runtime_guards import (
    looks_like_action_request,
    looks_like_incomplete_progress,
    looks_like_output_file_claim,
    looks_like_tool_claim,
)

GroundingDetector = Callable[[str, str, list[str]], dict[str, Any] | None]

"""
只负责校验事实是否满足，比如是否真的读文件、真的保存文件、真的执行脚本、是否假称已完成。
"""
@dataclass
class RuntimeVerificationState:
    requires_file_grounding: bool = False
    file_grounding_evidence_collected: bool = False
    requires_output_file: bool = False
    output_file_created: bool = False
    force_skill_script_tool: bool = False
    skill_script_succeeded: bool = False
    tools_executed_in_turn: int = 0
    tool_names_executed_in_turn: list[str] = field(default_factory=list)


@dataclass
class VerificationResult:
    ok: bool
    violation_type: str = ""
    trace_kind: str = ""
    finish_reason: str = ""
    recovery_kind: str = ""
    user_preview: str = ""
    reply_preview: str = ""
    tool_names: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def passed(cls) -> "VerificationResult":
        return cls(ok=True)

    @classmethod
    def failed(
        cls,
        *,
        violation_type: str,
        trace_kind: str,
        finish_reason: str,
        recovery_kind: str,
        latest_user: str,
        reply: str,
        tool_names: list[str],
        payload: dict[str, Any] | None = None,
    ) -> "VerificationResult":
        base_payload = {
            "violation_type": violation_type,
            "user_preview": latest_user[:500],
            "reply_preview": str(reply or "")[:500],
            "tool_names": list(tool_names),
        }
        if payload:
            base_payload.update(payload)
        return cls(
            ok=False,
            violation_type=violation_type,
            trace_kind=trace_kind,
            finish_reason=finish_reason,
            recovery_kind=recovery_kind,
            user_preview=latest_user[:500],
            reply_preview=str(reply or "")[:500],
            tool_names=list(tool_names),
            payload=base_payload,
        )


def verify_final_reply(
    *,
    latest_user: str,
    reply: str,
    state: RuntimeVerificationState,
    grounding_detector: GroundingDetector | None = None,
) -> VerificationResult:
    tool_names = list(state.tool_names_executed_in_turn)

    if state.force_skill_script_tool and not state.skill_script_succeeded:
        return VerificationResult.failed(
            violation_type="script_tool_required_without_success",
            trace_kind="script_tool_violation",
            finish_reason="script_tool_violation",
            recovery_kind="force_skill_script",
            latest_user=latest_user,
            reply=reply,
            tool_names=tool_names,
        )

    if state.requires_file_grounding and not state.file_grounding_evidence_collected:
        return VerificationResult.failed(
            violation_type="file_grounding_required_without_evidence",
            trace_kind="grounding_violation",
            finish_reason="grounding_violation",
            recovery_kind="force_file_grounding",
            latest_user=latest_user,
            reply=reply,
            tool_names=tool_names,
        )

    if state.requires_output_file and not state.output_file_created:
        return VerificationResult.failed(
            violation_type="output_file_required_without_artifact",
            trace_kind="output_violation",
            finish_reason="output_violation",
            recovery_kind="force_output_file",
            latest_user=latest_user,
            reply=reply,
            tool_names=tool_names,
        )

    if looks_like_output_file_claim(reply) and not state.output_file_created and not _looks_like_runtime_error(reply):
        return VerificationResult.failed(
            violation_type="output_file_claim_without_artifact",
            trace_kind="output_violation",
            finish_reason="output_violation",
            recovery_kind="correct_output_claim",
            latest_user=latest_user,
            reply=reply,
            tool_names=tool_names,
        )

    if grounding_detector is not None:
        grounding_payload = grounding_detector(latest_user, reply, tool_names)
        if grounding_payload is not None:
            violation_type = str(
                grounding_payload.get("violation_type")
                or "file_content_claim_without_evidence"
            )
            return VerificationResult.failed(
                violation_type=violation_type,
                trace_kind="grounding_violation",
                finish_reason="grounding_violation",
                recovery_kind="force_file_grounding",
                latest_user=latest_user,
                reply=reply,
                tool_names=tool_names,
                payload=grounding_payload,
            )

    if (
        state.tools_executed_in_turn == 0
        and looks_like_action_request(latest_user)
        and looks_like_tool_claim(reply)
    ):
        return VerificationResult.failed(
            violation_type="action_claim_without_tool",
            trace_kind="action_violation",
            finish_reason="forced_tool_use_failed",
            recovery_kind="force_real_tool_or_disclaim",
            latest_user=latest_user,
            reply=reply,
            tool_names=tool_names,
        )

    if (
        state.tools_executed_in_turn > 0
        and looks_like_action_request(latest_user)
        and looks_like_incomplete_progress(reply)
    ):
        return VerificationResult.failed(
            violation_type="progress_only_after_tool",
            trace_kind="completion_violation",
            finish_reason="incomplete_progress",
            recovery_kind="force_completion",
            latest_user=latest_user,
            reply=reply,
            tool_names=tool_names,
        )

    return VerificationResult.passed()


def _looks_like_runtime_error(reply: str) -> bool:
    return str(reply or "").lstrip().lower().startswith("error:")
