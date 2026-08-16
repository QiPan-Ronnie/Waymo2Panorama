"""Convert every nuScenes scene present locally and produce its DB-241 sample.

Hood variant alternates by scene index, which is how koi asked for it on 08-14:
"you don't give the same scene two masks - pick one" and roughly 50/50 across
scenes, so the model sees both a hooded and an unhooded street without ever
seeing the same street twice.
"""
from __future__ import annotations

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "code"))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, HERE)

from db181_multids_snapshot.nuscenes_adapter import convert_nuscenes_scene  # noqa: E402
import db241_driver as D  # noqa: E402

NUSC = r"E:/w2p_data/nuscenes"
OUT = r"E:/w2p_data/dataset_out"
PSEUDO = os.path.join(NUSC, "pseudo_av2")
COMMIT = "cb39ff6"
STAMP = datetime.datetime(2026, 8, 16, 5, 0, 0).isoformat() + "Z"


def main():
    meta = os.path.join(NUSC, "v1.0-mini")
    scenes = json.load(open(os.path.join(meta, "scene.json")))
    results = []
    for i, sc in enumerate(scenes):
        name, tok = sc["name"], sc["token"]
        log_id = "nusc_" + name
        log_dir = os.path.join(PSEUDO, log_id)
        try:
            if not os.path.isdir(log_dir):
                convert_nuscenes_scene(
                    source_root=NUSC, metadata_root=meta, scene_id=tok,
                    output_root=PSEUDO, output_log_id=log_id, mode="B",
                    converter_git_commit=COMMIT, created_at=STAMP)
            hood = "keep" if i % 2 == 0 else "black"
            man = D.build_sample(log_dir, OUT, "nuscenes", name, 0, 93, hood)
            results.append(man)
            print(D.summarise(man), flush=True)
        except Exception as exc:                       # noqa: BLE001
            print("%-14s FAILED  %s: %s" % (name, type(exc).__name__, str(exc)[:160]),
                  flush=True)
    ok = [m for m in results if m["accepted"]]
    print("\nnuScenes: %d/%d accepted" % (len(ok), len(scenes)))
    if ok:
        fr = [100 * m["rule_mask_frac_of_band"] for m in ok]
        print("mask %% of band: min %.1f  med %.1f  max %.1f"
              % (min(fr), sorted(fr)[len(fr) // 2], max(fr)))
        print("hood variants: keep=%d black=%d"
              % (sum(m["hood_variant"] == "keep" for m in ok),
                 sum(m["hood_variant"] == "black" for m in ok)))


if __name__ == "__main__":
    main()
