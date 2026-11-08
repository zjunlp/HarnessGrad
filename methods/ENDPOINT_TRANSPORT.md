Endpoint-based method editing uses a standard-library Chat Completions client;
it does not require an OpenAI SDK. The configured URL is a base URL ending in
`/v1` for a conventional OpenAI-compatible service. Proxy and CA settings use
the Python standard library's environment and TLS configuration.

Transport failures and HTTP 408, 409, 429, and 5xx can retry at most twice
(three attempts total); the separate existing parse retry can make additional
model calls. Redirects and URLs containing user information, queries, or
fragments are refused. Timeout must be finite and positive; responses are limited to 8 MiB. Failure messages
report only the failure category, HTTP status when available, and attempt count;
they do not include response bodies or credentials. Successful endpoint traces
retain the existing prompt/reply/usage format and include transport attempt count.
A reported `usage` object must contain nonnegative integer prompt and completion
counts. If cache counts are present they must be null or consistent nonnegative integers
no larger than prompt tokens. Missing usage remains unreported; malformed usage
is a response error. This is stricter than SDKs that coerce malformed fields.
Retryable failures use bounded backoff and honor `Retry-After` / `retry-after-ms`
and `x-should-retry: false`. If the requested wait exceeds five seconds the
call fails instead of retrying sooner. A missing or null message content is
recorded as an empty reply, including usage, and follows the usual parse retry.
A received response with malformed schema or absent usage records the call as
having unreported token usage rather than claiming a zero-cost call.
A request that was attempted but failed may still have been processed or billed.
Its token cost is unknown, including failed retries before a later successful
response; the method receipt records the count of such transport attempts.
A configuration error before any request leaves the no-call budget unchanged.
