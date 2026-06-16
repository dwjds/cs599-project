from __future__ import annotations

from dataclasses import dataclass

from miniagent_core.app import MiniAgentApp, build_channels, build_default_tools
from miniagent_core.attachments import Attachment, AttachmentStore
from miniagent_core.channels import BaseChannel
from miniagent_core.memory import ContextBuilder, MemoryStore, SessionManager
from miniagent_core.message import MessageBus
from miniagent_core.skills import SkillLoader
from miniagent_core.tools import ToolRegistry

from .config import HarnessConfig
from .context import RuntimeContext
from .trace import TraceSink


@dataclass
class RuntimeComponents:
    bus: MessageBus
    attachment_store: AttachmentStore
    skill_loader: SkillLoader
    context_builder: ContextBuilder
    memory: MemoryStore
    sessions: SessionManager
    trace_sink: TraceSink


class AgentAssembly:
    """Centralizes construction of MiniAgent runtime dependencies."""

    def __init__(self, config: HarnessConfig):
        self.config = config

    def build_app(
        self,
        *,
        context: RuntimeContext | None = None,
        components: RuntimeComponents | None = None,
    ) -> MiniAgentApp:
        runtime_context = context or RuntimeContext.live(
            workspace=self.config.workspace,
            results_dir=self.config.results_dir,
            tmp_dir=self.config.tmp_dir,
        )
        runtime_components = components or self.build_components(runtime_context)
        return MiniAgentApp(
            workspace=runtime_context.state_workspace,
            model=self.config.model,
            llm_client=self.config.llm_client,
            bus=runtime_components.bus,
            skill_loader=runtime_components.skill_loader,
            context_builder=runtime_components.context_builder,
            attachment_store=runtime_components.attachment_store,
            memory=runtime_components.memory,
            sessions=runtime_components.sessions,
            trace_sink=runtime_components.trace_sink,
        )

    def build_channels(self, app: MiniAgentApp, channel_names: list[str]) -> list[BaseChannel]:
        return build_channels(app, channel_names)

    def build_components(self, context: RuntimeContext) -> RuntimeComponents:
        context.ensure_dirs()
        trace_sink = TraceSink(
            workspace=context.state_workspace,
            run_id=context.run_id,
            session_key=context.session_key,
            mode=context.mode,
        )
        skill_loader = SkillLoader(
            context.project_workspace,
            runtime_workspace=context.state_workspace,
            trace_sink=trace_sink,
        )
        return RuntimeComponents(
            bus=MessageBus(),
            attachment_store=AttachmentStore(context.state_workspace),
            skill_loader=skill_loader,
            context_builder=ContextBuilder(
                context.state_workspace,
                skill_loader=skill_loader,
                bootstrap_workspace=context.project_workspace,
            ),
            memory=MemoryStore(context.state_workspace),
            sessions=SessionManager(context.state_workspace),
            trace_sink=trace_sink,
        )

    def build_turn_tools(
        self,
        *,
        context: RuntimeContext,
        components: RuntimeComponents,
        inbound_attachments: list[Attachment] | None = None,
        session_attachments: list[Attachment] | None = None,
    ) -> ToolRegistry:
        return build_default_tools(
            llm_client=self.config.llm_client,
            model=self.config.model,
            attachment_store=components.attachment_store,
            session_key=context.session_key,
            inbound_attachments=inbound_attachments or [],
            session_attachments=session_attachments or [],
            skill_loader=components.skill_loader,
            workspace=context.state_workspace,
        )
