"""Host-only autorater using the pinned prompt with an explicit provider."""

from flashrsi.benchmarks.deepsearchqa._official import MODEL, counts, prompt


async def judge(row, prediction, config):
    if not prediction.strip():
        raise ValueError("DeepSearchQA cannot rate an empty model response")
    llm = config["llm"]
    if config.get("provider", "google") == "openai":
        from openai import AsyncOpenAI

        async with AsyncOpenAI(
            api_key=llm["api_key"],
            base_url=llm.get("base_url") or "https://api.openai.com/v1",
            timeout=config["timeout"],
            max_retries=config.get("max_retries", 4),
        ) as client:
            response = await client.chat.completions.create(
                model=llm["model_name"],
                messages=[{"role": "user", "content": prompt(row, prediction)}],
                max_tokens=4096,
            )
        if not response.choices or response.choices[0].finish_reason != "stop":
            raise ValueError("Incomplete DeepSearchQA custom judge response")
        usage = response.usage.model_dump(mode="json") if response.usage else {}
        return counts(response.choices[0].message.content or ""), usage

    from google import genai
    from google.genai import types

    options = types.HttpOptions(
        timeout=int(config["timeout"] * 1000),
        base_url=llm.get("base_url") or None,
        retry_options=types.HttpRetryOptions(attempts=config.get("max_retries", 4) + 1),
    )
    with genai.Client(
        api_key=llm["api_key"], vertexai=False, enterprise=False, http_options=options
    ) as client:
        async with client.aio as async_client:
            response = await async_client.models.generate_content(
                model=MODEL,
                contents=prompt(row, prediction),
            )
    if (
        not response.candidates
        or response.candidates[0].finish_reason != types.FinishReason.STOP
    ):
        raise ValueError("Incomplete DeepSearchQA judge response")
    usage = (
        response.usage_metadata.model_dump(mode="json")
        if response.usage_metadata
        else {}
    )
    return counts(response.text or ""), usage
