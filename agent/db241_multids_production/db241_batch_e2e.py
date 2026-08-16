"""Produce Waymo E2E samples from the range index.

For each chosen segment: pull only its own records (~190 MB) out of the shuffled
shards, write them as a local tfrecord, convert to a pseudo-AV2 log, produce the
sample, then delete the intermediate.  Downloading the split to do this would
cost 230 GB; this costs about what the sample itself is worth.

E2E-specific handling, all of it already encoded in the driver profile:
  - per-camera timestamps are zeroed, so EMC degenerates to a static rig and the
    seam strips get 6 px of skirt instead of 4 (measured on AV2: static strips
    run <=2 px narrow and leave 9 px of contradiction uncovered)
  - lens distortion k=0.0736 displaces an edge pixel ~6.8 px, more than the
    skirt, so the converter rectifies before writing
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db241_e2e_index as E  # noqa: E402
import db241_waymo_tfrecord as W  # noqa: E402
import db241_driver as D  # noqa: E402

PLAN = r"E:/w2p_data/waymo_e2e/plan.json"
TMP = r"E:/w2p_data/waymo_e2e/_seg.tfrecord"
PSEUDO = r"E:/w2p_data/waymo_e2e/pseudo_av2"
OUT = r"E:/w2p_data/dataset_out"


def main():
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    with open(PLAN, encoding="utf-8") as fh:
        plan = json.load(fh)
    keys = sorted(plan)
    print("producible segments: %d, taking %d" % (len(keys), want), flush=True)
    os.makedirs(PSEUDO, exist_ok=True)
    done = 0
    for key in keys:
        if done >= want:
            break
        sid = key[:16]
        if os.path.isfile(os.path.join(OUT, "waymo_e2e", sid, "manifest.json")):
            continue
        entries = [tuple(e) for e in plan[key]]
        try:
            mb = E.fetch_records(entries, TMP) / 1e6
            log = os.path.join(PSEUDO, "e2e_" + sid)
            if not os.path.isdir(log):
                W.convert(TMP, log, e2e=True, max_frames=93)
            hood = "keep" if done % 2 == 0 else "black"
            m = D.build_sample(log, OUT, "waymo_e2e", sid, 0, 93, hood)
            done += 1
            print("[%d/%d] %s  %.0f MB  %s" % (done, want, sid, mb, D.summarise(m)),
                  flush=True)
        except Exception as exc:                       # noqa: BLE001
            print("  skip %s: %s: %s" % (sid, type(exc).__name__, str(exc)[:150]),
                  flush=True)
        finally:
            if os.path.isfile(TMP):
                os.remove(TMP)
    print("done: %d E2E samples" % done, flush=True)


if __name__ == "__main__":
    main()
