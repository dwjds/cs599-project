from __future__ import annotations

import asyncio
from typing import Any, Callable


async def run_blocking(func: Callable[..., Any], *args, **kwargs) -> Any:
    """在 Python 3.11 运行时中把阻塞调用放到线程池执行。"""
    return await asyncio.to_thread(func, *args, **kwargs)
