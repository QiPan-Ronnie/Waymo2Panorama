"""
Self-stereo depth pipeline vs L1+L2+L3 OF pipeline — side-by-side BMW anchor.

Renders ONE BMW anchor (02a00399 anchor 0 by spec; pi3_cache anchor_000
locally — same frame, pre-letterboxed to 504x504) at 2048x4096 ERP and
saves a side-by-side comparison panel + per-method full ERPs + the derived
depth visualization.

  (a) hard_hdr_of           : current shipped pipeline (L1 hard + L2 HDR + L3 OF).
  (b) hard_hdr_of_selfstereo : new pipeline (L1 hard + L2 HDR + self-stereo
                               depth + N1 reproject).

Two input modes:
  --input-mode pi3-cache --pi3-cache <dir>  (default; LOCAL, no Drive needed)
  --input-mode av2 --log-dir <av2_log>      (for Drive/cluster use)

Per task: DO NOT run on Colab. Designed to run on the local Windows box
in 5-10 min @ 2048x4096 ERP.

Usage (local default):
    python scripts/phase3/test_selfstereo.py

Usage (custom):
    python scripts/phase3/test_selfstereo.py \
        --pi3-cache outputs/phase3/pi3_cache/anchor_000 \
        --output-dir deliverables/selfstereo \
        --erp-h 2048 --erp-w 4096
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Wire up the package
sys.path.insert(0, str(REPO_ROOT / "code"))


# ---------------------------------------------------------------------------
# Local "FrameSample-like" container for pi3-cache mode (av2_loader needs
# real AV2 log dir; pi3_cache has the data we need without depending on it).
# ---------------------------------------------------------------------------

@dataclass
class _CalibLike:
    K: np.ndarray
    T_ego_cam: np.ndarray


@dataclass
class _FrameLike:
    images: dict
    calibrations: dict


RING_CAMS_7 = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
)


def load_frame_from_pi3_cache(pi3_dir: Path) -> _FrameLike:
    images = {}; calibs = {}
    for cam in RING_CAMS_7:
        img = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
        K = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy").astype(np.float64)
        T = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy").astype(np.float64)
        images[cam] = img
        calibs[cam] = _CalibLike(K=K, T_ego_cam=T)
    return _FrameLike(images=images, calibrations=calibs)


def load_frame_from_av2(log_dir: Path, anchor_idx: int) -> _FrameLike:
    from waymo2panorama.data_io.av2_loader import AV2RingLoader
    loader = AV2RingLoader(log_dir)
    ts = loader.anchor_timestamps_ns()
    return loader.load_synced_frame(ts[anchor_idx])


# ---------------------------------------------------------------------------
# Render slabs (legacy mode) for input to both pipelines
# ---------------------------------------------------------------------------

def render_legacy_slabs(frame, erp_hw):
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    slabs, weights = [], []
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        slabs.append(rgb); weights.append(w)
    return slabs, weights


# ---------------------------------------------------------------------------
# Panel helpers
# ---------------------------------------------------------------------------

def label_img(arr: np.ndarray, text: str, fontsize: int = 28) -> np.ndarray:
    pil = Image.fromarray(arr.copy())
    draw = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", fontsize)
    except Exception:
        try:
            f = ImageFont.truetype("arial.ttf", fontsize)
        except Exception:
            f = ImageFont.load_default()
    # outline
    for dx in (-2, 2, 0, 0):
        for dy in (0, 0, -2, 2):
            draw.text((10 + dx, 6 + dy), text, font=f, fill="black")
    draw.text((10, 6), text, font=f, fill="white")
    return np.array(pil)


def downsize_erp(arr: np.ndarray, target_h: int = 720) -> np.ndarray:
    """Lanczos-downsize a (H, W, 3) ERP to height target_h preserving 2:1."""
    h, w = arr.shape[:2]
    if h <= target_h:
        return arr.copy()
    new_w = int(w * target_h / h)
    return np.array(Image.fromarray(arr).resize((new_w, target_h), Image.LANCZOS))


def crop_bmw(arr: np.ndarray) -> np.ndarray:
    """Tight crop on the BMW (front-right seam region).

    BMW lives in front-right seam zone in AV2 02a00399 anchor 0. Coordinates
    are relative to ERP shape so it works at 1024x2048 or 2048x4096.
    """
    H, W = arr.shape[:2]
    # Empirically: BMW spans u ~ 0.78..0.92 * W (front-right seam), v ~ 0.43..0.65 * H.
    u_l = int(0.74 * W); u_r = int(0.95 * W)
    v_t = int(0.40 * H); v_b = int(0.68 * H)
    return arr[v_t:v_b, u_l:u_r]


def depth_to_viz(depth_map: np.ndarray, coverage: np.ndarray,
                 d_min: float = 2.0, d_max: float = 60.0) -> np.ndarray:
    """Visualize a per-ERP-pixel depth map as a colorized image (turbo)."""
    d = depth_map.copy().astype(np.float32)
    d = np.clip(d, d_min, d_max)
    # log-normalize for visual contrast
    norm = (np.log(d) - np.log(d_min)) / (np.log(d_max) - np.log(d_min))
    norm = np.clip(norm, 0, 1)
    norm_8 = (norm * 255).astype(np.uint8)
    color = cv2.applyColorMap(norm_8, cv2.COLORMAP_TURBO)
    color = cv2.cvtColor(color, cv2.COLOR_BGR2RGB)
    color[~coverage] = (40, 40, 40)
    return color


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-mode", choices=("pi3-cache", "av2"), default="pi3-cache")
    ap.add_argument("--pi3-cache", type=Path,
                    default=REPO_ROOT / "outputs" / "phase3" / "pi3_cache" / "anchor_000")
    ap.add_argument("--log-dir", type=Path, default=None,
                    help="AV2 log directory (only used in --input-mode av2)")
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--output-dir", type=Path,
                    default=REPO_ROOT / "deliverables" / "selfstereo")
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    erp_hw = (args.erp_h, args.erp_w)

    # Load frame
    print(f"[load] input_mode={args.input_mode}")
    if args.input_mode == "pi3-cache":
        frame = load_frame_from_pi3_cache(args.pi3_cache)
        print(f"[load] pi3-cache from {args.pi3_cache}")
    else:
        if args.log_dir is None:
            raise SystemExit("--input-mode av2 requires --log-dir")
        frame = load_frame_from_av2(args.log_dir, args.anchor)
        print(f"[load] av2 from {args.log_dir} anchor={args.anchor}")

    # Render legacy slabs once (shared input to both pipelines)
    t0 = time.time()
    print(f"[proj] rendering 7 legacy slabs @ {erp_hw}...")
    slabs, weights = render_legacy_slabs(frame, erp_hw)
    print(f"[proj] {time.time()-t0:.1f}s")

    # ---- (a0) Baseline: L1+L2 only (no OF, no self-stereo) for reference ----
    from waymo2panorama.blending.hard_hdr_of import blend_hard_hdr_of
    t0 = time.time()
    print("[a0] hard_hdr (L1+L2 only)...")
    erp_a0 = blend_hard_hdr_of(slabs, weights, apply_of=False)
    print(f"[a0] {time.time()-t0:.1f}s")

    # ---- (a) Current shipped pipeline (L1+L2+L3 OF) ----
    t0 = time.time()
    print("[a] hard_hdr_of (L1+L2+L3 OF)...")
    erp_a = blend_hard_hdr_of(slabs, weights, apply_of=True)
    print(f"[a] {time.time()-t0:.1f}s")

    # ---- (b) New self-stereo pipeline ----
    from waymo2panorama.blending.hard_hdr_of_selfstereo import (
        blend_hard_hdr_of_selfstereo, FAR_DEPTH_M,
    )
    t0 = time.time()
    print("[b] hard_hdr_of_selfstereo (L1+L2+depth+N1)...")
    erp_b, diag = blend_hard_hdr_of_selfstereo(
        slabs, weights, frame=frame, erp_hw=erp_hw,
        return_diagnostics=True,
    )
    print(f"[b] {time.time()-t0:.1f}s")

    # ---- Diagnostic: per-pair depth coverage (proves self-stereo recovered
    #      real depth even though N1 mode then drops some of it) ----
    print("[diag] per-pair depth-recovery stats:")
    for (i, j), (dpth, valid) in zip(
        [(0,1),(1,2),(2,3),(0,6),(6,5),(5,4),(3,4)], diag["pair_depths"],
    ):
        finite = dpth[(dpth < FAR_DEPTH_M - 1) & np.isfinite(dpth)]
        if finite.size > 0:
            print(f"  pair ({i:>1},{j:>1}): valid={100.*valid.mean():6.3f}%, "
                  f"median={np.median(finite):6.2f}m, "
                  f"p10={np.percentile(finite, 10):6.2f}m, "
                  f"p90={np.percentile(finite, 90):6.2f}m")
        else:
            print(f"  pair ({i:>1},{j:>1}): NO VALID DEPTH")

    # ---- Save full ERPs ----
    Image.fromarray(erp_a0).save(args.output_dir / "erp_hard_hdr.png")
    Image.fromarray(erp_a).save(args.output_dir / "erp_hard_hdr_of.png")
    Image.fromarray(erp_b).save(args.output_dir / "erp_hard_hdr_of_selfstereo.png")
    print(f"[save] erp_hard_hdr.png + erp_hard_hdr_of.png + erp_hard_hdr_of_selfstereo.png")

    # ---- Save depth viz ----
    depth_viz = depth_to_viz(diag["global_depth_map"], diag["depth_coverage"])
    Image.fromarray(depth_viz).save(args.output_dir / "depth_viz.png")
    # Depth stats
    finite_depth = diag["global_depth_map"][np.isfinite(diag["global_depth_map"])]
    cov_pct = 100.0 * float(diag["depth_coverage"].sum()) / diag["depth_coverage"].size
    if finite_depth.size > 0:
        print(f"[depth] coverage={cov_pct:.1f}%, median={np.median(finite_depth):.2f}m, "
              f"p10={np.percentile(finite_depth, 10):.2f}m, "
              f"p90={np.percentile(finite_depth, 90):.2f}m")
    else:
        print(f"[depth] coverage={cov_pct:.1f}% NO FINITE DEPTH")
    print(f"[depth] hdr_gains = {[round(g, 3) for g in diag['hdr_gains']]}")

    # ---- 2-way primary BMW comparison panel (per spec) ----
    crop_a0 = crop_bmw(erp_a0)
    crop_a = crop_bmw(erp_a)
    crop_b = crop_bmw(erp_b)
    crop_a_l = label_img(crop_a, "(a) hard_hdr_of  [L1+L2+L3 OF]")
    crop_b_l = label_img(crop_b, "(b) hard_hdr_of_selfstereo  [L1+L2+depth+N1]")
    def _pad(arr, h, w):
        oh, ow = arr.shape[:2]
        out = np.zeros((h, w, 3), dtype=np.uint8)
        out[:oh, :ow] = arr
        return out
    H_lab = max(crop_a_l.shape[0], crop_b_l.shape[0])
    W_lab = max(crop_a_l.shape[1], crop_b_l.shape[1])
    crop_a_l = _pad(crop_a_l, H_lab, W_lab)
    crop_b_l = _pad(crop_b_l, H_lab, W_lab)
    sep = np.full((H_lab, 8, 3), 255, dtype=np.uint8)
    panel = np.concatenate([crop_a_l, sep, crop_b_l], axis=1)
    Image.fromarray(panel).save(args.output_dir / "bmw_compare.png")
    print(f"[save] bmw_compare.png   {panel.shape}")

    # ---- 3-way bonus: also include the L1+L2-only baseline for context ----
    crop_a0_l = label_img(crop_a0, "(a0) hard_hdr  [L1+L2 only]")
    crop_a_l_3 = label_img(crop_a, "(a) hard_hdr_of  [L1+L2+L3 OF]")
    crop_b_l_3 = label_img(crop_b, "(b) hard_hdr_of_selfstereo  [L1+L2+depth+N1]")
    H_lab3 = max(crop_a0_l.shape[0], crop_a_l_3.shape[0], crop_b_l_3.shape[0])
    W_lab3 = max(crop_a0_l.shape[1], crop_a_l_3.shape[1], crop_b_l_3.shape[1])
    crop_a0_l = _pad(crop_a0_l, H_lab3, W_lab3)
    crop_a_l_3 = _pad(crop_a_l_3, H_lab3, W_lab3)
    crop_b_l_3 = _pad(crop_b_l_3, H_lab3, W_lab3)
    sep3 = np.full((H_lab3, 8, 3), 255, dtype=np.uint8)
    panel3 = np.concatenate([crop_a0_l, sep3, crop_a_l_3, sep3, crop_b_l_3], axis=1)
    Image.fromarray(panel3).save(args.output_dir / "bmw_compare_3way.png")
    print(f"[save] bmw_compare_3way.png   {panel3.shape}")

    # ---- Also save downsized 3-way full ERP stack for context ----
    dsd_a0 = label_img(downsize_erp(erp_a0, 480), "(a0) hard_hdr  [L1+L2 only]", fontsize=20)
    dsd_a = label_img(downsize_erp(erp_a, 480), "(a) hard_hdr_of  [L1+L2+L3 OF]", fontsize=20)
    dsd_b = label_img(downsize_erp(erp_b, 480), "(b) hard_hdr_of_selfstereo  [L1+L2+depth+N1]", fontsize=20)
    H_lab2 = max(dsd_a0.shape[0], dsd_a.shape[0], dsd_b.shape[0])
    W_lab2 = max(dsd_a0.shape[1], dsd_a.shape[1], dsd_b.shape[1])
    dsd_a0 = _pad(dsd_a0, H_lab2, W_lab2)
    dsd_a = _pad(dsd_a, H_lab2, W_lab2)
    dsd_b = _pad(dsd_b, H_lab2, W_lab2)
    sep2 = np.full((6, W_lab2, 3), 255, dtype=np.uint8)
    full_panel = np.concatenate([dsd_a0, sep2, dsd_a, sep2, dsd_b], axis=0)
    Image.fromarray(full_panel).save(args.output_dir / "full_compare.png")
    print(f"[save] full_compare.png  {full_panel.shape}")

    print(f"[done] outputs in {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
