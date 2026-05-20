"""
P3.1 — Pi3X on N anchor frames in one Python process (model loaded once).

Loops the same letterbox + Pi3 forward as scripts/phase2/run_pi3_one_frame.py
but for a list of anchor indices, writing per-anchor subdirs:

    <output_dir>/anchor_<idx>/<all the same .npy + summary.json files>
    <output_dir>/multi_summary.json   aggregate run info

Why a separate script (not a flag on the single-frame one): keeps the
production-grade single-frame entry point unchanged, and aggregates timing
across the batch run for fair per-anchor cost reporting.
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


def run_one_anchor(model, RING_CAMS_7, loader, recover_intrinsic_from_rays_d,
                   torch, anchor_idx: int, target_side: int, device,
                   use_bf16: bool, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_all = loader.anchor_timestamps_ns()
    if anchor_idx >= len(ts_all):
        raise IndexError(f"anchor_idx {anchor_idx} >= {len(ts_all)}")
    anchor_ts = ts_all[anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)

    imgs_np: list[np.ndarray] = []
    per_cam_K_rescaled: dict[str, np.ndarray] = {}
    per_cam_T_ego_cam: dict[str, np.ndarray] = {}
    per_cam_letterbox_info: dict[str, dict] = {}

    for cam in RING_CAMS_7:
        img = frame.images[cam]
        calib = frame.calibrations[cam]
        sq, lb = letterbox_to_square(img, target_side=target_side)
        K2 = rescale_K_for_letterbox(calib.K, lb)
        imgs_np.append(sq)
        per_cam_K_rescaled[cam] = K2
        per_cam_T_ego_cam[cam] = calib.T_ego_cam
        per_cam_letterbox_info[cam] = lb
        Image.fromarray(sq).save(out_dir / f"image_{cam}.png")

    arr = np.stack(imgs_np, axis=0).astype(np.float32) / 255.0
    arr = np.transpose(arr, (0, 3, 1, 2))
    imgs_tensor = torch.from_numpy(arr).unsqueeze(0).to(device)

    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()

    t0 = time.time()
    with torch.no_grad():
        if use_bf16:
            with torch.amp.autocast("cuda", dtype=torch.bfloat16):
                res = model(imgs=imgs_tensor)
        else:
            res = model(imgs=imgs_tensor)
    if device.type == "cuda":
        torch.cuda.synchronize()
    fwd_s = time.time() - t0

    rays_d = torch.nn.functional.normalize(res["local_points"], dim=-1)
    K_recovered = recover_intrinsic_from_rays_d(rays_d, force_center_principal_point=True)

    V = len(RING_CAMS_7)
    points_full = res["points"][0].detach().float().cpu().numpy()
    local_points_full = res["local_points"][0].detach().float().cpu().numpy()
    conf_full = res["conf"][0].detach().float().cpu().numpy()
    pose_full = res["camera_poses"][0].detach().float().cpu().numpy()
    K_recovered_full = K_recovered[0].detach().float().cpu().numpy()

    if conf_full.ndim == 4 and conf_full.shape[-1] == 1:
        conf_full = conf_full[..., 0]

    per_cam_summary: dict[str, dict] = {}
    for i, cam in enumerate(RING_CAMS_7):
        np.save(out_dir / f"points_{cam}.npy", points_full[i])
        np.save(out_dir / f"local_points_{cam}.npy", local_points_full[i])
        np.save(out_dir / f"conf_{cam}.npy", conf_full[i])
        np.save(out_dir / f"pose_{cam}.npy", pose_full[i])
        np.save(out_dir / f"intrinsic_recovered_{cam}.npy", K_recovered_full[i])
        np.save(out_dir / f"av2_K_letterboxed_{cam}.npy", per_cam_K_rescaled[cam])
        np.save(out_dir / f"av2_T_ego_cam_{cam}.npy", per_cam_T_ego_cam[cam])

        conf_prob = 1.0 / (1.0 + np.exp(-conf_full[i]))
        local_z = local_points_full[i, ..., 2]
        valid = (conf_prob > 0.1) & np.isfinite(local_z) & (local_z > 0)
        per_cam_summary[cam] = {
            "letterbox": per_cam_letterbox_info[cam],
            "conf_pct_gt_0.1": float(valid.mean()),
            "conf_pct_gt_0.5": float(((conf_prob > 0.5) & valid).mean()),
            "local_z_median_when_valid": float(np.median(local_z[valid])) if valid.any() else None,
            "K_recovered": K_recovered_full[i].tolist(),
            "K_av2_letterboxed": per_cam_K_rescaled[cam].tolist(),
        }

    peak_mem_mb = None
    if device.type == "cuda":
        peak_mem_mb = torch.cuda.max_memory_allocated() / 1024**2

    summary = {
        "backbone": "Pi3X",
        "checkpoint": "yyfz233/Pi3X",
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
    ap.add_argument("--pi3-repo", default=None)
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--target-side", type=int, default=504)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    pi3_repo = Path(args.pi3_repo) if args.pi3_repo else (here / DEFAULT_PI3_REPO_REL).resolve()
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(pi3_repo, w2p_code)

    import torch
    from pi3.models.pi3x import Pi3X
    from pi3.utils.geometry import recover_intrinsic_from_rays_d
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7

    anchor_indices = [int(x) for x in args.anchor_indices.split(",")]
    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    loader = AV2RingLoader(Path(args.log_dir))
    n_anchors = loader.num_anchor_frames()
    print(f"[multi] log has {n_anchors} anchors; running {len(anchor_indices)}: {anchor_indices}")

    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    print(f"[multi] device={device}")

    t_load = time.time()
    model = Pi3X.from_pretrained("yyfz233/Pi3X").eval()
    model.disable_multimodal()
    model = model.to(device)
    load_s = time.time() - t_load
    use_bf16 = device.type == "cuda" and torch.cuda.get_device_capability()[0] >= 8
    print(f"[multi] model loaded in {load_s:.1f}s, bf16={use_bf16}")

    per_anchor_records = []
    t_total = time.time()
    for idx in anchor_indices:
        sub = out_root / f"anchor_{idx:03d}"
        print(f"[multi] === anchor {idx} ===")
        rec = run_one_anchor(model, RING_CAMS_7, loader, recover_intrinsic_from_rays_d,
                             torch, idx, args.target_side, device, use_bf16, sub)
        per_anchor_records.append(rec)
        print(f"[multi] anchor {idx} done: {rec['forward_s']:.2f}s")
    total_s = time.time() - t_total

    multi_summary = {
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
    print(f"[multi] DONE: {len(anchor_indices)} anchors in {total_s:.1f}s "
          f"(mean fwd {multi_summary['mean_forward_s']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
