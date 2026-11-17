# Pre-migration snapshot

`before.json` is a synthetic snapshot written at revision `8badeff8` before the
snapshot implementation moved. It contains only the authored `agent.py` text,
not an experiment, model output, credential or private task input. Migration
controls verify its content ID and compare freshly written bytes.
