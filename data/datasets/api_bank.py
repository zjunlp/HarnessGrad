r"""API-Bank -- **refused**: its official metric is not reproducible from the released data.

    python3 tools/datasets.py --fetch --dataset api_bank     # stages the vendored tree
    python3 data/scaffolds/qa/control.py --dataset api_bank            # says why there is no control
    python3 tools/dataset_lint.py --dataset api_bank         # REFUSED, not FAIL

What it is, and why this file exists
------------------------------------
API-Bank (`AlibabaResearch/DAMO-ConvAI`, subtree `api-bank/`) asks a model for one API call,
`[ApiName(param1='value1', ...)]`, and scores it by **executing** that call and comparing the
reply with the recorded one:

    evaluator.py::Evaluator.evaluate
        api_name, param_dict = parse_api_call(model_output)
        result = tool_manager.api_call(api_name, **param_dict)
        correct = api.check_api_call_correctness(result, ground_truth['result'])

That is a strong, honest metric -- and it is the reason this dataset cannot run here.

The measurement (all 508 API-role samples)
------------------------------------------
Executing each **gold** call and scoring it against its own recorded `result`:

| outcome | n | share |
| --- | --- | --- |
| reproduces the recorded result | 262 | 51.6% |
| runs, produces something else | 187 | 36.8% |
| the call itself raises | 55 | 10.8% |
| no `class X(API)` in the repository | 4 | 0.8% |

The raises are specific: 45 are every `ToolSearcher` sample (there is no
`class ToolSearcher(API)`), 8 name a parameter the class does not declare
(`invalid parameter name: 'device_id'`), 2 are `KeyError: 'time'`. And `GetUserToken` -- an API
whose call is a pure function of its arguments -- splits 53 correct against 55 wrong, which is
the shape of the whole problem: **the recorded `result` depends on state the released samples do
not carry** (a token minted earlier in the dialogue, a database mutated by a previous turn). The
official evaluator has the same hole: it builds a fresh `ToolManager` per sample and replays only
that sample's `param_dict`.

The refusal, and its reason
---------------------------
`load()` raises. The doctrine is `docs/dataset-onboarding-skill.md` §2 and §15.9: an oracle
control that cannot reach 1 means the check is unreachable, and every harness score on the
dataset would be meaningless. Here it is worse than unreachable for a subset -- it is
**unreproducible for 48.4%** of the suite, so:

* an oracle built from the gold `param_dict` cannot score 1, which is exactly what the oracle
  control exists to detect;
* restricting to the 262 that do reproduce would be a number about a hand-picked half wearing the
  benchmark's name, unless the record says so in every row;
* reporting execution success (51.6%) as "API-Bank accuracy" would be the §1 error with the sign
  flipped.

Two constructive paths, either of which needs a decision above the adapter
--------------------------------------------------------------------------
1. **Restrict to the reproducible subset.** The machinery is in place -- `lib/ab_env.py` loads
   the vendored APIs (52 classes; `googletrans` is the only absent package and it costs 4
   samples) and grades with no model, no API key and no network. The record would have to carry
   the exclusion per row.
2. **Grade the request instead of executing it** -- compare name and parameter values against the
   gold request. Reproducible, and a **different benchmark**: it drops the "would the call do the
   right thing" half that gives API-Bank its name.

What is staged, and what stays
------------------------------
* `datasets/api-bank/_src/api-bank-vendor.tar.gz` (sha256 `79b21e09…`, 340 files / 1.1 MB): the
   56 `apis/*.py`, 20 `init_database/*.json`, 264 sample JSONL files and `tool_manager.py`,
   assembled from the 92 MB upstream zip.
* `data/datasets/api_bank/lib/ab_env.py` stays in git. It is the thing that made the measurement
   possible, and either constructive path needs it.
"""
from __future__ import annotations

import os
from pathlib import Path

KIND = "dataset"

#: Where the vendored tree and the upstream zip live. Outside the platform tree on purpose.
DATASET_ROOT = Path(os.environ.get("HG_API_BANK_DATA",
                                   "/mnt/20t/xubuqiang/datasets/api-bank"))

#: The upstream repository. Pinned by the archive digest; the repository has no usable tag for
#: this subtree and the API that would resolve a commit is unreachable from this host -- an
#: invented sha would look like a pin and reproduce nothing, so the digest is the pin.
REPO_URL = "https://codeload.github.com/AlibabaResearch/DAMO-ConvAI/zip/refs/heads/main"
REPO_SHA256 = "38a08acd60fecc3449f4afd9ba4df18bdc086dee4f4517566678dc74c03463f5"
REPO_NAME = "DAMO-ConvAI.zip"
SUBTREE = "api-bank"

#: The assembled minimal tree (the 340 files the loader needs), and its digest.
VENDOR_NAME = "api-bank-vendor.tar.gz"
VENDOR_SHA256 = "79b21e0992707f98cada065aa8212ab62e662c69e6f3376f026f61abf35edb85"

REFUSED = (
    "api_bank: the official metric executes the requested API and compares its reply with the "
    "recorded one (`evaluator.py::evaluate` -> `check_api_call_correctness`), and the released "
    "samples do not reproduce it: measured over all 508 API-role samples, the **gold** call "
    "reproduces its own recorded result 262 times (51.6%), produces something else 187 times, "
    "raises 55 times, and 4 samples have no API class at all. The recorded results depend on "
    "state the samples do not carry -- a token minted earlier in the dialogue, a database "
    "mutated by a previous turn -- and the official evaluator has the same hole because it "
    "builds a fresh tool manager per sample. An oracle built from the gold parameters therefore "
    "cannot reach 1, so this dataset cannot pass its oracle control. Reporting the reproducible "
    "half, or the execution success rate, under the benchmark's name would each be a different "
    "number. See data/datasets/api_bank.py for the two constructive paths."
)


def downloads() -> list[dict]:
    """The upstream zip (for provenance) and the assembled tree the loader reads."""
    return [
        {
            "kind": "http",
            "name": "DAMO-ConvAI (api-bank subtree)",
            "version": REPO_SHA256,
            "url": REPO_URL,
            "size": "92 MB zip; the `api-bank/` subtree inside it is what matters",
            "sha256": REPO_SHA256,
            "dst": "_src/" + REPO_NAME,
            "note": "Trap 1: the dataset is a **subtree of a multi-project repository**, not its "
                    "own repo, and the repository has no tagged release for it -- so the pin is "
                    "the archive digest (an invented commit sha would look like a pin and "
                    "reproduce nothing). Trap 2: only 340 of the zip's files are needed; the "
                    "assembled `api-bank-vendor.tar.gz` is the loader's input and the 92 MB zip "
                    "is kept for provenance.",
        },
        {
            "kind": "http",
            "name": "assembled API-Bank vendor tree",
            "version": VENDOR_SHA256,
            "url": "local:assembled from the subtree above",
            "size": "~1.1 MB (340 files: 56 apis, 20 init_database JSONs, 264 sample JSONLs)",
            "sha256": VENDOR_SHA256,
            "dst": "_src/" + VENDOR_NAME,
            "note": "The minimal tree `lib/ab_env.py` loads. Assembled from the zip above; "
                    "checked by digest so the assembly cannot drift silently.",
        },
        {
            "kind": "pip",
            "name": "googletrans",
            "package": "googletrans",
            "version": "4.0.0",
            "size": "~50 KB",
            "note": "**Not needed to grade**, and deliberately not installed: it is the only "
                    "absent package among the APIs the samples use, and it affects 4 samples "
                    "(`Translate`). Installing it would put a network-touching package into the "
                    "interpreter the whole suite runs on. Recorded so a reader knows the 4 are "
                    "a dependency, not a bug.",
        },
    ]


def provision_status() -> dict:
    """What is present locally. A pure filesystem answer: no import, no API execution."""
    tarball = DATASET_ROOT / "_src" / VENDOR_NAME
    zip_path = DATASET_ROOT / "_src" / REPO_NAME
    return {
        "root": str(DATASET_ROOT),
        "present": tarball.is_file(),
        "split_dir": str(DATASET_ROOT),
        "vendor_tarball": str(tarball) if tarball.is_file() else None,
        "upstream_zip": str(zip_path) if zip_path.is_file() else None,
        "tasks": 0,
        "refused": True,
    }


def load() -> tuple[list[dict], dict[str, str]]:
    raise ValueError(REFUSED)


__all__ = ["KIND", "DATASET_ROOT", "REFUSED", "load", "downloads", "provision_status"]
