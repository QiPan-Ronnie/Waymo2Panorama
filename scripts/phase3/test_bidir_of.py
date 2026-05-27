"""
3-way comparison: shipped hard_hdr_of vs new hard_hdr_of_bidir (chain) vs joint.

Renders the BMW anchor (02a00399 anchor 0) at 2048x4096 with each pipeline
and produces a side-by-side panel + a BMW zoom panel.

Uses the LOCALLY CACHED anchor data at
    outputs/phase3/pi3_cache/anchor_000/
(image_ring_*.png + av2_K_letterboxed_*.npy + av2_T_ego_cam_*.npy)
so this script DOES NOT need to talk to Colab / Drive / raw AV2 logs.

Usage:
    python scripts/phase3/test_bidir_of.py
    # → writes deliverables/bidir_of/bmw_compare.png + the 3 full ERPs
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2  # noqa: F401  (forces cv2 import resolution before our blending module)
import numpy as np
from PIL import Image, ImageDraw, ImageFont


CAMS = [
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
]


def load_cached_anchor(cache_dir: Path) -> dict[str, dict]:
    """Load per-cam {image, K, T_ego_cam} from a pi3_cache anchor directory."""
    out: dict[str, dict] = {}
    for cam in CAMS:
        img_path = cache_dir / f"image_{cam}.png"
        K_path = cache_dir / f"av2_K_letterboxed_{cam}.npy"
        T_path = cache_dir / f"av2_T_ego_cam_{cam}.npy"
        if not (img_path.exists() and K_path.exists() and T_path.exists()):
            raise FileNotFoundError(
                f"Missing cache file for {cam} under {cache_dir}.\n"
                f"  Expected: {img_path.name}, {K_path.name}, {T_path.name}"
            )
        img = np.array(Image.open(img_path).convert("RGB"))
        K = np.load(K_path)
        T = np.load(T_path)
        out[cam] = {"image": img, "K": K, "T_ego_cam": T}
    return out


def project_all_cams(per_cam: dict[str, dict], erp_hw: tuple[int, int]):
    """Render each cam to its ERP slab + weight."""
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for cam in CAMS:
        d = per_cam[cam]
        rgb, _, w = render_camera_to_erp(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w)
    return slabs, weights


def label_image(arr: np.ndarray, text: str, fontsize: int = 22) -> np.ndarray:
    pil = Image.fromarray(arr.copy())
    draw = ImageDraw.Draw(pil)
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", fontsize)
    except Exception:
        try:
            f = ImageFont.truetype("arialbd.ttf", fontsize)
        except Exception:
            f = ImageFont.load_default()
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((6 + dx, 4 + dy), text, font=f, fill="black")
    draw.text((6, 4), text, font=f, fill="white")
    return np.array(pil)


def crop_region(arr: np.ndarray, col_c: int, row_t: int, row_b: int, half: int) -> np.ndarray:
    W = arr.shape[1]
    col_l = max(0, col_c - half); col_r = min(W, col_c + half)
    return arr[row_t:row_b, col_l:col_r]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--cache-dir", type=Path,
        default=Path(__file__).resolve().parent.parent.parent
                / "outputs" / "phase3" / "pi3_cache" / "anchor_000",
        help="Pi3 cache directory for the BMW anchor.",
    )
    ap.add_argument(
        "--output-dir", type=Path,
        default=Path(__file__).resolve().parent.parent.parent
                / "deliverables" / "bidir_of",
    )
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--skip-joint", action="store_true",
                    help="Skip the joint solve (faster, only chain comparison).")
    ap.add_argument(
        "--w2p-code", type=Path,
        default=Path(__file__).resolve().parent.parent.parent / "code",
    )
    args = ap.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(args.w2p_code))
    # Late imports so sys.path is set first
    from waymo2panorama.blending.hard_hdr_of import blend_hard_hdr_of
    from waymo2panorama.blending.hard_hdr_of_bidir import blend_hard_hdr_of_bidir

    print(f"Loading cached BMW anchor: {args.cache_dir}")
    per_cam = load_cached_anchor(args.cache_dir)
    for cam in CAMS:
        h, w = per_cam[cam]["image"].shape[:2]
        print(f"  {cam:<22s}: image {h}x{w}, "
              f"K[0,0]={per_cam[cam]['K'][0,0]:.1f}")

    erp_hw = (args.erp_h, args.erp_w)
    print(f"\nProjecting 7 cams to ERP {erp_hw[0]}x{erp_hw[1]}...")
    t0 = time.time()
    slabs, weights = project_all_cams(per_cam, erp_hw)
    print(f"  projection done in {time.time()-t0:.1f}s")

    # --- 1) shipped hard_hdr_of (full asymmetric chain warp) ---
    print("\n[1/3] shipped hard_hdr_of ...")
    t0 = time.time()
    erp_shipped = blend_hard_hdr_of(slabs, weights, apply_of=True)
    t_shipped = time.time() - t0
    print(f"  shipped pipeline: {t_shipped:.1f}s")

    # --- 2) bidirectional chain ---
    print("\n[2/3] hard_hdr_of_bidir (chain) ...")
    t0 = time.time()
    erp_bidir_chain = blend_hard_hdr_of_bidir(
        slabs, weights, apply_of=True, mode="chain",
    )
    t_chain = time.time() - t0
    print(f"  bidir chain pipeline: {t_chain:.1f}s")

    erps = [erp_shipped, erp_bidir_chain]
    labels = ["SHIPPED (asym chain)", "BIDIR CHAIN (half warp)"]
    save_names = ["erp_shipped.png", "erp_bidir_chain.png"]

    # --- 3) joint solve (optional) ---
    if not args.skip_joint:
        print("\n[3/3] hard_hdr_of_bidir (joint solve) ...")
        t0 = time.time()
        try:
            erp_joint = blend_hard_hdr_of_bidir(
                slabs, weights, apply_of=True, mode="joint",
            )
            t_joint = time.time() - t0
            print(f"  joint solve pipeline: {t_joint:.1f}s")
            erps.append(erp_joint)
            labels.append("BIDIR JOINT (global)")
            save_names.append("erp_bidir_joint.png")
        except Exception as e:
            print(f"  joint solve FAILED: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            print("  → continuing with chain comparison only")

    # Save full ERPs
    for arr, name in zip(erps, save_names):
        Image.fromarray(arr).save(args.output_dir / name)

    # BMW zoom crop (same coords as previous deliverables)
    H, W = args.erp_h, args.erp_w
    bmw_col_c = int(round(3500 / 4096 * W))
    bmw_row_t = int(round(900 / 2048 * H))
    bmw_row_b = int(round(1300 / 2048 * H))
    bmw_half = int(round(250 / 4096 * W))

    bmw_crops = [
        crop_region(e, bmw_col_c, bmw_row_t, bmw_row_b, bmw_half)
        for e in erps
    ]
    bmw_labeled = [label_image(c, lbl) for c, lbl in zip(bmw_crops, labels)]
    bmw_panel = np.concatenate(bmw_labeled, axis=1)
    Image.fromarray(bmw_panel).save(args.output_dir / "bmw_compare.png")

    # Porsche zoom (also from previous deliverables)
    por_col_c = int(round(1500 / 4096 * W))
    por_row_t = int(round(1000 / 2048 * H))
    por_row_b = int(round(1300 / 2048 * H))
    por_half = int(round(300 / 4096 * W))
    por_crops = [
        crop_region(e, por_col_c, por_row_t, por_row_b, por_half)
        for e in erps
    ]
    por_labeled = [label_image(c, lbl) for c, lbl in zip(por_crops, labels)]
    por_panel = np.concatenate(por_labeled, axis=1)
    Image.fromarray(por_panel).save(args.output_dir / "porsche_compare.png")

    # Full ERP stack: downsized rows of each variant
    def downsize(a, h=512):
        w = int(a.shape[1] * h / a.shape[0])
        return np.array(Image.fromarray(a).resize((w, h), Image.LANCZOS))
    full_panel = np.concatenate([
        label_image(downsize(e), l, fontsize=18) for e, l in zip(erps, labels)
    ], axis=0)
    Image.fromarray(full_panel).save(args.output_dir / "full_compare.png")

    # Absolute-difference images vs shipped (per-cam differences highlighted as
    # heatmaps — useful for spotting *where* the bidir variants change things)
    def diff_image(a, b):
        d = np.abs(a.astype(np.int16) - b.astype(np.int16)).astype(np.float32)
        d = d.mean(axis=2)            # per-pixel scalar diff
        d_vis = np.clip(d * 4.0, 0, 255).astype(np.uint8)  # boost 4x for visibility
        # Apply colormap (jet) so small diffs visible
        return cv2.applyColorMap(d_vis, cv2.COLORMAP_JET)[..., ::-1]  # BGR->RGB

    if len(erps) >= 2:
        diff_chain = diff_image(erps[1], erps[0])
        Image.fromarray(label_image(downsize(diff_chain), "BIDIR CHAIN - SHIPPED (4x boost)", 18)
                        ).save(args.output_dir / "diff_chain_vs_shipped.png")
    if len(erps) >= 3:
        diff_joint = diff_image(erps[2], erps[0])
        Image.fromarray(label_image(downsize(diff_joint), "BIDIR JOINT - SHIPPED (4x boost)", 18)
                        ).save(args.output_dir / "diff_joint_vs_shipped.png")

    print(f"\nSaved {len(erps)} ERPs + 3 panels to: {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
