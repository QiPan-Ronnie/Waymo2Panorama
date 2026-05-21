"""
新-F — VGGT as 3rd depth backbone for L3 forward-splat NEG fortification.

Mirrors scripts/phase3/run_pi3_multi_anchor.py but swaps Pi3 -> VGGT
(facebookresearch/vggt, CVPR 2025 Best Paper).

Loads VGGT-1B-Commercial once, loops over N anchor indices, writes per-anchor
outputs in the SAME filename convention as Pi3 multi-anchor so that the
existing batch_eval_cycle.py and batch_eval_lidar.py pick them up
unmodified.

Per anchor (out_dir/anchor_<idx>/):
    image_<cam>.png                 letterboxed 504x504 RGB
    points_<cam>.npy                (H, W, 3) ego-frame 3D points
    local_points_<cam>.npy          (H, W, 3) cam-frame 3D points
    conf_<cam>.npy                  (H, W) confidence (uniform 1.0 - VGGT has no built-in conf)
    av2_K_letterboxed_<cam>.npy     (3, 3) rescaled intrinsic
    av2_T_ego_cam_<cam>.npy         (4, 4) ego-from-cam extrinsic
    summary.json                    per-anchor metadata

After all anchors:
    multi_summary.json              aggregate timings + config
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

DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"missing waymo2panorama code dir: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


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


def depth_to_cam_points(depth: np.ndarray, K: np.ndarray) -> np.ndarray:
    H, W = depth.shape
    fx, fy = float(K[0, 0]), float(K[1, 1])
    cx, cy = float(K[0, 2]), float(K[1, 2])
    uu, vv = np.meshgrid(np.arange(W, dtype=np.float64),
                         np.arange(H, dtype=np.float64))
    x_n = (uu - cx) / fx
    y_n = (vv - cy) / fy
    d = depth.astype(np.float64)
    pts = np.stack([x_n * d, y_n * d, d], axis=-1)
    invalid = ~np.isfinite(d) | (d <= 0)
    if invalid.any():
        pts[invalid] = np.nan
    return pts.astype(np.float32)


def cam_points_to_ego(pts_cam: np.ndarray, T_ego_cam: np.ndarray) -> np.ndarray:
    R = T_ego_cam[:3, :3].astype(np.float32)
    t = T_ego_cam[:3, 3].astype(np.float32)
    flat = pts_cam.reshape(-1, 3)
    out = flat @ R.T + t
    return out.reshape(pts_cam.shape).astype(np.float32)


def _resize_depth_to(depth: np.ndarray, target_h: int, target_w: int) -> np.ndarray:
    if depth.shape == (target_h, target_w):
        return depth
    pil = Image.fromarray(depth.astype(np.float32))
    return np.asarray(pil.resize((target_w, target_h), Image.Resampling.BILINEAR),
                      dtype=np.float32)


def run_one_anchor_vggt(model, vggt_load_fn, RING_CAMS_7, loader, torch,
                        anchor_idx: int, target_side: int, device,
                        use_bf16: bool, out_dir: Path) -> dict:
    """One anchor: letterbox 7 cams -> VGGT joint forward -> back-project -> save."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_all = loader.anchor_timestamps_ns()
    if anchor_idx >= len(ts_all):
        raise IndexError(f"anchor_idx {anchor_idx} >= {len(ts_all)}")
    anchor_ts = ts_all[anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)

    per_cam_K_rescaled: dict[str, np.ndarray] = {}
    per_cam_T_ego_cam: dict[str, np.ndarray] = {}
    per_cam_letterbox_info: dict[str, dict] = {}
    image_paths: list[str] = []

    tmp_dir = out_dir / "_vggt_input_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cams_ordered = list(RING_CAMS_7)
    for cam in cams_ordered:
        img = frame.images[cam]
        calib = frame.calibrations[cam]
        sq, lb = letterbox_to_square(img, target_side=target_side)
        K2 = rescale_K_for_letterbox(calib.K, lb)
        per_cam_K_rescaled[cam] = K2
        per_cam_T_ego_cam[cam] = calib.T_ego_cam
        per_cam_letterbox_info[cam] = lb

        # Persist letterboxed input next to outputs (mirrors Pi3 pattern)
        Image.fromarray(sq).save(out_dir / f"image_{cam}.png")
        np.save(out_dir / f"av2_K_letterboxed_{cam}.npy", K2)
        np.save(out_dir / f"av2_T_ego_cam_{cam}.npy", calib.T_ego_cam)

        # Save a temp copy for VGGT loader (which takes file paths)
        tmp_path = tmp_dir / f"{cam}.png"
        Image.fromarray(sq).save(tmp_path)
        image_paths.append(str(tmp_path))

    # ---- VGGT joint 7-cam forward ----
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()

    images = vggt_load_fn(image_paths).to(device)
    t0 = time.time()
    with torch.no_grad():
        if use_bf16:
            with torch.amp.autocast("cuda", dtype=torch.bfloat16):
                predictions = model(images)
        else:
            predictions = model(images)
    if device.type == "cuda":
        torch.cuda.synchronize()
    fwd_s = time.time() - t0

    # ---- Extract per-cam depth at input letterbox resolution ----
    # VGGT `predictions` is a dict; depth maps live under "depth" (B, V, H, W, 1) or
    # under a per-cam list. We handle both defensively.
    depth_full = None
    if isinstance(predictions, dict):
        for key in ("depth", "depth_maps", "predicted_depth"):
            if key in predictions:
                depth_full = predictions[key]
                break
    elif hasattr(predictions, "depth_maps"):
        depth_full = predictions.depth_maps
    elif hasattr(predictions, "depth"):
        depth_full = predictions.depth

    if depth_full is None:
        raise RuntimeError(f"VGGT prediction has no recognizable depth field. "
                            f"Available keys/attrs: {dir(predictions)[:30]}")

    # Normalize to a list of (H, W) per cam
    if hasattr(depth_full, "shape"):
        # tensor; squeeze batch
        d = depth_full.detach().float().cpu().numpy()
        # drop trailing channel-1 if present
        while d.ndim > 0 and d.shape[-1] == 1:
            d = d[..., 0]
        # drop leading batch-1 if present
        while d.ndim > 3 and d.shape[0] == 1:
            d = d[0]
        # Now expect (V, H, W) or maybe (V, 1, H, W) collapsed
        if d.ndim == 4 and d.shape[1] == 1:
            d = d[:, 0]
        if d.ndim != 3:
            raise RuntimeError(f"unexpected VGGT depth shape after squeeze: {d.shape}")
        depth_per_cam = [d[i] for i in range(d.shape[0])]
    elif isinstance(depth_full, (list, tuple)):
        depth_per_cam = []
        for x in depth_full:
            xn = x.detach().float().cpu().numpy()
            while xn.ndim > 0 and xn.shape[-1] == 1:
                xn = xn[..., 0]
            depth_per_cam.append(xn)
    else:
        raise RuntimeError(f"unexpected VGGT depth container: {type(depth_full)}")

    if len(depth_per_cam) != len(cams_ordered):
        raise RuntimeError(f"VGGT returned {len(depth_per_cam)} depth maps for "
                            f"{len(cams_ordered)} cams")

    # ---- Back-project, persist Pi3-compatible filenames ----
    per_cam_summary: dict[str, dict] = {}
    for i, cam in enumerate(cams_ordered):
        depth = depth_per_cam[i].astype(np.float32)
        # Resize to letterbox resolution if VGGT outputs different shape
        depth = _resize_depth_to(depth, target_side, target_side)

        K = per_cam_K_rescaled[cam]
        T = per_cam_T_ego_cam[cam]
        pts_cam = depth_to_cam_points(depth, K)
        pts_ego = cam_points_to_ego(pts_cam, T)
        conf = np.ones((target_side, target_side), dtype=np.float32)  # VGGT: no conf

        np.save(out_dir / f"local_points_{cam}.npy", pts_cam)
        np.save(out_dir / f"points_{cam}.npy", pts_ego)
        np.save(out_dir / f"conf_{cam}.npy", conf)
        np.save(out_dir / f"vggt_depth_{cam}.npy", depth)  # extra: raw depth for debug
        # Save pose_<cam>.npy = AV2 T_ego_cam so eval_cycle_consistency.py's
        # Sim(3) fit collapses to identity (Pi3 used predicted poses; VGGT uses
        # AV2 truth, so points are already in ego frame and need no transform).
        np.save(out_dir / f"pose_{cam}.npy", T.astype(np.float32))

        valid = np.isfinite(depth) & (depth > 0)
        per_cam_summary[cam] = {
            "letterbox": per_cam_letterbox_info[cam],
            "depth_min_when_valid": float(depth[valid].min()) if valid.any() else None,
            "depth_median_when_valid": float(np.median(depth[valid])) if valid.any() else None,
            "depth_p90_when_valid": float(np.percentile(depth[valid], 90)) if valid.any() else None,
            "depth_max_when_valid": float(depth[valid].max()) if valid.any() else None,
            "valid_frac": float(valid.mean()),
            "K_av2_letterboxed": K.tolist(),
        }

    peak_mem_mb = None
    if device.type == "cuda":
        peak_mem_mb = torch.cuda.max_memory_allocated() / 1024**2

    summary = {
        "backbone": "VGGT",
        "checkpoint": "facebook/VGGT-1B-Commercial",
        "anchor_idx": anchor_idx,
        "anchor_timestamp_ns": int(anchor_ts),
        "cameras": list(RING_CAMS_7),
        "target_side": target_side,
        "letterbox_method": "pad_to_square_then_resize_lanczos",
        "device": str(device),
        "autocast_bf16": use_bf16,
        "forward_s": round(fwd_s, 3),
        "peak_gpu_memory_mb": peak_mem_mb,
        "per_cam": per_cam_summary,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Cleanup VGGT input tmp dir to save disk
    for p in tmp_dir.glob("*"):
        try:
            p.unlink()
        except OSError:
            pass
    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    return {"anchor_idx": anchor_idx, "anchor_ts": int(anchor_ts),
            "forward_s": round(fwd_s, 3),
            "peak_gpu_mem_mb": peak_mem_mb}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-indices", required=True,
                    help="comma-separated list, e.g. 0,30,60,90,120,150,180,210,240,270")
    ap.add_argument("--output-dir", required=True,
                    help="creates anchor_<idx>/ subdir per anchor")
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--vggt-repo", default=None,
                    help="Path to facebookresearch/vggt clone for non-pip install. "
                         "If unset, assumes `pip install -e .` already done.")
    ap.add_argument("--checkpoint", default="facebook/VGGT-1B-Commercial",
                    help="HF model id; -Commercial excludes military training data.")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--target-side", type=int, default=504)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    if args.vggt_repo:
        vggt_repo = Path(args.vggt_repo)
        if not vggt_repo.exists():
            raise FileNotFoundError(f"missing VGGT repo: {vggt_repo}")
        sys.path.insert(0, str(vggt_repo))

    import torch
    from vggt.models.vggt import VGGT
    from vggt.utils.load_fn import load_and_preprocess_images
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7

    anchor_indices = [int(x) for x in args.anchor_indices.split(",")]
    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    loader = AV2RingLoader(Path(args.log_dir))
    n_anchors = loader.num_anchor_frames()
    print(f"[vggt-multi] log has {n_anchors} anchors; running {len(anchor_indices)}: {anchor_indices}")

    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    use_bf16 = device.type == "cuda" and torch.cuda.get_device_capability()[0] >= 8
    print(f"[vggt-multi] device={device} bf16={use_bf16}")

    t_load = time.time()
    model = VGGT.from_pretrained(args.checkpoint).to(device).eval()
    load_s = time.time() - t_load
    print(f"[vggt-multi] model loaded in {load_s:.1f}s ckpt={args.checkpoint}")

    per_anchor_records: list[dict] = []
    t_total = time.time()
    for idx in anchor_indices:
        sub = out_root / f"anchor_{idx:03d}"
        print(f"[vggt-multi] === anchor {idx} ===")
        rec = run_one_anchor_vggt(model, load_and_preprocess_images, RING_CAMS_7,
                                   loader, torch, idx, args.target_side, device,
                                   use_bf16, sub)
        per_anchor_records.append(rec)
        print(f"[vggt-multi] anchor {idx} done: fwd={rec['forward_s']:.2f}s "
              f"peak_mem={rec['peak_gpu_mem_mb']}MB")
    total_s = time.time() - t_total

    multi_summary = {
        "backbone": "VGGT",
        "checkpoint": args.checkpoint,
        "log_dir": str(args.log_dir),
        "anchor_indices": anchor_indices,
        "n_anchors_run": len(anchor_indices),
        "n_anchors_in_log": n_anchors,
        "model_load_s": round(load_s, 3),
        "total_inference_s": round(total_s, 3),
        "mean_forward_s": round(float(np.mean([r["forward_s"] for r in per_anchor_records])), 3),
        "device": str(device),
        "autocast_bf16": use_bf16,
        "per_anchor": per_anchor_records,
    }
    (out_root / "multi_summary.json").write_text(json.dumps(multi_summary, indent=2),
                                                  encoding="utf-8")
    print(f"[vggt-multi] DONE: {len(anchor_indices)} anchors in {total_s:.1f}s "
          f"(mean fwd {multi_summary['mean_forward_s']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
