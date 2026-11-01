---
title: 5-minute harness upload
description: A short path from a local harness tree to a reviewed HarnessGrad measurement.
---

## 1. Validate the tree

Your archive must contain `harness.json` and an entrypoint that accepts `--task` and `--workdir`. Run the local validator before creating the archive:

```bash
python3 tools/validate_harness.py /path/to/harness --smoke
```

The smoke check must write an answer artifact. Keep the archive below 20 MiB compressed and 200 MiB after extraction.

## 2. Start the reviewed console

Use the endpoint supplied by the project maintainer. For a local mock:

```bash
python3 console/uat/mock_submissions.py --port 4471
```

Open `/submit.html?api=http://127.0.0.1:4471`, choose an available scenario, and select your archive. The page validates the extension and compressed size before it sends anything.

## 3. Confirm the measured state

The service returns a `submission_id`. The page follows `validating`, `queued`, and `running` until it reaches `scored`, `failed`, or `rejected`. A scored response shows each returned pool on its own row, counts `n/measured/invalid/harness_failed`, costs when available, and evidence references.

For the mock, the normal flow returns an example identifier such as `sub-20261101T000000Z-1234abcd`. The identifier is a receipt handle for that mock service and is not a benchmark result.

## 4. Read the evidence

Keep the `submission_id`, the uploaded archive digest, the tree digest, the scenario name and the permanent receipt together. Do not compare a pool score with another pool's score or treat an absent value as zero.

The service is currently internal and invite-only. Do not publish a submission URL or expose the loopback service publicly.
