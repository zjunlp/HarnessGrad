---
title: Submit a harness
sidebar:
  order: 1
description: Upload a complete harness to a reviewed HarnessGrad scenario and inspect its measured result.
---

HarnessGrad measures a harness with the platform's own task runner, sandbox, verifier and record. The submission service is a thin HTTP layer over the same `driver.py` path used by local runs. It does not accept a reported score.

For the shortest local walkthrough, see [5-minute harness upload](../submit-harness/).

## Current access

The v1 endpoint is **internal and invite-only**. It listens on a loopback address, has no public authentication layer, and must not be exposed while the platform's egress and service admission prerequisites remain open. Ask the project maintainer for the current endpoint and an invitation before uploading anything.

The browser page is available from the console at `/submit.html`. Set `?api=http://127.0.0.1:<port>` only when using a separate reviewed mock or service during local testing. The page sends `POST` only for an explicit upload action; scenario discovery and status reads use `GET`.

## What to upload

Upload one `tar.gz`, `tar`, or `zip` archive. The archive must contain one harness tree, either at its root or under one top-level directory. The tree needs a valid `harness.json` with a non-empty `name`, `version`, and `entrypoint`.

The runner invokes the entrypoint as:

```text
<entrypoint> --task <task.json> --workdir <dir>
```

The harness reads the public task, works in the supplied directory, and leaves the requested artifact there. The platform performs all scoring after the harness exits. A harness does not receive a verifier definition or a trusted score.

The archive limits are 20 MiB compressed, 2,000 entries, and 200 MiB after extraction. Absolute paths, `..` paths, links, devices, pipes and setuid entries are rejected. `backend` is currently `cli`; `env_kinds` must cover the selected scenario. If `install` is declared, its file must be inside the archive.

## Choose a scenario

Scenarios are reviewed files, not arbitrary request parameters. The page shows the dataset, side, pool, task count and measured expected time when the service has one. An unavailable scenario stays visible with its reason. The submit button remains disabled until an available scenario is selected.

Results keep pools separate. A result can contain `train`, `dev`, `exam`, or another named pool. The service never publishes a cross-pool average. Missing values appear as `—`; `invalid` and `harness_failed` remain separate counts and do not become zero scores.

A scored response links to the run directory, curve, per-task verdict directory, log and permanent receipt. These paths are evidence references owned by the service. The current v1 contract does not provide a browser download route for every filesystem path, so the UI does not pretend that a local path is a clickable public file.

The console may enrich a scored response with the existing read-only run endpoints (`GET /api/runs/<run_id>` and its round/trace routes). This only displays recorded tokens, calls, wall time and task evidence; it never recomputes a score or fills a missing field.

## Lifecycle

```text
received → validating → rejected
                     ↘ queued → running → scored
                                        ↘ failed
```

Validation failures explain the error code and the change needed. A successful queued submission receives a `submission_id` and `run_id`; polling continues until the service returns `scored` or `failed`.

## Local mock

The repository includes a standard-library mock for page and API work:

```bash
python3 console/uat/mock_submissions.py --port 4471
# open the console's submit page with ?api=http://127.0.0.1:4471
```

The mock runs deterministic states, exposes one unavailable scenario, provides an invalid-result scenario, and maps rejection cases from filenames such as `reject-unsafe-path.tar.gz`. It never executes an uploaded archive and its scores are interface fixtures.

## What a result means

A result is a measured platform record for one reviewed scenario and one uploaded harness tree. It includes the harness identity, model/backend identity when available, platform content identity, pool counts, costs when the service has them, and task evidence. It does not mean the harness is universally better, and a mock result is not benchmark evidence.

Before using a result in research, check that the response is `scored`, the required pools are measured, `invalid` and `harness_failed` are understood, and the permanent receipt and per-task evidence are retained. The service's retention window applies to public detail and logs; permanent receipts remain for deduplication and audit.
