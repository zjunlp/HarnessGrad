"""OpenAI-compatible model calls for the MiniAgent runtime.

Connection and request options are supplied by run(llm_config=...).
side_model / side_base_url optionally override reader and side calls.
Model names do not imply provider-specific options.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_FATAL_HTTP_CODES = (400, 401, 402, 403, 404, 422)


def is_fatal_api_error(e: Exception) -> bool:
    """True for request, configuration, authentication, or permission errors."""
    status = getattr(e, "status_code", None)
    if status in _FATAL_HTTP_CODES:
        return True
    cur: Exception | None = e
    while cur is not None:
        resp = getattr(cur, "response", None)
        if resp is not None and hasattr(resp, "status_code"):
            if resp.status_code in _FATAL_HTTP_CODES:
                return True
            break
        cur = getattr(cur, "original_exception", None) or getattr(
            cur, "__cause__", None
        )
    return False


def response_error(response: Any, *, responses_api: bool = False) -> str | None:
    if responses_api:
        status = getattr(response, "status", None)
        if status not in {"incomplete", "failed", "cancelled"}:
            return None
        details = getattr(response, "incomplete_details", None)
        error = getattr(response, "error", None)
        reason = (
            getattr(details, "reason", None) or getattr(error, "code", None) or status
        )
        message = f"Responses generation did not complete: {reason}"
        token_field = "output_tokens"
    else:
        choices = getattr(response, "choices", None)
        if not choices:
            return "Chat Completions returned no choices"
        reason = getattr(choices[0], "finish_reason", None)
        if reason not in {"length", "content_filter"}:
            return None
        message = f"Chat Completions generation did not complete: {reason}"
        token_field = "completion_tokens"
    if reason in {"length", "max_output_tokens"}:
        usage = getattr(response, "usage", None)
        output_tokens = getattr(usage, token_field, None)
        token_details = getattr(usage, token_field + "_details", None)
        reasoning_tokens = getattr(token_details, "reasoning_tokens", None)
        usage_text = []
        if output_tokens is not None:
            usage_text.append(f"output_tokens={output_tokens}")
        if reasoning_tokens is not None:
            usage_text.append(f"reasoning_tokens={reasoning_tokens}")
        if usage_text:
            message += " (" + ", ".join(usage_text) + ")"
        message += (
            "; output budget exhausted. Increase llm.max_tokens "
            "without disabling reasoning; the configured budget is not increased automatically."
        )
    return message


def usage_dict(response: Any) -> dict:
    """Return the provider's complete usage object as JSON-safe data."""

    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        try:
            value = usage.model_dump(by_alias=True, exclude_none=True, mode="json")
        except TypeError:
            value = usage.model_dump()
    elif hasattr(usage, "dict"):
        value = usage.dict(exclude_none=True)
    elif isinstance(usage, dict):
        value = dict(usage)
    else:
        return {"value": repr(usage)}
    return value if isinstance(value, dict) else {"value": value}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def configure(config: dict) -> None:
    """Initialize the native runtime in its independent process."""
    global API_KEY, DEFAULT_MODEL, SIDE_MODEL, BASE_URL, SIDE_BASE_URL
    global MAX_TOKENS, LLM_MAX_RETRIES, REASONING_EFFORT, EXTRA_BODY
    API_KEY = config.get("api_key") or ""
    DEFAULT_MODEL = config.get("model_name") or ""
    SIDE_MODEL = config.get("side_model") or DEFAULT_MODEL
    BASE_URL = config.get("base_url") or "https://api.openai.com/v1"
    SIDE_BASE_URL = config.get("side_base_url") or BASE_URL
    MAX_TOKENS = config.get("max_tokens", 4096)
    LLM_MAX_RETRIES = max(1, config.get("llm_max_retries", 1))
    REASONING_EFFORT = config.get("reasoning_effort") or ""
    EXTRA_BODY = config.get("extra_body") or {}


configure({})

_clients: dict[tuple[str, str], AsyncOpenAI] = {}


def _get_client(base_url: str) -> AsyncOpenAI:
    if not API_KEY:
        raise RuntimeError("MiniAgent requires llm.api_key in config")
    key = (base_url, API_KEY)
    if key not in _clients:
        _clients[key] = AsyncOpenAI(api_key=API_KEY, base_url=base_url, max_retries=0)
    return _clients[key]


def is_openai_model(model: str) -> bool:
    m = model.removeprefix("openai/")
    return m.startswith(("gpt-", "o1", "o3", "o4"))


# ---------------------------------------------------------------------------
# Responses API helpers (OpenAI models)
# ---------------------------------------------------------------------------


def chat_tools_to_responses(tools: list[dict] | None) -> list[dict] | None:
    """Convert Chat Completions tool schemas to Responses API format."""
    if not tools:
        return None
    out: list[dict] = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            fn = t["function"]
            out.append(
                {
                    "type": "function",
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                }
            )
        else:
            out.append(t)
    return out


def extract_responses_output(resp) -> tuple[str, str, list]:
    """Extract (content, reasoning, function_calls) from a Responses API response.

    Returns:
        content: concatenated text from message output items
        reasoning: concatenated reasoning summary text
        function_calls: list of output items with type "function_call"
    """
    content = ""
    reasoning = ""
    function_calls: list = []

    for item in resp.output or []:
        if isinstance(item, dict):
            item_type = item.get("type")
        else:
            item_type = getattr(item, "type", None)

        if item_type == "message":
            for c in (
                getattr(item, "content", None)
                or (item.get("content") if isinstance(item, dict) else [])
                or []
            ):
                t = getattr(c, "text", None) or (
                    c.get("text") if isinstance(c, dict) else None
                )
                if t:
                    content += t

        elif item_type == "reasoning":
            for c in (
                getattr(item, "summary", None)
                or (item.get("summary") if isinstance(item, dict) else [])
                or []
            ):
                t = getattr(c, "text", None) or (
                    c.get("text") if isinstance(c, dict) else None
                )
                if t:
                    reasoning += t

        elif item_type == "function_call":
            function_calls.append(item)

    return content, reasoning, function_calls


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict = "auto",
    max_tokens: int | None = None,
) -> Any:
    """One-shot chat completion. Returns an OpenAI ChatCompletion response."""
    target = model or DEFAULT_MODEL
    token_field = "max_completion_tokens" if is_openai_model(target) else "max_tokens"
    kwargs: dict = {
        "model": target.removeprefix("openai/"),
        "messages": messages,
        token_field: max_tokens or MAX_TOKENS,
    }
    if tools:
        kwargs.update(tools=tools, tool_choice=tool_choice)
    elif tool_choice == "none":
        kwargs["tool_choice"] = "none"
    if REASONING_EFFORT:
        kwargs["reasoning_effort"] = REASONING_EFFORT
    if EXTRA_BODY:
        kwargs["extra_body"] = EXTRA_BODY
    base_url = SIDE_BASE_URL if target == SIDE_MODEL else BASE_URL
    return await _get_client(base_url).chat.completions.create(**kwargs)


async def responses(
    input: list | str,
    *,
    model: str,
    instructions: str | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | None = None,
    reasoning: dict | None = None,
    session_id: str | None = None,
    previous_response_id: str | None = None,
    max_output_tokens: int | None = None,
) -> Any:
    """Use the official OpenAI client without mutating process-global routing."""
    kwargs: dict = {
        "model": model.removeprefix("openai/"),
        "input": input,
        "max_output_tokens": max_output_tokens or MAX_TOKENS,
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }
    if instructions is not None:
        kwargs["instructions"] = instructions
    if tools:
        kwargs["tools"] = tools
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    if reasoning:
        kwargs["reasoning"] = reasoning
    if session_id:
        kwargs["prompt_cache_key"] = session_id
        kwargs["extra_headers"] = {"session_id": session_id}
    if previous_response_id:
        kwargs["previous_response_id"] = previous_response_id
    if EXTRA_BODY:
        kwargs["extra_body"] = EXTRA_BODY
    return await _get_client(BASE_URL).responses.create(**kwargs)


async def text(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 2048,
    _max_retries: int | None = None,
    _return_response: bool = False,
) -> Any:
    """Convenience: single-prompt completion -> text, with retry."""
    max_retries = LLM_MAX_RETRIES if _max_retries is None else max(1, _max_retries)
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = await chat(
                [{"role": "user", "content": prompt}],
                model=model,
                max_tokens=max_tokens,
            )
            failure = response_error(resp)
            if failure:
                raise RuntimeError(failure)
            content = (resp.choices[0].message.content or "").strip()
            if not content:
                raise RuntimeError("model returned no final answer text")
            return (content, resp) if _return_response else content
        except Exception as e:
            last_err = e
            if is_fatal_api_error(e):
                logger.error("llm.text() fatal API error (no retry): %s", e)
                raise
            logger.warning(
                "llm.text() failed (attempt %d/%d): %s", attempt, max_retries, e
            )
            if attempt < max_retries:
                await asyncio.sleep(2**attempt)
    raise last_err  # type: ignore[misc]
