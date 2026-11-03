"""
Reader: wraps web_fetch with LLM extraction.

The extract prompt is supplied by the caller (loaded from the active
workspace), not imported — keeps reader.py independent of which prompt set
is in play this iteration.
"""

from __future__ import annotations

from . import llm
from .tools import web_fetch


async def read_url_with_raw(
    url: str, focus: str, extract_prompt: str
) -> dict[str, str]:
    """
    Fetch the page and produce a focused, bulleted extraction tied to `focus`.
    `extract_prompt` is a format string with {focus} and {chunk} placeholders;
    {chunk} receives the full fetched page text.
    Returns both extracted notes and raw page text for File-as-Bus tracing.
    """
    raw = await web_fetch(url)
    if not raw or raw.startswith("[fetch]"):
        return {
            "extracted": f"[reader] fetch failed for {url}: {raw}",
            "raw": raw or "",
        }

    page_text = raw
    prompt = extract_prompt.replace("{focus}", focus).replace("{chunk}", page_text)
    try:
        out = await llm.text(prompt, model=llm.SIDE_MODEL, max_tokens=12800)
    except Exception as e:
        return {"extracted": f"[reader] extract error: {e}", "raw": raw}

    out = out.strip()
    if not out or out.upper() == "NONE":
        return {
            "extracted": f"[reader] no relevant content found at {url} for focus={focus!r}",
            "raw": raw,
        }

    header = f"[reader] {url}  (focus: {focus})"
    return {"extracted": header + "\n" + out, "raw": raw}
