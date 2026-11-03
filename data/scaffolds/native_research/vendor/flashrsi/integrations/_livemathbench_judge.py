"""Async transport for the pinned OpenCompass LiveMathBench judge protocol."""

import json

from flashrsi.benchmarks.livemathbench._extract import math_postprocess_v2
from flashrsi.benchmarks.livemathbench._label import extract_judge_label
from flashrsi.benchmarks.livemathbench._prompts import JUDGE_PROMPT_CN, JUDGE_PROMPT_EN


async def judge(question, answer, prediction, language, config):
    from openai import AsyncOpenAI

    llm = json.loads(json.dumps(config["llm"], default=dict))
    model = llm.pop("model_name")
    key = llm.pop("api_key")
    base_url = llm.pop("base_url", None) or "https://api.openai.com/v1"
    llm.setdefault("temperature", 0.0)
    if "max_completion_tokens" not in llm:
        llm.setdefault("max_tokens", 8192)
    prompt = JUDGE_PROMPT_CN if language == "cn" else JUDGE_PROMPT_EN
    prompt = prompt.format(
        question=question, gold_answer=answer, answer=math_postprocess_v2(prediction)
    )
    async with AsyncOpenAI(
        api_key=key,
        base_url=base_url,
        timeout=config["timeout"],
        max_retries=config.get("max_retries", 1),
    ) as client:
        response = await client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}], **llm
        )
    if not response.choices or response.choices[0].finish_reason != "stop":
        raise ValueError("Incomplete LiveMathBench judge response")
    usage = response.usage.model_dump(mode="json") if response.usage else {}
    return int(extract_judge_label(response.choices[0].message.content) == "yes"), {
        **usage,
        "requests": 1,
    }
