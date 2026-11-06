# Contribution and agent guidance

- Work in an isolated branch or registered worktree. Preserve concurrent user changes; never reset, overwrite, or remove them without authorization. Review the exact diff and test results before integration.
- Before pushing, squash related development commits into a few coherent commits. Keep independent features separately reviewable; rewrite already published history only when explicitly requested.
- Read the relevant interface and tests before changing behavior. Keep edits scoped and document externally visible configuration and compatibility changes.
- Use synthetic fixtures and offline controls by default. Do not invoke paid services, real models, benchmarks, or production systems without explicit authorization.
- Preserve original evidence, provenance and historical receipts. Distinguish measured, invalid, and unmeasured results; never infer scores from incomplete records or alter pinned scorer artifacts without a dedicated review.
- Treat benchmark tasks, answers, credentials, runtime environments, and tool output as potentially sensitive or untrusted. Keep private evaluator inputs out of actor/method contexts and never commit secrets.
- Run focused tests using a temporary directory outside the checkout. Report commands, exact results, skips, and known limitations; do not equate a mock or a passing subset with full-system validation.
- Make resource limits, endpoint identities, and deployment configuration explicit. Never stop another process or change shared infrastructure without its owner's authorization.
