"""Where a dataset's bytes live: one resolver, four rules, no hardcoded checkout.

Why this module exists
----------------------
Every adapter used to spell the operator's absolute data root as a literal
(``Path("/mnt/.../datasets/<name>")``), and ``tools/datasets.py`` resolves the
destination of ``--fetch`` from that same attribute. A clone of this repository could
therefore neither fetch nor load anything: the only destination it knew was a path that
exists on one machine. This module is the one place that answers "where should this
dataset go"; every adapter asks it instead of spelling a path.

Resolution order
----------------
1. ``HG_DATA_<NAME>`` -- a per-dataset override. ``<NAME>`` is the dataset name with
   every run of non-alphanumeric characters replaced by ``_`` and upper-cased, so
   ``bfcl-v3`` is ``HG_DATA_BFCL_V3`` and ``strongreject/native-v1`` is
   ``HG_DATA_STRONGREJECT_NATIVE_V1``.
2. ``HG_DATA_ROOT`` + ``/<name>`` -- the whole collection moved. One variable says where
   every dataset lives, so relocating forty roots is one export. This is the variable
   ``docs/dataset-first-day.md`` tells a reader to set.
3. ``legacy`` -- the absolute root the adapter was written against, **only when that path
   exists**. An existing run on the machine that wrote the literal keeps resolving to
   exactly the same bytes, while a stale literal is skipped instead of beating rule 4.
4. ``~/.local/share/harnessgrad/datasets/<name>`` -- the default a fresh clone gets.
   This module never creates it; ``tools/datasets.py --fetch`` creates the destination
   on demand, which is what makes ``--fetch`` work on a machine that has never held the
   data.

Nothing here raises on a missing directory and nothing here creates one: a missing root
is an answer ("this is where the bytes should go"), not an error. ``describe()`` reports
which rule won and whether the directory is there, for ``--check``-style diagnostics.

The old per-dataset variables
-----------------------------
Fifteen adapters already had their own override (``HG_ALFWORLD_DATA``,
``HG_LOCOMO_DATA``, ``HG_TERMINAL_BENCH``, ...). They pass
``os.environ.get(<old name>, <old literal>)`` as ``legacy``, so the old name keeps
working as the legacy tier: it wins only when it names an existing directory, and rules
1 and 2 take precedence over it. New documentation should name ``HG_DATA_ROOT`` or
``HG_DATA_<NAME>``; the old spellings survive so a run recorded before this module keeps
resolving where it did.
"""
from __future__ import annotations

import os
import pathlib
import re

#: Rule 4's per-user collection, relative to home. Named here and nowhere else, so
#: "the default moved" is one edit instead of forty.
DEFAULT_COLLECTION = pathlib.Path(".local") / "share" / "harnessgrad" / "datasets"

#: Rule 2: the variable that relocates the whole collection.
COLLECTION_ENV = "HG_DATA_ROOT"


def env_name(name: str) -> str:
    """Rule 1's variable for `name`: ``HG_DATA_<UPPER(name, non-alphanumeric -> _)>``."""
    return "HG_DATA_" + re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").upper()


def default_root(name: str) -> pathlib.Path:
    """Rule 4: the per-user directory a fresh clone uses. Never created here."""
    return pathlib.Path.home() / DEFAULT_COLLECTION / name


def _resolve(name: str, legacy) -> tuple[pathlib.Path, str]:
    """The winning path and the name of the rule that produced it."""
    override = os.environ.get(env_name(name))
    if override:
        return pathlib.Path(override).expanduser(), env_name(name)
    collection = os.environ.get(COLLECTION_ENV)
    if collection:
        return pathlib.Path(collection).expanduser() / name, COLLECTION_ENV
    if legacy is not None:
        path = pathlib.Path(legacy).expanduser()
        if path.exists():
            return path, "legacy"
    return default_root(name), "default"


def dataset_root(name: str, legacy=None) -> pathlib.Path:
    """The root for `name`, per the four rules in the module docstring.

    `legacy` is the adapter's old absolute literal (or its old per-dataset variable's
    value). It is used only when the path exists, so the operator's provisioned
    datasets keep resolving exactly where they already are.
    """
    return _resolve(name, legacy)[0]


def describe(name: str, resolved=None, *, legacy=None) -> dict:
    """``{"name", "root", "source", "exists"}`` -- which rule won, and whether it is there.

    `resolved` is an already-resolved root (typically the adapter's ``DATASET_ROOT``).
    When it differs from what the rules produce it is reported with source
    ``"explicit"``, so a monkeypatched or engine-supplied root is never mistaken for
    configuration.
    """
    root, source = _resolve(name, legacy)
    if resolved is not None:
        given = pathlib.Path(resolved).expanduser()
        if given != root:
            root, source = given, "explicit"
    return {"name": name, "root": str(root), "source": source, "exists": root.exists()}
