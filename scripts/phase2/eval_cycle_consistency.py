"""
Phase 2 P2.7 — Cycle-consistency 量化评估 (L1 vs L3).

对每个 cam 做 hold-out:
  ground truth = AV2 该 cam 的 504x504 letterboxed input image (已存在 pi3_one_frame/ 里)
  L1 reconstruction = 用其它 6 cam, 反向球面投影到 held-out cam 像平面 (cam → ego → cam)
  L3 reconstruction = 用其它 6 cam 的 Pi3 3D 点, 转到 held-out cam 平面 + z-buffer
  metrics = PSNR, SSIM, L1 (mean abs diff) on the intersection mask

输出:
  cycle_consistency.json          全部数字 + per-cam 表
  reconstruction_{cam}.png         3 panel: GT / L1 / L3 (debug 用)
  cycle_consistency_bars.png       L1 vs L3 PSNR per cam 柱状图 (可选, 用 PIL 自绘)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def psnr(a: np.ndarray, b: np.ndarray, mask: Optional[np.ndarray] = None, max_val: float = 255.0) -> float:
    """PSNR on uint8 RGB. If mask provided, score only where mask is True."""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    if mask is None:
        mse = np.mean((a - b) ** 2)
    else:
        if mask.sum() == 0:
            return float("nan")
        diff_sq = (a - b) ** 2
        # average over RGB channels, then over masked pixels
        mse = diff_sq[mask].mean()
    if mse <= 1e-12:
        return float("inf")
    return 20.0 * np.log10(max_val) - 10.0 * np.log10(mse)


def ssim_masked(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    """SSIM via skimage. Computes on full image then averages over masked pixels.
    If skimage unavailable, returns NaN."""
    try:
        from skimage.metrics import structural_similarity as ssim
    except ImportError:
        return float("nan")
    a_g = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]).astype(np.float64)
    b_g = (0.299 * b[..., 0] + 0.587 * b[..., 1] + 0.114 * b[..., 2]).astype(np.float64)
    if mask.sum() == 0:
        return float("nan")
    # skimage SSIM returns full = per-pixel map when full=True
    _, ssim_map = ssim(a_g, b_g, data_range=255.0, full=True)
    return float(ssim_map[mask].mean())


def l1_mae(a: np.ndarray, b: np.ndarray, mask: Optional[np.ndarray] = None) -> float:
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    diff = np.abs(a - b)
    if mask is None:
        return float(diff.mean())
    if mask.sum() == 0:
        return float("nan")
    return float(diff[mask].mean())


# ---- reconstruction algorithms ----


def reconstruct_l1(
    holdout_cam: str,
    other_cams: list[str],
    cam_K: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    cam_rgb: dict[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    """L1 reconstruction: for each pixel in held-out cam, find which OTHER cam(s)
    see that ego direction, sample, cos^2 feather blend. Returns (rgb, mask)."""
    import cv2  # noqa: PLC0415

    K_h = cam_K[holdout_cam]
    T_ego_cam_h = cam_T_ego_cam[holdout_cam]
    H, W = cam_rgb[holdout_cam].shape[:2]

    # Build held-out pixel grid -> ego unit ray
    uu, vv = np.meshgrid(np.arange(W), np.arange(H))
    pix_h = np.stack([uu, vv, np.ones_like(uu)], axis=-1).astype(np.float64)  # (H, W, 3)
    K_h_inv = np.linalg.inv(K_h)
    d_cam_h = pix_h @ K_h_inv.T              # (H, W, 3) cam-frame ray
    d_cam_h = d_cam_h / np.linalg.norm(d_cam_h, axis=-1, keepdims=True)
    R_ego_cam_h = T_ego_cam_h[:3, :3]
    d_ego = d_cam_h @ R_ego_cam_h.T          # (H, W, 3) ego-frame ray

    rgb_sum = np.zeros((H, W, 3), dtype=np.float32)
    w_sum = np.zeros((H, W), dtype=np.float32)

    for cam_j in other_cams:
        K_j = cam_K[cam_j]
        T_ego_cam_j = cam_T_ego_cam[cam_j]
        rgb_j = cam_rgb[cam_j].astype(np.float32)
        H_j, W_j = rgb_j.shape[:2]

        # Rotate ego ray into cam_j frame (R_cam_ego_j = R_ego_cam_j^T)
        R_cam_ego_j = T_ego_cam_j[:3, :3].T
        d_cam_j = d_ego @ R_cam_ego_j.T

        z_j = d_cam_j[..., 2]
        in_front = z_j > 1e-6
        z_safe = np.where(in_front, z_j, 1.0)
        u_j = K_j[0, 0] * d_cam_j[..., 0] / z_safe + K_j[0, 2]
        v_j = K_j[1, 1] * d_cam_j[..., 1] / z_safe + K_j[1, 2]

        margin = 0.5
        in_bounds = (u_j >= margin) & (u_j <= W_j - 1 - margin) & (v_j >= margin) & (v_j <= H_j - 1 - margin)
        valid = in_front & in_bounds

        map_x = np.where(valid, u_j, -1.0).astype(np.float32)
        map_y = np.where(valid, v_j, -1.0).astype(np.float32)
        sampled = cv2.remap(
            rgb_j, map_x, map_y, interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0.0, 0.0, 0.0),
        )
        # cos^2 feather based on angle from cam_j optical axis (z is forward)
        cos_axis = np.clip(z_j, 0.0, 1.0)
        w_j = (cos_axis ** 2) * valid.astype(np.float32)

        rgb_sum += sampled * w_j[..., None]
        w_sum += w_j

    w_safe = np.where(w_sum > 1e-6, w_sum, 1.0)
    rgb_out = rgb_sum / w_safe[..., None]
    rgb_out = np.where(w_sum[..., None] > 1e-6, rgb_out, 0.0)
    mask = w_sum > 1e-6
    return rgb_out.astype(np.float32), mask


def reconstruct_l3(
    holdout_cam: str,
    other_cams: list[str],
    cam_K: dict[str, np.ndarray],
    cam_T_ego_cam: dict[str, np.ndarray],
    cam_rgb: dict[str, np.ndarray],
    cam_points_ego: dict[str, np.ndarray],
    cam_conf: dict[str, np.ndarray],
    conf_threshold: float = 0.3,
    min_dist: float = 0.5,
    max_dist: float = 60.0,
) -> tuple[np.ndarray, np.ndarray]:
    """L3 reconstruction: project OTHER cams' Pi3 3D points (already in ego frame
    after Sim3) into held-out cam image plane. Z-buffer keeps nearest. Returns (rgb, mask).
    """
    T_ego_cam_h = cam_T_ego_cam[holdout_cam]
    K_h = cam_K[holdout_cam]
    H, W = cam_rgb[holdout_cam].shape[:2]

    R_cam_ego_h = T_ego_cam_h[:3, :3].T
    t_cam_ego_h = -R_cam_ego_h @ T_ego_cam_h[:3, 3]

    # Collect all valid projected points from all other cams
    all_ui: list[np.ndarray] = []
    all_vi: list[np.ndarray] = []
    all_rgb: list[np.ndarray] = []
    all_z: list[np.ndarray] = []
    for cam_j in other_cams:
        pts_ego = cam_points_ego[cam_j]              # (Hj, Wj, 3)
        conf = cam_conf[cam_j]                        # (Hj, Wj)
        rgb_j = cam_rgb[cam_j]                        # (Hj, Wj, 3)

        pts_h = pts_ego @ R_cam_ego_h.T + t_cam_ego_h[None, None, :]
        z_h = pts_h[..., 2]
        z_safe = np.maximum(z_h, 1e-6)
        u_h = K_h[0, 0] * pts_h[..., 0] / z_safe + K_h[0, 2]
        v_h = K_h[1, 1] * pts_h[..., 1] / z_safe + K_h[1, 2]
        r = np.linalg.norm(pts_ego, axis=-1)

        valid = (
            (z_h > 0)
            & (u_h >= 0) & (u_h < W) & (v_h >= 0) & (v_h < H)
            & (r > min_dist) & (r < max_dist)
            & (conf > conf_threshold)
        )
        if not np.any(valid):
            continue
        all_ui.append(np.round(u_h[valid]).astype(np.int64))
        all_vi.append(np.round(v_h[valid]).astype(np.int64))
        all_rgb.append(rgb_j[valid].astype(np.float32))
        all_z.append(z_h[valid].astype(np.float32))

    if not all_ui:
        canvas = np.zeros((H, W, 3), dtype=np.float32)
        return canvas, np.zeros((H, W), dtype=bool)

    ui = np.concatenate(all_ui)
    vi = np.concatenate(all_vi)
    rgb_v = np.concatenate(all_rgb)
    z_v = np.concatenate(all_z)

    # Z-buffer: sort by depth ascending (nearest first); first occurrence wins
    order = np.argsort(z_v)
    ui_s = ui[order]; vi_s = vi[order]; rgb_s = rgb_v[order]
    flat = vi_s.astype(np.int64) * W + ui_s
    _, first = np.unique(flat, return_index=True)
    keep_ui = ui_s[first]; keep_vi = vi_s[first]; keep_rgb = rgb_s[first]

    canvas = np.zeros((H, W, 3), dtype=np.float32)
    canvas[keep_vi, keep_ui] = keep_rgb
    mask = np.zeros((H, W), dtype=bool)
    mask[keep_vi, keep_ui] = True
    return canvas, mask


def _make_bars_png(
    cams: list[str],
    psnr_l1: list[float],
    psnr_l3: list[float],
    out_path: Path,
) -> None:
    """Cheap horizontal bar chart without matplotlib. Each row: cam name + L1 bar (white) + L3 bar (yellow)."""
    cell_h = 30
    name_w = 240
    bar_w = 400
    max_psnr = max(max(psnr_l1, default=0), max(psnr_l3, default=0), 30.0)
    H = (len(cams) + 2) * cell_h + 40
    W = name_w + bar_w * 2 + 40
    img = Image.new("RGB", (W, H), color=(20, 20, 24))
    from PIL import ImageDraw, ImageFont  # noqa: PLC0415
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 16)
        big = ImageFont.truetype("DejaVuSans.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
        big = font
    draw.text((10, 10), "Cycle-consistency PSNR: L1 (white) vs L3 (yellow)", fill=(255, 255, 255), font=big)
    for i, cam in enumerate(cams):
        y = 50 + i * cell_h
        draw.text((10, y + 4), cam, fill=(220, 220, 220), font=font)
        l1_len = int(bar_w * psnr_l1[i] / max_psnr)
        draw.rectangle([(name_w, y), (name_w + l1_len, y + cell_h // 2 - 2)], fill=(240, 240, 240))
        draw.text((name_w + l1_len + 6, y), f"{psnr_l1[i]:.2f}", fill=(200, 200, 200), font=font)
        l3_len = int(bar_w * psnr_l3[i] / max_psnr)
        draw.rectangle([(name_w, y + cell_h // 2 + 2), (name_w + l3_len, y + cell_h - 2)], fill=(255, 220, 70))
        draw.text((name_w + l3_len + 6, y + cell_h // 2 + 2), f"{psnr_l3[i]:.2f}", fill=(255, 200, 100), font=font)
    img.save(out_path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pi3-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--conf-threshold", type=float, default=0.3)
    ap.add_argument("--min-distance-m", type=float, default=0.5)
    ap.add_argument("--max-distance-m", type=float, default=60.0)
    ap.add_argument("--save-recon-pngs", action="store_true",
                    help="save per-cam 3-panel reconstruction PNG (GT/L1/L3)")
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.alignment.sim3_align import fit_sim3_from_camera_translations
    from waymo2panorama.pipeline.lift_and_project import apply_sim3_to_points

    pi3_dir = Path(args.pi3_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pi3_summary = json.loads((pi3_dir / "summary.json").read_text())
    cams = pi3_summary["cameras"]

    # ---- Load per-cam data ----
    cam_K: dict[str, np.ndarray] = {}
    cam_T_ego_cam: dict[str, np.ndarray] = {}
    cam_rgb: dict[str, np.ndarray] = {}
    cam_points_world: dict[str, np.ndarray] = {}
    cam_conf: dict[str, np.ndarray] = {}
    pi3_cam_pos: dict[str, np.ndarray] = {}
    av2_cam_pos: dict[str, np.ndarray] = {}

    for cam in cams:
        cam_K[cam] = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
        cam_T_ego_cam[cam] = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
        img = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
        cam_rgb[cam] = img.astype(np.uint8)
        cam_points_world[cam] = np.load(pi3_dir / f"points_{cam}.npy")
        conf_raw = np.load(pi3_dir / f"conf_{cam}.npy")
        cam_conf[cam] = _sigmoid(conf_raw).astype(np.float32)
        pose_pi3 = np.load(pi3_dir / f"pose_{cam}.npy")
        pi3_cam_pos[cam] = pose_pi3[:3, 3].astype(np.float64)
        av2_cam_pos[cam] = cam_T_ego_cam[cam][:3, 3].astype(np.float64)

    # ---- Fit Sim(3) using ALL 7 cams (we want the canonical alignment) ----
    sim3, sim3_diag = fit_sim3_from_camera_translations(pi3_cam_pos, av2_cam_pos)
    print(f"[cycle] Sim(3): scale={sim3.scale:.4f}, mean_residual={sim3_diag['mean_residual_m']:.3f} m")

    cam_points_ego: dict[str, np.ndarray] = {
        cam: apply_sim3_to_points(cam_points_world[cam], sim3) for cam in cams
    }

    # ---- For each cam: hold out, reconstruct via L1 and L3, score ----
    rows: list[dict] = []
    print(f"{'cam':22s}  {'cov_L1':>7s}  {'cov_L3':>7s}  {'cov_∩':>7s}  "
          f"{'PSNR_L1':>8s}  {'PSNR_L3':>8s}  ΔPSNR  {'SSIM_L1':>8s}  {'SSIM_L3':>8s}  ΔSSIM  "
          f"{'MAE_L1':>7s}  {'MAE_L3':>7s}")
    print("-" * 130)

    for holdout in cams:
        others = [c for c in cams if c != holdout]
        gt = cam_rgb[holdout]
        H, W = gt.shape[:2]

        l1_rgb, l1_mask = reconstruct_l1(holdout, others, cam_K, cam_T_ego_cam, cam_rgb)
        l3_rgb, l3_mask = reconstruct_l3(
            holdout, others, cam_K, cam_T_ego_cam, cam_rgb,
            cam_points_ego, cam_conf,
            conf_threshold=args.conf_threshold,
            min_dist=args.min_distance_m,
            max_dist=args.max_distance_m,
        )

        l1_rgb_u8 = np.clip(l1_rgb, 0, 255).astype(np.uint8)
        l3_rgb_u8 = np.clip(l3_rgb, 0, 255).astype(np.uint8)
        intersect = l1_mask & l3_mask

        cov_l1 = float(l1_mask.mean())
        cov_l3 = float(l3_mask.mean())
        cov_inter = float(intersect.mean())

        psnr_l1 = psnr(gt, l1_rgb_u8, mask=intersect)
        psnr_l3 = psnr(gt, l3_rgb_u8, mask=intersect)
        ssim_l1 = ssim_masked(gt, l1_rgb_u8, intersect)
        ssim_l3 = ssim_masked(gt, l3_rgb_u8, intersect)
        mae_l1 = l1_mae(gt, l1_rgb_u8, mask=intersect)
        mae_l3 = l1_mae(gt, l3_rgb_u8, mask=intersect)

        rows.append({
            "cam": holdout,
            "coverage_L1": cov_l1, "coverage_L3": cov_l3, "coverage_intersection": cov_inter,
            "PSNR_L1": psnr_l1, "PSNR_L3": psnr_l3, "PSNR_delta_L3_minus_L1": psnr_l3 - psnr_l1,
            "SSIM_L1": ssim_l1, "SSIM_L3": ssim_l3, "SSIM_delta_L3_minus_L1": ssim_l3 - ssim_l1,
            "MAE_L1": mae_l1, "MAE_L3": mae_l3,
        })
        d_psnr = psnr_l3 - psnr_l1
        d_ssim = ssim_l3 - ssim_l1
        print(f"{holdout:22s}  {cov_l1:7.1%}  {cov_l3:7.1%}  {cov_inter:7.1%}  "
              f"{psnr_l1:8.2f}  {psnr_l3:8.2f}  {d_psnr:+5.2f}  "
              f"{ssim_l1:8.3f}  {ssim_l3:8.3f}  {d_ssim:+5.3f}  "
              f"{mae_l1:7.2f}  {mae_l3:7.2f}")

        if args.save_recon_pngs:
            gap = 4
            panel = np.full((H, 3 * W + 2 * gap, 3), 32, dtype=np.uint8)
            panel[:, :W] = gt
            panel[:, W + gap:2 * W + gap] = l1_rgb_u8
            panel[:, 2 * W + 2 * gap:] = l3_rgb_u8
            Image.fromarray(panel).save(out_dir / f"reconstruction_{holdout}.png")

    # ---- Aggregate ----
    mean_psnr_l1 = float(np.mean([r["PSNR_L1"] for r in rows if np.isfinite(r["PSNR_L1"])]))
    mean_psnr_l3 = float(np.mean([r["PSNR_L3"] for r in rows if np.isfinite(r["PSNR_L3"])]))
    mean_ssim_l1 = float(np.nanmean([r["SSIM_L1"] for r in rows]))
    mean_ssim_l3 = float(np.nanmean([r["SSIM_L3"] for r in rows]))
    mean_mae_l1 = float(np.mean([r["MAE_L1"] for r in rows]))
    mean_mae_l3 = float(np.mean([r["MAE_L3"] for r in rows]))

    print("-" * 130)
    print(f"{'MEAN':22s}                            "
          f"{mean_psnr_l1:8.2f}  {mean_psnr_l3:8.2f}  {mean_psnr_l3 - mean_psnr_l1:+5.2f}  "
          f"{mean_ssim_l1:8.3f}  {mean_ssim_l3:8.3f}  {mean_ssim_l3 - mean_ssim_l1:+5.3f}  "
          f"{mean_mae_l1:7.2f}  {mean_mae_l3:7.2f}")

    # ---- Bar chart ----
    _make_bars_png(
        cams,
        [r["PSNR_L1"] for r in rows],
        [r["PSNR_L3"] for r in rows],
        out_dir / "cycle_consistency_bars.png",
    )

    # ---- Summary JSON ----
    summary = {
        "pi3_dir": str(pi3_dir),
        "conf_threshold": args.conf_threshold,
        "min_distance_m": args.min_distance_m,
        "max_distance_m": args.max_distance_m,
        "sim3": {
            "scale": sim3.scale,
            "mean_residual_m": sim3_diag["mean_residual_m"],
            "max_residual_m": sim3_diag["max_residual_m"],
        },
        "metric": "PSNR/SSIM/MAE on intersection mask (pixels both methods can reconstruct)",
        "per_cam": rows,
        "mean": {
            "PSNR_L1": mean_psnr_l1, "PSNR_L3": mean_psnr_l3,
            "PSNR_delta_L3_minus_L1": mean_psnr_l3 - mean_psnr_l1,
            "SSIM_L1": mean_ssim_l1, "SSIM_L3": mean_ssim_l3,
            "SSIM_delta_L3_minus_L1": mean_ssim_l3 - mean_ssim_l1,
            "MAE_L1": mean_mae_l1, "MAE_L3": mean_mae_l3,
        },
        "verdict_hint": (
            "L3 PSNR > L1 by >= 1 dB -> L3 quantitatively wins. "
            "L3 PSNR ~ L1 (within 0.5 dB) -> forward-splat ineffective for this scene. "
            "L3 PSNR < L1 -> negative confirmed; abandon forward-splat path."
        ),
    }
    (out_dir / "cycle_consistency.json").write_text(json.dumps(summary, indent=2))
    print(f"[cycle] wrote {out_dir / 'cycle_consistency.json'}")
    print(f"[cycle] wrote {out_dir / 'cycle_consistency_bars.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
