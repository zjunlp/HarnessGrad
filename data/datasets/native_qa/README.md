# Native QA preparation dependencies

This directory declares a shared preparation-only execution artifact, not a dataset
adapter. It does not load or score tasks. The exact lock includes the full Python
dependency closure for pinned Hub acquisition and Parquet conversion.

Explicitly prepare dependencies with:
`python -m data.scaffolds.native_qa.prepare --prepare-runtime`
Check without installing or downloading with:
`python -m data.scaffolds.native_qa.prepare --check-runtime`
Both support `--runtime-root` for an external artifact store. Data preparation keeps
the existing `--dataset`, `--root`, and repeated `--local SPLIT=PATH` arguments.
Local JSON, JSONL and CSV require only the standard library. Local Parquet and Hub
acquisition require the prepared artifact; they never fall back to host packages.

Conversion receives a restricted environment. Only explicit Hub acquisition forwards
HF_TOKEN, HUGGING_FACE_HUB_TOKEN, HF_ENDPOINT and download proxies/certificate
settings. Local conversion and dependency probes do not receive model credentials.
HF caches reside under the retained preparation attempt. Worker failures report no
raw stderr. Every attempt is retained for provenance or failure inspection; an
existing manifest or rows directory is never overwritten. Completed rows remain in
their unique attempt directory. A fully written manifest is published by an atomic
non-replacing hard link; a failed or competing attempt has no canonical entry point. Successful manifests bind
local input bytes, available downloaded source bytes, controller/worker and artifact
identity. Hub streaming sources are revision-bound and output-digest-bound; they do
not claim a digest of every upstream shard before library decoding.
