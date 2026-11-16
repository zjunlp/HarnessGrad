# Explicit improver artifact persistence

A launcher may provide `HG_IMPROVER_ARTIFACT_MANIFEST=<workspace>/evidence-artifacts/artifact-manifest.json`
to a producer. The producer explicitly declares individual files; the persistence
API does not scan its home, configuration directories, or workspace for bodies.
A missing manifest records `not_declared`, never complete native body coverage.

```json
{"version":1,"session_id":"session-123","artifacts":[
  {"path":"exports/call-1.json","sha256":"<64 lowercase hex digits>",
   "tool_call_id":"call-1","producer":"example-cli","coverage":"response body"}
]}
```

`session_id` is required at the top level. An optional entry `session_id` must
match it. `tool_call_id`, `producer`, and `coverage` are optional nonempty strings;
no missing call identity is invented. Paths must be relative POSIX paths beneath
the dedicated artifact export root. Absolute paths, traversal, symlinks, hardlinks,
nonregular files, duplicate paths, identity mismatches, and hash mismatches fail
terminally with `ArchiveFailure`, whose `retryable` flag is false.

Call `persist_artifact_manifest(manifest_path, allowed_root=artifact_root,
destination=log_directory, credentials=credentials)` from
`methods.improver_artifacts`. Use the dedicated `HG_IMPROVER_ARTIFACT_ROOT` directory,
not the whole CLI workspace: seeded credential/configuration homes are outside
this export subtree. The existing destination receives unique private
0600 files ending in `.log`, directly in that directory. The returned dictionary
contains `manifest` (receipt file record), `artifacts` (individual provenance and
output file records), and `status`. Copies survive source deletion. Receipt
coverage distinguishes producer claims from verified declared file bytes; it
does not certify that a producer exported every native request or response.

Persistence limits are 16 MiB per file, 64 MiB total source body bytes, 1 MiB for
the declaration, and 1024 entries. These limits do not set model budgets.
Known passed credentials are redacted from body copies; original and persisted
hashes are distinct fields. Credential-bearing declarations are rejected. On
failure, completed copies and a failure receipt remain available when the
storage itself permits it. Missing declared files remain explicitly `missing`.
