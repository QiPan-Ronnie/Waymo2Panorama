"""Render the KEEP twin of every argoverse2 scene shipped as hood-BLACK.

Completes the A/B set. db270_hood_pairs handles the cheap direction (keep ->
black is post-processing); this handles the expensive one, which needs an
actual re-render because a black sample's hood pixels were destroyed at
production time and the npz backup died with its VM.

Cost, measured rather than assumed: argoverse2 is the per-FILE S3 source, and a
box sustains ~250 samples/hour on it, so ~476 scenes across the fleet is 1-2
hours - not the tens of hours a per-scene-minutes estimate suggests.

Mechanics: reuse run_all's own stages, with two overrides.
  hood = "keep"           -> stage_gpu skips db267's blackening
  archive_root -> _hood_pairs  -> the primary 3652-sample delivery is untouched

    python3 pipeline/db270_hood_rerender.py --shard 0/5 [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=0)
    a = ap.parse_args()

    import run_all as RA
    import plan_jobs as PJ
    import db270_catalog as C

    ROOT = RA.ROOT
    CFG = RA.CFG
    primary = CFG["archive_root"]
    pairs = primary.rstrip("/\\") + "_hood_pairs"

    # Scenes shipped as BLACK in the primary archive are exactly the ones
    # missing a keep twin. Read the archive rather than recomputing quotas: it
    # is the durable truth about what was actually delivered.
    black = []
    for split in ("train", "test"):
        d = os.path.join(primary, split, "argoverse2")
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".tgz") or "(" in f:
                continue
            scene = f[:-4]
            if PJ._hood_of("argoverse2", scene) == "black":
                black.append(scene)
    want = set(black)

    # already-rendered twins are skipped, so this is resumable
    have = set()
    for split in ("train", "test"):
        d = os.path.join(pairs, split, "argoverse2")
        if os.path.isdir(d):
            have |= {f[:-4] for f in os.listdir(d) if f.endswith(".tgz")}
    todo_scenes = [s for s in black if s not in have]

    pool = {j["scene"]: j for j in C.catalog("argoverse2", ROOT)}
    jobs = []
    for s in todo_scenes:
        j = pool.get(s)
        if not j:
            continue
        j = dict(j)
        j["split"] = PJ._split_of("argoverse2", s, CFG)
        j["hood"] = "keep"          # the whole point
        jobs.append(j)

    if a.shard:
        i, n = (int(v) for v in a.shard.split("/"))
        jobs = jobs[i::n]
    if a.limit:
        jobs = jobs[:a.limit]

    print("black scenes in primary: %d | twins already rendered: %d | "
          "this shard will render: %d" % (len(want), len(have), len(jobs)),
          flush=True)
    if not jobs:
        print("nothing to do", flush=True)
        return 0

    shape = RA.box_shape()
    if a.workers:
        shape["gpu_workers"] = a.workers
    print("box: %d CPU -> %d GPU workers, %d CPU workers"
          % (shape["cpu"], shape["gpu_workers"], shape["cpu_workers"]),
          flush=True)

    # Redirect archiving. stage_gpu reads CFG at call time, so this is enough
    # to keep every produced sample out of the primary delivery.
    CFG["archive_root"] = pairs
    d = RA.run(jobs, shape)
    print("FINISHED ok=%d fail=%d" % (d["ok"], d["fail"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
