# Fixed W3C PDF dependency

LlamaIndex task18813's released baseline tests fetch the W3C `dummy.pdf` through
ordinary `httpx` and `requests`. They use its bytes for PDF format, MIME, buffer
and base64 checks. This optional environment layer supplies that same public
13,264-byte resource at its original URL, with SHA-256
`3df79d34abbca99308e79cb94461c1893582604d68329a41fd4bec1885e6adb4`.
It does not change the official tests, loader, oracle or score.

`prepare.py --root /external/fresh-assets` downloads the resource with normal
public TLS verification and records the original URL, response headers, retrieval
time and hash. An already downloaded file can be supplied together with its
recorded metadata using `--source` and `--metadata`; bytes and URL remain fixed.
Preparation generates a dedicated local CA constrained to `www.w3.org` and a
server certificate. The CA signing key is deleted before publication. The server
key enters the runtime as root-owned mode `0400`; the actor cannot read it.

`build.py` accepts the same `--assets`, `--output`, immutable `--base-image` and
`--python` arguments as the sibling HF mirror builder. It reuses the isolated
interpreter wrapper and root-owned service lifecycle. The dedicated mode sets an
explicit loopback HTTPS proxy and scoped `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`.
**This is a local-CA mirror, not the W3C public certificate chain.** The original
clients still verify the certificate chain and hostname. Default public CA trust
rejects the mirror; verification is never disabled.

The server accepts only `CONNECT www.w3.org:443`, the matching TLS SNI and Host,
and GET/HEAD of the exact fixed PDF path without a query. It cannot connect to an
origin or forward traffic. Unknown authorities, paths and methods fail closed;
headers and connection time are bounded. Docker remains `network=none` and the
asset layer stays read-only. Ordinary response bytes use process memory; any test
file writes remain under the declared workspace quota. Workers and the verifier
start independent services. Failed services are never restarted and yield exit
78; Cooper's platform-owned completion contract withholds the score even when a
failed child has already written a reward.

The optional `HG_COOPERBENCH_HTTPS_TEST_IMAGE` and
`HG_COOPERBENCH_HTTPS_TEST_ASSETS` controls exercise real clients, the original
`DocumentBlock`, UID separation, TLS rejection, service loss and cleanup in an
observed runtime. They do not establish full benchmark coverage.
