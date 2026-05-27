"""
Calibration check — verify AV2 ring-cam extrinsics/intrinsics are accurate
enough that seam misalignment is purely parallax (not calibration bias).

METHOD: Sampson distance to calibration-implied epipolar line.

For each adjacent ring-cam pair (cam_A, cam_B):
  1. SIFT-match keypoints.
  2. Compute fundamental matrix F from AV2 extrinsics+intrinsics
     (F = K_B^-T * [t]_x R * K_A^-1, where (R,t) is cam_A -> cam_B).
  3. For each match (xA, xB), compute Sampson distance (perpendicular
     distance from xB to the calibration-implied epipolar line of xA).

Sampson distance is DEPTH-INVARIANT. If calibration is perfect, distance
should be sub-pixel for inliers (limited only by SIFT localization noise).
If calibration has systematic bias, distance has a DC offset.

DECISION RULE:
  - median Sampson < 0.5 px  -> calibration is excellent, seam errors are pure parallax
                                 -> direction A is dead end, move to direction B (geometry)
  - 0.5-2 px median         -> mild bias, BA refinement might help marginally
  - > 2 px median           -> real calibration bias, BA refinement is high-value next step

OUTPUT:
  - JSON per anchor/pair: num_matches, num_inliers, sampson stats
  - PNG: per-pair histogram + sample epipolar-line visualization

USAGE (on Colab):
  python scripts/phase3/calibration_check.py \
    --log-dir /content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/02a00399-15e8-3c97-8e60-2d52f1f9e3b8 \
    --anchor-indices 0,30,60,90,120 \
    --out-json outputs/calibration_check/02a00399.json \
    --out-png  outputs/calibration_check/02a00399_summary.png
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7

# Adjacent pairs in CCW order; closing the ring at front_right -> front_center
PAIRS = [
    ("ring_front_center", "ring_front_left"),
    ("ring_front_left",   "ring_side_left"),
    ("ring_side_left",    "ring_rear_left"),
    ("ring_rear_left",    "ring_rear_right"),
    ("ring_rear_right",   "ring_side_right"),
    ("ring_side_right",   "ring_front_right"),
    ("ring_front_right",  "ring_front_center"),
]


def skew(v: np.ndarray) -> np.ndarray:
    return np.array(
        [[0.0, -v[2], v[1]],
         [v[2], 0.0, -v[0]],
         [-v[1], v[0], 0.0]],
        dtype=np.float64,
    )


def relative_pose(T_ego_A: np.ndarray, T_ego_B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (R, t) such that point in cam_A frame maps to cam_B frame as x_B = R @ x_A + t."""
    T_B_A = np.linalg.inv(T_ego_B) @ T_ego_A
    R = T_B_A[:3, :3]
    t = T_B_A[:3, 3]
    return R, t


def fundamental_from_calib(K_A: np.ndarray, K_B: np.ndarray, R: np.ndarray, t: np.ndarray) -> np.ndarray:
    E = skew(t) @ R
    F = np.linalg.inv(K_B).T @ E @ np.linalg.inv(K_A)
    return F


def sampson_distance(F: np.ndarray, pts_A: np.ndarray, pts_B: np.ndarray) -> np.ndarray:
    """Returns Sampson distance squared in pixels^2, one per match."""
    N = len(pts_A)
    pts_A_h = np.hstack([pts_A, np.ones((N, 1))])
    pts_B_h = np.hstack([pts_B, np.ones((N, 1))])

    Fx_A = (F @ pts_A_h.T).T            # N x 3
    Ftx_B = (F.T @ pts_B_h.T).T          # N x 3

    num = np.sum(pts_B_h * Fx_A, axis=1) ** 2
    den = Fx_A[:, 0] ** 2 + Fx_A[:, 1] ** 2 + Ftx_B[:, 0] ** 2 + Ftx_B[:, 1] ** 2
    return num / (den + 1e-12)


def reprojection_error_at_depth(
    K_A: np.ndarray, K_B: np.ndarray, R: np.ndarray, t: np.ndarray,
    pts_A: np.ndarray, pts_B: np.ndarray, Z: float = 10.0,
) -> np.ndarray:
    """Assume each xA is at depth Z (meters) in cam_A frame; project to cam_B; return px error vs actual xB."""
    N = len(pts_A)
    pts_A_h = np.hstack([pts_A, np.ones((N, 1))])
    rays_A = (np.linalg.inv(K_A) @ pts_A_h.T).T              # N x 3 (camera-frame rays, z=1)
    X_A = rays_A * Z                                          # N x 3 (3D at depth Z)
    X_B = (R @ X_A.T).T + t                                   # N x 3 in cam_B frame
    x_B_h = (K_B @ X_B.T).T                                   # N x 3
    x_B_pred = x_B_h[:, :2] / np.maximum(np.abs(x_B_h[:, 2:3]), 1e-6)
    err = np.linalg.norm(x_B_pred - pts_B, axis=1)
    return err


def match_pair(img_A_gray: np.ndarray, img_B_gray: np.ndarray,
               sift, bf, ratio: float = 0.75) -> tuple[np.ndarray, np.ndarray]:
    kp_A, des_A = sift.detectAndCompute(img_A_gray, None)
    kp_B, des_B = sift.detectAndCompute(img_B_gray, None)
    if des_A is None or des_B is None or len(kp_A) < 10 or len(kp_B) < 10:
        return np.empty((0, 2)), np.empty((0, 2))
    matches = bf.knnMatch(des_A, des_B, k=2)
    good = [m[0] for m in matches if len(m) == 2 and m[0].distance < ratio * m[1].distance]
    if len(good) < 10:
        return np.empty((0, 2)), np.empty((0, 2))
    pts_A = np.array([kp_A[m.queryIdx].pt for m in good], dtype=np.float64)
    pts_B = np.array([kp_B[m.trainIdx].pt for m in good], dtype=np.float64)
    return pts_A, pts_B


def analyze(loader: AV2RingLoader, anchor_indices: list[int], sift_nfeat: int = 4000):
    sift = cv2.SIFT_create(nfeatures=sift_nfeat)
    bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
    ts = loader.anchor_timestamps_ns()
    results = []

    for a_idx in anchor_indices:
        if a_idx >= len(ts):
            continue
        frame = loader.load_synced_frame(ts[a_idx])
        for cam_A, cam_B in PAIRS:
            img_A = cv2.cvtColor(frame.images[cam_A], cv2.COLOR_RGB2GRAY)
            img_B = cv2.cvtColor(frame.images[cam_B], cv2.COLOR_RGB2GRAY)
            pts_A, pts_B = match_pair(img_A, img_B, sift, bf)
            n_match = len(pts_A)

            entry: dict = {
                "anchor_idx": int(a_idx),
                "cam_A": cam_A,
                "cam_B": cam_B,
                "num_matches": int(n_match),
            }

            if n_match < 30:
                entry["status"] = "too_few_matches"
                results.append(entry)
                continue

            cal_A = frame.calibrations[cam_A]
            cal_B = frame.calibrations[cam_B]
            R, t = relative_pose(cal_A.T_ego_cam, cal_B.T_ego_cam)
            F_calib = fundamental_from_calib(cal_A.K, cal_B.K, R, t)
            baseline = float(np.linalg.norm(t))

            # Step 1: RANSAC-fit a data-driven F to throw out SIFT mismatches.
            # Threshold 3 px = matches must agree with SOME 2-view geometry within 3 px.
            # This is INDEPENDENT of our calibration F.
            F_data, ransac_mask = cv2.findFundamentalMat(
                pts_A, pts_B, cv2.FM_RANSAC, ransacReprojThreshold=3.0, confidence=0.99,
            )
            if F_data is None or ransac_mask is None:
                entry["status"] = "ransac_failed"
                entry["baseline_m"] = baseline
                results.append(entry)
                continue
            ransac_mask = ransac_mask.ravel().astype(bool)
            pts_A_inl = pts_A[ransac_mask]
            pts_B_inl = pts_B[ransac_mask]
            n_inl = int(ransac_mask.sum())
            if n_inl < 15:
                entry["status"] = "too_few_inliers"
                entry["baseline_m"] = baseline
                entry["num_ransac_inliers"] = n_inl
                results.append(entry)
                continue

            # Step 2: Sampson distance to BOTH calibration F and data-driven F, on RANSAC inliers.
            # If calibration ≈ data, both distances are small => calibration correct.
            # If calib_dist >> data_dist => calibration is biased (epipolar geometry doesn't match).
            sd_calib = np.sqrt(np.maximum(sampson_distance(F_calib, pts_A_inl, pts_B_inl), 0.0))
            sd_data = np.sqrt(np.maximum(sampson_distance(F_data,  pts_A_inl, pts_B_inl), 0.0))

            # Parallax sanity check: reprojection error assuming Z=10m
            rep_err_10m = reprojection_error_at_depth(
                cal_A.K, cal_B.K, R, t, pts_A_inl, pts_B_inl, Z=10.0,
            )

            entry.update(
                status="ok",
                num_ransac_inliers=n_inl,
                inlier_rate=float(n_inl) / float(n_match),
                baseline_m=baseline,
                sampson_calib_median_px=float(np.median(sd_calib)),
                sampson_calib_mean_px=float(np.mean(sd_calib)),
                sampson_calib_p95_px=float(np.percentile(sd_calib, 95)),
                sampson_data_median_px=float(np.median(sd_data)),
                sampson_data_mean_px=float(np.mean(sd_data)),
                calib_bias_px=float(np.median(sd_calib) - np.median(sd_data)),
                reproj_at_10m_median_px=float(np.median(rep_err_10m)),
            )
            results.append(entry)
    return results


def summarize(results: list[dict]) -> dict:
    by_pair: dict[str, dict] = {}
    for r in results:
        if r.get("status") != "ok":
            continue
        key = f"{r['cam_A']} -> {r['cam_B']}"
        d = by_pair.setdefault(key, {"calib": [], "data": [], "bias": [], "inlier_rate": []})
        d["calib"].append(r["sampson_calib_median_px"])
        d["data"].append(r["sampson_data_median_px"])
        d["bias"].append(r["calib_bias_px"])
        d["inlier_rate"].append(r["inlier_rate"])

    pair_summary = {
        k: {
            "n_anchors": len(v["calib"]),
            "sampson_calib_median_px": float(np.median(v["calib"])),
            "sampson_data_median_px":  float(np.median(v["data"])),
            "calib_bias_median_px":    float(np.median(v["bias"])),
            "calib_bias_max_px":       float(np.max(v["bias"])),
            "inlier_rate_median":      float(np.median(v["inlier_rate"])),
        }
        for k, v in by_pair.items()
    }
    all_calib   = [r["sampson_calib_median_px"] for r in results if r.get("status") == "ok"]
    all_data    = [r["sampson_data_median_px"]  for r in results if r.get("status") == "ok"]
    all_bias    = [r["calib_bias_px"]           for r in results if r.get("status") == "ok"]
    global_summary = {
        "n_pair_observations": len(all_calib),
        "global_sampson_calib_median_px": float(np.median(all_calib)) if all_calib else None,
        "global_sampson_data_median_px":  float(np.median(all_data))  if all_data  else None,
        "global_calib_bias_median_px":    float(np.median(all_bias))  if all_bias  else None,
    }
    return {"per_pair": pair_summary, "global": global_summary}


def render_summary_png(results: list[dict], out_path: Path) -> None:
    import matplotlib.pyplot as plt

    ok = [r for r in results if r.get("status") == "ok"]
    if not ok:
        return
    pairs = sorted({f"{r['cam_A']} -> {r['cam_B']}" for r in ok})
    labels = [p.replace("ring_", "").replace(" -> ", "→\n") for p in pairs]
    fig, axes = plt.subplots(1, 2, figsize=(16, 5))

    # Left: calibration F vs data F per-pair (both should be small if calib is right)
    ax = axes[0]
    calib_data = [[r["sampson_calib_median_px"] for r in ok if f"{r['cam_A']} -> {r['cam_B']}" == p] for p in pairs]
    data_data  = [[r["sampson_data_median_px"]  for r in ok if f"{r['cam_A']} -> {r['cam_B']}" == p] for p in pairs]
    positions = np.arange(len(pairs))
    bp1 = ax.boxplot(calib_data, positions=positions - 0.18, widths=0.3,
                     patch_artist=True, boxprops=dict(facecolor="lightcoral"))
    bp2 = ax.boxplot(data_data,  positions=positions + 0.18, widths=0.3,
                     patch_artist=True, boxprops=dict(facecolor="lightgreen"))
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_ylabel("Sampson distance (px) — per anchor median")
    ax.set_title("calib F (red) vs data-driven F (green)\nlarge red+small green = calibration bias")
    ax.axhline(0.5, color="green", linestyle="--", alpha=0.4)
    ax.axhline(2.0, color="red",   linestyle="--", alpha=0.4)
    ax.set_yscale("log")
    ax.grid(True, which="both", alpha=0.3)

    # Right: pure bias (calib - data); positive = calibration is worse than data fit
    ax = axes[1]
    bias = [[r["calib_bias_px"] for r in ok if f"{r['cam_A']} -> {r['cam_B']}" == p] for p in pairs]
    ax.boxplot(bias, tick_labels=labels)
    ax.set_ylabel("calib bias (px) = sampson(calib_F) - sampson(data_F)")
    ax.set_title("calibration bias per pair\n0 = perfect | >2 px = real bias")
    ax.axhline(0.0, color="black", linewidth=1)
    ax.axhline(2.0, color="red", linestyle="--", alpha=0.4, label="bias threshold")
    ax.grid(True, alpha=0.3)
    plt.xticks(rotation=15, ha="right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-indices", required=True, help="comma-separated anchor indices, e.g. 0,30,60")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-png", default=None)
    ap.add_argument("--sift-nfeat", type=int, default=4000)
    args = ap.parse_args()

    anchors = [int(x) for x in args.anchor_indices.split(",") if x.strip()]
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    loader = AV2RingLoader(Path(args.log_dir))
    print(f"[load] {args.log_dir}  ({loader.num_anchor_frames()} anchors)")
    results = analyze(loader, anchors, sift_nfeat=args.sift_nfeat)
    summary = summarize(results)

    with open(out_json, "w") as f:
        json.dump({"per_observation": results, "summary": summary}, f, indent=2)
    print(f"[json] {out_json}")
    print(json.dumps(summary, indent=2))

    if args.out_png:
        out_png = Path(args.out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        render_summary_png(results, out_png)
        print(f"[png]  {out_png}")


if __name__ == "__main__":
    main()
