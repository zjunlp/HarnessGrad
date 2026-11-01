"""
Base web tools — explicitly configured HTTPS search/browse services.

Minimal: one query / one URL in, raw results out. No retries, no text
cleanup, no truncation, no multi-input. Any of those belong in the
evolvable harness layer (searcher / reader), not in the base tool.

Endpoints:
  POST /search_tool/search   { query, max_num_results, provider }
  POST /search_tool/browse   { url, max_tokens }
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import requests

from .web_config import WebToolError, endpoint, headers

logger = logging.getLogger(__name__)

_MAX_RESULTS_PER_QUERY = 10
_BROWSE_MAX_TOKENS = 100000


# ---------------------------------------------------------------------------
# Sync impls
# ---------------------------------------------------------------------------


def _execute_search(query: str) -> tuple[str, list[dict]]:
    """One query → (text_result, structured_items)."""
    payload = {
        "query": query,
        "max_num_results": _MAX_RESULTS_PER_QUERY,
        "provider": "google",
    }
    url = endpoint("web_search")
    try:
        resp = requests.post(
            url, json=payload, headers=headers(), timeout=(5, 30), allow_redirects=False
        )
        resp.raise_for_status()
        if resp.is_redirect:
            raise WebToolError("search endpoint redirects are not allowed")
        data = resp.json()
    except Exception as exc:
        raise WebToolError("search service failed (%s)" % type(exc).__name__) from exc

    if not isinstance(data, dict) or not data.get("overall_success"):
        raise WebToolError("search service did not return overall_success=true")

    items = data.get("items") or []
    if not items:
        return f"[search] no results for: {query}", []

    structured: list[dict] = []
    lines = [f"Search results for: {query}", ""]
    for page in items[:_MAX_RESULTS_PER_QUERY]:
        url = str(page.get("url", "")).strip()
        if not url:
            continue
        domain = urlparse(url).netloc.lower()
        if not domain:
            continue
        title = str(page.get("title", "") or "").strip() or "No Title"
        snippet = str(page.get("snippets", "") or "").strip()
        structured.append(
            {
                "rank": len(structured) + 1,
                "url": url,
                "title": title,
                "domain": domain,
                "snippet": snippet,
            }
        )
        lines.append(f"- {title}")
        lines.append(f"  URL: {url}")
        if snippet:
            lines.append(f"  Snippet: {snippet}")
        lines.append("")
    return "\n".join(lines).rstrip(), structured


def _execute_visit(url: str) -> str:
    """One URL → raw page content as returned by the browse API."""
    payload = {"url": url, "max_tokens": _BROWSE_MAX_TOKENS}
    service_url = endpoint("read_url")
    try:
        resp = requests.post(
            service_url,
            json=payload,
            headers=headers(),
            timeout=(10, 60),
            allow_redirects=False,
        )
        resp.raise_for_status()
        if resp.is_redirect:
            raise WebToolError("browse endpoint redirects are not allowed")
        data = resp.json()
    except Exception as exc:
        raise WebToolError("browse service failed (%s)" % type(exc).__name__) from exc

    if not isinstance(data, dict) or not data.get("overall_success"):
        raise WebToolError("browse service did not return overall_success=true")

    return str(data.get("semanticDocument", "") or "")


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def web_search(query: str) -> str:
    """Run a web search; return up to 10 results (title/URL/snippet)."""
    text, _ = await asyncio.to_thread(_execute_search, query)
    return text


async def web_search_structured(query: str) -> tuple[str, list[dict]]:
    """Run a web search; return (text_result, structured_items)."""
    return await asyncio.to_thread(_execute_search, query)


async def web_fetch(url: str) -> str:
    """Fetch a URL; return its raw page content."""
    return await asyncio.to_thread(_execute_visit, url)


__all__ = ["web_search", "web_search_structured", "web_fetch"]
