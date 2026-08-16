"""DB-239 A/B: is BAND_RULE5 pure-angular hole fill what duplicates the pedestrian?

Evidence so far, all from outside the renderer:

  * the duplicated bald man on 00a6ffc1 fr_0037 (anchor 100) sits in a region
    that is 92% `ring_side_right` territory - a cross-camera seam cannot reach
    it, and the raw side_right frame contains exactly one man;
  * a plain single-camera resample of the same rays, with production EMC poses
    and production's DEPTH_SEAMRAMP depth, shows one head;
  * 14.7% of the pixels on his body are better explained by a ROTATION-ONLY
    sample than by the metric projection, in thin vertical slivers - the shape
    of disocclusion holes behind a vertical silhouette;
  * that rotation-only sample is displaced by |t_cam - C| / Z = 0.378 / 6.25
    = 60.5 mrad = 19.7 ERP px, matching the observed offset of the second crown;
  * `BAND_RULE5_ORTHOGONAL` and `BAND_RULE5_PURE_ANGULAR` are True production
    defaults (db89_ghost_recovery.py:107-108), and `db144_v15.py` never turns
    them off - so every one of the 555 delivered v15 samples carries them.

This job settles it from inside.  Both arms use v15's own band settings; the
ONLY difference is Rule-5.  Arm A must reproduce the shipped defect, or the
hypothesis is wrong and gets recorded as wrong.

Arm B is also, independently, exactly what koi asked for on 2026-08-07 (00:44:21):
"你不用任何手法, 纯投影拼接, 具体内容不改变."  Rule-5 angular hole fill IS a
手法 - it paints pixels the metric projection abstained from.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, "/content")

try:
    from agent.db236_scene_band import db236_phase0_provenance_job as phase0
except ImportError:  # pragma: no cover - deployed flat under /content
    import db236_phase0_provenance_job as phase0

UUID = "00a6ffc1-6ce9-3bc3-a060-6006e9893a1a"
ANCHORS = [100, 95]          # 100 = the user-marked frame (v15 fr_0037); 95 = control
DRIVE_ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/"
                  "db239_seam_mask/rule5_ab_00a6ffc1")

# v15's own band settings (db144_v15.py `extra_bg`), so arm A reproduces what
# actually shipped.  EGO_IMG_MASK is cleared because the analytic ego mask is a
# per-run artifact and only affects the ego-vehicle footprint at the frame
# bottom, far from the pedestrian; it is identical in both arms either way.
V15_BAND = [
    ['GROUND_MODE = "fill"', 'GROUND_MODE = "off"'],
    ['ANNOTATION_POLICY = "composite"', 'ANNOTATION_POLICY = "raw_sensor"'],
    ["EGO_BLACK = False", "EGO_BLACK = True"],
    ['EGO_IMG_MASK = "/content/egomask_cur.npz"', 'EGO_IMG_MASK = ""'],
    ["SCENE_BAND_PROVENANCE = False", "SCENE_BAND_PROVENANCE = True"],
    ["EMC_RENDER = True", "EMC_RENDER = False"],
]
RULE5_OFF = [
    ["BAND_RULE5_ORTHOGONAL = True", "BAND_RULE5_ORTHOGONAL = False"],
    ["BAND_RULE5_PURE_ANGULAR = True", "BAND_RULE5_PURE_ANGULAR = False"],
]

ARMS = [
    ("A_rule5_on", V15_BAND, "reproduces v15 as shipped"),
    ("B_rule5_off", V15_BAND + RULE5_OFF, "koi's pure projection, no 手法"),
]


def run_arm(name, replacements, note):
    root = Path("/content/db239_rule5_ab_%s" % name)
    tag = "db239_%s" % name
    phase0.ROOT = str(root)
    phase0.DRIVE_ROOT = str(DRIVE_ROOT / name)
    phase0.EXTRA = json.dumps(replacements)
    root.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    _, process, log_handle = phase0._start(UUID, ANCHORS, tag)
    rc = int(process.wait())
    log_handle.close()
    log_path = root / ("%s.log" % tag)
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:] \
        if log_path.is_file() else ""
    produced = sorted(p.name for p in (root / tag).glob("*_segcomposite.png")) \
        if (root / tag).is_dir() else []
    out = {"arm": name, "note": note, "rc": rc, "runtime_s": round(time.time() - t0, 1),
           "segcomposite": produced}
    if rc != 0:
        out["log_tail"] = tail
    else:
        dest = DRIVE_ROOT / name
        dest.mkdir(parents=True, exist_ok=True)
        for pattern in ("*_segcomposite.png", "*_scene_band_owner.png",
                        "*_scene_band_support.png", "*_scene_band_failure.png",
                        "*_scene_band_provenance.json"):
            for p in (root / tag).glob(pattern):
                shutil.copy2(p, dest / p.name)
        shutil.copy2(log_path, dest / log_path.name)
    print("DB239_ARM_DONE " + json.dumps(out), flush=True)
    return out


def main():
    phase0.ensure_s5cmd()
    phase0._localize(UUID)
    DRIVE_ROOT.mkdir(parents=True, exist_ok=True)
    results = [run_arm(*a) for a in ARMS]
    summary = {"schema_version": "db239.rule5_ab.v1", "uuid": UUID,
               "anchors": ANCHORS, "arms": results,
               "single_variable": "BAND_RULE5_ORTHOGONAL + BAND_RULE5_PURE_ANGULAR"}
    (DRIVE_ROOT / "summary.json").write_text(json.dumps(summary, indent=2),
                                             encoding="utf-8")
    print("DB239_RULE5_AB_DONE " + json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
