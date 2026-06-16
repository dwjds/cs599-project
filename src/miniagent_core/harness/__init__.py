"""Runtime harness facade for MiniAgent."""

from miniagent_core.harness.config import HarnessConfig
from miniagent_core.harness.runtime import (
    MiniAgentHarness,
    async_main,
    main,
)
from miniagent_core.harness.runtime_session import AgentRuntimeSession, AgentTurnResult

__all__ = [
    "HarnessConfig",
    "AgentRuntimeSession",
    "AgentTurnResult",
    "MiniAgentHarness",
    "async_main",
    "main",
    "compare_reports",
    "run_replay_report",
    "run_benchmark",
    "run_memory_retrieval_benchmark",
]


def __getattr__(name: str):
    if name == "run_benchmark":
        from miniagent_core.benchmark import run_benchmark

        return run_benchmark
    if name == "run_memory_retrieval_benchmark":
        from miniagent_core.benchmark import run_memory_retrieval_benchmark

        return run_memory_retrieval_benchmark
    if name == "run_replay_report":
        from miniagent_core.harness.replay import run_replay_report

        return run_replay_report
    if name == "compare_reports":
        from miniagent_core.harness.regression import compare_reports

        return compare_reports
    raise AttributeError(name)
