"""Golden test: the rule mask must stay bit-identical to what koi signed off on.

koi approved `clip_broute_rulemask.mp4` (AV2 00a6ffc1, 2026-08-14).  The recipe
that built it was an ad-hoc heredoc that was never committed; it was recovered
from the session transcript on 2026-08-16.  This test is what keeps it recovered:
it pins the exact mask, so any future edit to the geometry chain that would have
silently changed what we ship fails here instead of in a delivered dataset.

It deliberately needs no images and no LiDAR.  The rule mask is a function of
calibration, ego pose and per-camera exposure timestamps only - which is also the
reason the recipe ports to datasets that have no LiDAR at all (Waymo E2E).  The
whole fixture is 217 KB, so this runs anywhere, offline, in seconds.

Run:  python agent/db241_multids_production/test_db241_golden_rule_mask.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
GOLD = os.path.join(HERE, "golden")
for _p in ("db238_screening", "db239_seam_mask", "db240_rule_dataset"):
    sys.path.insert(0, os.path.join(AGENT, _p))

import db238_screen as SC       # noqa: E402
import db239_seam_mask as SM    # noqa: E402
import db240_rule_dataset as DS  # noqa: E402

# What the approved run printed, kept as literals so a drift is legible as a
# number and not just as "the PNG differs".
APPROVED_WIDTHS = [58, 60, 61, 62, 63, 68, 68]
APPROVED_FRAC_OF_BAND = 0.2148       # the run itself printed 30.52%, which came
                                     # from dividing a band-rectangle numerator
                                     # by a rendered-domain denominator
FRAC_TOL = 0.0005


def _load_meta():
    with open(os.path.join(GOLD, "manifest_93.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _install_manifest_shim(meta):
    """Serve cam_ts from the fixture instead of listing 651 JPEGs.

    Production reads timestamps off the filenames; the fixture carries the same
    integers.  Patching here keeps the production path untouched by the test.
    """
    mans = meta["manifests"]
    original = SC.manifest_from_dir

    def shim(log_dir, anchor_idx, n_lidar=0, cameras=None):
        m = mans[str(anchor_idx)]
        return {"anchor_ts": m["anchor_ts"], "cam_ts": dict(m["cam_ts"])}

    SC.manifest_from_dir = shim
    return original


def main():
    meta = _load_meta()
    cams = meta["cameras"]
    restore = _install_manifest_shim(meta)
    try:
        from PIL import Image

        cal = SC.load_calibration(GOLD)
        cte = SM.load_ego_interp(GOLD)
        elev = np.degrees(np.arcsin(np.clip(SC.DIRS[:, :, 2], -1, 1)))
        band = np.abs(elev) < DS.ELEV_DEG

        m0 = SC.manifest_from_dir(GOLD, 0, 1)
        pose0 = SM.emc_poses({c: cal[c] for c in cams}, m0["cam_ts"],
                             m0["anchor_ts"], cte)
        sup0 = SM.camera_support_emc(pose0)
        pairs = DS.adjacent_pairs(cams, pose0, sup0)
        rect, stats = DS.rule_mask(GOLD, cal, cte, cams, pairs, band,
                                   n=meta["frames"])
        rect &= band

        approved = np.asarray(
            Image.open(os.path.join(GOLD, "seam_rule_mask_KOI_APPROVED.png"))
            .convert("L")) > 127
        measured = np.asarray(
            Image.open(os.path.join(GOLD, "seam_union_measured.png"))
            .convert("L")) > 127

        fails = []

        # 1. the ring must close for a 7-camera 360 rig
        if len(pairs) != len(cams):
            fails.append("ring did not close: %d pairs for %d cameras"
                         % (len(pairs), len(cams)))

        # 2. strip widths
        widths = sorted(v["strip_w"] for v in stats.values())
        if widths != APPROVED_WIDTHS:
            fails.append("strip widths %s != approved %s" % (widths, APPROVED_WIDTHS))

        # 3. the mask itself, pixel for pixel
        diff = int((rect ^ (approved & band)).sum())
        if diff:
            fails.append("mask differs from koi-approved by %d px" % diff)

        # 4. the invariant that makes "blanket" mean something: the rule mask
        #    must swallow every pixel the measured 93-frame disagreement found
        uncovered = int((measured & ~rect).sum())
        if uncovered:
            fails.append("%d measured-contradiction px fall OUTSIDE the rule mask "
                         "- the blanket is not blanket" % uncovered)

        # 5. area, so a silent widening is caught even while it stays a superset
        frac = float(rect.sum()) / max(int(band.sum()), 1)
        if abs(frac - APPROVED_FRAC_OF_BAND) > FRAC_TOL:
            fails.append("mask area %.4f of band drifted from approved %.4f"
                         % (frac, APPROVED_FRAC_OF_BAND))

        print("cameras            : %d  (ring closed: %s)" % (len(cams), len(pairs) == len(cams)))
        print("strip widths       : %s" % widths)
        print("mask vs approved   : %d px differ" % diff)
        print("uncovered measured : %d px" % uncovered)
        print("mask area          : %.4f of band" % frac)

        if fails:
            print("\nFAIL")
            for f in fails:
                print("  - %s" % f)
            return 1
        print("\nPASS - rule mask is bit-identical to the version koi approved")
        return 0
    finally:
        SC.manifest_from_dir = restore


if __name__ == "__main__":
    raise SystemExit(main())
