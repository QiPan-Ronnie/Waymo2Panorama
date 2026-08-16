"""DB-239 seam mask - koi's 2026-08-07 instruction, made measurable.

koi, 2026-08-07 meeting, 00:41:36 and 00:42:20:

    "我宁愿你把部分的资讯选择 mask 掉或挖掉, 但你保留下来是很正确的 GT,
     而不是说乱拼."
    "A 版本就是接缝处会很漂亮, 但场景会有点 skew; B 就是所有东西都像 GT
     一样很正常, 只是说接缝处可能会不漂亮 ... 你把接缝处直接涂一条, 像
     mask 直接变黑的接缝处, 就让 [Cosmos] 的 model 自己去补就好了."

DB-236 proved why this is the only self-consistent option: at a 0.28 m
baseline two adjacent ring cameras looking at a 6 m object see *different
faces* of it, so "straight" and "content-complete" cannot both hold in a
single-layer depth representation.  Compositing bends the column; single-camera
ownership deletes a face.  koi's answer is to stop hiding it and declare it.

The only quantity koi left open is HOW WIDE the black strip should be.  A fixed
strip is either wasteful (parallax at 20 m is ~5 px) or insufficient (at 3 m it
is ~30 px).  This module measures the disagreement per pixel and blacks out only
where the two cameras actually contradict each other, so real pixels are kept
wherever the evidence is consistent.

Contract, deliberately one-directional:

    the seam mask may only demote KEEP -> RECONSTRUCT.
    It never invents a pixel, never promotes RECONSTRUCT, never moves content.

Geometry mirrors production `db89_ghost_recovery.depth_field` via
`db238_screen` (verified to 0.015 m on 00a6ffc1 a100).
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "db238_screening"))
sys.path.insert(0, "/content")

import db238_screen as SC  # noqa: E402

H, W = SC.H, SC.W
ADJACENT = SC.ADJACENT
CAMERAS = SC.CAMERAS
DIRS = SC.DIRS


# --------------------------------------------------------- ego-motion (EMC)

def load_ego_interp(log_dir):
    """The renderer's `cte`: city_SE3_ego interpolated to any timestamp.

    Reproduces db214_artifact_primitives.load_ego_pose_interpolators exactly -
    Slerp on rotation, linear on translation, clipped to the table's extent.
    """
    import pandas as pd
    from scipy.spatial.transform import Rotation, Slerp
    p = os.path.join(log_dir, "city_SE3_egovehicle.feather")
    if not os.path.isfile(p):
        return None
    poses = (pd.read_feather(p).sort_values("timestamp_ns")
             .drop_duplicates("timestamp_ns").reset_index(drop=True))
    ts = poses["timestamp_ns"].to_numpy(np.int64)
    if len(ts) == 0:
        return None
    origin = int(ts[0])
    rel = (ts - origin).astype(np.float64)
    quat = poses[["qx", "qy", "qz", "qw"]].to_numpy(np.float64)
    tr = poses[["tx_m", "ty_m", "tz_m"]].to_numpy(np.float64)
    keep = np.concatenate([[True], np.diff(rel) > 0])
    rel, quat, tr = rel[keep], quat[keep], tr[keep]
    lo, hi = float(rel.min()), float(rel.max())
    slerp = Slerp(rel, Rotation.from_quat(quat)) if len(rel) > 1 else None

    def cte(t_ns):
        q = float(np.clip(float(int(t_ns) - origin), lo, hi))
        R = (slerp(q).as_matrix() if slerp is not None
             else Rotation.from_quat(quat[0]).as_matrix())
        t = np.array([np.interp(q, rel, tr[:, a]) for a in range(3)])
        return R, t
    return cte


def emc_poses(cal, cam_ts, anchor_ts, cte):
    """Per-camera pose in the anchor ego frame, corrected for ego motion.

    The seven cameras do not expose simultaneously.  Production renders each one
    from where the rig actually was at that camera's exposure time
    (db89_ghost_recovery.py:980-984); a static rig is wrong by ego_speed x
    exposure offset, which on this data is ~0.25 m and shows up as a ~13 px
    azimuth error at 6 m - enough to put a seam mask in the wrong place.
    """
    Ra, ta = cte(int(anchor_ts))
    out = {}
    for cam, c in cal.items():
        Ri, ti = cte(int(cam_ts[cam]))
        out[cam] = {"K": c["K"], "shape": c["shape"],
                    "R": Ra.T @ Ri @ c["R"],
                    "t": Ra.T @ (Ri @ c["t"] + ti - ta)}
    return out


DIRECTIONAL_FORWARD_MIN = 0.05   # db214_artifact_primitives.py:17
DEPTH_SEAMRAMP_DEG = 10.546875   # db89_ghost_recovery.py:72


def angular_overlap_weight(overlap, ramp_rad):
    """db214_artifact_primitives.py:292 - fine-depth weight, wrapping at the meridian."""
    from scipy.ndimage import distance_transform_edt
    tiled = np.concatenate([overlap, overlap, overlap], axis=1)
    dist = distance_transform_edt(
        ~tiled, sampling=(np.pi / H, 2.0 * np.pi / W))[:, W:2 * W]
    return np.clip(1.0 - dist / ramp_rad, 0.0, 1.0).astype(np.float32)


def production_depth(Zd, nv, ramp_deg=DEPTH_SEAMRAMP_DEG):
    """The depth production actually projects with (db89_ghost_recovery:1048-1064).

    DB-184 kept fine LiDAR depth only near multi-camera overlap and fell back to
    an 8x-downsampled median field elsewhere, so glyph strokes could not inherit
    LiDAR depth gradients.  The blend is done in INVERSE depth, and it is the
    field every ERP ray is projected with - including deep inside one camera's
    own territory, where the rig-centre-to-camera offset (~0.28 m) still turns a
    depth error into a real angular displacement.
    """
    import cv2
    Z32 = Zd.astype(np.float32)
    wf = angular_overlap_weight(nv >= 2, np.radians(float(ramp_deg)))
    sm = cv2.resize(Z32, (W // 8, H // 8), interpolation=cv2.INTER_NEAREST)
    Zc = cv2.resize(cv2.medianBlur(sm, 5), (W, H), interpolation=cv2.INTER_LINEAR)
    inv = wf / np.maximum(Z32, 1e-3) + (1.0 - wf) / np.maximum(Zc, 1e-3)
    return (1.0 / np.maximum(inv, 1e-6)).astype(np.float32), wf, Zc


def camera_support_emc(pose):
    """Production's `_dir_support`: depth-free, EMC rotation, 1 px border.

    Mirrors db89_ghost_recovery.py:1022-1028 + directional_projection_valid.
    db238's variant used the static rotation and a 0 px border, which puts the
    overlap band in the wrong place by the same ~13 px as the pose error.
    """
    sup = {}
    for cam, c in pose.items():
        dcam = SC.DIRS_FLAT @ c["R"]
        z = dcam[:, 2]
        K = c["K"]; hh, ww = c["shape"]
        px = K[0, 0] * dcam[:, 0] / np.maximum(z, 1e-6) + K[0, 2]
        py = K[1, 1] * dcam[:, 1] / np.maximum(z, 1e-6) + K[1, 2]
        sup[cam] = ((z > DIRECTIONAL_FORWARD_MIN) & (px >= 1.0) & (px < ww - 1.0)
                    & (py >= 1.0) & (py < hh - 1.0)).reshape(H, W)
    return sup


# ------------------------------------------------------------------- sampling

def bilinear_rgb(img, px, py):
    """Bilinear sample of an HxWx3 float image at float coordinates."""
    h, w = img.shape[:2]
    x0 = np.floor(px).astype(np.int64)
    y0 = np.floor(py).astype(np.int64)
    fx = (px - x0).astype(np.float32)[:, None]
    fy = (py - y0).astype(np.float32)[:, None]
    x0 = np.clip(x0, 0, w - 2)
    y0 = np.clip(y0, 0, h - 2)
    v00 = img[y0, x0]
    v01 = img[y0, x0 + 1]
    v10 = img[y0 + 1, x0]
    v11 = img[y0 + 1, x0 + 1]
    return ((v00 * (1 - fx) + v01 * fx) * (1 - fy)
            + (v10 * (1 - fx) + v11 * fx) * fy)


def affine_match(A, B):
    """Match A's per-channel mean/std to B's.

    The seven ring cameras run independent auto-exposure and auto-white-balance
    (DB-184), so a raw difference would be dominated by exposure, not geometry.
    This removes the first-order photometric offset and leaves the structural
    disagreement, which is what the seam mask is supposed to find.
    """
    out = np.empty_like(A)
    for ch in range(A.shape[1]):
        sa = A[:, ch].std() + 1e-6
        sb = B[:, ch].std() + 1e-6
        out[:, ch] = (A[:, ch] - A[:, ch].mean()) / sa * sb + B[:, ch].mean()
    return out


# --------------------------------------------------------------- disagreement

def seam_disagreement(cal, imgs, sup, Zd, C, domain=None, min_px=300):
    """Per-pixel cross-camera disagreement, 0-255 units.

    Returns (D, OV, per_pair):
        D       HxW float32, max over adjacent pairs of |affine-matched a - b|
        OV      HxW bool, pixels where at least one adjacent pair was measurable
        per_pair dict of median/p90 per pair, for the run record
    `domain` optionally restricts the measurement (e.g. to the delivered mask's
    KEEP region, so the seam mask can only ever demote).
    """
    D = np.zeros((H, W), np.float32)
    OV = np.zeros((H, W), bool)
    per_pair = {}
    for a, b in ADJACENT:
        m = sup[a] & sup[b]
        if domain is not None:
            m = m & domain
        key = "%s|%s" % (a, b)
        if int(m.sum()) < min_px:
            per_pair[key] = {"n": int(m.sum()), "reason": "overlap_too_small"}
            continue
        ys, xs = np.nonzero(m)
        X = C[None, :] + Zd[ys, xs][:, None] * DIRS[ys, xs]
        pa, qa, oka = SC._project(cal[a], X)
        pb, qb, okb = SC._project(cal[b], X)
        ok = oka & okb
        if int(ok.sum()) < min_px:
            per_pair[key] = {"n": int(ok.sum()), "reason": "projection_too_small"}
            continue
        Aa = affine_match(bilinear_rgb(imgs[a], pa[ok], qa[ok]),
                          bilinear_rgb(imgs[b], pb[ok], qb[ok]))
        Bb = bilinear_rgb(imgs[b], pb[ok], qb[ok])
        d = np.abs(Aa - Bb).mean(1).astype(np.float32)
        yy, xx = ys[ok], xs[ok]
        np.maximum.at(D, (yy, xx), d)
        OV[yy, xx] = True
        per_pair[key] = {"n": int(ok.sum()),
                         "median": round(float(np.median(d)), 3),
                         "p90": round(float(np.percentile(d, 90)), 3),
                         "p99": round(float(np.percentile(d, 99)), 3)}
    return D, OV, per_pair


def seam_mask(D, OV, tau, close_px=7, min_island=120, grow_px=3):
    """Threshold the disagreement into a clean RECONSTRUCT region.

    Raw thresholding gives speckle: JPEG noise crosses any tau on a few isolated
    pixels.  Closing then island-removal keeps only coherent contradictions, and
    a small dilation covers the sub-threshold skirt of a real misalignment.
    """
    import cv2
    Ds = cv2.medianBlur(D, 5)
    bad = ((Ds > float(tau)) & OV).astype(np.uint8)
    if close_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px, close_px))
        bad = cv2.morphologyEx(bad, cv2.MORPH_CLOSE, k)
    if min_island > 0:
        n, lab, stats, _ = cv2.connectedComponentsWithStats(bad, 8)
        keep = np.zeros(n, bool)
        for i in range(1, n):
            keep[i] = stats[i, cv2.CC_STAT_AREA] >= min_island
        bad = keep[lab].astype(np.uint8)
    if grow_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow_px + 1,) * 2)
        bad = cv2.dilate(bad, k)
    return bad.astype(bool)


def apply_seam_mask(frame_rgb, keep_mask, bad):
    """koi's 'mask 直接变黑': black the pixel AND demote it in the contract.

    Both must happen together.  Blacking without demoting would hand Cosmos a
    black pixel labelled 'this is real' - the exact failure the mask pool exists
    to prevent.
    """
    out = frame_rgb.copy()
    out[bad] = 0
    new_keep = keep_mask.copy()
    new_keep[bad] = False
    return out, new_keep
