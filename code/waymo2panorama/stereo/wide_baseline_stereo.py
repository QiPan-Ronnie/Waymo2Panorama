"""
Phase 3 route 13 / 新-D — Wide-baseline stereo on adjacent AV2 ring-cam pairs.

This module implements *known-extrinsics* sparse stereo between adjacent
ring cameras: rather than running full SfM (extrinsics are factory-calibrated
on AV2 to ~±5 mm / ±0.1°), we use the known relative pose `T_a_b` to
(a) build the fundamental matrix `F` directly, (b) reject outlier matches
against that epipolar constraint, and (c) triangulate inlier pixel
correspondences via DLT (`cv2.triangulatePoints`) into 3D points in
the ego frame.

Pipeline (see notes/new_d_wide_baseline_stereo_research.md §3):
    1. DISK feature extraction on both images
    2. LightGlue matching (deep learned matcher)
    3. F from known T_a_b: F = K_b^{-T} [t]_x R K_a^{-1}
    4. Epipolar RANSAC filter (Sampson distance ≤ threshold)
    5. DLT triangulation → ego-frame 3D points

Adjacency = the 7-cam ring topology (front_center neighbouring both
front_left and front_right; rear_left ↔ rear_right close the ring).

Outputs per cam-pair: (N, 3) points in ego frame, plus inlier mask
and matched keypoints for diagnostics. Designed to be a drop-in
input for an L1 "Option B" reweighting layer; this module itself
only produces the sparse 3D evidence.

CPU-friendly: kornia LightGlue runs in PyTorch and works fine on CPU
(~500 ms - 2 s per pair on 504×504 images). All other code is pure
numpy / cv2.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import torch
from PIL import Image


# 7 ring cams in clockwise order. Adjacency wraps around (rear_left -> rear_right
# bridges back through the rear). front_center sits between front_left and
# front_right with the tightest baselines (~0.3-0.5 m).
RING_CAMS_7: tuple[str, ...] = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
)

# The 7 cam-pairs that bound the overlap regions on the ring (matches
# research doc §4 expected-overlap table).
ADJACENT_PAIRS: tuple[tuple[str, str], ...] = (
    ("ring_front_center", "ring_front_left"),
    ("ring_front_left", "ring_side_left"),
    ("ring_side_left", "ring_rear_left"),
    ("ring_rear_left", "ring_rear_right"),
    ("ring_rear_right", "ring_side_right"),
    ("ring_side_right", "ring_front_right"),
    ("ring_front_right", "ring_front_center"),
)


@dataclass
class StereoMatchResult:
    """Output of stereo processing on one cam-pair."""

    cam_a: str
    cam_b: str
    mkpts_a: np.ndarray            # (M, 2) float32 pixel coords in image_a
    mkpts_b: np.ndarray            # (M, 2) float32 pixel coords in image_b
    inlier_mask: np.ndarray        # (M,) bool, True for epipolar inliers (post-all-filters)
    pts_3d_ego: np.ndarray         # (N, 3) float32, N == inlier_mask.sum()
    pts_3d_cam_a: np.ndarray       # (N, 3) float32, same points in cam_a frame
    depth_cam_a: np.ndarray        # (N,) float32, Z in cam_a frame (forward depth)
    parallax_deg: np.ndarray       # (N,) float32, parallax angle per point
    F: np.ndarray                  # (3, 3) fundamental matrix used
    baseline_m: float              # ||T_ego_cam_a - T_ego_cam_b|| in meters
    time_s: float = 0.0
    n_kpts_a: int = 0
    n_kpts_b: int = 0
    notes: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Step 1+2: feature extraction + matching
# ---------------------------------------------------------------------------

_DISK_CACHE: dict[str, object] = {}


def _get_disk_and_lightglue(device: torch.device):
    """Lazy singleton: DISK extractor + LightGlue (disk weights) matcher.

    Both modules download weights on first construction (~30 MB DISK,
    ~50 MB LightGlue). Cached per-device.
    """
    key = str(device)
    if key in _DISK_CACHE:
        return _DISK_CACHE[key]
    import kornia.feature as KF

    disk = KF.DISK.from_pretrained("depth").to(device).eval()
    # LightGlue with DISK features
    lg = KF.LightGlue(features="disk").to(device).eval()
    _DISK_CACHE[key] = (disk, lg)
    return _DISK_CACHE[key]


def _to_tensor(img_rgb: np.ndarray, device: torch.device) -> torch.Tensor:
    """Convert HxWx3 uint8 RGB -> (1, 3, H, W) float [0,1] on device."""
    if img_rgb.dtype != np.uint8:
        raise ValueError(f"expected uint8 RGB, got {img_rgb.dtype}")
    t = torch.from_numpy(img_rgb.astype(np.float32) / 255.0)
    t = t.permute(2, 0, 1).unsqueeze(0).contiguous()
    return t.to(device)


def extract_pair_features(
    img_a: np.ndarray,
    img_b: np.ndarray,
    device: str | torch.device = "cpu",
    max_num_keypoints: int = 2048,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """Run DISK on both images. Returns (kpts_a, desc_a, kpts_b, desc_b).

    kpts shapes:   (N, 2) float32 pixel coords
    desc shapes:   (N, 128) float32, L2-normalized DISK descriptors
    """
    device = torch.device(device)
    disk, _ = _get_disk_and_lightglue(device)
    ta = _to_tensor(img_a, device)
    tb = _to_tensor(img_b, device)

    # DISK requires H, W % 16 == 0. Pad with zeros, then keep all kpts (they
    # live in the original-image coord system because we pad bottom/right).
    def _pad_to_mult16(t: torch.Tensor) -> torch.Tensor:
        _, _, h, w = t.shape
        nh = ((h + 15) // 16) * 16
        nw = ((w + 15) // 16) * 16
        if nh == h and nw == w:
            return t
        out = torch.zeros((t.shape[0], t.shape[1], nh, nw), dtype=t.dtype, device=t.device)
        out[:, :, :h, :w] = t
        return out

    ta_p = _pad_to_mult16(ta)
    tb_p = _pad_to_mult16(tb)

    with torch.no_grad():
        feats_a = disk(ta_p, n=max_num_keypoints, pad_if_not_divisible=False)
        feats_b = disk(tb_p, n=max_num_keypoints, pad_if_not_divisible=False)
    # DISK returns a list of `DiskFeatures` (one per batch); we used batch=1.
    fa = feats_a[0]
    fb = feats_b[0]
    return fa.keypoints, fa.descriptors, fb.keypoints, fb.descriptors


def match_with_lightglue(
    kpts_a: torch.Tensor,
    desc_a: torch.Tensor,
    kpts_b: torch.Tensor,
    desc_b: torch.Tensor,
    img_a_hw: tuple[int, int],
    img_b_hw: tuple[int, int],
    device: str | torch.device = "cpu",
    min_confidence: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run LightGlue. Returns (mkpts_a, mkpts_b, scores) as numpy arrays.

    Args:
        img_*_hw: (H, W) — required by LightGlue for relative position encoding.
        min_confidence: optionally drop matches below this score.
    """
    device = torch.device(device)
    _, lg = _get_disk_and_lightglue(device)
    h_a, w_a = img_a_hw
    h_b, w_b = img_b_hw

    # LightGlue expects [B, N, 2] / [B, N, D]. We have [N, 2].
    in_dict = {
        "image0": {
            "keypoints": kpts_a.unsqueeze(0),
            "descriptors": desc_a.unsqueeze(0),
            "image_size": torch.tensor([[w_a, h_a]], device=device).float(),
        },
        "image1": {
            "keypoints": kpts_b.unsqueeze(0),
            "descriptors": desc_b.unsqueeze(0),
            "image_size": torch.tensor([[w_b, h_b]], device=device).float(),
        },
    }
    with torch.no_grad():
        out = lg(in_dict)
    matches = out["matches"][0]   # (M, 2) long tensor
    scores = out["scores"][0]     # (M,) float
    if min_confidence > 0:
        keep = scores >= min_confidence
        matches = matches[keep]
        scores = scores[keep]
    idx_a = matches[:, 0].detach().cpu().numpy().astype(np.int64)
    idx_b = matches[:, 1].detach().cpu().numpy().astype(np.int64)
    mkpts_a = kpts_a.detach().cpu().numpy()[idx_a].astype(np.float32)
    mkpts_b = kpts_b.detach().cpu().numpy()[idx_b].astype(np.float32)
    scores_np = scores.detach().cpu().numpy().astype(np.float32)
    return mkpts_a, mkpts_b, scores_np


# ---------------------------------------------------------------------------
# Step 3: fundamental matrix from known relative pose
# ---------------------------------------------------------------------------


def _skew(v: np.ndarray) -> np.ndarray:
    """3-vector -> 3x3 skew-symmetric (cross-product) matrix."""
    return np.array(
        [
            [0.0, -v[2], v[1]],
            [v[2], 0.0, -v[0]],
            [-v[1], v[0], 0.0],
        ],
        dtype=np.float64,
    )


def compute_F_from_known_T(
    T_ego_a: np.ndarray, T_ego_b: np.ndarray, K_a: np.ndarray, K_b: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Build F (and the relative pose T_a_b) from known extrinsics.

    Convention: pts_in_a = T_a_b @ pts_in_b. With R, t such that
        x_a = R @ x_b + t,
    the essential matrix is E = [t]_x R and the fundamental matrix is
        F = K_b^{-T} @ E @ K_a^{-1}
    so that x_a^T F x_b = 0 (with x in pixel-homogeneous coords).

    Args:
        T_ego_a: (4,4) SE(3) cam_a -> ego
        T_ego_b: (4,4) SE(3) cam_b -> ego
        K_a, K_b: (3,3) intrinsics

    Returns:
        F: (3, 3) fundamental matrix (cam_a is image0, cam_b is image1)
        T_a_b: (4, 4) SE(3) cam_b -> cam_a
    """
    T_a_b = np.linalg.inv(T_ego_a) @ T_ego_b
    R = T_a_b[:3, :3]
    t = T_a_b[:3, 3]
    E = _skew(t) @ R
    F = np.linalg.inv(K_b).T @ E @ np.linalg.inv(K_a)
    F = F / (np.linalg.norm(F) + 1e-12)
    return F, T_a_b


# ---------------------------------------------------------------------------
# Step 4: epipolar inlier filtering (Sampson distance on a KNOWN F)
# ---------------------------------------------------------------------------


def _sampson_distance(F: np.ndarray, mkpts_a: np.ndarray, mkpts_b: np.ndarray) -> np.ndarray:
    """Symmetric (Sampson) epipolar distance for a known F.

    Returns: (M,) float distance in pixels. Smaller = better.

    We use Sampson (a first-order linearization of the geometric error)
    because it's the same metric cv2.findFundamentalMat uses internally
    and is numerically well-behaved when matches are near the epipole.
    """
    ones = np.ones((mkpts_a.shape[0], 1), dtype=np.float64)
    xa = np.hstack([mkpts_a, ones])  # (M, 3)
    xb = np.hstack([mkpts_b, ones])  # (M, 3)
    # x_a^T F x_b — note ordering: cam_a is image0 with our F
    Fxb = (F @ xb.T).T               # (M, 3)
    FTxa = (F.T @ xa.T).T            # (M, 3)
    num = np.einsum("ij,ij->i", xa, Fxb)     # (M,)
    denom = (
        Fxb[:, 0] ** 2 + Fxb[:, 1] ** 2 + FTxa[:, 0] ** 2 + FTxa[:, 1] ** 2 + 1e-12
    )
    return np.sqrt(num**2 / denom).astype(np.float32)


def epipolar_ransac_filter(
    mkpts_a: np.ndarray,
    mkpts_b: np.ndarray,
    F: np.ndarray,
    threshold_px: float = 2.0,
) -> np.ndarray:
    """Reject matches whose Sampson distance to the KNOWN F exceeds threshold.

    NOTE: this is *not* RANSAC over F (we know F exactly from extrinsics).
    It is "RANSAC-lite": we just threshold against the known epipolar
    constraint. With a slightly noisier F (e.g. extrinsics drift) you'd
    pre-estimate F with cv2.findFundamentalMat — left as a hook below.
    """
    d = _sampson_distance(F, mkpts_a, mkpts_b)
    return d <= threshold_px


# ---------------------------------------------------------------------------
# Step 5: DLT triangulation
# ---------------------------------------------------------------------------


def triangulate_sparse(
    mkpts_a: np.ndarray,
    mkpts_b: np.ndarray,
    K_a: np.ndarray,
    K_b: np.ndarray,
    T_a_b: np.ndarray,
    T_ego_cam_a: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """DLT triangulate (cv2.triangulatePoints) and map to ego frame.

    Args:
        mkpts_a, mkpts_b: (N, 2) matched pixel correspondences
        K_a, K_b: (3, 3) intrinsics
        T_a_b: (4, 4) cam_b -> cam_a (i.e. x_a = R x_b + t)
        T_ego_cam_a: (4, 4) cam_a -> ego (so we can output ego-frame pts)

    Returns:
        pts_3d_ego: (N, 3) float32 in ego frame
        pts_3d_cam_a: (N, 3) float32 in cam_a frame
        depth_cam_a: (N,) float32 = Z in cam_a frame (forward distance)
        depth_cam_b: (N,) float32 = Z in cam_b frame (cheirality check)
        parallax_deg: (N,) float32 = angle (degrees) between rays
                       from cam_a-centre and cam_b-centre to the triangulated point.
                       Very small parallax => degenerate triangulation
                       (far-distance content w/ tiny stereo baseline).
    """
    if mkpts_a.shape[0] == 0:
        z = np.zeros((0, 3), dtype=np.float32)
        z0 = np.zeros((0,), dtype=np.float32)
        return z, z, z0, z0, z0

    # Camera projection matrices in cam_a-centred world frame.
    # P_a projects 3D points expressed in cam_a's frame.
    # cv2.triangulatePoints expects projection matrices that map world -> image.
    # We pick "world" = cam_a, so:
    #   P_a = K_a [I | 0]
    #   P_b = K_b [R_b_a | t_b_a]   where x_b = R_b_a x_a + t_b_a   ( = T_b_a )
    T_b_a = np.linalg.inv(T_a_b)
    R_b_a = T_b_a[:3, :3]
    t_b_a = T_b_a[:3, 3]
    P_a = K_a @ np.hstack([np.eye(3), np.zeros((3, 1))])
    P_b = K_b @ np.hstack([R_b_a, t_b_a.reshape(3, 1)])

    pts4 = cv2.triangulatePoints(P_a, P_b, mkpts_a.T.astype(np.float64), mkpts_b.T.astype(np.float64))
    pts_cam_a = (pts4[:3] / (pts4[3:4] + 1e-12)).T.astype(np.float32)  # (N, 3)

    depth_a = pts_cam_a[:, 2].astype(np.float32)  # forward Z in cam_a

    # cheirality test in cam_b too: project pts_cam_a into cam_b
    pts_cam_b = (R_b_a @ pts_cam_a.T + t_b_a.reshape(3, 1)).T.astype(np.float32)
    depth_b = pts_cam_b[:, 2].astype(np.float32)

    # parallax angle (deg): angle between (point - cam_a_centre) and (point - cam_b_centre)
    # cam_a centre is origin (in cam_a frame). cam_b centre = T_a_b[:3,3] (in cam_a frame).
    cb_in_a = T_a_b[:3, 3].astype(np.float32)
    ra = pts_cam_a                              # cam_a -> point, in cam_a frame
    rb = pts_cam_a - cb_in_a[None, :]          # cam_b -> point, in cam_a frame
    na = np.linalg.norm(ra, axis=1) + 1e-9
    nb = np.linalg.norm(rb, axis=1) + 1e-9
    cosang = np.clip((ra * rb).sum(axis=1) / (na * nb), -1.0, 1.0)
    parallax_rad = np.arccos(cosang)
    parallax_deg = np.degrees(parallax_rad).astype(np.float32)

    # cam_a -> ego
    pts_ego_h = (T_ego_cam_a @ np.hstack([pts_cam_a, np.ones((pts_cam_a.shape[0], 1))]).T).T
    pts_ego = pts_ego_h[:, :3].astype(np.float32)
    return pts_ego, pts_cam_a, depth_a, depth_b, parallax_deg


# ---------------------------------------------------------------------------
# Step 0: one-pair full pipeline
# ---------------------------------------------------------------------------


def process_cam_pair(
    img_a: np.ndarray,
    img_b: np.ndarray,
    K_a: np.ndarray,
    K_b: np.ndarray,
    T_ego_a: np.ndarray,
    T_ego_b: np.ndarray,
    cam_a_name: str,
    cam_b_name: str,
    device: str = "cpu",
    max_num_keypoints: int = 2048,
    lightglue_min_confidence: float = 0.2,
    epipolar_threshold_px: float = 3.0,
    min_depth_m: float = 0.5,
    max_depth_m: float = 120.0,
    min_parallax_deg: float = 0.5,
) -> StereoMatchResult:
    """End-to-end stereo on one adjacent cam pair (no L1 modification).

    Filters applied to candidate 3D points (after LightGlue + epipolar
    inlier mask):
        1. **cheirality** — depth_cam_a > 0 AND depth_cam_b > 0
           (point must be in front of BOTH cameras)
        2. **depth band** — min_depth_m <= depth_cam_a <= max_depth_m
        3. **parallax** — parallax_deg >= min_parallax_deg
           Sparse stereo on AV2 ring cams has ~0.2-0.5 m baselines,
           so a parallax < 0.3-0.5 deg corresponds to >> 50 m distance
           where 1 px noise dominates triangulation. We drop those
           to keep only points with reliable depth.

    Filter (3) is the empirically-tuned fix for the "nearly-parallel
    ray, content at infinity" failure mode seen on side cam pairs.
    """
    t0 = time.time()
    h_a, w_a = img_a.shape[:2]
    h_b, w_b = img_b.shape[:2]

    # Steps 1+2: features + match
    kpts_a, desc_a, kpts_b, desc_b = extract_pair_features(
        img_a, img_b, device=device, max_num_keypoints=max_num_keypoints
    )
    mkpts_a, mkpts_b, scores = match_with_lightglue(
        kpts_a, desc_a, kpts_b, desc_b,
        img_a_hw=(h_a, w_a), img_b_hw=(h_b, w_b),
        device=device, min_confidence=lightglue_min_confidence,
    )

    # Step 3: F from known T
    F, T_a_b = compute_F_from_known_T(T_ego_a, T_ego_b, K_a, K_b)
    baseline = float(np.linalg.norm(T_ego_a[:3, 3] - T_ego_b[:3, 3]))

    # Step 4: epipolar inlier filter
    if mkpts_a.shape[0] > 0:
        epi_inliers = epipolar_ransac_filter(mkpts_a, mkpts_b, F, threshold_px=epipolar_threshold_px)
    else:
        epi_inliers = np.zeros((0,), dtype=bool)

    # Step 5: triangulate inliers
    mkpts_a_in = mkpts_a[epi_inliers]
    mkpts_b_in = mkpts_b[epi_inliers]
    pts_ego, pts_cam_a, depth_a, depth_b, parallax_deg = triangulate_sparse(
        mkpts_a_in, mkpts_b_in, K_a, K_b, T_a_b, T_ego_a
    )

    cheirality = (depth_a > 0.0) & (depth_b > 0.0)
    depth_band = (depth_a >= min_depth_m) & (depth_a <= max_depth_m)
    parallax_ok = parallax_deg >= min_parallax_deg
    keep = cheirality & depth_band & parallax_ok

    pts_ego = pts_ego[keep]
    pts_cam_a = pts_cam_a[keep]
    depth_final = depth_a[keep]
    parallax_final = parallax_deg[keep]

    # Update full inlier_mask so that .sum() == final pts count and
    # mkpts_a[inlier_mask] == final 2D keypoints.
    final_mask = np.zeros((mkpts_a.shape[0],), dtype=bool)
    where_epi = np.where(epi_inliers)[0]
    final_mask[where_epi[keep]] = True

    t_total = time.time() - t0
    return StereoMatchResult(
        cam_a=cam_a_name,
        cam_b=cam_b_name,
        mkpts_a=mkpts_a,
        mkpts_b=mkpts_b,
        inlier_mask=final_mask,
        pts_3d_ego=pts_ego,
        pts_3d_cam_a=pts_cam_a,
        depth_cam_a=depth_final,
        parallax_deg=parallax_final,
        F=F.astype(np.float32),
        baseline_m=baseline,
        time_s=t_total,
        n_kpts_a=int(kpts_a.shape[0]),
        n_kpts_b=int(kpts_b.shape[0]),
        notes={
            "n_matches_lightglue": int(mkpts_a.shape[0]),
            "epipolar_inliers_before_geom_filter": int(epi_inliers.sum()),
            "n_dropped_cheirality": int((epi_inliers.sum() - int(cheirality.sum())) if epi_inliers.any() else 0),
            "n_dropped_depth_band": int(((cheirality & ~depth_band).sum()) if epi_inliers.any() else 0),
            "n_dropped_parallax": int(((cheirality & depth_band & ~parallax_ok).sum()) if epi_inliers.any() else 0),
            "median_parallax_deg_kept": float(np.median(parallax_final)) if len(parallax_final) else None,
            "lightglue_min_confidence": float(lightglue_min_confidence),
            "epipolar_threshold_px": float(epipolar_threshold_px),
            "depth_band_m": [float(min_depth_m), float(max_depth_m)],
            "min_parallax_deg": float(min_parallax_deg),
        },
    )


# ---------------------------------------------------------------------------
# Anchor-level driver
# ---------------------------------------------------------------------------


def _load_pi3_anchor(anchor_dir: Path, cam: str) -> dict:
    """Load image + K + T_ego_cam from a pi3_cache anchor directory."""
    image = np.asarray(Image.open(anchor_dir / f"image_{cam}.png").convert("RGB"))
    K = np.load(anchor_dir / f"av2_K_letterboxed_{cam}.npy").astype(np.float64)
    T = np.load(anchor_dir / f"av2_T_ego_cam_{cam}.npy").astype(np.float64)
    return {"image": image, "K": K, "T_ego_cam": T}


def process_anchor_all_pairs(
    anchor_dir: Path,
    device: str = "cpu",
    **stereo_kwargs,
) -> dict[tuple[str, str], StereoMatchResult]:
    """Run the full stereo pipeline on all 7 adjacent pairs of an anchor."""
    cams_data = {cam: _load_pi3_anchor(anchor_dir, cam) for cam in RING_CAMS_7}
    out: dict[tuple[str, str], StereoMatchResult] = {}
    for cam_a, cam_b in ADJACENT_PAIRS:
        da = cams_data[cam_a]
        db = cams_data[cam_b]
        res = process_cam_pair(
            img_a=da["image"], img_b=db["image"],
            K_a=da["K"], K_b=db["K"],
            T_ego_a=da["T_ego_cam"], T_ego_b=db["T_ego_cam"],
            cam_a_name=cam_a, cam_b_name=cam_b,
            device=device, **stereo_kwargs,
        )
        out[(cam_a, cam_b)] = res
    return out
