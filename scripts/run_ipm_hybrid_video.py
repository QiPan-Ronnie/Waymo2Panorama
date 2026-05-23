"""
IPM (T14) ground hybrid video driver — Pi3 ground detection + IPM projection
of road pixels + sphere fallback for non-ground, per frame -> mp4.

Per frame:
  1. Pi3 forward on 7 letterboxed cams -> local_points + conf
  2. For each cam:
     a. detect_ground_from_pi3 -> ground mask
     b. ipm_project_ground(image, K, T, ground_mask) -> IPM slab
     c. render_camera_to_erp -> sphere slab
     d. Merge: where IPM has coverage prefer IPM (weight boost), else sphere
  3. multiband_blend(merged_slabs, merged_weights) -> ERP frame
  4. Append to mp4

GPU required (Pi3). Per frame ~3-4s. Expected ~6-8 min for 5sec video.

Usage on Colab A100:
    python scripts/run_ipm_hybrid_video.py \
        --log-dir /content/drive/MyDrive/.../02a00399-... \
        --pi3-repo /content/Pi3 \
        --out-dir /content/drive/MyDrive/.../outputs/ipm_hybrid_video \
        --start-sec 3.0 --duration-sec 5.0
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import imageio
import numpy as np
from PIL import Image


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
    return np.asarray(pil), {"pad_top": pad_top, "pad_left": pad_left, "scale": target_side / side, "side": side}


def rescale_K_for_letterbox(K: np.ndarray, lb: dict) -> np.ndarray:
    K2 = K.astype(np.float64).copy()
    K2[0, 2] += lb["pad_left"]; K2[1, 2] += lb["pad_top"]
    K2[0, 0] *= lb["scale"]; K2[1, 1] *= lb["scale"]
    K2[0, 2] *= lb["scale"]; K2[1, 2] *= lb["scale"]
    return K2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--pi3-repo", required=True)
    ap.add_argument("--w2p-code", default=None)
    ap.add_argument("--target-side", type=int, default=504)
    ap.add_argument("--erp-height", type=int, default=1024)
    ap.add_argument("--erp-width", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--start-sec", type=float, default=3.0)
    ap.add_argument("--duration-sec", type=float, default=5.0)
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--max-distance-m", type=float, default=60.0)
    ap.add_argument("--min-distance-m", type=float, default=0.5)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / "../code").resolve()
    _wire_imports(Path(args.pi3_repo), w2p_code)

    import torch
    from pi3.models.pi3x import Pi3X
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.projection.ipm_ground import detect_ground_from_pi3, ipm_project_ground
    from waymo2panorama.blending.multiband import multiband_blend

    log_id = args.log_dir.name
    out_dir = args.out_dir / log_id
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / "ipm_hybrid_video.mp4"
    done_marker = out_dir / "done.json"

    # Load Pi3
    print("[ipm-hybrid-video] Loading Pi3X...", flush=True)
    t_load = time.time()
    model = Pi3X.from_pretrained("yyfz233/Pi3X").eval()
    model.disable_multimodal()
    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    model = model.to(device)
    use_bf16 = device.type == "cuda" and torch.cuda.get_device_capability()[0] >= 8
    print(f"[ipm-hybrid-video] Pi3 loaded in {time.time() - t_load:.1f}s, device={device}, bf16={use_bf16}", flush=True)

    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    start_idx = max(0, int(args.start_sec * args.fps))
    stop_idx = min(len(ts_all), start_idx + int(args.duration_sec * args.fps))
    indices = list(range(start_idx, stop_idx))
    n_frames = len(indices)
    print(f"[ipm-hybrid-video] Rendering {n_frames} frames", flush=True)

    writer = imageio.get_writer(mp4_path, fps=args.fps, codec="libx264", quality=8)
    erp_hw = (args.erp_height, args.erp_width)
    cams = list(RING_CAMS_7)

    t_total = time.time()
    per_frame_times: list[float] = []

    for i, idx in enumerate(indices):
        frame = loader.load_synced_frame(ts_all[idx])
        t0 = time.time()

        # Letterbox 7 cam + run Pi3
        imgs_lb: dict[str, np.ndarray] = {}
        K_lb: dict[str, np.ndarray] = {}
        T_cam: dict[str, np.ndarray] = {}
        imgs_np_list: list[np.ndarray] = []
        for cam in cams:
            sq, lb = letterbox_to_square(frame.images[cam], target_side=args.target_side)
            K2 = rescale_K_for_letterbox(frame.calibrations[cam].K, lb)
            imgs_lb[cam] = sq
            K_lb[cam] = K2
            T_cam[cam] = frame.calibrations[cam].T_ego_cam
            imgs_np_list.append(sq)

        arr = np.stack(imgs_np_list, axis=0).astype(np.float32) / 255.0
        arr = np.transpose(arr, (0, 3, 1, 2))
        imgs_t = torch.from_numpy(arr).unsqueeze(0).to(device)
        with torch.no_grad():
            if use_bf16:
                with torch.amp.autocast("cuda", dtype=torch.bfloat16):
                    res = model(imgs=imgs_t)
            else:
                res = model(imgs=imgs_t)
        if device.type == "cuda":
            torch.cuda.synchronize()
        local_points = res["local_points"][0].detach().float().cpu().numpy()  # (V, H, W, 3)
        conf = res["conf"][0].detach().float().cpu().numpy()
        if conf.ndim == 4 and conf.shape[-1] == 1:
            conf = conf[..., 0]

        # Per-cam IPM-ground + sphere merge
        merged_slabs: list[np.ndarray] = []
        merged_weights: list[np.ndarray] = []
        for c_i, cam in enumerate(cams):
            image = imgs_lb[cam]
            K = K_lb[cam]
            T = T_cam[cam]
            lp = local_points[c_i]
            cf = conf[c_i]

            ground_mask = detect_ground_from_pi3(
                local_points_cam=lp,
                T_ego_cam=T,
                ego_z_thresh_m=0.30,
                min_forward_m=1.0,
                max_radius_m=args.max_distance_m,
            )
            ipm_rgb, ipm_alpha, ipm_w = ipm_project_ground(
                image=image, K=K, T_ego_cam=T,
                ground_mask=ground_mask, erp_hw=erp_hw,
                max_distance_m=args.max_distance_m,
                min_distance_m=args.min_distance_m,
            )
            sph_rgb, sph_alpha, sph_w = render_camera_to_erp(
                image=image, K=K, T_ego_cam=T, erp_hw=erp_hw,
            )
            merged_rgb = sph_rgb.copy()
            merged_w = sph_w.copy()
            if ipm_alpha.any():
                merged_rgb[ipm_alpha] = ipm_rgb[ipm_alpha]
                merged_w[ipm_alpha] = np.maximum(merged_w[ipm_alpha], ipm_w[ipm_alpha] * 2.0 + 0.5)
            merged_slabs.append(merged_rgb)
            merged_weights.append(merged_w)

        erp = multiband_blend(merged_slabs, merged_weights, num_bands=args.num_bands, wrap=True)
        per_frame_times.append(time.time() - t0)
        writer.append_data(erp)

        if (i + 1) % 10 == 0 or i == 0 or i == n_frames - 1:
            elapsed = time.time() - t_total
            print(f"  [{i + 1:4d}/{n_frames:4d}] frame={per_frame_times[-1]:.2f}s elapsed={elapsed:6.1f}s", flush=True)

    writer.close()
    total = time.time() - t_total
    summary = {
        "route": "ipm_ground_hybrid",
        "log_id": log_id,
        "n_frames": n_frames,
        "mp4_path": str(mp4_path),
        "wall_time_s": round(total, 2),
        "mean_frame_s": round(float(np.mean(per_frame_times)), 3),
    }
    done_marker.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[ipm-hybrid-video] DONE in {total:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
