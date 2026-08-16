"""Delete source logs whose sample is finished and validated.

The raw AV2 ring JPEGs are ~250 MB per log and the pseudo-AV2 trees another few
hundred MB each; at the production target they add up to more than the dataset
itself. They are inputs, not outputs - once a sample exists with a manifest and
its 93 frame/mask pairs, the log has nothing left to give.

Deletes only when all three hold, so a half-produced sample can still be redone:
  - the sample directory has a manifest.json
  - it has 93 frames and 93 masks
  - the manifest says the sample was accepted

Dry-run by default. Pass --apply to actually remove.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys

OUT = r"E:/w2p_data/dataset_out"
SOURCES = {
    "argoverse2": (r"E:/w2p_data/av2", lambda sid, d: d.startswith(sid)),
    "nuscenes": (r"E:/w2p_data/nuscenes/pseudo_cams", lambda sid, d: d == "nusc_" + sid),
    "waymo_perception": (r"E:/w2p_data/waymo_percep/pseudo_av2",
                         lambda sid, d: d.endswith(sid[:20])),
    "waymo_e2e": (r"E:/w2p_data/waymo_e2e/pseudo_av2", lambda sid, d: d == "e2e_" + sid),
}


def finished(sample_dir):
    mp = os.path.join(sample_dir, "manifest.json")
    if not os.path.isfile(mp):
        return False
    try:
        with open(mp, encoding="utf-8") as fh:
            m = json.load(fh)
    except Exception:
        return False
    if not m.get("accepted"):
        return False
    return (len(glob.glob(os.path.join(sample_dir, "frames", "fr_*.png"))) == 93
            and len(glob.glob(os.path.join(sample_dir, "masks", "mk_*.png"))) == 93)


def main():
    apply = "--apply" in sys.argv
    freed = 0
    for ds, (root, match) in SOURCES.items():
        if not os.path.isdir(root):
            continue
        done = {s for s in os.listdir(os.path.join(OUT, ds))
                if finished(os.path.join(OUT, ds, s))} if os.path.isdir(
                    os.path.join(OUT, ds)) else set()
        for d in os.listdir(root):
            p = os.path.join(root, d)
            if not os.path.isdir(p):
                continue
            if not any(match(sid, d) for sid in done):
                continue
            sz = sum(os.path.getsize(os.path.join(r, f))
                     for r, _, fs in os.walk(p) for f in fs)
            freed += sz
            print("  %-18s %-40s %6.0f MB%s" % (ds, d, sz / 1e6,
                                                "" if apply else "  (dry-run)"))
            if apply:
                shutil.rmtree(p, ignore_errors=True)
    print("%s %.1f GB" % ("freed" if apply else "would free", freed / 1e9))


if __name__ == "__main__":
    main()
