# Locked tokenizer resource acceptance

The original NLTK-data revision `550b6625bcef1f2abff2ff770a5a0d272c9c6b2a`
archive was obtained with TLS verification enabled and a bounded request. Its
4,319,076 bytes matched the unchanged archive SHA-256
`e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106`.
All four English members matched their original independent sizes and digests
in `specs/resources.lock.json`; the retained original-lock BPE also matched.
Offline restoration verified **five files, 3,851,679 bytes** before publication.
No resource, source or dependency pin was changed.

Earlier unavailable-resource receipts remain valid historical evidence. The new
acquisition/provision receipts supersede their missing-resource status only.
`prepare_resources.py` now records the archive path, archive digest/size and each
consumed member in `verified_archive_source`, alongside individual-file import
provenance. `kind: local_archive` identifies a verified local input path; only
`kind: download` records an `acquisition_url`. The locked URL remains separately
labelled `declared_source_url`, and any written cache path is `cache_destination`,
not an acquisition source. It stages and fsyncs verified bytes before atomic per-file replacement;
this is not a transaction across all five paths. A failed write must be followed
by the normal full verification before using the store.

## Reproduce restoration

Use an external resource store. `--download` permits only the original locked
URLs and checks their original digests; ordinary verification remains offline:

```sh
python data/datasets/memoryagentbench/prepare_resources.py \
  --resources "$MAB_RESOURCES" --download > acquisition-provision.json
python data/datasets/memoryagentbench/prepare_resources.py \
  --resources "$MAB_RESOURCES" > offline-check.json
```

Alternatively retain the exact locked zip separately and import the BPE from an
existing original-lock-matching resource root. This performs no network access:

```sh
python data/datasets/memoryagentbench/prepare_resources.py \
  --resources "$MAB_RESOURCES" --archive "$PINNED_PUNKT_ZIP" \
  --from-resources "$EXISTING_RESOURCES" > offline-import.json
```

`EXISTING_RESOURCES` uses the `nltk/` and `tiktoken/` layout. It may contain only
the BPE: the zip supplies the four tables. Every provided member must pass its
original pin; a corrupt supplied member is rejected before writes.

## Reproduce real tokenizer and upstream controls

Run these commands with the **isolated pinned preparation interpreter** described
in `requirements-prepare.txt`, not platform Python. This acceptance used that
separate venv, not a role-admitted `dataset_runtime` execution artifact. No NLTK,
tiktoken or other dependency was installed into the platform interpreter.

Retain `utils/eval_other_utils.py` from
`HUST-AI-HYZ/MemoryAgentBench@538026089d1a8a8eff05121d0db89b388f360eba`.
Its SHA-256 must match the unchanged policy pin
`d77976be409298970614d477a9d8003850caddb0510e56a7e821a037d98493a2`.
The control extracts the actual pinned chunk function by AST and removes only
its explicit `nltk.download('punkt')` statement, the policy's declared offline
resource-provisioning delta. It executes the original grouping algorithm with
real `nltk.sent_tokenize` and `tiktoken.encoding_for_model`, using the verified
local resources. It does not import the source's model or scorer dependencies.

```sh
"$HG_MAB_PYTHON" -I -B data/datasets/memoryagentbench/verify/tokenizer_smoke.py \
  --resources "$MAB_RESOURCES" > tokenizer-smoke.json
"$HG_MAB_PYTHON" -I -B data/datasets/memoryagentbench/verify/tokenizer_differential.py \
  --resources "$MAB_RESOURCES" --upstream-module "$PINNED_CHUNK_MODULE" \
  > tokenizer-differential.json
```

The real microepisode reported `full_tokenizer_ready: true` and
`synthetic_sentence_boundary: false`. All six authored-text cases matched real
sentence boundaries, complete BPE token IDs and upstream chunk strings:

| Case | Sentences | Tokens | Chunks | Empty chunks |
| --- | ---: | ---: | ---: | ---: |
| empty | 0 | 0 | 0 | 0 |
| abbreviations | 3 | 26 | 1 | 0 |
| quotes and multilingual text | 4 | 23 | 1 | 0 |
| special token | 2 | 12 | 1 | 0 |
| threshold crossing | 900 | 5401 | 2 | 0 |
| oversized first sentence | 2 | 5004 | 3 | 1 |

The last case confirms the original empty chunk before an oversized first
sentence; the algorithm does not silently enforce a hard 4096-token sentence
limit. These are author-owned controls, not released benchmark episodes.
Resources and tokenizer controls passing do **not** establish released corpus
preparation, frozen episode reproduction across the corpus, live judge
acceptance or full benchmark readiness. Retain acquisition, provision, smoke
and differential JSON receipts with the external resources and licenses.
