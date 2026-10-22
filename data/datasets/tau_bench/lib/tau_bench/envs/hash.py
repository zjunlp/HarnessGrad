"""The database hash tau-bench scores with -- extracted, not rewritten.

The block below is copied verbatim from `tau_bench/envs/base.py` in the
upstream checkout (everything from `ToHashable =` up to `class Env`), with the
`sha256` and `typing` names it uses added back because they are imported above
that region in the original. `Env.get_data_hash()` is exactly
`consistent_hash(to_hashable(self.data))`, which is what the verifier calls.

    source   tau_bench/envs/base.py
    sha256   1ad0402073f0ac9ca6b527efa0706e2aa8836dd2e5c1d453ed414ecb768c6d21
"""
from hashlib import sha256
from typing import Dict, List, Set, Tuple, Union

ToHashable = Union[
    str, int, float, Dict[str, "ToHashable"], List["ToHashable"], Set["ToHashable"]
]
Hashable = Union[str, int, float, Tuple["Hashable"], Tuple[Tuple[str, "Hashable"]]]


def to_hashable(item: ToHashable) -> Hashable:
    if isinstance(item, dict):
        return tuple((key, to_hashable(value)) for key, value in sorted(item.items()))
    elif isinstance(item, list):
        return tuple(to_hashable(element) for element in item)
    elif isinstance(item, set):
        return tuple(sorted(to_hashable(element) for element in item))
    else:
        return item


def consistent_hash(
    value: Hashable,
) -> str:
    return sha256(str(value).encode("utf-8")).hexdigest()
