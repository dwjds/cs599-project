from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from miniagent_core.app import MiniAgentApp
from miniagent_core.attachments import Attachment
from miniagent_core.config import WORKSPACE

from .assembly import AgentAssembly
from .config import HarnessConfig
from .context import RuntimeContext
from .runtime_session import AgentRuntimeSession, AgentTurnResult


DEFAULT_TASKS_FILE = WORKSPACE / "benchmarks" / "tasks.json"
DEFAULT_MEMORY_TASKS_FILE = WORKSPACE / "benchmarks" / "memory_retrieval_tasks.json"


class MiniAgentHarness:
    """Top-level runtime harness for assembling and controlling MiniAgent.

    The harness is intentionally broader than benchmark code. It is the
    engineering control layer that can run the live agent, run evaluation
    suites, and later host workflow planners, replay runners, and regression
    comparison tools.
    """

    def __init__(self, config: HarnessConfig | None = None):
        self.config = config or HarnessConfig()
        self.assembly = AgentAssembly(self.config)

    def build_app(self) -> MiniAgentApp:
        context = RuntimeContext.live(
            workspace=self.config.workspace,
            results_dir=self.config.results_dir,
            tmp_dir=self.config.tmp_dir,
        )
        components = self.assembly.build_components(context)
        return self.assembly.build_app(context=context, components=components)

    def build_runtime_session(self, context: RuntimeContext) -> AgentRuntimeSession:
        components = self.assembly.build_components(context)
        return AgentRuntimeSession(
            config=self.config,
            context=context,
            assembly=self.assembly,
            components=components,
        )

    def build_eval_session(
        self,
        *,
        run_id: str,
        task_id: str,
        isolated: bool | None = None,
    ) -> AgentRuntimeSession:
        context = RuntimeContext.eval_task(
            workspace=self.config.workspace,
            results_dir=self.config.results_dir,
            tmp_dir=self.config.tmp_dir,
            run_id=run_id,
            task_id=task_id,
            isolated=self.config.isolated if isolated is None else isolated,
        )
        return self.build_runtime_session(context)

    async def run_live(self, channel_names: list[str]) -> None:
        app = self.build_app()
        channels = self.assembly.build_channels(app, channel_names)
        print(f"Mini Agent (workspace: {self.config.workspace})")
        print(f"Channels: {', '.join(channel_names)}")
        if "cli" in [name.lower() for name in channel_names]:
            print("输入 exit 退出 | 输入 /new 清空会话\n")
        else:
            print("服务模式已启动，终端不会接收输入；按 Ctrl+C 退出。\n")
        await app.run(channels)

    async def run_channels(self, channel_names: list[str]) -> None:
        await self.run_live(channel_names)

    async def run_eval_turn(
        self,
        *,
        run_id: str,
        task_id: str,
        prompt: str,
        attachments: list[Attachment] | None = None,
        max_iterations: int = 10,
        isolated: bool | None = None,
    ) -> AgentTurnResult:
        session = self.build_eval_session(
            run_id=run_id,
            task_id=task_id,
            isolated=self.config.isolated if isolated is None else isolated,
        )
        return await session.run_turn(
            prompt,
            attachments=attachments or [],
            max_iterations=max_iterations,
        )

    async def run_agent_eval(
        self,
        *,
        tasks_file: Path = DEFAULT_TASKS_FILE,
        limit: int | None = None,
        delay_seconds: float = 3.0,
    ) -> dict[str, Any]:
        from miniagent_core.benchmark import run_benchmark

        return await run_benchmark(
            tasks_file=tasks_file,
            results_dir=self.config.results_dir,
            limit=limit,
            delay_seconds=delay_seconds,
            model=self.config.model,
            harness=self,
        )

    async def run_memory_eval(
        self,
        *,
        tasks_file: Path = DEFAULT_MEMORY_TASKS_FILE,
    ) -> dict[str, Any]:
        from miniagent_core.benchmark import run_memory_retrieval_benchmark

        return await run_memory_retrieval_benchmark(
            tasks_file=tasks_file,
            results_dir=self.config.results_dir,
        )

    async def run_replay(self, *, source: Path) -> dict[str, Any]:
        from .replay import run_replay_report

        return run_replay_report(source=source, results_dir=self.config.results_dir)

    async def compare_runs(self, *, base: Path, head: Path) -> dict[str, Any]:
        from .regression import compare_reports

        return compare_reports(base=base, head=head, results_dir=self.config.results_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MiniAgent runtime harness.")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run live MiniAgent channels.")
    run_parser.add_argument(
        "--channels",
        default="cli",
        help="Comma-separated channels to enable, e.g. cli, qq, or cli,qq.",
    )

    eval_parser = subparsers.add_parser("eval", help="Run agent task evaluation suite.")
    eval_parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS_FILE)
    eval_parser.add_argument("--limit", type=int, default=None)
    eval_parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds to wait between eval tasks. Use 0 to disable.",
    )
    eval_parser.add_argument("--json", action="store_true")
    eval_parser.add_argument(
        "--isolated",
        action="store_true",
        help="Run each eval task with inbox/outbox/session/memory state in benchmarks/tmp.",
    )

    memory_parser = subparsers.add_parser("memory", help="Run memory retrieval evaluation suite.")
    memory_parser.add_argument("--tasks", type=Path, default=DEFAULT_MEMORY_TASKS_FILE)
    memory_parser.add_argument("--json", action="store_true")

    replay_parser = subparsers.add_parser("replay", help="Summarize a session or trace JSONL file.")
    replay_parser.add_argument("--source", type=Path, required=True)
    replay_parser.add_argument("--json", action="store_true")

    compare_parser = subparsers.add_parser("compare", help="Compare two harness JSON reports.")
    compare_parser.add_argument("--base", type=Path, required=True)
    compare_parser.add_argument("--head", type=Path, required=True)
    compare_parser.add_argument("--json", action="store_true")

    return parser


async def async_main(argv: list[str] | None = None) -> int:
    raw_args = list(argv or [])
    # Backward compatibility:
    #   miniagent.py harness --limit 1
    #   miniagent.py benchmark --limit 1
    # should still mean "run agent eval".
    if not raw_args or (raw_args[0].startswith("-") and raw_args[0] not in {"-h", "--help"}):
        raw_args = ["eval", *raw_args]

    args = build_parser().parse_args(raw_args)
    config = HarnessConfig(isolated=bool(getattr(args, "isolated", False)))
    harness = MiniAgentHarness(config)

    if args.command == "run":
        channel_names = [item.strip() for item in args.channels.split(",") if item.strip()]
        await harness.run_live(channel_names)
        return 0

    if args.command == "memory":
        report = await harness.run_memory_eval(tasks_file=args.tasks)
        if args.json:
            import json

            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_memory_summary(report["summary"])
        return 1 if report["summary"]["misses"] else 0

    if args.command == "replay":
        report = await harness.run_replay(source=args.source)
        if args.json:
            import json

            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_replay_summary(report["summary"])
        return 0

    if args.command == "compare":
        report = await harness.compare_runs(base=args.base, head=args.head)
        if args.json:
            import json

            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_regression_summary(report["summary"], report["task_changes"])
        return 0

    report = await harness.run_agent_eval(
        tasks_file=args.tasks,
        limit=args.limit,
        delay_seconds=max(0.0, args.delay),
    )
    if args.json:
        import json

        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_agent_eval_summary(report["summary"])
    return 1 if report["summary"]["failed"] else 0


def print_agent_eval_summary(summary: dict[str, Any]) -> None:
    print("\nBenchmark Summary")
    print(f"Run ID: {summary['run_id']}")
    print(f"Tasks: {summary['total']}")
    print(f"Passed: {summary['passed']}")
    print(f"Failed: {summary['failed']}")
    print(f"Success Rate: {summary['success_rate']:.2%}")
    print(f"Total Tool Calls: {summary['total_tool_calls']}")
    print(f"Avg Tool Calls: {summary['avg_tool_calls']}")
    print(f"Avg Steps: {summary['avg_steps']}")
    print(f"Failure Types: {summary['failure_types'] or {}}")
    print(f"JSON Report: {summary['json_report']}")
    print(f"Markdown Report: {summary['markdown_report']}")


def print_memory_summary(summary: dict[str, Any]) -> None:
    print("\nMemory Retrieval Benchmark Summary")
    print(f"Run ID: {summary['run_id']}")
    print(f"Cases: {summary['total']}")
    print(f"Hits: {summary['hits']}")
    print(f"Misses: {summary['misses']}")
    print(f"Recall@task_k: {summary['recall_at_task_k']:.2%}")
    print(f"MRR: {summary['mrr']}")
    for key in sorted(k for k in summary if k.startswith("recall@")):
        print(f"{key}: {summary[key]:.2%}")
    print(f"JSON Report: {summary['json_report']}")
    print(f"Markdown Report: {summary['markdown_report']}")


def print_replay_summary(summary: dict[str, Any]) -> None:
    print("\nReplay Summary")
    print(f"Run ID: {summary['run_id']}")
    print(f"Source: {summary['source']}")
    print(f"Events: {summary['events']}")
    print(f"Roles: {summary['roles'] or {}}")
    print(f"Kinds: {summary['kinds'] or {}}")
    print(f"Replay Status: {summary['replay_status']}")
    print(f"Replayed Iterations: {summary['replayed_iterations']}")
    print(f"Replayed Tool Calls: {summary['replayed_tool_calls']}")
    if summary["replay_issues"]:
        print(f"Replay Issues: {summary['replay_issues']}")
    diagnostics = list(summary.get("diagnostics") or [])
    print(f"Diagnostics: {len(diagnostics)}")
    for item in diagnostics[:5]:
        print(
            "  - "
            f"{item.get('violation_type', 'diagnostic')} "
            f"at event {item.get('event_index')}: "
            f"{str(item.get('reply_preview') or '')[:120]}"
        )
    print(f"JSON Report: {summary['json_report']}")
    print(f"Markdown Report: {summary['markdown_report']}")


def print_regression_summary(summary: dict[str, Any], task_changes: list[dict[str, Any]]) -> None:
    print("\nRegression Summary")
    print(f"Run ID: {summary['run_id']}")
    print(f"Base: {summary['base_report']}")
    print(f"Head: {summary['head_report']}")
    print(f"Deltas: {summary['deltas'] or {}}")
    print(f"Failure Type Deltas: {summary['failure_type_deltas'] or {}}")
    print(f"Task Changes: {len(task_changes)}")
    print(f"JSON Report: {summary['json_report']}")
    print(f"Markdown Report: {summary['markdown_report']}")


def main(argv: list[str] | None = None) -> int:
    import asyncio

    return asyncio.run(async_main(argv))
