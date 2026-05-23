"""
L3 forward-splat video driver — Pi3 + Sim(3) + lift-and-project per frame -> mp4.

Mirrors scripts/run_l1_baseline.py structure but the per-frame step is L3
(neural depth + 3D forward-splat) instead of L1 (sphere projection + multiband).

Pipeline per timestamp:
  1. Load 7-cam synchronized frame from AV2 log
  2. Letterbox each cam to 504x504 + rescale K (for Pi3 input)
  3. Pi3X joint forward (~1.2s on A100, bf16) -> local_points, points (Pi3 frame), conf
  4. Fit Sim(3) Pi3-frame -> AV2 ego frame (uses cam translation correspondences)
  5. Apply Sim(3) to world_points -> ego frame
  6. Lift+project multiview -> single ERP frame (1024x2048)
  7. Append to mp4

Sim(3) is computed PER frame (frame-to-frame consistency: Pi3's predicted
frame may drift slightly between AV2 timestamps; recomputing keeps the ERP
content geometrically stable).

Usage on Colab A100:
    python scripts/run_l3_video.py \
        --log-dir /content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/02a00399-... \
        --out-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/l3_video \
        --start-sec 3.0 --duration-sec 5.0 \
        --target-side 504

Expected wall time on A100: ~6-10 min for 5 sec video (100 frames).
Output: <out-dir>/<log_id>/l3_video.mp4 + done.json marker.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--pi3-repo", default=None,
                    help="Path to Pi3 repo (default: ../../../../../01-pi3/code/official/Pi3 relative to this script)")
    ap.add_argument("--w2p-code", default=None,
                    help="Path to waymo2panorama code (default: ../code relative to this script)")
    ap.add_argument("--target-side", type=int, default=504,
                    help="Pi3 input letterbox side (default 504, matches W2 P3.1 cache)")
    ap.add_argument("--erp-height", type=int, default=1024)
    ap.add_argument("--erp-width", type=int, default=2048)
    ap.add_argument("--start-sec", type=float, default=3.0,
                    help="Start of video clip in seconds from log start")
    ap.add_argument("--duration-sec", type=float, default=5.0)
    ap.add_argument("--fps", type=int, default=20,
                    help="AV2 ring cams 20 Hz; keep unless decimating")
    ap.add_argument("--frame-step", type=int, default=1)
    ap.add_argument("--conf-threshold", type=float, default=0.1)
    ap.add_argument("--min-distance-m", type=float, default=0.5)
    ap.add_argument("--max-distance-m", type=float, default=200.0)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    pi3_repo = Path(args.pi3_repo) if args.pi3_repo else (here / "../../../01-pi3/code/official/Pi3").resolve()
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / "../code").resolve()
    _wire_imports(pi3_repo, w2p_code)

    # ---- Imports (after sys.path wiring) ----
    import torch
    from pi3.models.pi3x import Pi3X
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.alignment.sim3_align import fit_sim3_from_camera_translations
    from waymo2panorama.pipeline.lift_and_project import (
        apply_sim3_to_points, lift_and_project_multiview,
    )

    # ---- Output dir ----
    log_id = args.log_dir.name
    out_dir = args.out_dir / log_id
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / "l3_video.mp4"
    done_marker = out_dir / "done.json"

    # ---- Load Pi3 model once ----
    print(f"[l3-video] Loading Pi3X model ...")
    t_load = time.time()
    model = Pi3X.from_pretrained("yyfz233/Pi3X").eval()
    model.disable_multimodal()
    device = torch.device(args.device if args.device == "cpu" or torch.cuda.is_available() else "cpu")
    model = model.to(device)
    use_bf16 = device.type == "cuda" and torch.cuda.get_device_capability()[0] >= 8
    print(f"[l3-video] Pi3 loaded in {time.time() - t_load:.1f}s on {device}, bf16={use_bf16}")

    # ---- AV2 frame range ----
    loader = AV2RingLoader(args.log_dir)
    ts_all = loader.anchor_timestamps_ns()
    start_idx = max(0, int(args.start_sec * args.fps))
    stop_idx = min(len(ts_all), start_idx + int(args.duration_sec * args.fps))
    indices = list(range(start_idx, stop_idx, args.frame_step))
    n_frames = len(indices)
    print(f"[l3-video] Rendering {n_frames} frames "
          f"({args.start_sec:.1f}s -> {args.start_sec + args.duration_sec:.1f}s)")

    # ---- mp4 writer ----
    writer = imageio.get_writer(mp4_path, fps=args.fps, codec="libx264", quality=8)
    cams = list(RING_CAMS_7)
    erp_hw = (args.erp_height, args.erp_width)

    t_total = time.time()
    pi3_times: list[float] = []
    splat_times: list[float] = []

    for i, idx in enumerate(indices):
        anchor = ts_all[idx]
        frame = loader.load_synced_frame(anchor)

        # 1. Letterbox 7 cam + collect K_rescaled + T_ego_cam
        imgs_np: list[np.ndarray] = []
        K_letterboxed: dict[str, np.ndarray] = {}
        T_ego_cam: dict[str, np.ndarray] = {}
        for cam in cams:
            img = frame.images[cam]
            calib = frame.calibrations[cam]
            sq, lb = letterbox_to_square(img, target_side=args.target_side)
            K2 = rescale_K_for_letterbox(calib.K, lb)
            imgs_np.append(sq)
            K_letterboxed[cam] = K2
            T_ego_cam[cam] = calib.T_ego_cam

        # 2. Pi3 forward
        arr = np.stack(imgs_np, axis=0).astype(np.float32) / 255.0
        arr = np.transpose(arr, (0, 3, 1, 2))  # (V, 3, H, W)
        imgs_tensor = torch.from_numpy(arr).unsqueeze(0).to(device)

        t_pi3 = time.time()
        with torch.no_grad():
            if use_bf16:
                with torch.amp.autocast("cuda", dtype=torch.bfloat16):
                    res = model(imgs=imgs_tensor)
            else:
                res = model(imgs=imgs_tensor)
        if device.type == "cuda":
            torch.cuda.synchronize()
        pi3_times.append(time.time() - t_pi3)

        # 3. Extract Pi3 outputs
        local_points = res["local_points"][0].detach().float().cpu().numpy()  # (V, H, W, 3) cam frame
        world_points = res["points"][0].detach().float().cpu().numpy()        # (V, H, W, 3) Pi3 frame
        conf = res["conf"][0].detach().float().cpu().numpy()
        if conf.ndim == 4 and conf.shape[-1] == 1:
            conf = conf[..., 0]
        conf_prob = 1.0 / (1.0 + np.exp(-conf))
        pose_pi3 = res["camera_poses"][0].detach().float().cpu().numpy()  # (V, 4, 4) Pi3 cam pose

        pi3_world_points = {cam: world_points[c_i] for c_i, cam in enumerate(cams)}
        pi3_confs = {cam: conf_prob[c_i].astype(np.float32) for c_i, cam in enumerate(cams)}
        pi3_cam_pos = {cam: pose_pi3[c_i, :3, 3].astype(np.float64) for c_i, cam in enumerate(cams)}
        av2_cam_pos = {cam: T_ego_cam[cam][:3, 3].astype(np.float64) for cam in cams}
        cam_colors = {cam: imgs_np[c_i].astype(np.float32) / 255.0 for c_i, cam in enumerate(cams)}

        # 4. Sim(3) fit (per-frame for stability)
        sim3, sim3_diag = fit_sim3_from_camera_translations(pi3_cam_pos, av2_cam_pos)

        # 5. Apply Sim(3) -> ego frame
        cam_points_ego = {cam: apply_sim3_to_points(pi3_world_points[cam], sim3) for cam in cams}

        # 6. Lift+project -> ERP
        t_splat = time.time()
        erp_rgb, erp_w, l3_diag = lift_and_project_multiview(
            cam_points_ego, cam_colors, pi3_confs,
            erp_hw=erp_hw,
            conf_threshold=args.conf_threshold,
            min_distance_m=args.min_distance_m,
            max_distance_m=args.max_distance_m,
            bilinear=True,
        )
        splat_times.append(time.time() - t_splat)

        erp_u8 = (np.clip(erp_rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
        writer.append_data(erp_u8)

        if (i + 1) % 10 == 0 or i == 0 or i == n_frames - 1:
            elapsed = time.time() - t_total
            rate = (i + 1) / max(elapsed, 1e-9)
            print(f"  [{i + 1:4d}/{n_frames:4d}] idx={idx} ts={anchor} "
                  f"pi3={pi3_times[-1]:.2f}s splat={splat_times[-1]:.2f}s "
                  f"sim3_scale={sim3.scale:.3f} residual={sim3_diag['mean_residual_m']:.3f}m "
                  f"elapsed={elapsed:6.1f}s rate={rate:.2f}fps")

    writer.close()
    total = time.time() - t_total

    summary = {
        "log_id": log_id,
        "n_frames": n_frames,
        "mp4_path": str(mp4_path),
        "wall_time_s": round(total, 2),
        "pi3_mean_s": round(float(np.mean(pi3_times)), 3),
        "splat_mean_s": round(float(np.mean(splat_times)), 3),
        "device": str(device),
        "bf16": use_bf16,
        "erp_hw": list(erp_hw),
        "args": {
            "start_sec": args.start_sec,
            "duration_sec": args.duration_sec,
            "fps": args.fps,
            "target_side": args.target_side,
            "conf_threshold": args.conf_threshold,
        },
    }
    done_marker.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[l3-video] DONE in {total:.1f}s ({n_frames} frames, mean Pi3={summary['pi3_mean_s']}s splat={summary['splat_mean_s']}s)")
    print(f"[l3-video] mp4: {mp4_path}")
    print(f"[l3-video] marker: {done_marker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
