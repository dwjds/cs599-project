from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import WORKSPACE
from .base import Tool

try:
    from playwright.async_api import async_playwright
except Exception as exc:  # pragma: no cover
    async_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_IMPORT_ERROR = exc
else:  # pragma: no cover
    PLAYWRIGHT_IMPORT_ERROR = None


class BrowserAutomationTool(Tool):
    """浏览器自动化工具。"""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def name(self) -> str:
        return "browser_automation"

    @property
    def description(self) -> str:
        return (
            "Control a Playwright browser session. Allowed actions: launch, goto, click, fill, "
            "press, wait_for, text_content, page_text, title, current_url, screenshot, close."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Browser action name"},
                "url": {"type": "string", "description": "Target URL for goto"},
                "selector": {"type": "string", "description": "CSS/text selector"},
                "text": {"type": "string", "description": "Text payload for fill"},
                "key": {"type": "string", "description": "Key name for press"},
                "path": {"type": "string", "description": "Optional output path for screenshots"},
                "browser": {"type": "string", "description": "Browser type: chromium, firefox, webkit"},
                "headless": {"type": "boolean", "description": "Whether to launch headless"},
                "timeout_ms": {"type": "integer", "description": "Optional timeout in milliseconds"},
                "full_page": {"type": "boolean", "description": "Whether screenshot should capture full page"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        url: str = "",
        selector: str = "",
        text: str = "",
        key: str = "",
        path: str = "",
        browser: str = "chromium",
        headless: bool = True,
        timeout_ms: int = 5000,
        full_page: bool = True,
        **kwargs,
    ) -> str:
        if async_playwright is None:
            detail = f" ({PLAYWRIGHT_IMPORT_ERROR})" if PLAYWRIGHT_IMPORT_ERROR else ""
            return "Error: playwright is unavailable. Install it with: pip install playwright" + detail

        action_name = (action or "").strip().lower()

        if action_name == "launch":
            await self._launch_browser(browser=(browser or "chromium").strip().lower(), headless=bool(headless))
            return f"Launched {browser} browser (headless={bool(headless)})"

        if action_name == "close":
            await self._close_browser()
            return "Closed browser session"

        page = await self._require_page()

        if action_name == "goto":
            safe_url = self._validate_url(url)
            await page.goto(safe_url, wait_until="domcontentloaded", timeout=max(1000, int(timeout_ms)))
            return f"Navigated to {page.url}"

        if action_name == "click":
            if not selector.strip():
                return "Error: selector is required for click."
            await page.click(selector, timeout=max(1000, int(timeout_ms)))
            return f"Clicked selector: {selector}"

        if action_name == "fill":
            if not selector.strip():
                return "Error: selector is required for fill."
            if len(text or "") > 2000:
                return "Error: fill text is limited to 2000 characters."
            await page.fill(selector, text or "", timeout=max(1000, int(timeout_ms)))
            return f"Filled selector: {selector}"

        if action_name == "press":
            if not selector.strip():
                return "Error: selector is required for press."
            if not key.strip():
                return "Error: key is required for press."
            await page.press(selector, key.strip(), timeout=max(1000, int(timeout_ms)))
            return f"Pressed {key.strip()} on selector: {selector}"

        if action_name == "wait_for":
            if not selector.strip():
                return "Error: selector is required for wait_for."
            await page.wait_for_selector(selector, timeout=max(1000, int(timeout_ms)))
            return f"Selector became available: {selector}"

        if action_name == "text_content":
            if not selector.strip():
                return "Error: selector is required for text_content."
            content = await page.text_content(selector, timeout=max(1000, int(timeout_ms)))
            return (content or "").strip() or "(empty)"

        if action_name == "page_text":
            body = await page.locator("body").inner_text(timeout=max(1000, int(timeout_ms)))
            return (body or "").strip()[:12000] or "(empty)"

        if action_name == "title":
            return await page.title()

        if action_name == "current_url":
            return page.url or "(no url)"

        if action_name == "screenshot":
            target = self._resolve_output_path(path, "browser-screenshot.png")
            await page.screenshot(path=str(target), full_page=bool(full_page))
            return f"Saved browser screenshot to {target}"

        return (
            "Error: unsupported browser automation action. "
            "Use one of: launch, goto, click, fill, press, wait_for, text_content, "
            "page_text, title, current_url, screenshot, close."
        )

    async def _launch_browser(self, browser: str, headless: bool):
        if self._browser is not None and self._page is not None:
            return
        self._playwright = await async_playwright().start()
        launcher = getattr(self._playwright, browser, None)
        if launcher is None:
            await self._close_browser()
            raise ValueError(f"Unsupported browser type: {browser}")
        self._browser = await launcher.launch(headless=headless)
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()

    async def _require_page(self):
        if self._page is None:
            await self._launch_browser(browser="chromium", headless=True)
        return self._page

    async def _close_browser(self):
        if self._page is not None:
            await self._page.close()
            self._page = None
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    def _validate_url(self, url: str) -> str:
        target = (url or "").strip()
        if not target:
            raise ValueError("url is required for goto.")
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https", "file"}:
            raise ValueError("Only http, https, and file URLs are allowed.")
        return target

    def _resolve_output_path(self, path: str, default_name: str) -> Path:
        target = Path(path).expanduser().resolve() if path else (WORKSPACE / default_name).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
