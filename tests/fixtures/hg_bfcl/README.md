# w-bfcl public-source custody

This bundle preserves five successful public HTTPS GET responses: v1.3 tag,
release, immutable commit, root license Git blob, and the complete compressed
release archive. `responses/*.headers` preserves curl's response headers,
including proxy CONNECT headers; `*.receipt.json` preserves exact request command,
URL, UTC acquisition times, HTTP status, byte counts and recomputed SHA-256.
No token or paid service was used. TLS verification stayed enabled.

The archive is opaque custody evidence, **not an actor projection**. It includes
upstream repository payloads. Only the 83 code/license/spec members already
allowlisted by the E3 inventory are opened by the offline verifier; no question,
answer, result or other benchmark member is extracted or interpreted. Nothing
is provisioned into a dataset root. Do not supply this archive to an actor.

`inventory.json` binds all 83 original retained files to exact archive member
names, measured sizes, SHA-256 and reconstructed Git blobs. Earlier original
files remain at their existing paths. B5's original recursive/root tree response
bodies are reused and freshly hashed; all 93 Git tree objects and the signed Git
commit object are reconstructed. The archive PAX comment agrees with the commit.
The GitHub signature status is a server assertion; this work does not verify a
PGP key or signature independently. `LICENSE.upstream` is the unchanged root
license decoded from the fresh raw Git-blob response and matched to the archive.

`SHA256SUMS` covers every file in this directory except itself. Recompute it only
when intentionally changing locally authored documentation/scripts/derived
inventory; never overwrite raw responses. `acquire.py` refuses an existing
response name. Use a fresh directory/name for any future acquisition.

Reproduce the offline checks from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/fixtures/hg_bfcl/verify.py
(cd tests/fixtures/hg_bfcl && sha256sum -c SHA256SUMS)
```

`verify.py --write` creates the deterministic derived inventory and manifest;
the default mode compares them and performs the same byte/identity checks.
No upstream module is imported or executed. The AST audit records imports and
explicit eval/exec/literal-eval/dynamic-import calls; static reachability and
semantics are reviewed in `reports/w-bfcl-review.md`.

**Disposition:** public-source archive/tag/commit/member/root-license custody is
established for the retained 83-file graph. Complete offline third-party/native
runtime custody and official scorer execution are absent. The blocker remains
external. Independent approval requirements were withdrawn by the operator in
`docs/independent-review-deferred.md`; no signature or human sign-off is a gate.
Historical fixtures, failures and approval flags remain unchanged. No official
score, full-call differential result, model/task/GPU/endpoint activity, or
production authorization is claimed.
