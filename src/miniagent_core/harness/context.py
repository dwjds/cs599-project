from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


@dataclass
class RuntimeContext:
    run_id: str
    mode: str
    workspace: Path
    project_workspace: Path
    state_workspace: Path
    session_key: str
    results_dir: Path
    tmp_dir: Path
    isolated: bool = False

    @classmethod
    def live(
        cls,
        *,
        workspace: Path,
        results_dir: Path,
        tmp_dir: Path,
        run_id: str | None = None,
    ) -> "RuntimeContext":
        current_run_id = run_id or new_run_id()
        return cls(
            run_id=current_run_id,
            mode="live",
            workspace=workspace,
            project_workspace=workspace,
            state_workspace=workspace,
            session_key=f"live:{current_run_id}",
            results_dir=results_dir,
            tmp_dir=tmp_dir,
            isolated=False,
        )

    @classmethod
    def eval_task(
        cls,
        *,
        workspace: Path,
        results_dir: Path,
        tmp_dir: Path,
        run_id: str,
        task_id: str,
        isolated: bool = False,
    ) -> "RuntimeContext":
        project_workspace = workspace
        state_workspace = workspace
        if isolated:
            state_workspace = tmp_dir / run_id / task_id
        return cls(
            run_id=run_id,
            mode="agent_eval",
            workspace=state_workspace,
            project_workspace=project_workspace,
            state_workspace=state_workspace,
            session_key=f"benchmark:{run_id}:{task_id}",
            results_dir=results_dir,
            tmp_dir=tmp_dir,
            isolated=isolated,
        )

    def ensure_dirs(self) -> None:
        self.state_workspace.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
