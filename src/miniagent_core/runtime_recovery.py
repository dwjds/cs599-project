from __future__ import annotations

from dataclasses import dataclass

from .runtime_verifier import VerificationResult

"""
只负责恢复策略，比如追加哪条 system message、重试几次、超过次数后返回什么真实错误。
"""
@dataclass
class RuntimeRecoveryState:
    forced_tool_retry: bool = False
    forced_grounding_retries: int = 0
    forced_output_retries: int = 0
    forced_script_retries: int = 0
    forced_completion_retries: int = 0
    next_forced_tools: list[str] | None = None


@dataclass
class RecoveryPlan:
    action: str
    finish_reason: str = ""
    error_message: str = ""
    system_message: str = ""
    recovery_kind: str = ""
    forced_tools: list[str] | None = None
    retry_count: int = 0
    max_retries: int = 0

    @property
    def should_retry(self) -> bool:
        return self.action == "retry"

    @property
    def should_fail(self) -> bool:
        return self.action == "fail"


class RuntimeRecoveryController:
    def __init__(self, *, max_retries: int = 2):
        self.state = RuntimeRecoveryState()
        self.max_retries = max_retries

    def plan(self, verification: VerificationResult) -> RecoveryPlan:
        if verification.ok:
            return RecoveryPlan(action="pass")

        kind = verification.recovery_kind
        if kind == "force_skill_script":
            self.state.forced_script_retries += 1
            if self.state.forced_script_retries > self.max_retries:
                return RecoveryPlan(
                    action="fail",
                    finish_reason=verification.finish_reason,
                    error_message=(
                        "Error: this turn requires a skill script, but `run_skill_script` did not successfully run. "
                        "Please retry the request; I must use the script tool before answering."
                    ),
                    recovery_kind=kind,
                    retry_count=self.state.forced_script_retries,
                    max_retries=self.max_retries,
                )
            return RecoveryPlan(
                action="retry",
                system_message=(
                    "This turn requires a real skill script execution before any final answer. "
                    "Do not answer from memory, assumptions, prior messages, or natural-language promises. "
                    "You must now call `run_skill_script` with the selected skill's script and valid arguments. "
                    "Only after `run_skill_script` returns `Return code: 0` may you provide the final answer."
                ),
                recovery_kind=kind,
                forced_tools=["run_skill_script"],
                retry_count=self.state.forced_script_retries,
                max_retries=self.max_retries,
            )

        if kind == "force_file_grounding":
            self.state.forced_grounding_retries += 1
            if self.state.forced_grounding_retries > self.max_retries:
                return RecoveryPlan(
                    action="fail",
                    finish_reason=verification.finish_reason,
                    error_message=(
                        "Error: this turn requires file evidence, but the assistant did not successfully read or extract "
                        "the uploaded file content. Please retry the request; if needed, ask me to first list and read "
                        "the uploaded file."
                    ),
                    recovery_kind=kind,
                    retry_count=self.state.forced_grounding_retries,
                    max_retries=self.max_retries,
                )
            return RecoveryPlan(
                action="retry",
                system_message=(
                    "This turn requires file-content evidence before any final answer. "
                    "You have not successfully read or extracted the uploaded file content in this turn. "
                    "Do not answer from the filename, session history, or prior assistant claims. "
                    "Call `list_uploaded_files` if needed, then call `read_uploaded_file` for the relevant file. "
                    "For precise PDF extraction, you may call `run_skill_script` with the PDF extraction script. "
                    "Only after a successful file-reading or extraction tool result may you summarize, deny, "
                    "classify, transform, or create output from the document."
                ),
                recovery_kind=kind,
                forced_tools=["read_uploaded_file", "list_uploaded_files", "run_skill_script"],
                retry_count=self.state.forced_grounding_retries,
                max_retries=self.max_retries,
            )

        if kind == "force_output_file":
            self.state.forced_output_retries += 1
            if self.state.forced_output_retries > self.max_retries:
                return RecoveryPlan(
                    action="fail",
                    finish_reason=verification.finish_reason,
                    error_message=(
                        "Error: this turn requires an output file, but the assistant did not successfully create or save one. "
                        "Please retry the request; I must call `save_outbox_file` or another real output tool before claiming a file path."
                    ),
                    recovery_kind=kind,
                    retry_count=self.state.forced_output_retries,
                    max_retries=self.max_retries,
                )
            return RecoveryPlan(
                action="retry",
                system_message=(
                    "The user requested a saved/exported output file in this turn, but no output artifact has been "
                    "created by tools yet. Do not claim a filename, path, or saved PDF/Word/Excel file unless a "
                    "tool successfully creates it. Use `save_outbox_file` now with the generated content/table. "
                    "For PDF output, set `filename` to a `.pdf` name and provide `title`, `content`, or `table_json`. "
                    "For Excel-to-PDF export, call `run_skill_script` with `skill_name=\"xlsx\"`, "
                    "`script_path=\"scripts/convert_to_pdf.py\"`, and arguments `[input_xlsx, output_pdf]`. "
                    "After the tool succeeds, reply with the actual path returned by the tool."
                ),
                recovery_kind=kind,
                forced_tools=["save_outbox_file", "run_skill_script", "write_file"],
                retry_count=self.state.forced_output_retries,
                max_retries=self.max_retries,
            )

        if kind == "correct_output_claim":
            self.state.forced_output_retries += 1
            if self.state.forced_output_retries > self.max_retries:
                return RecoveryPlan(
                    action="fail",
                    finish_reason=verification.finish_reason,
                    error_message=(
                        "Error: the assistant claimed an output file was saved, but no file-creation tool succeeded in this turn."
                    ),
                    recovery_kind=kind,
                    retry_count=self.state.forced_output_retries,
                    max_retries=self.max_retries,
                )
            return RecoveryPlan(
                action="retry",
                system_message=(
                    "The previous assistant draft claimed a file was saved or gave a path, but no output file "
                    "tool has succeeded in this turn. You must either call `save_outbox_file` now and then report "
                    "the returned path, or explicitly state that no file was created. Do not invent paths."
                ),
                recovery_kind=kind,
                forced_tools=[],
                retry_count=self.state.forced_output_retries,
                max_retries=self.max_retries,
            )

        if kind == "force_real_tool_or_disclaim":
            if self.state.forced_tool_retry:
                return RecoveryPlan(
                    action="fail",
                    finish_reason=verification.finish_reason,
                    error_message=(
                        "Error: the assistant did not produce a trusted tool-backed result for this action request. "
                        "Please start a new session with /new and try again."
                    ),
                    recovery_kind=kind,
                    retry_count=2,
                    max_retries=1,
                )
            self.state.forced_tool_retry = True
            return RecoveryPlan(
                action="retry",
                system_message=(
                    "The user asked for a real action in this turn, but you have not called any tools yet. "
                    "Do not claim that you executed actions, inspected windows, captured screenshots, or saved files. "
                    "Ignore prior assistant claims in session history unless they are revalidated by tool outputs in this turn. "
                    "You must either call the appropriate tool(s) now or explicitly say you did not execute anything."
                ),
                recovery_kind=kind,
                forced_tools=[],
                retry_count=1,
                max_retries=1,
            )

        if kind == "force_completion":
            self.state.forced_completion_retries += 1
            if self.state.forced_completion_retries > self.max_retries:
                return RecoveryPlan(
                    action="fail",
                    finish_reason=verification.finish_reason,
                    error_message=(
                        "Error: the assistant repeatedly stopped at progress-only updates instead of finishing the requested action. "
                        "The task was not completed in this turn. Please retry the request; if the session contains old failed attempts, use /new first."
                    ),
                    recovery_kind=kind,
                    retry_count=self.state.forced_completion_retries,
                    max_retries=self.max_retries,
                )
            return RecoveryPlan(
                action="retry",
                system_message=(
                    "The previous assistant draft is not acceptable as a final answer because it promises future work "
                    "or says it is still processing. Continue the task now in this same turn. "
                    "If more work is needed, call the appropriate tool(s). "
                    "For Excel row filtering, prefer run_skill_script with xlsx/scripts/filter_workbook.py when available. "
                    "Do not invent a skill script path that has not been confirmed to exist. "
                    "A valid final answer must either include the completed result/output path, or clearly state the "
                    "actual blocking failure with the tool evidence. Do not send another progress-only message."
                ),
                recovery_kind=kind,
                forced_tools=[],
                retry_count=self.state.forced_completion_retries,
                max_retries=self.max_retries,
            )

        return RecoveryPlan(
            action="fail",
            finish_reason=verification.finish_reason or "verification_failed",
            error_message=f"Error: runtime verification failed: {verification.violation_type}",
            recovery_kind=kind,
        )

    def apply_plan(self, plan: RecoveryPlan) -> None:
        if plan.should_retry:
            self.state.next_forced_tools = list(plan.forced_tools or [])

    def consume_forced_tools(self) -> list[str]:
        tools = list(self.state.next_forced_tools or [])
        self.state.next_forced_tools = None
        return tools
