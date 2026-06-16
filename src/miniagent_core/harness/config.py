from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from miniagent_core.config import MODEL, WORKSPACE, client


@dataclass
class HarnessConfig:
    workspace: Path = WORKSPACE
    model: str = MODEL
    llm_client: Any = client
    results_dir: Path = WORKSPACE / "benchmarks" / "results"
    tmp_dir: Path = WORKSPACE / "benchmarks" / "tmp"
    isolated: bool = False
