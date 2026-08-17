"""Work partitioning so several producers can share one source without racing.

Each batch script walks an ordered list of scenes and skips what already has a
manifest. Two copies of the same script therefore pick the *same* next scene and
do the work twice - and on the Waymo paths they used to fight over the scratch
file as well.

`W2P_SHARD="i/N"` gives worker i every N-th item, so N workers cover the list
once between them with no coordination and no shared state.

Sizing: the per-frame cost is 48% camera-support, 30% render, 17% JPEG decode -
all CPU, all per-process. This box has 32 cores and was running four producers,
one per source, so 28 cores sat idle.
"""
from __future__ import annotations

import os


def shard():
    """-> (index, total). Defaults to (0, 1), i.e. take everything."""
    spec = os.environ.get("W2P_SHARD", "")
    if "/" not in spec:
        return 0, 1
    try:
        i, n = spec.split("/", 1)
        i, n = int(i), int(n)
        if n < 1 or not 0 <= i < n:
            return 0, 1
        return i, n
    except ValueError:
        return 0, 1


def mine(seq):
    """Yield (position, item) for the items belonging to this shard."""
    i, n = shard()
    for pos, item in enumerate(seq):
        if pos % n == i:
            yield pos, item


def label():
    i, n = shard()
    return "" if n == 1 else " [shard %d/%d]" % (i, n)
