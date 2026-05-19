"""
scripts/run_l1_baseline.py

Run the L1 baseline (spherical projection + multi-band blending) over one AV2 sensor log,
producing per-frame ERP PNGs and an mp4.

Usage (Colab/Linux):
    python scripts/run_l1_baseline.py \
        --log-dir /content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/<UUID> \
        --out-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/l1 \
        --duration-sec 5 \
        --save-frames

The output mp4 plays at native 20 Hz. Save the first frame PNG separately for inline display.

Phase 1 goal: produce ONE clip; capture failure modes (ghosting, hood, exposure mismatch,
seam visibility) into notes/baseline_diagnosis.md. Do NOT chase quality at L1 — it's a
deliberate floor to measure Phase 2 (3D-aware) gains against.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import imageio
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "code"))

from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.pipeline.stitch_frame import stitch_one_frame  # noqa: E402


def load_ego_masks(masks_dir: Path | None) -> dict[str, np.ndarray]:
    """Per-camera ego masks: masks_dir/<cam>.png (8-bit grayscale; >127 = keep)."""
    masks: dict[str, np.ndarray] = {}
    if masks_dir is None or not masks_dir.exists():
        return masks
    for cam in RING_CAMS_7:
        p = masks_dir / f"{cam}.png"
        if p.exists():
            m = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            masks[cam] = (m > 127).astype(np.uint8)
    return masks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", type=Path, required=True, help="One AV2 sensor log root")
    ap.add_argument("--out-dir", type=Path, required=True, help="Output dir (a <log_id>/ subdir will be created)")
    ap.add_argument("--erp-height", type=int, default=1024)
    ap.add_argument("--erp-width", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--no-wrap", action="store_true", help="Disable horizontal wrap padding in multiband (debug)")
    ap.add_argument("--start-sec", type=float, default=0.0)
    ap.add_argument("--duration-sec", type=float, default=5.0)
    ap.add_argument("--fps", type=int, default=20, help="AV2 ring cams are 20 Hz; keep at 20 unless decimating")
    ap.add_argument("--frame-step", type=int, default=1)
    ap.add_argument("--ego-masks-dir", type=Path, default=None)
    ap.add_argument("--save-frames", action="store_true", help="Also save per-frame PNGs to <out_dir>/<log_id>/")
    args = ap.parse_args()

    loader = AV2RingLoader(args.log_dir)
    log_id = args.log_dir.name
    out_dir = args.out_dir / log_id
    out_dir.mkdir(parents=True, exist_ok=True)

    ego_masks = load_ego_masks(args.ego_masks_dir)
    print(f"AV2RingLoader log_id={log_id}; ring cams={RING_CAMS_7}")
    print(f"anchor frames in log: {loader.num_anchor_frames()}")
    if ego_masks:
        print(f"ego masks loaded for: {sorted(ego_masks)}")
    else:
        print("No ego masks provided. Vehicle hood may appear in the lower ERP region.")
    print(f"ERP: {args.erp_height} x {args.erp_width};  num_bands={args.num_bands};  wrap={not args.no_wrap}")

    ts_all = loader.anchor_timestamps_ns()
    start_idx = max(0, int(args.start_sec * args.fps))
    stop_idx = min(len(ts_all), start_idx + int(args.duration_sec * args.fps))
    indices = list(range(start_idx, stop_idx, args.frame_step))
    print(f"Rendering {len(indices)} frames ({args.start_sec:.1f}s -> {args.start_sec + args.duration_sec:.1f}s) at step={args.frame_step}")

    mp4_path = out_dir / "baseline.mp4"
    writer = imageio.get_writer(mp4_path, fps=args.fps, codec="libx264", quality=8)

    t0 = time.time()
    for i, idx in enumerate(indices):
        anchor = ts_all[idx]
        frame = loader.load_synced_frame(anchor)
        erp = stitch_one_frame(
            frame,
            erp_hw=(args.erp_height, args.erp_width),
            num_bands=args.num_bands,
            ego_masks=ego_masks,
            wrap=not args.no_wrap,
        )
        writer.append_data(erp)
        if args.save_frames:
            frame_path = out_dir / f"frame_{idx:05d}.png"
            cv2.imwrite(str(frame_path), cv2.cvtColor(erp, cv2.COLOR_RGB2BGR))
        if (i + 1) % 10 == 0 or i == 0 or i == len(indices) - 1:
            elapsed = time.time() - t0
            rate = (i + 1) / max(elapsed, 1e-9)
            print(f"  [{i + 1:4d}/{len(indices):4d}] idx={idx} ts={anchor} elapsed={elapsed:6.1f}s ({rate:.2f} fps)")
    writer.close()

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s. mp4: {mp4_path}")
    if args.save_frames:
        n = len(indices)
        print(f"Saved {n} PNGs to: {out_dir}/frame_*.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
