# Submission UI mock

Start the contract-only service in a second terminal:

```bash
python3 console/uat/mock_submissions.py --port 4471
```

Open the console submit page with `?api=` pointing at it:

```text
http://127.0.0.1:4470/submit.html?api=http://127.0.0.1:4471
```

The service never opens or executes an uploaded archive. It uses the uploaded filename as
a deterministic fixture selector:

- `demo-valid.tar.gz` → `queued → running → scored`;
- `demo-invalid.tar.gz` → scored with all `dev` tasks invalid, `score` omitted for that pool;
- `demo-failed.tar.gz` → `queued → running → failed`;
- `reject-<code>.tar.gz` → that frozen request/rejection code for archive codes;
- `GET /mock/cases` lists the available filename fixtures.

The mock has the three reviewed scenarios in `GET /api/scenarios`, supports loopback CORS for
a console on another local port, and exposes the existing read-only run shapes used to enrich
a scored submission:

```text
GET /api/runs/<submission_id>
GET /api/runs/<submission_id>/rounds/0/traces
GET /api/runs/<submission_id>/rounds/0/trace?name=<task.jsonl>
```

Those routes provide cost and task evidence. The frozen submission `result` remains limited to
pools, warnings and identity. Missing values stay absent or `null`; invalid tasks are not given
score zero. `tree_sha256` is prefixed `mock:` because the service never extracts the archive and
must not imply a real tree identity.

Query validation follows v1: `/api/submissions` accepts only `limit` (1..200) and `cursor`, detail
accepts no query, and `/log` accepts only `tail` (1..200000). Unknown, repeated, malformed, or
out-of-range fields return structured `bad-query` errors.
