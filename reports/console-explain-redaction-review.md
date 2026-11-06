# Console explain transport redaction review

Base: `ff64836`. Scope: explain completion transport errors for both single-round and range APIs.

## Finding and change

An upstream HTTP error body or URL exception text can contain endpoint userinfo, path/query tokens, headers, or other sensitive material not equal to the API key. Request construction could also raise a `ValueError` before the transport handler. HTTP errors now report only a validated numeric HTTP status (or a generic HTTP error); URL, timeout, OS, and request value errors report a generic failure. Exception text and bodies are not returned. Existing non-JSON upstream response handling remains generic.

The `response_format` fallback comment was incorrect: there is no retry without that field. HTTP rejection still returns 502 immediately; invalid model content is still retried once with the field. Degraded model output is deliberately returned as raw content and is not covered by transport redaction.

## Verification

Offline synthetic fixtures only; no model or network calls. Ran `python -m pytest -q -p no:cacheprovider --basetemp=/tmp/hg-explain-redaction-pytest tests/quality/test_explain_redaction.py tests/quality/test_explain_range.py`: **13 passed in 0.13s**. Tests exercise both API variants, secret-bearing HTTPError body and URLError, Request ValueError, non-JSON response, HTTP no-fallback behavior, and invalid-content retry. This focused suite is not full-system validation.

## Follow-up: HTTP protocol exceptions

An independent audit identified `http.client.InvalidURL` and other `HTTPException` subclasses as additional transport failures whose messages can contain sensitive URL material. These now produce the same generic error; process-control exceptions are not caught. The same offline command after the follow-up produced **17 passed in 0.13s**, including synthetic `InvalidURL` checks for both APIs and propagation checks for `KeyboardInterrupt` and `SystemExit`.
