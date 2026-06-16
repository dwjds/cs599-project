from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen
import re

from ..async_compat import run_blocking
from .base import Tool


@dataclass
class _HttpResponse:
    url: str
    content_type: str
    text: str


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs):
        tag_name = (tag or "").lower()
        if tag_name in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag_name == "title":
            self._in_title = True

    def handle_endtag(self, tag: str):
        tag_name = (tag or "").lower()
        if tag_name in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag_name == "title":
            self._in_title = False

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        text = " ".join((data or "").split())
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
        self._parts.append(text)

    def get_text(self) -> str:
        return " ".join(self._parts)


class WebSearchTool(Tool):
    """联网搜索网页结果。"""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the public web and return a short ranked result list."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Maximum number of results to return"},
            },
            "required": ["query"],
        }

    async def execute(self, query: str, max_results: int = 5, **kwargs) -> str:
        search_query = (query or "").strip()
        if not search_query:
            return "Error: query is required."

        limit = max(1, min(int(max_results or 5), 10))
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(search_query)}"

        try:
            response = await run_blocking(lambda: _http_get(url))
        except Exception as exc:
            return f"Error: web search failed: {exc}"

        results = _parse_duckduckgo_results(response.text, limit)
        if not results:
            return f"No search results found for query: {search_query}"

        lines = [f"Search results for: {search_query}"]
        for index, item in enumerate(results, start=1):
            lines.append(f"{index}. {item['title']}")
            lines.append(f"   URL: {item['url']}")
            if item["snippet"]:
                lines.append(f"   Snippet: {item['snippet']}")
        return "\n".join(lines)


class WebFetchTool(Tool):
    """抓取网页并抽取文本。"""

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "Fetch a known web URL and return the page text or raw HTML."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "HTTP or HTTPS URL to fetch"},
                "max_chars": {"type": "integer", "description": "Maximum number of characters to return"},
                "raw_html": {"type": "boolean", "description": "Whether to return raw HTML instead of extracted text"},
            },
            "required": ["url"],
        }

    async def execute(
        self,
        url: str,
        max_chars: int = 12000,
        raw_html: bool = False,
        **kwargs,
    ) -> str:
        target = _validate_web_url(url)
        limit = max(200, min(int(max_chars or 12000), 50000))

        try:
            response = await run_blocking(lambda: _http_get(target))
        except Exception as exc:
            return f"Error: web fetch failed: {exc}"

        body = response.text
        title = ""
        if not raw_html and "html" in response.content_type.lower():
            parser = _VisibleTextParser()
            parser.feed(body)
            parser.close()
            body = _normalize_text(parser.get_text())
            title = parser.title

        body = (body or "").strip()
        if not body:
            return f"Fetched URL: {response.url}\nContent: (empty)"

        lines = [f"Fetched URL: {response.url}"]
        if title:
            lines.append(f"Title: {title}")
        lines.append("Content:")
        lines.append(body[:limit])
        return "\n".join(lines)


def _http_get(url: str, timeout: int = 15) -> _HttpResponse:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        text = raw.decode(charset, errors="replace")
        return _HttpResponse(
            url=response.geturl(),
            content_type=response.headers.get("Content-Type", ""),
            text=text,
        )


def _validate_web_url(url: str) -> str:
    target = (url or "").strip()
    if not target:
        raise ValueError("url is required.")
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https URLs are allowed.")
    return target


def _strip_tags(text: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(text or "")
    parser.close()
    return _normalize_text(parser.get_text())


def _normalize_text(text: str) -> str:
    compact = " ".join((text or "").split())
    return re.sub(r"\s+([,.;:!?])", r"\1", compact)


def _decode_duckduckgo_href(href: str) -> str:
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com"):
        uddg = parse_qs(parsed.query).get("uddg", [])
        if uddg:
            return unquote(uddg[0])
    return href


def _parse_duckduckgo_results(html: str, limit: int) -> list[dict[str, str]]:
    anchor_pattern = (
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    )
    snippet_pattern = r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>'

    anchors = re.findall(anchor_pattern, html or "", flags=re.IGNORECASE | re.DOTALL)
    snippets = re.findall(snippet_pattern, html or "", flags=re.IGNORECASE | re.DOTALL)

    results: list[dict[str, str]] = []
    for index, (href, title_html) in enumerate(anchors):
        title = _strip_tags(unescape(title_html))
        url = _decode_duckduckgo_href(unescape(href))
        snippet_html = ""
        if index < len(snippets):
            snippet_html = snippets[index][0] or snippets[index][1]
        snippet = _strip_tags(unescape(snippet_html))
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= limit:
            break
    return results
