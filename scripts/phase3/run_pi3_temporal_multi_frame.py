"""
T12 — Multi-frame temporal Pi3 inference (7×K view joint forward pass).

Hypothesis (original idea):
  Pi3 is permutation-equivariant over the N-view dimension. If we feed K
  consecutive ego frames × 7 ring cams = 7K views in a SINGLE forward pass,
  the model implicitly gets *temporal stereo* baselines (ego moved between
  frames) on top of the 7-cam spatial baselines. This should reduce Pi3's
  structural far-field depth compression (Phase 3 P3.3 found −24% bias at
  >40m, monotonic with depth).

Approach:
  - center anchor index N, K consecutive frames at indices N-K//2 ... N+K//2
    (handles edge cases by clipping into the valid range and re-centering)
  - per frame, letterbox 7 cams to TARGET_SIDE × TARGET_SIDE (same recipe as
    run_pi3_multi_anchor.py — shared helpers re-used here verbatim so geometry
    is identical to Phase 3 W1)
  - stack to (1, 7K, 3, H, W); single Pi3X forward (bf16 on A100)
  - decompose outputs into per-(frame_local_idx, cam) folders so existing
    single-frame eval pipelines (eval_pi3_vs_lidar, eval_pi3_lidar_binned)
    work on each one unchanged.

Output layout (under <output_dir>):
    frame_000/  ... summary.json + the same npy set as run_pi3_multi_anchor
    frame_001/  (center frame for K=3; this is what gets LiDAR-evaluated)
    frame_002/
    temporal_summary.json   K, anchor_idx, frame_indices, fwd_s, peak_mem
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

DEFAULT_PI3_REPO_REL = "../../../../../01-pi3/code/official/Pi3"
DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(pi3_repo: Path, w2p_code: Path) -> None:
    for p in (pi3_repo, w2p_code):
        if not p.exists():
            raise FileNotFoundError(f"missing: {p}")
        sys.path.insert(0, str(p))


def letterbox_to_square(img: np.ndarray, target_side: int = 504) -> tuple[np.ndarray, dict]:
    h, w, c = img.shape
    side = max(h, w)
    pad_top = (side - h) // 2
    pad_left = (side - w) // 2
    sq = np.zeros((side, side, c), dtype=img.dtype)
    sq[pad_top:pad_top + h, pad_left:pad_left + w] = img
    pil = Image.fromarray(sq).resize((target_side, target_side), Image.Resampling.LANCZOS)
    out = np.asarray(pil)
    scale = target_side / side
    return out, {"pad_top": pad_top, "pad_left": pad_left, "scale": scale, "side": side}


def rescale_K_for_letterbox(K: np.ndarray, lb: dict) -> np.ndarray:
    K2 = K.astype(np.float64).copy()
    K2[0, 2] += lb["pad_left"]
    K2[1, 2] += lb["pad_top"]
    K2[0, 0] *= lb["scale"]
    K2[1, 1] *= lb["scale"]
    K2[0, 2] *= lb["scale"]
    K2[1, 2] *= lb["scale"]
    return K2


def _pick_window(center_idx: int, k: int, n_total: int) -> list[int]:
    """Pick K consecutive anchor indices centered on center_idx, clamped to
    [0, n_total). If we hit a boundary, slide the window to keep K frames.
    """
    if k <= 0:
        raise ValueError(f"frames-per-window must be >=1, got {k}")
    if k > n_total:
        raise ValueError(f"frames-per-window {k} > anchors-in-log {n_total}")
    half = k // 2
    start = center_idx - half
    end = start + k
    if start < 0:
        start = 0
        end = k
    if end > n_total:
        end = n_total
        start = end - k
    return list(range(start, end))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-idx", type=int, required=True,
                    help="CENTER anchor index — the frame whose depth we'll compare to LiDAR.")
    ap.add_argument("--frames-per-window", type=int, default=3,
                    help="K consecutive frames around anchor-idx (default 3, options 2/3/5)")
    ap.add_argument("--output-dir", required=True,
                    help="creates frame_<local_idx:03d>/ subdir per frame")
    ap.add_argument("--pi3-repo", default=None)
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--target-side", type=int, default=504,
                    help="Pi3 input square side; must be divisible by 14 (504, 392, 280 ...)")
    args = ap.parse_args()

    if args.target_side % 14 != 0:
        raise ValueError(f"target_side={args.target_side} not divisible by 14")

    here = Path(__file__).resolve().parent
    pi3_repo = Path(args.pi3_repo) if args.pi3_repo else (here / DEFAULT_PI3_REPO_REL).resolve()
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(pi3_repo, w2p_code)

    import torch
    from pi3.models.pi3x import Pi3X
    from pi3.utils.geometry import recover_intrinsic_from_rays_d
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7

    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    loader = AV2RingLoader(Path(args.log_dir))
    ts_all = loader.anchor_timestamps_ns()
    n_total = len(ts_all)
    frame_indices = _pick_window(args.anchor_idx, args.frames_per_window, n_total)
    center_local_idx = frame_indices.index(args.anchor_idx) if args.anchor_idx in frame_indices else len(frame_indices) // 2
    print(f"[temporal] anchor_idx={args.anchor_idx} K={args.frames_per_window} "
          f"window={frame_indices} center_local_idx={center_local_idx} n_anchors_in_log={n_total}")

    # --- Load + letterbox all K*V views, build big tensor ---
    V = len(RING_CAMS_7)
    K = len(frame_indices)
    NV = K * V

    all_imgs_np: list[np.ndarray] = []
    # per (frame_local_idx, cam) bookkeeping
    per_view_K_lb: dict[tuple[int, str], np.ndarray] = {}
    per_view_T_ego_cam: dict[tuple[int, str], np.ndarray] = {}
    per_view_letterbox: dict[tuple[int, str], dict] = {}
    per_view_image_lb: dict[tuple[int, str], np.ndarray] = {}
    per_frame_anchor_ts: list[int] = []

    for f_local, abs_idx in enumerate(frame_indices):
        anchor_ts = ts_all[abs_idx]
        per_frame_anchor_ts.append(int(anchor_ts))
        frame = loader.load_synced_frame(anchor_ts)
        for cam in RING_CAMS_7:
            img = frame.images[cam]
            calib = frame.calibrations[cam]
            sq, lb = letterbox_to_square(img, target_side=args.target_side)
            K_lb = rescale_K_for_letterbox(calib.K, lb)
            all_imgs_np.append(sq)
            per_view_K_lb[(f_local, cam)] = K_lb
            per_view_T_ego_cam[(f_local, cam)] = calib.T_ego_cam
            per_view_letterbox[(f_local, cam)] = lb
            per_view_image_lb[(f_local, cam)] = sq

    arr = np.stack(all_imgs_np, axis=0).astype(np.float32) / 255.0  # (NV, H, W, 3)
    arr = np.transpose(arr, (0, 3, 1, 2))                             # (NV, 3, H, W)
    imgs_tensor = torch.from_numpy(arr).unsqueeze(0)                  # (1, NV, 3, H, W)

    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    print(f"[temporal] device={device} input_shape={tuple(imgs_tensor.shape)} (K={K}, V={V}, total views={NV})")
    imgs_tensor = imgs_tensor.to(device)

    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.empty_cache()

    t_load_start = time.time()
    model = Pi3X.from_pretrained("yyfz233/Pi3X").eval()
    model.disable_multimodal()
    model = model.to(device)
    t_load_s = time.time() - t_load_start

    use_bf16 = device.type == "cuda" and torch.cuda.get_device_capability()[0] >= 8

    t_fwd_start = time.time()
    try:
        with torch.no_grad():
            if use_bf16:
                with torch.amp.autocast("cuda", dtype=torch.bfloat16):
                    res = model(imgs=imgs_tensor)
            else:
                res = model(imgs=imgs_tensor)
        if device.type == "cuda":
            torch.cuda.synchronize()
    except torch.cuda.OutOfMemoryError as e:
        peak_mem_mb = torch.cuda.max_memory_allocated() / 1024**2
        msg = (f"[temporal] OOM during forward at K={K}, V={V}, target_side={args.target_side}, "
               f"peak_mem_mb={peak_mem_mb:.0f}. Try smaller K or target_side.")
        print(msg)
        (out_root / "oom_failure.json").write_text(
            json.dumps({"status": "oom", "K": K, "V": V, "target_side": args.target_side,
                        "peak_mem_mb": peak_mem_mb, "error": str(e)}, indent=2),
            encoding="utf-8",
        )
        raise
    t_fwd_s = time.time() - t_fwd_start

    rays_d = torch.nn.functional.normalize(res["local_points"], dim=-1)
    K_recovered = recover_intrinsic_from_rays_d(rays_d, force_center_principal_point=True)

    peak_mem_mb = None
    if device.type == "cuda":
        peak_mem_mb = torch.cuda.max_memory_allocated() / 1024**2

    # res tensors are (1, NV, ...) — slice into K blocks of V views.
    points_full = res["points"][0].detach().float().cpu().numpy()              # (NV, H, W, 3)
    local_points_full = res["local_points"][0].detach().float().cpu().numpy()  # (NV, H, W, 3)
    conf_full = res["conf"][0].detach().float().cpu().numpy()                  # (NV, H, W, 1) or (NV,H,W)
    pose_full = res["camera_poses"][0].detach().float().cpu().numpy()          # (NV, 4, 4)
    K_rec_full = K_recovered[0].detach().float().cpu().numpy()                 # (NV, 3, 3)

    if conf_full.ndim == 4 and conf_full.shape[-1] == 1:
        conf_full = conf_full[..., 0]

    # Free GPU memory before per-frame dump (saves CPU/disk-bottleneck time)
    if device.type == "cuda":
        del res, rays_d, K_recovered, imgs_tensor
        torch.cuda.empty_cache()

    per_frame_records = []
    for f_local in range(K):
        sub = out_root / f"frame_{f_local:03d}"
        sub.mkdir(parents=True, exist_ok=True)
        per_cam_summary: dict[str, dict] = {}
        for c_idx, cam in enumerate(RING_CAMS_7):
            view_idx = f_local * V + c_idx
            np.save(sub / f"points_{cam}.npy", points_full[view_idx])
            np.save(sub / f"local_points_{cam}.npy", local_points_full[view_idx])
            np.save(sub / f"conf_{cam}.npy", conf_full[view_idx])
            np.save(sub / f"pose_{cam}.npy", pose_full[view_idx])
            np.save(sub / f"intrinsic_recovered_{cam}.npy", K_rec_full[view_idx])
            np.save(sub / f"av2_K_letterboxed_{cam}.npy", per_view_K_lb[(f_local, cam)])
            np.save(sub / f"av2_T_ego_cam_{cam}.npy", per_view_T_ego_cam[(f_local, cam)])
            Image.fromarray(per_view_image_lb[(f_local, cam)]).save(sub / f"image_{cam}.png")

            conf_prob = 1.0 / (1.0 + np.exp(-conf_full[view_idx]))
            local_z = local_points_full[view_idx, ..., 2]
            valid = (conf_prob > 0.1) & np.isfinite(local_z) & (local_z > 0)
            per_cam_summary[cam] = {
                "letterbox": per_view_letterbox[(f_local, cam)],
                "conf_pct_gt_0.1": float(valid.mean()),
                "conf_pct_gt_0.5": float(((conf_prob > 0.5) & valid).mean()),
                "local_z_median_when_valid": float(np.median(local_z[valid])) if valid.any() else None,
                "K_recovered": K_rec_full[view_idx].tolist(),
                "K_av2_letterboxed": per_view_K_lb[(f_local, cam)].tolist(),
            }

        # eval scripts expect this exact schema (summary.json with anchor_timestamp_ns)
        frame_summary = {
            "backbone": "Pi3X-temporal",
            "checkpoint": "yyfz233/Pi3X",
            "anchor_idx": frame_indices[f_local],
            "anchor_timestamp_ns": per_frame_anchor_ts[f_local],
            "frame_local_idx": f_local,
            "window_size_K": K,
            "is_center_frame": f_local == center_local_idx,
            "cameras": list(RING_CAMS_7),
            "target_side": args.target_side,
            "letterbox_method": "pad_to_square_then_resize_lanczos",
            "device": str(device),
            "autocast_bf16": use_bf16,
            "forward_s_shared": round(t_fwd_s, 3),
            "peak_gpu_memory_mb_shared": peak_mem_mb,
            "per_cam": per_cam_summary,
        }
        (sub / "summary.json").write_text(json.dumps(frame_summary, indent=2), encoding="utf-8")
        per_frame_records.append({
            "frame_local_idx": f_local,
            "anchor_idx": frame_indices[f_local],
            "anchor_timestamp_ns": per_frame_anchor_ts[f_local],
            "is_center": f_local == center_local_idx,
        })

    temporal_summary = {
        "backbone": "Pi3X-temporal",
        "checkpoint": "yyfz233/Pi3X",
        "log_dir": str(args.log_dir),
        "center_anchor_idx": args.anchor_idx,
        "frames_per_window_K": K,
        "ring_cams_V": V,
        "total_views_NV": NV,
        "frame_indices_abs": frame_indices,
        "center_local_idx": center_local_idx,
        "target_side": args.target_side,
        "input_shape": [1, NV, 3, args.target_side, args.target_side],
        "device": str(device),
        "autocast_bf16": use_bf16,
        "model_load_s": round(t_load_s, 3),
        "forward_s": round(t_fwd_s, 3),
        "peak_gpu_memory_mb": peak_mem_mb,
        "per_frame": per_frame_records,
    }
    (out_root / "temporal_summary.json").write_text(
        json.dumps(temporal_summary, indent=2), encoding="utf-8",
    )
    print(f"[temporal] forward {NV} views in {t_fwd_s:.2f}s, peak GPU mem {peak_mem_mb:.0f} MB")
    print(f"[temporal] wrote {K} frame dirs + temporal_summary.json -> {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
