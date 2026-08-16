"""DB-239 B-93 hardening - the pre-registered fallback ladder, step 1.

The plain per-frame mask passed the area gates (5.7-9.9% vs [3,15]% budget,
adjacent ratio 1.19 vs 1.5) but failed the temporal ones (flip median 1.99% vs
0.5%, blink 27.3% vs 10%).  This applies exactly the hardening registered
BEFORE that run:

    hysteresis          tau_hi = 16 triggers, tau_lo = 11.2 sustains
    k-of-n persistence  mask only where the lo-threshold fires now AND the
                        hi-threshold fires in >= 2 of the 5 frames centred here
    min_island          120 -> keep everything (R3 measured 42-88 real
                        contradiction islands per frame being silently dropped)

All three are one-way KEEP -> RECONSTRUCT; no pixel is ever un-masked relative
to the contract's meaning, and the RGB itself is untouched.  Re-judged on the
same gates.  If this still fails, the registered landing zone is the
window-union mask (measured 16.4% <= 20% budget, zero flicker by construction).
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

import db238_screen as SC  # noqa: E402
import db239_seam_mask as SM  # noqa: E402
from db239_broute_temporal import rot, ELEV_DEG  # noqa: E402

TAU_HI, TAU_LO = 16.0, 11.2
K_WIN, K_NEED = 2, 2          # +/-2 frames, >=2 hi hits


def d_field(log_dir, local_idx, cal, cte, elev_domain, raw_path=None):
    """Ds/OV/domain for one frame; optionally save the UNMASKED render.

    The plain-run frames on disk already carry the per-frame mask baked into
    the RGB, so re-masking them would ship the union of both masks and keep the
    very flicker the hardening removes.  The hardened product must be built
    from an unmasked render.
    """
    import cv2
    from PIL import Image
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
    if raw_path is not None:
        erp = np.zeros((SC.H, SC.W, 3), np.float32)
        for i, cam in enumerate(SC.CAMERAS):
            m = (own == i) & domain
            if not m.any():
                continue
            ys, xs = np.nonzero(m)
            v, ok = rot(cam, pose, imgs, ys, xs)
            erp[ys[ok], xs[ok]] = v[ok]
        Image.fromarray(np.clip(erp, 0, 255).astype(np.uint8)).save(raw_path)
    D = np.zeros((SC.H, SC.W), np.float32)
    OV = np.zeros((SC.H, SC.W), bool)
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
    return cv2.medianBlur(D, 5), OV, domain


def morph(bad):
    import cv2
    k7 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    b = cv2.morphologyEx(bad.astype(np.uint8), cv2.MORPH_CLOSE, k7)
    k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    return cv2.dilate(b, k3).astype(bool)          # grow 3 px radius, keep all islands


def main(log_dir, out_dir, n_frames=93):
    from PIL import Image
    import cv2
    os.makedirs(os.path.join(out_dir, "seam_hard"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "masks_hard"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "frames_raw"), exist_ok=True)
    cal = SC.load_calibration(log_dir)
    cte = SM.load_ego_interp(log_dir)
    elev = np.degrees(np.arcsin(np.clip(SC.DIRS[:, :, 2], -1, 1)))
    elev_domain = np.abs(elev) < ELEV_DEG

    t0 = time.time()
    HI = np.zeros((n_frames, SC.H, SC.W), bool)
    LO = np.zeros((n_frames, SC.H, SC.W), bool)
    DOM = None
    for k in range(n_frames):
        Ds, OV, domain = d_field(log_dir, k, cal, cte, elev_domain,
                                 raw_path=os.path.join(out_dir, "frames_raw",
                                                       "fr_%04d.png" % k))
        HI[k] = (Ds > TAU_HI) & OV
        LO[k] = (Ds > TAU_LO) & OV
        if DOM is None:
            DOM = domain
        if k % 20 == 0:
            print("D fields k=%d (%.0fs)" % (k, time.time() - t0), flush=True)

    seams, flips, prev = [], [], None
    duty = np.zeros((SC.H, SC.W), np.int32)
    mf = []
    for k in range(n_frames):
        lo_, hi_ = k - K_WIN, k + K_WIN + 1
        hi_count = HI[max(0, lo_):min(n_frames, hi_)].sum(0)
        seam = morph(LO[k] & (hi_count >= K_NEED))
        seams.append(seam)
        mf.append(float((seam & DOM).sum() / max(DOM.sum(), 1)))
        duty += seam.astype(np.int32)
        if prev is not None:
            flips.append(float(np.logical_xor(seam, prev).sum() / max(DOM.sum(), 1)))
        prev = seam
        Image.fromarray((seam * 255).astype(np.uint8)).save(
            os.path.join(out_dir, "seam_hard", "sm_%04d.png" % k))

    mf = np.array(mf)
    ratios = np.maximum(mf[1:], 1e-9) / np.maximum(mf[:-1], 1e-9)
    ratios = np.maximum(ratios, 1.0 / ratios)
    ever = duty > 0
    blink = ever & (duty < 0.2 * n_frames)
    union = np.any(seams, 0)
    gates = {"masked_frac_min": round(float(mf.min()), 5),
             "masked_frac_max": round(float(mf.max()), 5),
             "max_adjacent_ratio": round(float(ratios.max()), 3),
             "flip_rate_median": round(float(np.median(flips)), 5),
             "flip_rate_max": round(float(np.max(flips)), 5),
             "blink_frac_of_cummask": round(float(blink.sum() / max(ever.sum(), 1)), 4),
             "union_mask_frac_of_domain": round(float(union.sum() / max(DOM.sum(), 1)), 5)}
    verdict = {"masked_frac": bool(0.03 <= mf.min() and mf.max() <= 0.15),
               "adjacent_ratio": bool(ratios.max() <= 1.5),
               "flip_median": bool(np.median(flips) <= 0.005),
               "flip_max": bool(np.max(flips) <= 0.015),
               "blink": bool(blink.sum() / max(ever.sum(), 1) <= 0.10)}
    out = {"gates": gates, "verdict": verdict, "all_pass": all(verdict.values())}
    with open(os.path.join(out_dir, "b93_harden.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out, indent=1), flush=True)

    # rebuild the masked frames + clips with the hardened mask
    vw1 = cv2.VideoWriter(os.path.join(out_dir, "clip_broute_hard.mp4"),
                          cv2.VideoWriter_fourcc(*"mp4v"), 10, (SC.W, SC.H))
    vw2 = cv2.VideoWriter(os.path.join(out_dir, "clip_broute_hard_maskred.mp4"),
                          cv2.VideoWriter_fourcc(*"mp4v"), 10, (SC.W, SC.H))
    for k in range(n_frames):
        fr = np.asarray(Image.open(os.path.join(out_dir, "frames_raw",
                                                "fr_%04d.png" % k))).copy()
        s = seams[k]
        fr2 = fr.copy()
        fr2[s] = 0
        keep = DOM & ~s
        Image.fromarray((keep * 255).astype(np.uint8)).save(
            os.path.join(out_dir, "masks_hard", "mk_%04d.png" % k))
        vw1.write(fr2[:, :, ::-1])
        red = fr.copy()
        red[s] = [255, 40, 40]
        vw2.write(red[:, :, ::-1])
    vw1.release()
    vw2.release()
    print("DB239_B93_HARDEN_DONE", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 93)
