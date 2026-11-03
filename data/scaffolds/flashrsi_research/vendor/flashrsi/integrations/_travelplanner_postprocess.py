"""Native TravelPlanner converter prompt; isolated async SDK and safe extraction."""

import ast
import json

from flashrsi.benchmarks.travelplanner._prompts import CONVERSION_PREFIX

MODEL = "gpt-4-1106-preview"


class _ConversionError(ValueError):
    """Keep the completed API response available when native extraction fails."""

    def __init__(self, message, reply, usage):
        super().__init__(message)
        self.reply = reply
        self.usage = usage


async def convert(prediction, config):
    from openai import AsyncOpenAI

    # Native parsing.py skips these solver outputs rather than asking a parser.
    if prediction in ("", "Max Token Length Exceeded."):
        return None, {"requests": 0}
    llm = config["llm"]
    async with AsyncOpenAI(
        api_key=llm["api_key"],
        base_url=llm.get("base_url") or "https://api.openai.com/v1",
        timeout=config["timeout"],
        max_retries=config["max_retries"],
    ) as client:
        # Constructor None opts into environment defaults. Clear only this
        # client's routing before any request; other roles keep their settings.
        client.organization = None
        client.project = None
        response = await client.chat.completions.create(
            model=llm["model_name"],
            temperature=0,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {
                    "role": "user",
                    "content": CONVERSION_PREFIX + "Text:\n" + prediction + "\nJSON:\n",
                },
            ],
        )
    usage = {
        **(response.usage.model_dump(mode="json") if response.usage else {}),
        "requests": 1,
    }
    reply = (response.choices[0].message.content or "") if response.choices else ""
    if not response.choices or response.choices[0].finish_reason != "stop":
        raise _ConversionError(
            "Incomplete TravelPlanner converter response", reply, usage
        )
    # Keep the official fenced-literal path. Custom converters may omit only
    # the Markdown wrapper; accept their complete JSON document without
    # extracting fragments, repairing fields or changing the daily plan.
    flat = "\t".join(reply.split("\n"))
    try:
        if "```json" in reply:
            plan = ast.literal_eval(flat.split("```json")[1].split("```")[0])
        else:
            plan = json.loads(reply)
    except (IndexError, SyntaxError, ValueError):
        raise _ConversionError(
            "TravelPlanner converter must return a fenced literal or complete JSON plan",
            reply,
            usage,
        ) from None
    required = {
        "current_city",
        "transportation",
        "breakfast",
        "attraction",
        "lunch",
        "dinner",
        "accommodation",
    }
    if not isinstance(plan, list) or any(
        not isinstance(day, dict)
        or required - day.keys()
        or any(not isinstance(day[k], str) for k in required)
        for day in plan
    ):
        raise _ConversionError(
            "TravelPlanner converter returned a malformed daily plan", reply, usage
        )
    return plan, usage
