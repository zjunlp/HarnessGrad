"""The refusal *mechanism*, exercised by a module that refuses on purpose.

No dataset in this repository refuses today: `tau2_bench` was the last one, and its second party
(an LLM user simulator) and its judge channel now ship in the repository, so on 2026-11-04 it
became an ordinary dual-control dataset. `docs/refusal-policy.md` keeps the lifted reason, and
`api_bank`/`nestful`/`sotuqa` were removed outright on 2026-11-03 for reasons that document keeps.

That leaves the mechanism itself -- `REFUSED` is a sentence, `load()` raises it, and the parts a
program can still decide are still decided -- with no live subject. It is tested here against a
synthetic module built in `tmp_path`, so the contract survives the day the last real refusal is
lifted, and the check cannot be satisfied by a dataset that has simply stopped refusing.

The second half is the policy the mechanism serves: a refusal must name what is *missing*, and it
must point at what would make the benchmark reachable, so a reader does not conclude the metric is
unscoreable rather than unreachable *here*.
"""
from __future__ import annotations

import textwrap

import pytest

#: The two kinds of refusal this repository has, and the words either kind must contain.
REFUSAL_KINDS = {
    "contract_gap": ("evidence", "supporting", "judge", "answer channel", "contract"),
    "unreproducible": ("reproduc", "does not reproduce", "recorded result", "state the samples"),
}

CONTRACT_GAP = (
    "synthetic_refusal: the release's headline metric needs predicted evidence, and this "
    "project's answer channel carries a label only, so the exact-match half would be a "
    "different number under the same name. It lifts when the answer contract carries "
    "evidence, which is a platform change rather than a data change."
)
UNREPRODUCIBLE = (
    "synthetic_refusal: executing the released gold call reproduces the recorded result for "
    "51.6% of the suite, so even the gold solution cannot produce the official number. Only a "
    "subset whose state the samples state would be measurable."
)


def _module(tmp_path, reason: str):
    """A refusing adapter, built the way a real one is: literals plus a raising `load()`."""
    source = textwrap.dedent(f'''
        from pathlib import Path

        KIND = "dataset"
        REFUSED = {reason!r}

        def downloads():
            return [{{"kind": "http", "name": "synthetic", "version": "v1",
                      "url": "https://example.invalid/x.tgz", "sha256": "0" * 64,
                      "dst": "x.tgz", "note": "a note long enough to satisfy the download schema"}}]

        def load():
            raise ValueError(REFUSED)
    ''')
    path = tmp_path / "synthetic_refusal.py"
    path.write_text(source, encoding="utf-8")
    import importlib.util

    spec = importlib.util.spec_from_file_location("synthetic_refusal", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("reason", [CONTRACT_GAP, UNREPRODUCIBLE])
def test_the_refusal_is_declared_and_load_raises_it(tmp_path, reason):
    module = _module(tmp_path, reason)
    assert isinstance(module.REFUSED, str) and len(module.REFUSED) > 40
    with pytest.raises(ValueError) as excinfo:
        module.load()
    assert module.REFUSED in str(excinfo.value), "load() must raise the declared reason"


@pytest.mark.parametrize("reason", [CONTRACT_GAP, UNREPRODUCIBLE])
def test_the_refusal_names_what_is_missing(tmp_path, reason):
    """A refusal has to say *what* is missing, not only that something is."""
    lowered = _module(tmp_path, reason).REFUSED.lower()
    kinds = [kind for kind, needles in REFUSAL_KINDS.items()
             if any(needle in lowered for needle in needles)]
    assert kinds, f"refuses without naming a contract gap or an unreproducible metric: {lowered[:200]}"


@pytest.mark.parametrize("reason", [CONTRACT_GAP, UNREPRODUCIBLE])
def test_the_refusal_does_not_claim_the_metric_is_absent(tmp_path, reason):
    """Reachable ≠ reachable *here*: each reason has to point at what would make it reachable."""
    lowered = _module(tmp_path, reason).REFUSED.lower()
    reachable = ("contract" in lowered or "answer channel" in lowered or "path" in lowered
                 or "subset" in lowered or "instead of" in lowered or "reproduc" in lowered)
    assert reachable, lowered


def test_a_refusing_module_still_declares_the_dataset_contract(tmp_path):
    """A refusal does not excuse the parts a program can still decide."""
    module = _module(tmp_path, CONTRACT_GAP)
    assert module.KIND == "dataset"
    downloads = module.downloads()
    assert downloads and all(entry.get("version") for entry in downloads)
    assert all(len(str(entry.get("note") or "")) >= 20 for entry in downloads)
