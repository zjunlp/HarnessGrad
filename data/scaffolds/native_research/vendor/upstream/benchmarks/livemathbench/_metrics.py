"""GPassK 20a02a1479c2db19d9b8a8676c809c4696f9e889 hypergeometric metrics.

Use exact integer combinations for the same hypergeometric tail as the
official scipy.stats.hypergeom.sf implementation, without a SciPy runtime.
"""

import math


def metrics(labels, ks):
    n, c = len(labels), sum(labels)

    def tail(k, minimum):
        return sum(
            math.comb(c, i) * math.comb(n - c, k - i)
            for i in range(max(minimum, k - (n - c)), min(c, k) + 1)
        ) / math.comb(n, k)

    result = {"accuracy": c / n}
    for k in ks:
        for threshold in (0.0, 0.25, 0.5, 0.75, 1.0):
            result[f"G-Pass@{k}_{threshold}"] = tail(
                k, max(math.ceil(k * threshold), 1)
            )
        # Retain the official discrete definition, including odd-k behavior.
        result[f"mG-Pass@{k}"] = (
            2 * sum(tail(k, i) for i in range(math.ceil(k * 0.5) + 1, k + 1)) / k
        )
    return result
