"""DB-239 B-93: temporal closure of the B-route over the whole delivery window.

The first-principles workflow (wf_83703c10, 15 opus agents, 2026-08-10) killed
every competing route on this scene and left exactly one open risk: the B-route
mask thresholds a quantity db238 proved TRANSIENT (worst-pair residual moved
24.85 -> 56.75 -> 11.80 within 14 frames on this very log), so per-frame masks
may flicker in the 93-frame video that the human acceptance gate actually
watches.  This job renders all 93 frames and measures exactly that, plus the
two zero-cost riders that settle this week's remaining arguments:

  R2  mask/wedge saturation per pair (if ~1.0, every reroute-inside-the-wedge
      route is permanently dead)
  R3  min_island audit (real contradictions silently returned to KEEP)

Pre-registered gates (frozen before the run, from the workflow synthesis):
  PASS: masked_frac in [3%,15%] on all 93 frames; adjacent-frame ratio <= 1.5x;
        median flip rate <= 0.5% of domain, max <= 1.5%; blink pixels
        (duty < 20%) <= 10% of the cumulative mask; and the played-back clip
        shows no boiling seams to the human eye.
  FAIL -> hysteresis + k-of-n persistence + keep-islands, re-judge;
  still FAIL -> window-union mask, accept if <= 20% of domain;
  > 25% -> stop engineering, hand the number to koi.
Registered prediction to falsify: at least one frame exceeds 15%.

Runs anywhere the AV2 log dir exists (local or Colab); no GPU, no LiDAR - the
B-route never touches depth.  Domain here is self-defined (calibrated support
within |elevation| < 35 deg) rather than the v15 mask, because the v15 masks
live on Drive which the current network SNI-blocks; the k=37 frame is
additionally scored on a v15-derived domain when a reference image is given.
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "db238_screening"))
sys.path.insert(0, "/content")

import db238_screen as SC  # noqa: E402
import db239_seam_mask as SM  # noqa: E402

TAU = 16.0
ELEV_DEG = 35.0


def rot(cam, pose, imgs, ys, xs):
    X = 1e6 * SC.DIRS[ys, xs]
    px, py, ok = SC._project(pose[cam], X)
    v = np.zeros((len(ys), 3), np.float32)
    v[ok] = SM.bilinear_rgb(imgs[cam], px[ok], py[ok])
    return v, ok


def one_frame(log_dir, local_idx, cal, cte, elev_domain):
    import cv2
    man = SC.manifest_from_dir(log_dir, local_idx, 1)
    imgs = SC.load_images(log_dir, man["cam_ts"])
    pose = SM.emc_poses(cal, man["cam_ts"], man["anchor_ts"], cte)
    sup = SM.camera_support_emc(pose)
    face = np.full((SC.H, SC.W), -2.0, np.float32)
    own = np.full((SC.H, SC.W), -1, np.int16)
    for i, cam in enumerate(SC.CAMERAS):
        f = (SC.DIRS_FLAT @ pose[cam]["R"])[:, 2].reshape(SC.H, SC.W)
        u = sup[cam] & (f > face)
        face[u] = f[u]
        own[u] = i
    domain = (own >= 0) & elev_domain

    erp = np.zeros((SC.H, SC.W, 3), np.float32)
    for i, cam in enumerate(SC.CAMERAS):
        m = (own == i) & domain
        if not m.any():
            continue
        ys, xs = np.nonzero(m)
        v, ok = rot(cam, pose, imgs, ys, xs)
        erp[ys[ok], xs[ok]] = v[ok]

    D = np.zeros((SC.H, SC.W), np.float32)
    OV = np.zeros((SC.H, SC.W), bool)
    pair_stats = {}
    for a, b in SC.ADJACENT:
        m = sup[a] & sup[b] & domain
        if int(m.sum()) < 300:
            continue
        ys, xs = np.nonzero(m)
        va, oka = rot(a, pose, imgs, ys, xs)
        vb, okb = rot(b, pose, imgs, ys, xs)
        ok = oka & okb
        if int(ok.sum()) < 300:
            continue
        d = np.abs(SM.affine_match(va[ok], vb[ok]) - vb[ok]).mean(1)
        yy, xx = ys[ok], xs[ok]
        np.maximum.at(D, (yy, xx), d.astype(np.float32))
        OV[yy, xx] = True
        pair_stats["%s|%s" % (a, b)] = {
            "n": int(ok.sum()), "median": round(float(np.median(d)), 2),
            "p90": round(float(np.percentile(d, 90)), 2),
            "p99": round(float(np.percentile(d, 99)), 2)}

    # R3: what does min_island silently return to KEEP?
    Ds = cv2.medianBlur(D, 5)
    raw = ((Ds > TAU) & OV).astype(np.uint8)
    k7 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    closed = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, k7)
    n_cc, lab, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    dropped_n = dropped_area = 0
    for i in range(1, n_cc):
        if stats[i, cv2.CC_STAT_AREA] < 120:
            dropped_n += 1
            dropped_area += int(stats[i, cv2.CC_STAT_AREA])

    seam = SM.seam_mask(D, OV, TAU)
    out = erp.copy()
    out[seam] = 0
    keep = domain & ~seam

    # R2: per-pair saturation = masked width / wedge width, row-wise medians
    r2 = {}
    for a, b in SC.ADJACENT:
        wedge = sup[a] & sup[b] & domain
        if not wedge.any():
            continue
        rows = np.nonzero(wedge.any(1))[0]
        sat = []
        for y in rows[:: max(1, len(rows) // 60)]:
            ww = int(wedge[y].sum())
            if ww >= 8:
                sat.append(float((seam & wedge)[y].sum()) / ww)
        if sat:
            r2["%s|%s" % (a, b)] = round(float(np.median(sat)), 3)

    rec = {"masked_frac": round(float((seam & domain).sum() / max(domain.sum(), 1)), 5),
           "domain_px": int(domain.sum()), "pairs": pair_stats, "r2_saturation": r2,
           "r3_dropped_islands": {"n": dropped_n, "area_px": dropped_area}}
    return out, keep, seam, domain, rec


def main(log_dir, out_dir, first_anchor, n_frames=93, v15_ref=None):
    from PIL import Image
    import cv2
    os.makedirs(out_dir, exist_ok=True)
    for sub in ("frames", "masks", "seam"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
    cal = SC.load_calibration(log_dir)
    cte = SM.load_ego_interp(log_dir)
    elev = np.degrees(np.arcsin(np.clip(SC.DIRS[:, :, 2], -1, 1)))
    elev_domain = np.abs(elev) < ELEV_DEG

    recs, seams, prev = [], [], None
    duty = np.zeros((SC.H, SC.W), np.int32)
    flips = []
    t0 = time.time()
    for k in range(n_frames):
        out, keep, seam, domain, rec = one_frame(log_dir, first_anchor + k, cal, cte, elev_domain)
        rec["k"] = k
        Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(
            os.path.join(out_dir, "frames", "fr_%04d.png" % k))
        Image.fromarray((keep * 255).astype(np.uint8)).save(
            os.path.join(out_dir, "masks", "mk_%04d.png" % k))
        Image.fromarray((seam * 255).astype(np.uint8)).save(
            os.path.join(out_dir, "seam", "sm_%04d.png" % k))
        duty += seam.astype(np.int32)
        if prev is not None:
            flips.append(float(np.logical_xor(seam, prev).sum() / max(domain.sum(), 1)))
        prev = seam
        seams.append(seam)
        recs.append(rec)
        if k % 10 == 0 or k == n_frames - 1:
            print("k=%02d masked=%.2f%% dropped_islands=%d (%.0fs)" % (
                k, 100 * rec["masked_frac"], rec["r3_dropped_islands"]["n"],
                time.time() - t0), flush=True)

    mf = np.array([r["masked_frac"] for r in recs])
    ratios = np.maximum(mf[1:], 1e-9) / np.maximum(mf[:-1], 1e-9)
    ratios = np.maximum(ratios, 1.0 / ratios)
    union = np.any(seams, 0)
    ever = duty > 0
    blink = ever & (duty < 0.2 * n_frames)
    summary = {
        "gates": {"masked_frac_min": round(float(mf.min()), 5),
                  "masked_frac_max": round(float(mf.max()), 5),
                  "max_adjacent_ratio": round(float(ratios.max()), 3),
                  "flip_rate_median": round(float(np.median(flips)), 5),
                  "flip_rate_max": round(float(np.max(flips)), 5),
                  "blink_frac_of_cummask": round(float(blink.sum() / max(ever.sum(), 1)), 4),
                  "union_mask_frac_of_domain": round(float((union & (duty >= 0)).sum()
                                                          / max(recs[0]["domain_px"], 1)), 5)},
        "pass_rules": {"masked_frac": "[0.03, 0.15] every frame",
                       "adjacent_ratio": "<= 1.5", "flip_median": "<= 0.005",
                       "flip_max": "<= 0.015", "blink": "<= 0.10"},
        "frames": recs}
    if v15_ref and os.path.isfile(v15_ref):
        ref = np.asarray(Image.open(v15_ref).convert("RGB"))
        dom15 = ref.sum(2) > 12
        s37 = seams[37]
        summary["k37_on_v15_domain"] = round(float((s37 & dom15).sum() / max(dom15.sum(), 1)), 5)
    with open(os.path.join(out_dir, "broute_temporal.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print(json.dumps(summary["gates"], indent=1), flush=True)

    for name, red in (("clip_broute.mp4", False), ("clip_broute_maskred.mp4", True)):
        vw = cv2.VideoWriter(os.path.join(out_dir, name),
                             cv2.VideoWriter_fourcc(*"mp4v"), 10, (SC.W, SC.H))
        for k in range(n_frames):
            fr = np.asarray(Image.open(os.path.join(out_dir, "frames", "fr_%04d.png" % k)))
            if red:
                sm = np.asarray(Image.open(os.path.join(out_dir, "seam", "sm_%04d.png" % k))) > 127
                fr = fr.copy()
                fr[sm] = [255, 40, 40]
            vw.write(fr[:, :, ::-1])
        vw.release()
    print("DB239_B93_DONE", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0,
         int(sys.argv[4]) if len(sys.argv) > 4 else 93,
         sys.argv[5] if len(sys.argv) > 5 else None)
