"""Scene planning: which scenes each source contributes, and their split.

Enumeration lives in `db270_catalog`; this module does one job on top of it —
decide, before any byte moves, which half each scene lands in.

Split is decided HERE, once, deterministically, so a resumed or re-sharded run
puts the same scene in the same half. The rule is the 0814 contract: quota per
source, train/test inside each non-OOD source at config.train_frac, and the OOD
source held out whole.

Assignment is by hash of the scene id, not by arrival order — order depends on
which download finished first, and that would make the split irreproducible.

`want` is a QUOTA, not a batch size: re-running tops a source up to it instead
of planning another full `want` on top of what already landed.
"""
from __future__ import annotations

import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _split_of(source, sid, cfg):
    if source == cfg["ood_holdout"]:
        return "held_out_ood"
    h = int(hashlib.sha1(("%s/%s" % (source, sid)).encode()).hexdigest()[:8], 16)
    return "train" if (h % 1000) < cfg["train_frac"] * 1000 else "test"


def _hood_of(source, sid):
    """~50/50 per source, deterministic. Only argoverse2 can actually differ;
    the other rigs' cameras never see their own body, so a 'black' label there
    would name a file identical to 'keep' - they stay keep."""
    if source != "argoverse2":
        return "keep"
    h = int(hashlib.sha1(("hood/%s" % sid).encode()).hexdigest()[:8], 16)
    return "black" if h % 2 else "keep"


_ARCH = [None]      # archive_root, set once by plan()


def _done(root, source, sid):
    for sp in ("train", "test", "held_out_ood"):
        if os.path.isfile(os.path.join(root, "data", "samples", sp, source, sid,
                                       "manifest.json")):
            return True
    # The Drive archive outlives the VM and is shared across boxes, so a scene
    # any box ever finished is done here too - that is what makes a fresh VM
    # resume instead of restart, and what stops two boxes double-producing.
    if _ARCH[0]:
        import db270_archive as AR
        return AR.is_done(_ARCH[0], source, sid)
    return False


def _have(root, source):
    """Samples of this source already produced - local disk OR Drive archive."""
    seen = set()
    for sp in ("train", "test", "held_out_ood"):
        base = os.path.join(root, "data", "samples", sp, source)
        if os.path.isdir(base):
            seen.update(sid for sid in os.listdir(base) if os.path.isfile(
                os.path.join(base, sid, "manifest.json")))
        if _ARCH[0]:
            d = os.path.join(_ARCH[0], sp, source)
            if os.path.isdir(d):
                seen.update(f[:-4] for f in os.listdir(d) if f.endswith(".tgz"))
    return len(seen)


def plan(source, want, root, cfg, verbose=True, nuscenes_max_new=None,
         nuscenes_shard=(0, 1)):
    import db270_catalog as C

    _ARCH[0] = cfg.get("archive_root") or None
    want = max(0, want - _have(root, source))
    if want == 0:
        return []
    # nuScenes must open ~16.5 GB shards to enumerate at all, so it is told the
    # target; the other three enumerate for free and are simply truncated.
    # nuscenes_shard partitions shards across boxes - see C.nuscenes's docstring
    # for why sharing shard order across the fleet wastes 5x the download.
    if source == "nuscenes":
        pool = C.nuscenes(root, want=want, verbose=verbose,
                          max_new=nuscenes_max_new,
                          shard_i=nuscenes_shard[0], shard_n=nuscenes_shard[1])
    else:
        pool = C.catalog(source, root)
    jobs = []
    for j in pool:
        if _done(root, source, j["scene"]):
            continue
        j = dict(j)
        j["split"] = _split_of(source, j["scene"], cfg)
        j["hood"] = _hood_of(source, j["scene"])
        jobs.append(j)
        if len(jobs) >= want:
            break
    return jobs
