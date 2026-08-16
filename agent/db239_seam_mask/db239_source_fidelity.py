"""DB-239 source fidelity - is every delivered pixel actually a sensor pixel?

The seam-disagreement mask (db239_seam_mask) only looks where two cameras
overlap.  Measurement on 00a6ffc1 a100 showed that is not enough: the duplicated
pedestrian sits at ERP x~1630-1730, and the source map there is 92% one camera
(`ring_side_right`).  A cross-camera criterion cannot see a defect that lives
inside a single camera's territory, so a mask built only from it would ship the
duplicated person as if it were real.

This module asks the stronger and mechanism-independent question:

    for this delivered pixel, does ANY raw ring image, along this ray, at ANY
    plausible depth, contain this value?

If yes, the pixel is a real observation - possibly placed at the wrong azimuth,
but not invented.  If no, the renderer produced something no sensor recorded:
a blend of two positions, a ghost, a smear, a duplicated body.  That is exactly
the class koi asked to be masked rather than shipped:

    "我宁愿你把部分的资讯选择 mask 掉或挖掉, 但你保留下来是很正确的 GT,
     而不是说乱拼."  (2026-08-07, 00:41:36)

Sweeping depth is what makes this a fidelity test rather than a geometry test.
A wrong depth moves a pixel along its ray; if the value still appears somewhere
in the sweep, the content is genuine and only mis-placed.  Only content that
matches nothing is flagged.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/content")

import db238_screen as SC  # noqa: E402
import db239_seam_mask as SM  # noqa: E402

H, W = SC.H, SC.W
# geometric sweep from the near clip to effectively infinite; log-spaced because
# image displacement is linear in inverse depth, so this is uniform in the
# quantity that actually matters
DEPTH_SWEEP = (2.5, 3.2, 4.0, 5.0, 6.3, 8.0, 10.0, 13.0, 17.0, 22.0, 30.0,
               45.0, 70.0, 10000.0)


def per_camera_gain(delivered, imgs, pose, sup, Zd, own, min_px=2000):
    """Per-camera, per-channel affine correction between raw and delivered.

    Production applies exposure gains before writing the ERP, so a raw-vs-
    delivered difference would otherwise be dominated by photometry.  The fit is
    done on each camera's own uncontested interior, where the mapping should be
    exactly the gain and nothing else.
    """
    out = {}
    for ci, cam in enumerate(SC.CAMERAS):
        m = (own == ci) & sup[cam]
        ys, xs = np.nonzero(m)
        if len(ys) < min_px:
            out[cam] = (np.ones(3), np.zeros(3))
            continue
        if len(ys) > 60000:
            sel = np.random.default_rng(0).choice(len(ys), 60000, replace=False)
            ys, xs = ys[sel], xs[sel]
        X = SM_C[None, :] + Zd[ys, xs][:, None] * SC.DIRS[ys, xs]
        px, py, ok = SC._project(pose[cam], X)
        if ok.sum() < min_px:
            out[cam] = (np.ones(3), np.zeros(3))
            continue
        raw = SM.bilinear_rgb(imgs[cam], px[ok], py[ok])
        del_ = delivered[ys[ok], xs[ok]]
        a = np.ones(3); b = np.zeros(3)
        for ch in range(3):
            sr = raw[:, ch].std() + 1e-6
            a[ch] = (del_[:, ch].std() + 1e-6) / sr
            b[ch] = del_[:, ch].mean() - a[ch] * raw[:, ch].mean()
        out[cam] = (a, b)
    return out


SM_C = np.zeros(3)


def source_fidelity(delivered, imgs, pose, sup, Zd, own, domain,
                    sweep=DEPTH_SWEEP):
    """Min over (camera, depth) of |delivered - gain-corrected raw|, 0-255.

    Returns (resid, best_cam, best_z).  `resid` is only meaningful inside
    `domain`; elsewhere it is left at +inf sentinel 255.
    """
    gains = per_camera_gain(delivered, imgs, pose, sup, Zd, own)
    ys, xs = np.nonzero(domain)
    tgt = delivered[ys, xs]
    best = np.full(len(ys), 255.0, np.float32)
    bcam = np.full(len(ys), -1, np.int16)
    bz = np.zeros(len(ys), np.float32)
    for ci, cam in enumerate(SC.CAMERAS):
        s = sup[cam][ys, xs]
        if not s.any():
            continue
        idx = np.nonzero(s)[0]
        a, b = gains[cam]
        d_sel = SC.DIRS[ys[idx], xs[idx]]
        for Z in sweep:
            X = SM_C[None, :] + Z * d_sel
            px, py, ok = SC._project(pose[cam], X)
            if not ok.any():
                continue
            v = SM.bilinear_rgb(imgs[cam], px[ok], py[ok]) * a[None, :] + b[None, :]
            d = np.abs(v - tgt[idx[ok]]).mean(1).astype(np.float32)
            j = idx[ok]
            upd = d < best[j]
            best[j[upd]] = d[upd]
            bcam[j[upd]] = ci
            bz[j[upd]] = Z
    R = np.full((H, W), 255.0, np.float32)
    Cm = np.full((H, W), -1, np.int16)
    Zb = np.zeros((H, W), np.float32)
    R[ys, xs] = best
    Cm[ys, xs] = bcam
    Zb[ys, xs] = bz
    return R, Cm, Zb, {c: [list(np.round(g[0], 4)), list(np.round(g[1], 2))]
                       for c, g in gains.items()}
