"""Build one fully-traceable sample per dataset for koi to review the construction.

Each entry carries the finished clip, the 93 frame/mask pairs, AND the raw camera
images that produced them, so the whole chain source -> ERP -> mask is inspectable
without re-running anything.

Naming: <dataset>__<split>__<original source id>. The source id is the dataset's
own identifier, not ours, so koi can look the scene up in the original release.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import sys

A = r"D:\BaiduSyncdisk\2024 to future\koi chen\w2p-db236\agent"
sys.path.insert(0, os.path.join(A, "db241_multids_production"))
sys.path.insert(0, os.path.join(A, "db238_screening"))
sys.path.insert(0, os.path.join(A, "db239_seam_mask"))
sys.path.insert(0, os.path.join(A, "db240_rule_dataset"))
sys.path.insert(0, os.path.join(os.path.dirname(A), "code"))

import db241_driver as D          # noqa: E402
import db240_rule_dataset as DS   # noqa: E402
import db238_screen as SC         # noqa: E402

OUT = r"E:/w2p_data/koi_review_2026-08-16"
WORK = r"E:/w2p_data/_review_work"


def provenance(dataset, split, source_id, log_dir, man, extra=None):
    cams = DS.present_cameras(log_dir)
    m = SC.manifest_from_dir(log_dir, man["window"][0], 0, cams)
    lines = [
        "dataset          : %s" % dataset,
        "official split   : %s" % split,
        "SOURCE ID        : %s" % source_id,
        "   (this is the id in the original release, not ours - look it up there)",
        "",
        "window           : source frame index %d .. %d, %d frames"
        % (man["window"][0], man["window"][1], man["frames"]),
        "anchor timestamp : %s ns" % m["anchor_ts"],
        "cameras used     : %d  %s" % (man["n_cameras"], ", ".join(cams)),
        "camera ring      : %s" % ("closed" if man["ring_closed"]
                                   else "OPEN - this rig has no rear camera"),
        "",
        "frame spacing    : %.1f ms median, jitter cv %.3f"
        % (man.get("frame_gap_ms", 0), man.get("frame_gap_cv", 0)),
        "seam strips      : %s px" % sorted(v.get("strip_w", 0)
                                            for v in man["pairs"].values()),
        "seam mask        : %.1f%% of the band" % (100 * man["rule_mask_frac_of_band"]),
        "hood variant     : %s (mask applied: %s)"
        % (man.get("hood_variant"), man.get("hood_mask_applied")),
        "",
        "KEEP integrity   : keep_px_not_written = %d  (must be 0: every white mask"
        % man.get("keep_px_not_written", -1),
        "                   pixel carries a real sampled camera pixel)",
        "dark-scene px    : %d  (NOT a defect - night scenes are genuinely black)"
        % man.get("keep_px_dark_scene", 0),
        "",
        "projection       : rotation-only resample of one raw JPEG per pixel",
        "                   (DB-239 B-route: warp is identically zero)",
    ]
    if extra:
        lines += ["", "notes:"] + ["  " + x for x in extra]
    return "\n".join(lines) + "\n"


def package(dataset, split, source_id, log_dir, sample_dir, extra=None):
    with open(os.path.join(sample_dir, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    name = "%s__%s__%s" % (dataset, split, source_id)
    dst = os.path.join(OUT, name)
    os.makedirs(dst, exist_ok=True)

    for sub in ("frames", "masks"):
        s = os.path.join(sample_dir, sub)
        d = os.path.join(dst, sub)
        os.makedirs(d, exist_ok=True)
        for f in sorted(os.listdir(s)):
            if not os.path.exists(os.path.join(d, f)):
                try:
                    os.link(os.path.join(s, f), os.path.join(d, f))
                except OSError:
                    shutil.copyfile(os.path.join(s, f), os.path.join(d, f))
    for f in ("clip.mp4", "rule_mask.png", "manifest.json"):
        p = os.path.join(sample_dir, f)
        if os.path.isfile(p) and not os.path.isfile(os.path.join(dst, f)):
            shutil.copyfile(p, os.path.join(dst, f))

    # the raw camera images that actually went in, one folder per camera
    cams = DS.present_cameras(log_dir)
    idx = DS.uniform_time_indices(log_dir, cams, man["window"][0], man["frames"])
    if idx is None:
        idx = list(range(man["window"][0], man["window"][0] + man["frames"]))
    src_root = os.path.join(dst, "source_frames")
    n_src = 0
    for k in idx:
        m = SC.manifest_from_dir(log_dir, k, 0, cams)
        for cam in cams:
            sp = os.path.join(log_dir, "sensors", "cameras", cam,
                              "%d.jpg" % m["cam_ts"][cam])
            if not os.path.isfile(sp):
                continue
            cd = os.path.join(src_root, cam)
            os.makedirs(cd, exist_ok=True)
            dp = os.path.join(cd, os.path.basename(sp))
            if not os.path.exists(dp):
                try:
                    os.link(sp, dp)
                except OSError:
                    shutil.copyfile(sp, dp)
            n_src += 1

    with open(os.path.join(dst, "SOURCE.txt"), "w", encoding="utf-8") as fh:
        fh.write(provenance(dataset, split, source_id, log_dir, man, extra))
    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(dst) for f in fs) / 1e6
    print("  %-58s %3d src imgs  %.0f MB" % (name, n_src, size), flush=True)
    return name, man, n_src


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    print("packaging into", OUT)
