"""
Phase 2 D1 — Pi3X on one AV2 anchor frame (all 7 ring cams, single forward pass).

Runs Pi3X jointly on 7 letterboxed-to-504x504 ring camera views and dumps per-cam
3D outputs + summary for head-to-head comparison with DVGT (see
notes/phase2-d1-backbone-decision.md).

Letterboxing rather than center-crop: AV2's `ring_front_center` is portrait
(2048x1550) and the other 6 are landscape (1550x2048). Cropping to square would
drop the upper FOV (traffic lights / sky cues) or the side FOV. Letterboxing
preserves all original pixels, K rescales cleanly, and Pi3's conf map will
naturally down-weight the zero-padded borders.

Outputs (under <output_dir>):
    points_{cam}.npy             (H, W, 3) — Pi3 world-frame points
    local_points_{cam}.npy       (H, W, 3) — per-cam-frame points
    conf_{cam}.npy               (H, W)   — Pi3 raw conf logit
    pose_{cam}.npy               (4, 4)    — Pi3 predicted cam-to-world
    intrinsic_recovered_{cam}.npy (3, 3)   — K recovered from Pi3 rays (504x504 scale)
    av2_K_letterboxed_{cam}.npy  (3, 3)    — AV2 truth K, rescaled to 504x504 letterbox
    av2_T_ego_cam_{cam}.npy      (4, 4)    — AV2 truth ego<-cam SE(3)
    image_{cam}.png              (504, 504, 3) — letterboxed input (for inspection)
    summary.json                                — density, depth stats, gpu mem, latency
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


# ---- repo wiring ----

DEFAULT_PI3_REPO_REL = "../../../../../01-pi3/code/official/Pi3"   # from this script
DEFAULT_W2P_CODE_REL = "../../code"                                # waymo2panorama package


def _wire_imports(pi3_repo: Path, w2p_code: Path) -> None:
    for p in (pi3_repo, w2p_code):
        if not p.exists():
            raise FileNotFoundError(f"required path missing: {p}")
        sys.path.insert(0, str(p))


# ---- letterbox helpers ----

def letterbox_to_square(img: np.ndarray, target_side: int = 504) -> tuple[np.ndarray, dict]:
    """Pad shorter side with zeros to make square, then resize to target_side.

    Returns the new image (target_side, target_side, 3) uint8 and a dict recording
    the geometric transform applied (pad + scale), so K can be updated identically.
    """
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
    """Apply the same letterbox transform to a 3x3 intrinsic matrix."""
    K2 = K.astype(np.float64).copy()
    K2[0, 2] += lb["pad_left"]
    K2[1, 2] += lb["pad_top"]
    K2[0, 0] *= lb["scale"]
    K2[1, 1] *= lb["scale"]
    K2[0, 2] *= lb["scale"]
    K2[1, 2] *= lb["scale"]
    return K2


# ---- main ----

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-dir", required=True,
                    help="AV2 sensor log directory, e.g. .../argoverse2/val/<UUID>")
    ap.add_argument("--anchor-idx", type=int, default=0,
                    help="Index into anchor_timestamps_ns() (front_center).")
    ap.add_argument("--output-dir", required=True,
                    help="Where to write the per-cam .npy + summary.json")
    ap.add_argument("--pi3-repo", default=None,
                    help="Override path to 01-pi3/code/official/Pi3 (auto from this script if unset)")
    ap.add_argument("--w2p-code", default=None,
                    help="Override path to waymo2panorama package dir (auto if unset)")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--target-side", type=int, default=504,
                    help="Pi3 input square side; must be divisible by 14")
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

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- load AV2 frame ----
    loader = AV2RingLoader(Path(args.log_dir))
    ts_all = loader.anchor_timestamps_ns()
    if args.anchor_idx >= len(ts_all):
        raise IndexError(f"anchor_idx {args.anchor_idx} >= {len(ts_all)} anchors")
    anchor_ts = ts_all[args.anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)

    print(f"[pi3] log={Path(args.log_dir).name} anchor_idx={args.anchor_idx} ts={anchor_ts}")

    # ---- letterbox + collect ----
    imgs_np: list[np.ndarray] = []
    per_cam_K_rescaled: dict[str, np.ndarray] = {}
    per_cam_T_ego_cam: dict[str, np.ndarray] = {}
    per_cam_letterbox_info: dict[str, dict] = {}

    for cam in RING_CAMS_7:
        img = frame.images[cam]
        calib = frame.calibrations[cam]
        sq, lb = letterbox_to_square(img, target_side=args.target_side)
        K2 = rescale_K_for_letterbox(calib.K, lb)
        imgs_np.append(sq)
        per_cam_K_rescaled[cam] = K2
        per_cam_T_ego_cam[cam] = calib.T_ego_cam
        per_cam_letterbox_info[cam] = lb
        Image.fromarray(sq).save(out_dir / f"image_{cam}.png")

    # Stack to (1, V, 3, H, W) in [0, 1]
    arr = np.stack(imgs_np, axis=0).astype(np.float32) / 255.0          # (V, H, W, 3)
    arr = np.transpose(arr, (0, 3, 1, 2))                                # (V, 3, H, W)
    imgs_tensor = torch.from_numpy(arr).unsqueeze(0)                     # (1, V, 3, H, W)

    # ---- device + model ----
    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    print(f"[pi3] device={device} input_shape={tuple(imgs_tensor.shape)}")
    imgs_tensor = imgs_tensor.to(device)

    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()

    t_load_start = time.time()
    model = Pi3X.from_pretrained("yyfz233/Pi3X").eval()
    model.disable_multimodal()
    model = model.to(device)
    t_load_s = time.time() - t_load_start

    # autocast bf16 on A100/H100 only
    use_bf16 = device.type == "cuda" and torch.cuda.get_device_capability()[0] >= 8

    t_fwd_start = time.time()
    with torch.no_grad():
        if use_bf16:
            with torch.amp.autocast("cuda", dtype=torch.bfloat16):
                res = model(imgs=imgs_tensor)
        else:
            res = model(imgs=imgs_tensor)
    if device.type == "cuda":
        torch.cuda.synchronize()
    t_fwd_s = time.time() - t_fwd_start

    rays_d = torch.nn.functional.normalize(res["local_points"], dim=-1)
    K_recovered = recover_intrinsic_from_rays_d(rays_d, force_center_principal_point=True)

    # ---- dump per-cam ----
    V = len(RING_CAMS_7)
    points_full = res["points"][0].detach().float().cpu().numpy()         # (V, H, W, 3)
    local_points_full = res["local_points"][0].detach().float().cpu().numpy()
    conf_full = res["conf"][0].detach().float().cpu().numpy()             # (V, H, W, 1) or (V,H,W)
    pose_full = res["camera_poses"][0].detach().float().cpu().numpy()     # (V, 4, 4)
    K_recovered_full = K_recovered[0].detach().float().cpu().numpy()      # (V, 3, 3)

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

        conf_prob = 1.0 / (1.0 + np.exp(-conf_full[i]))   # sigmoid
        local_z = local_points_full[i, ..., 2]
        valid = (conf_prob > 0.1) & np.isfinite(local_z) & (local_z > 0)
        per_cam_summary[cam] = {
            "letterbox": per_cam_letterbox_info[cam],
            "conf_pct_gt_0.1": float(valid.mean()),
            "conf_pct_gt_0.5": float(((conf_prob > 0.5) & valid).mean()),
            "local_z_median_when_valid": float(np.median(local_z[valid])) if valid.any() else None,
            "local_z_p10_when_valid": float(np.percentile(local_z[valid], 10)) if valid.any() else None,
            "local_z_p90_when_valid": float(np.percentile(local_z[valid], 90)) if valid.any() else None,
            "K_recovered": K_recovered_full[i].tolist(),
            "K_av2_letterboxed": per_cam_K_rescaled[cam].tolist(),
        }

    # ---- summary ----
    peak_mem_mb = None
    if device.type == "cuda":
        peak_mem_mb = torch.cuda.max_memory_allocated() / 1024**2

    summary: dict[str, Any] = {
        "backbone": "Pi3X",
        "checkpoint": "yyfz233/Pi3X",
        "log_dir": str(args.log_dir),
        "anchor_idx": args.anchor_idx,
        "anchor_timestamp_ns": int(anchor_ts),
        "cameras": list(RING_CAMS_7),
        "input_shape": list(imgs_tensor.shape),
        "target_side": args.target_side,
        "letterbox_method": "pad_to_square_then_resize_lanczos",
        "device": str(device),
        "autocast_bf16": use_bf16,
        "model_load_s": round(t_load_s, 3),
        "forward_s": round(t_fwd_s, 3),
        "peak_gpu_memory_mb": peak_mem_mb,
        "per_cam": per_cam_summary,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_cam"}, indent=2))
    print(f"[pi3] done → {out_dir}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    raise SystemExit(main())
