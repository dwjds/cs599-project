from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from miniagent_core.app import (
    agent_loop,
    build_file_grounding_note,
)
from miniagent_core.attachments import Attachment, AttachmentStore
from miniagent_core.intent import infer_turn_intent

from .assembly import AgentAssembly, RuntimeComponents
from .config import HarnessConfig
from .context import RuntimeContext
from .trace import InstrumentedToolRegistry, ToolEvent


@dataclass
class AgentTurnResult:
    reply: str
    metrics: dict[str, Any]
    tool_events: list[ToolEvent]
    outbox_files: list[str]
    new_outbox_files: list[str]
    loop_error: str = ""


class AgentRuntimeSession:
    """Runs one controllable MiniAgent session through the shared runtime assembly."""

    def __init__(
        self,
        *,
        config: HarnessConfig,
        context: RuntimeContext,
        assembly: AgentAssembly,
        components: RuntimeComponents,
    ):
        self.config = config
        self.context = context
        self.assembly = assembly
        self.components = components

    async def run_turn(
        self,
        prompt: str,
        *,
        attachments: list[Attachment] | None = None,
        max_iterations: int = 10,
        extra_system_notes: list[str] | None = None,
        collect_tool_events: bool = True,
    ) -> AgentTurnResult:
        inbound_attachments = list(attachments or [])
        before_outbox = set(list_outbox_paths(self.components.attachment_store, self.context.session_key))
        script_skill_names = self.components.skill_loader.select_script_skill_names(
            prompt,
            attachments=inbound_attachments,
        )
        turn_intent = infer_turn_intent(
            prompt,
            attachments=inbound_attachments,
            has_visible_attachments=bool(inbound_attachments),
            script_skill_names=script_skill_names,
        )
        self.components.trace_sink.write("turn_intent", **turn_intent.to_trace_dict())
        requires_file_grounding = turn_intent.requires_file_grounding
        requires_output_file = turn_intent.requires_output_file
        file_grounding_note = ""
        initial_file_grounding_evidence = False
        if requires_file_grounding:
            file_grounding_note, initial_file_grounding_evidence = build_file_grounding_note(
                user_text=prompt,
                attachments=inbound_attachments,
                store=self.components.attachment_store,
                trace_sink=self.components.trace_sink,
            )

        runtime_skill_note = self.components.skill_loader.build_runtime_note(
            prompt,
            outbox_dir=self.components.attachment_store.outbox_session_dir(self.context.session_key),
            attachments=inbound_attachments,
        )
        notes = [
            note
            for note in [*(extra_system_notes or []), file_grounding_note, runtime_skill_note]
            if str(note or "").strip()
        ]
        messages = self.components.context_builder.build_messages(
            [],
            prompt,
            attachments=inbound_attachments,
            extra_system_notes=notes or None,
        )
        base_tools = self.assembly.build_turn_tools(
            context=self.context,
            components=self.components,
            inbound_attachments=inbound_attachments,
            session_attachments=[],
        )
        tools = InstrumentedToolRegistry(base_tools) if collect_tool_events else base_tools
        metrics: dict[str, Any] = {}
        loop_error = ""
        try:
            reply = await agent_loop(
                client=self.config.llm_client,
                model=self.config.model,
                messages=messages,
                tools=tools,  # type: ignore[arg-type]
                max_iterations=max_iterations,
                metrics=metrics,
                trace_sink=self.components.trace_sink,
                requires_file_grounding=requires_file_grounding,
                initial_file_grounding_evidence=initial_file_grounding_evidence,
                requires_output_file=requires_output_file,
                force_skill_script_tool=turn_intent.requires_script,
            )
        except Exception as exc:
            loop_error = f"{type(exc).__name__}: {exc}"
            reply = f"Error: {loop_error}"
            metrics["finish_reason"] = "agent_loop_error"

        after_outbox = list_outbox_paths(self.components.attachment_store, self.context.session_key)
        new_outbox = [path for path in after_outbox if path not in before_outbox]
        for path in new_outbox:
            self.components.trace_sink.write("file_created", path=path)
        tool_events = list(getattr(tools, "events", []))
        return AgentTurnResult(
            reply=reply,
            metrics=metrics,
            tool_events=tool_events,
            outbox_files=after_outbox,
            new_outbox_files=new_outbox,
            loop_error=loop_error,
        )


def list_outbox_paths(store: AttachmentStore, session_key: str) -> list[str]:
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
