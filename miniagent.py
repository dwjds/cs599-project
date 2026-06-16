from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""
兼容导出入口。

原来所有实现都堆在这个文件里；现在已按职责拆到 `src/miniagent_core/`:
- tools
- memory
- message
- channels
- skills

这里保留统一导出，避免现有导入路径全部失效。
"""

from miniagent_core import (
    API_KEY,
    BASE_DIR,
    BASE_URL,
    BrowserAutomationTool,
    CHANNELS,
    MAX_HISTORY_MESSAGES,
    MEMORY_CONSOLIDATE_TRIGGER,
    MEMORY_KEEP_RECENT,
    MODEL,
    QQ_CHANNEL,
    WORKSPACE,
    BaseChannel,
    CLIChannel,
    ContextBuilder,
    ExecTool,
    FindFilesTool,
    InboundMessage,
    MemoryStore,
    MessageBus,
    MiniAgentApp,
    OutboundMessage,
    QQChannel,
    ReadFileTool,
    SearchCodeTool,
    Session,
    SessionManager,
    SkillLoader,
    Tool,
    ToolRegistry,
    WebFetchTool,
    WebSearchTool,
    WriteFileTool,
    agent_loop,
    build_channels,
    build_default_tools,
    client,
    consolidate_memory,
    ensure_supported_python,
    parse_channel_args,
    run_selected_channels,
    run_terminal_app,
)

__all__ = [
    "API_KEY",
    "BASE_DIR",
    "BASE_URL",
    "BrowserAutomationTool",
    "CHANNELS",
    "MAX_HISTORY_MESSAGES",
    "MEMORY_CONSOLIDATE_TRIGGER",
    "MEMORY_KEEP_RECENT",
    "MODEL",
    "QQ_CHANNEL",
    "WORKSPACE",
    "BaseChannel",
    "CLIChannel",
    "ContextBuilder",
    "ExecTool",
    "FindFilesTool",
    "InboundMessage",
    "MemoryStore",
    "MessageBus",
    "MiniAgentApp",
    "OutboundMessage",
    "QQChannel",
    "ReadFileTool",
    "SearchCodeTool",
    "Session",
    "SessionManager",
    "SkillLoader",
    "Tool",
    "ToolRegistry",
    "WebFetchTool",
    "WebSearchTool",
    "WriteFileTool",
    "agent_loop",
    "build_channels",
    "build_default_tools",
    "client",
    "consolidate_memory",
    "ensure_supported_python",
    "parse_channel_args",
    "run_selected_channels",
    "run_terminal_app",
]


async def main(argv: list[str] | None = None):
    ensure_supported_python()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args[:2] == ["skills", "doctor"]:
        from miniagent_core.skills.doctor import main as doctor_main

        raise SystemExit(doctor_main(raw_args[2:]))
    if raw_args[:1] in (["benchmark"], ["harness"]):
        from miniagent_core.harness import async_main as benchmark_main

        raise SystemExit(await benchmark_main(raw_args[1:]))
    args = parse_channel_args(argv)
    channel_names = [item.strip() for item in args.channels.split(",") if item.strip()]
    from miniagent_core.harness import MiniAgentHarness

    await MiniAgentHarness().run_live(channel_names)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nMini Agent stopped.")
