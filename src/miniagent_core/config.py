from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]

API_KEY = os.environ.get("DASHSCOPE_API_KEY")
BASE_URL = os.environ.get(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
MODEL = os.environ.get("DASHSCOPE_MODEL", "qwen-plus-2025-09-11")
EMBEDDING_MODEL = os.environ.get("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = PROJECT_ROOT
WORKSPACE = PROJECT_ROOT / "workspace"
PREFERRED_PYTHON = "3.11.15"
REQUIRED_PYTHON_SERIES = (3, 11)
PREFERRED_PYTHON_EXECUTABLE = Path(r"D:\conda\envs\assistant\python.exe")
MAX_HISTORY_MESSAGES = 15
MEMORY_CONSOLIDATE_TRIGGER = 30
MEMORY_KEEP_RECENT = 15
MEMORY_RETRIEVAL_TOP_K = 4
MEMORY_RETRIEVAL_CANDIDATES = 8
SKILL_ROUTE_MODE = "hybrid"  # 可选: "hybrid" / "rule" / "llm"

CHANNELS: dict[str, dict[str, Any]] = {
    "qq": {
        "enabled": os.environ.get("QQ_BOT_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        "appId": os.environ.get("QQ_BOT_APP_ID", ""),
        "secret": os.environ.get("QQ_BOT_SECRET", ""),
        "allowFrom": [],
        "msgFormat": "plain",
        "ackMessage": os.environ.get("QQ_BOT_ACK_MESSAGE", "⏳ Processing..."),
    }
}
QQ_CHANNEL = CHANNELS["qq"]


def ensure_supported_python() -> None:
    current_series = sys.version_info[:2]
    if current_series != REQUIRED_PYTHON_SERIES:
        raise RuntimeError(
            "MiniAgent requires Python "
            f"{REQUIRED_PYTHON_SERIES[0]}.{REQUIRED_PYTHON_SERIES[1]} "
            f"(preferred environment: {PREFERRED_PYTHON} at {PREFERRED_PYTHON_EXECUTABLE}). "
            f"Current interpreter: {sys.executable} ({sys.version.split()[0]})."
        )


client: Any = OpenAI(api_key=API_KEY, base_url=BASE_URL) if OpenAI else None
