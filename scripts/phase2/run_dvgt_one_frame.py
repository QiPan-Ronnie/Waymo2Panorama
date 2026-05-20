"""
Phase 2 D1 — DVGT-1 on one AV2 anchor frame (all 7 ring cams, T=1, single forward).

DVGT is the driving-tuned counterpart to Pi3X. Input contract:
    images (B, T, V, 3, H, W) in [0, 1] — bilinear, no extra normalization.
For our 1-frame test: B=1, T=1, V=7. Image size 512 (DVGT native) letterboxed
to square (same protocol as the Pi3 sibling script, but at 512 not 504).

Returns (best-effort schema match with Pi3 script):
    world_points_{cam}.npy       (H, W, 3) — DVGT world-frame metric points
    conf_{cam}.npy               (H, W)   — DVGT raw conf
    pose_enc_{cam}.npy           (7,)      — DVGT ego-pose encoding row
    av2_K_letterboxed_{cam}.npy  (3, 3)    — AV2 truth K, rescaled to 512x512
    av2_T_ego_cam_{cam}.npy      (4, 4)    — AV2 truth ego<-cam SE(3)
    image_{cam}.png              (512, 512, 3) — letterboxed input
    summary.json                                — density, metric depth, gpu mem, latency

Checkpoint loading:
    --ckpt-path /content/ckpt/dvgt1.pt  (preferred — pre-downloaded)
    --ckpt-hf-repo RainyNight/DVGT-1     (fallback — script downloads via hf)
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


DEFAULT_DVGT_REPO_REL = "../../code/external/DVGT"
DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(dvgt_repo: Path, w2p_code: Path) -> None:
    for p in (dvgt_repo, w2p_code):
        if not p.exists():
            raise FileNotFoundError(f"required path missing: {p}")
        sys.path.insert(0, str(p))


def letterbox_to_square(img: np.ndarray, target_side: int) -> tuple[np.ndarray, dict]:
    """Same as Pi3 script — pad shorter side, then Lanczos resize."""
    h, w, c = img.shape
    side = max(h, w)
    pad_top = (side - h) // 2
    pad_left = (side - w) // 2
    sq = np.zeros((side, side, c), dtype=img.dtype)
    sq[pad_top:pad_top + h, pad_left:pad_left + w] = img
    pil = Image.fromarray(sq).resize((target_side, target_side), Image.Resampling.LANCZOS)
    return np.asarray(pil), {"pad_top": pad_top, "pad_left": pad_left,
                              "scale": target_side / side, "side": side}


def rescale_K_for_letterbox(K: np.ndarray, lb: dict) -> np.ndarray:
    K2 = K.astype(np.float64).copy()
    K2[0, 2] += lb["pad_left"]
    K2[1, 2] += lb["pad_top"]
    K2[0, 0] *= lb["scale"]
    K2[1, 1] *= lb["scale"]
    K2[0, 2] *= lb["scale"]
    K2[1, 2] *= lb["scale"]
    return K2


def _resolve_checkpoint(args) -> str:
    if args.ckpt_path:
        ckpt = Path(args.ckpt_path)
        if not ckpt.exists():
            raise FileNotFoundError(f"--ckpt-path does not exist: {ckpt}")
        return str(ckpt)
    if args.ckpt_hf_repo:
        from huggingface_hub import hf_hub_download
        print(f"[dvgt] downloading checkpoint from HF: {args.ckpt_hf_repo}")
        # Try common filenames in order
        for fname in ("dvgt1.pt", "model.pt", "pytorch_model.bin", "model.safetensors"):
            try:
                return hf_hub_download(repo_id=args.ckpt_hf_repo, filename=fname)
            except Exception:
                continue
        raise RuntimeError(
            f"Could not find a known checkpoint filename in {args.ckpt_hf_repo}"
        )
    raise ValueError("Must pass either --ckpt-path or --ckpt-hf-repo")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-idx", type=int, default=0)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--dvgt-repo", default=None,
                    help="Path to cloned DVGT repo (auto from this script if unset)")
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--ckpt-path", default=None, help="Local path to DVGT .pt checkpoint")
    ap.add_argument("--ckpt-hf-repo", default="RainyNight/DVGT-1",
                    help="HF repo to download checkpoint from if --ckpt-path not given")
    ap.add_argument("--model-variant", default="dvgt1", choices=["dvgt1", "dvgt2"])
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--target-side", type=int, default=512,
                    help="DVGT native side; must be divisible by 16")
    args = ap.parse_args()

    if args.target_side % 16 != 0:
        raise ValueError(f"target_side={args.target_side} not divisible by 16")

    here = Path(__file__).resolve().parent
    dvgt_repo = Path(args.dvgt_repo) if args.dvgt_repo else (here / DEFAULT_DVGT_REPO_REL).resolve()
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(dvgt_repo, w2p_code)

    import torch
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7

    if args.model_variant == "dvgt1":
        from dvgt.models.architectures.dvgt1 import DVGT1 as DVGTModel
    else:
        from dvgt.models.architectures.dvgt2 import DVGT2 as DVGTModel

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- load AV2 frame ----
    loader = AV2RingLoader(Path(args.log_dir))
    ts_all = loader.anchor_timestamps_ns()
    if args.anchor_idx >= len(ts_all):
        raise IndexError(f"anchor_idx {args.anchor_idx} >= {len(ts_all)} anchors")
    anchor_ts = ts_all[args.anchor_idx]
    frame = loader.load_synced_frame(anchor_ts)

    print(f"[dvgt] log={Path(args.log_dir).name} anchor_idx={args.anchor_idx} ts={anchor_ts}")

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

    # Stack to (1, T=1, V=7, 3, H, W) in [0, 1]
    arr = np.stack(imgs_np, axis=0).astype(np.float32) / 255.0          # (V, H, W, 3)
    arr = np.transpose(arr, (0, 3, 1, 2))                                # (V, 3, H, W)
    imgs_tensor = torch.from_numpy(arr).unsqueeze(0).unsqueeze(0)        # (1, 1, V, 3, H, W)

    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    print(f"[dvgt] device={device} input_shape={tuple(imgs_tensor.shape)}")
    imgs_tensor = imgs_tensor.to(device)

    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()

    t_load_start = time.time()
    ckpt_path = _resolve_checkpoint(args)
    print(f"[dvgt] loading checkpoint: {ckpt_path}")

    model = DVGTModel()
    state = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    if isinstance(state, dict) and "model" in state and isinstance(state["model"], dict):
        state = state["model"]
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing:
        print(f"[dvgt] missing keys: {len(missing)} (first 3: {missing[:3]})")
    if unexpected:
        print(f"[dvgt] unexpected keys: {len(unexpected)} (first 3: {unexpected[:3]})")
    model = model.to(device).eval()
    t_load_s = time.time() - t_load_start

    use_bf16 = device.type == "cuda" and torch.cuda.get_device_capability()[0] >= 8

    t_fwd_start = time.time()
    with torch.no_grad():
        if use_bf16:
            with torch.amp.autocast(device.type, dtype=torch.bfloat16):
                predictions = model(imgs_tensor)
        else:
            predictions = model(imgs_tensor)
    if device.type == "cuda":
        torch.cuda.synchronize()
    t_fwd_s = time.time() - t_fwd_start

    # ---- normalize prediction keys (DVGT readme and demo are slightly inconsistent) ----
    def _first_present(d: dict, names: list[str]):
        for n in names:
            if n in d:
                return n, d[n]
        return None, None

    pkey, pts = _first_present(predictions, ["world_points", "points"])
    ckey, conf = _first_present(predictions, ["world_points_conf", "points_conf", "conf"])
    posekey, pose_enc = _first_present(predictions,
                                       ["pose_enc", "absolute_ego_pose_enc", "ego_pose_enc"])

    if pts is None or conf is None:
        avail = list(predictions.keys())
        raise RuntimeError(f"DVGT predictions missing points/conf. Got keys: {avail}")

    print(f"[dvgt] prediction keys → points={pkey} conf={ckey} pose={posekey}")
    print(f"[dvgt] shapes → points={tuple(pts.shape)} conf={tuple(conf.shape)} "
          f"pose={tuple(pose_enc.shape) if pose_enc is not None else None}")

    # Strip B, T → (V, ...)
    pts_arr = pts[0, 0].detach().float().cpu().numpy()         # (V, H, W, 3)
    conf_arr = conf[0, 0].detach().float().cpu().numpy()       # (V, H, W) or (V,H,W,1)
    if conf_arr.ndim == 4 and conf_arr.shape[-1] == 1:
        conf_arr = conf_arr[..., 0]
    pose_arr = None
    if pose_enc is not None:
        pose_arr = pose_enc[0, 0].detach().float().cpu().numpy()   # (V, K)

    per_cam_summary: dict[str, dict] = {}
    for i, cam in enumerate(RING_CAMS_7):
        np.save(out_dir / f"world_points_{cam}.npy", pts_arr[i])
        np.save(out_dir / f"conf_{cam}.npy", conf_arr[i])
        if pose_arr is not None:
            np.save(out_dir / f"pose_enc_{cam}.npy", pose_arr[i])
        np.save(out_dir / f"av2_K_letterboxed_{cam}.npy", per_cam_K_rescaled[cam])
        np.save(out_dir / f"av2_T_ego_cam_{cam}.npy", per_cam_T_ego_cam[cam])

        # Use the L2 norm of (point - origin) as a "metric depth" proxy
        # (DVGT outputs in ego_0 frame; cam_0 is ring_front_center for our ordering)
        depth_metric = np.linalg.norm(pts_arr[i], axis=-1)
        finite = np.isfinite(depth_metric) & (depth_metric > 0)
        # No native [0,1] sigmoid for DVGT conf (it's not a logit per docs); report raw stats
        per_cam_summary[cam] = {
            "letterbox": per_cam_letterbox_info[cam],
            "conf_min": float(conf_arr[i].min()),
            "conf_max": float(conf_arr[i].max()),
            "conf_median": float(np.median(conf_arr[i])),
            "depth_metric_median": float(np.median(depth_metric[finite])) if finite.any() else None,
            "depth_metric_p10": float(np.percentile(depth_metric[finite], 10)) if finite.any() else None,
            "depth_metric_p90": float(np.percentile(depth_metric[finite], 90)) if finite.any() else None,
            "K_av2_letterboxed": per_cam_K_rescaled[cam].tolist(),
        }

    peak_mem_mb = None
    if device.type == "cuda":
        peak_mem_mb = torch.cuda.max_memory_allocated() / 1024**2

    summary: dict[str, Any] = {
        "backbone": args.model_variant.upper(),
        "checkpoint": str(ckpt_path),
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
        "prediction_keys_used": {"points": pkey, "conf": ckey, "pose": posekey},
        "per_cam": per_cam_summary,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_cam"}, indent=2))
    print(f"[dvgt] done → {out_dir}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    raise SystemExit(main())
