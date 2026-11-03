"""Independent client for the pinned BrowseComp text judge protocol."""

import json
import re

from flashrsi.benchmarks.browsecomp._official import GRADER_TEMPLATE


async def judge(question, answer, prediction, config):
    from openai import AsyncOpenAI

    llm = json.loads(json.dumps(config["llm"], default=dict))
    model = llm.pop("model_name")
    api_key = llm.pop("api_key")
    base_url = llm.pop("base_url", None) or "https://api.openai.com/v1"
    llm.setdefault("max_completion_tokens", 4096)
    async with AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=config["timeout"],
        max_retries=config.get("max_retries", 1),
    ) as client:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": GRADER_TEMPLATE.format(
                        question=question, correct_answer=answer, response=prediction
                    ),
                }
            ],
            **llm,
        )
    usage = response.usage.model_dump(mode="json") if response.usage else {}
    if not response.choices or response.choices[0].finish_reason != "stop":
        raise ValueError("BrowseComp judge did not finish")
    text = response.choices[0].message.content or ""
    match = re.search(r"correct: (yes|no)", text)
    # The pinned upstream returns group(0), then compares it to 'yes', always
    # scoring zero. Keep its prompt/pattern/default-no, correcting only the group.
    return bool(match and match.group(1) == "yes"), usage
