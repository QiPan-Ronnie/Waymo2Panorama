"""
Sim(3) alignment: Pi3 internal world frame -> AV2 ego frame.

Pi3 is scale-free; its "world" is some internal frame (typically the first camera).
AV2 ego frame is metric (meters), with x forward, y left, z up.

Strategy: use the 7 ring-camera POSITIONS as point correspondences.
  src = Pi3-predicted camera translations   (positions in Pi3 world)
  dst = AV2 ground-truth camera translations (positions in ego frame)

Run Umeyama (1991) to get (s, R, t) with y = s R x + t, minimizing
sum_i || dst_i - (s R src_i + t) ||^2.

This gives one Sim(3) transform that, when applied to ALL Pi3 world points,
brings them into the AV2 ego frame at correct metric scale.

NOTE: this assumes Pi3's predicted poses are roughly correct (modulo a global
similarity transform). Earlier Pi3 K-recovery error was 0.06–2.08% of AV2 truth
across 7 cams, so this assumption is well-supported.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Sim3:
    """Sim(3) transform y = s R x + t."""

    scale: float
    R: np.ndarray   # (3, 3)
    t: np.ndarray   # (3,)

    def apply(self, x: np.ndarray) -> np.ndarray:
        """Apply transform. x can be (3,), (N, 3), or (..., 3)."""
        return self.scale * x @ self.R.T + self.t

    def as_matrix(self) -> np.ndarray:
        """4x4 homogeneous matrix that maps homogeneous src -> homogeneous dst.

        Caveat: this collapses the similarity into an affine 4x4 (sR in upper-left,
        t in right column). True Sim(3) needs separate scale; this is only useful
        for visualization.
        """
        T = np.eye(4, dtype=np.float64)
        T[:3, :3] = self.scale * self.R
        T[:3, 3] = self.t
        return T


def umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool = True) -> Sim3:
    """Estimate y = s R x + t minimizing sum || y - (s R x + t) ||^2.

    Args:
        src: (N, 3) source points
        dst: (N, 3) target points
        with_scale: if False, forces s = 1 (rigid alignment only)

    Returns:
        Sim3(scale, R, t)

    Reference: Umeyama, "Least-squares estimation of transformation parameters
    between two point patterns" (1991).
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    assert src.shape == dst.shape, f"shape mismatch: {src.shape} vs {dst.shape}"
    assert src.ndim == 2 and src.shape[1] == 3
    n = src.shape[0]
    assert n >= 3, f"need at least 3 correspondences, got {n}"

    mu_src = src.mean(axis=0)
    mu_dst = dst.mean(axis=0)
    src_c = src - mu_src
    dst_c = dst - mu_dst

    var_src = (src_c ** 2).sum() / n  # = (1/n) sum ||x_i - mu_x||^2

    # Cross-covariance H = (1/n) sum (dst_c_i) (src_c_i)^T
    H = (dst_c.T @ src_c) / n  # (3, 3)

    U, sigma, Vt = np.linalg.svd(H)
    # Handle reflection: if det(U) * det(V) < 0, flip the last column
    D = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        D[2, 2] = -1.0

    R = U @ D @ Vt

    if with_scale:
        scale = (sigma * np.diag(D)).sum() / var_src
    else:
        scale = 1.0

    t = mu_dst - scale * R @ mu_src

    return Sim3(scale=float(scale), R=R.astype(np.float64), t=t.astype(np.float64))


def fit_sim3_from_camera_translations(
    src_positions: dict[str, np.ndarray],
    dst_positions: dict[str, np.ndarray],
) -> tuple[Sim3, dict]:
    """Fit Sim(3) using camera positions as correspondences.

    Args:
        src_positions: {cam_name: (3,) Pi3-world position}
        dst_positions: {cam_name: (3,) AV2-ego position}

    Returns:
        (Sim3, diagnostics) where diagnostics has:
          per_cam_residual_m  : {cam: distance (m) between aligned src and dst}
          mean_residual_m, max_residual_m
          n_correspondences
          src_scale_estimate  : ||centered src||
          dst_scale_estimate  : ||centered dst|| (== scale * src_scale, approximately)
    """
    cams = sorted(set(src_positions.keys()) & set(dst_positions.keys()))
    if not cams:
        raise ValueError("no overlapping cameras between src and dst")
    src = np.stack([src_positions[c] for c in cams], axis=0)
    dst = np.stack([dst_positions[c] for c in cams], axis=0)

    sim3 = umeyama(src, dst, with_scale=True)
    aligned = sim3.apply(src)
    residuals = np.linalg.norm(aligned - dst, axis=1)  # (N,) in metres (dst is metric)

    src_c = src - src.mean(axis=0)
    dst_c = dst - dst.mean(axis=0)

    diagnostics = {
        "cameras": cams,
        "per_cam_residual_m": {c: float(r) for c, r in zip(cams, residuals)},
        "mean_residual_m": float(residuals.mean()),
        "max_residual_m": float(residuals.max()),
        "n_correspondences": int(len(cams)),
        "src_centered_norm_mean": float(np.linalg.norm(src_c, axis=1).mean()),
        "dst_centered_norm_mean_m": float(np.linalg.norm(dst_c, axis=1).mean()),
        "fitted_scale": float(sim3.scale),
        "fitted_translation_m": sim3.t.tolist(),
    }
    return sim3, diagnostics
