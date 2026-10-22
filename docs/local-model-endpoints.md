# Where is the local model?

**FOUND — configured endpoint:** `http://127.0.0.1:8002/v1`, model
`qwen3.5-9b-local` (`AGENTS.md:48`). Staged policy admits only this URL
(`eval/policy/diagnostic_policy.py:29`, enforced at `:72-73`). The compatibility
module `eval/unix_model_relay.py:7-9` delegates to the implementation whose fixed
route is `127.0.0.1:8002`, `/v1/chat/completions`
(`eval/service/unix_model_relay.py:19`, used at `:128-129`). This relay route is
why 8002 was kept (`eval/policy/diagnostic_policy.py:25-28`).

**FOUND — retired endpoint:** the 8001 server was released on 2026-10-14
(`AGENTS.md:48-49`; `eval/policy/diagnostic_policy.py:24-25`). The receipt schema
deliberately retains 8001 alongside 8002 for frozen historical receipts
(`eval/diagnostic_receipt.schema.json:114-119`; preserve old/incomplete receipts:
`AGENTS.md:65`). Schema compatibility does not admit 8001 for a new staged run
(`eval/policy/diagnostic_policy.py:29`, `:72-73`).

**FOUND — historical drift snapshot:** the fidelity handoff and
`hg-wt/w-outcome/.env` used 8010; the same snapshot records main's `.env` using
8001 and all three ports answering 200
(`registry/w-evidence-external-sources.md:1057-1060`, embedded source lines 54–57).
Those are historical observations, not a current listener check. 8010 is outside
the current staged policy (`eval/policy/diagnostic_policy.py:29`).

**UNKNOWN — current listeners and server identity:** not inspected for this
change. To inspect local TCP listeners without issuing requests, run
`ss -ltnp '( sport = :8001 or sport = :8002 or sport = :8010 )'` and retain its
output; this command was not run here. A listener alone does not establish the
model's behavior (`tools/check_local_model.py:8-19`). When model calls are
authorized, `tools/check_local_model.py:6` shows the check command; its default
is defined at `:34` and used at `:49`.
