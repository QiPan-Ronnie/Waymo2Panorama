"""
Phase 3 route 14 / 新-E — Cross-cam HDR / exposure / WB compensation.

Re-runs the 7-ring-cam -> ERP stitch with a per-cam color correction applied
BEFORE multiband blending. Correction is solved via global least-squares with
Huber loss over the ERP-space overlap pixels (see
`code/waymo2panorama/color/hdr_gain_estimate.py`).

Outputs (under `<output-dir>/anchor_<id>/`):
    before.png                   — L1 sphere ERP, no correction
    after.png                    — L1 sphere ERP, with HDR correction
    correction_matrices.json     — per-cam {gain: [3], bias: [3]} + diagnostics
    summary.json                 — params, runtime, file paths, overlap stats

If `--also-make-compare-png <path>` is set, a stacked before/after panel is
also written there (used for the paper figure
`deliverables/images/route_hdr_before_after.png`).

Two input modes, mirroring `run_cylindrical_baseline.py`:
  - `--input-mode pi3-cache --pi3-cache <dir>`: dir contains `anchor_NNN/`
    subdirs with image_*.png + av2_K_letterboxed_*.npy + av2_T_ego_cam_*.npy
  - `--input-mode av2 --log-dir <av2_log> --anchor-frames N ...`: native AV2

Both modes accept multiple anchors via `--anchor-frames N N N` or `--anchor N`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

DEFAULT_W2P_CODE_REL = "../../code"

RING_CAMS_7 = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
)


# ---------------------------------------------------------------------------


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def _load_from_pi3_cache(pi3_dir: Path, cam: str) -> dict:
    image = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
    K = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
    T = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
    return {"image": image, "K": K, "T_ego_cam": T}


def _stack_compare(top: np.ndarray, bot: np.ndarray, label_h: int = 32) -> np.ndarray:
    """Stack two ERP images vertically with a 4 px white separator. Add side labels."""
    import cv2

    h, w = top.shape[:2]
    assert bot.shape[:2] == (h, w)
    sep = np.full((4, w, 3), 255, dtype=np.uint8)
    tag_top = np.full((label_h, w, 3), 32, dtype=np.uint8)
    tag_bot = np.full((label_h, w, 3), 32, dtype=np.uint8)
    cv2.putText(
        tag_top, "BEFORE  -  L1 sphere ERP, raw per-cam colors",
        (12, label_h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
        (255, 255, 255), 1, cv2.LINE_AA,
    )
    cv2.putText(
        tag_bot, "AFTER  -  L1 + cross-cam HDR/WB compensation (gain+bias, global LS Huber)",
        (12, label_h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
        (255, 255, 255), 1, cv2.LINE_AA,
    )
    return np.concatenate([tag_top, top, sep, tag_bot, bot], axis=0)


# ---------------------------------------------------------------------------


def run_one_anchor(
    *,
    anchor_idx: int,
    per_cam_data: dict[str, dict],
    out_dir: Path,
    erp_hw: tuple[int, int],
    num_bands: int,
    valid_threshold: float,
    huber_f_scale: float,
    max_pixels_per_pair: int,
    write_compare_to: Optional[Path] = None,
) -> dict:
    """Render before/after ERPs for one anchor and save diagnostics. Returns summary dict."""
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.color.hdr_gain_estimate import (
        apply_correction,
        extract_overlap_pixels,
        global_color_correction,
        overlap_diagnostics,
    )
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp

    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []

    cams = list(RING_CAMS_7)
    for cam in cams:
        d = per_cam_data[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=d["image"],
            K=d["K"],
            T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw,
        )
        slabs.append(rgb)
        weights.append(w)
    t_proj = time.time() - t0

    # Solve global LS for color correction
    t1 = time.time()
    overlaps = extract_overlap_pixels(
        slabs,
        weights,
        valid_threshold=valid_threshold,
        max_pixels_per_pair=max_pixels_per_pair,
    )
    corrections = global_color_correction(
        overlaps,
        num_cams=len(cams),
        huber_f_scale=huber_f_scale,
        verbose=True,
    )
    diag = overlap_diagnostics(overlaps, corrections)
    t_solve = time.time() - t1
    n_pairs = len(overlaps)
    print(f"[hdr] anchor {anchor_idx}: {n_pairs} cam pairs with overlap", flush=True)

    # Apply corrections
    slabs_after = [apply_correction(s, corrections[i]) for i, s in enumerate(slabs)]

    # Blend before + after
    t2 = time.time()
    before_png = multiband_blend(slabs, weights, num_bands=num_bands, wrap=True)
    after_png = multiband_blend(slabs_after, weights, num_bands=num_bands, wrap=True)
    t_blend = time.time() - t2

    Image.fromarray(before_png).save(out_dir / "before.png")
    Image.fromarray(after_png).save(out_dir / "after.png")

    if write_compare_to is not None:
        compare = _stack_compare(before_png, after_png)
        write_compare_to.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(compare).save(write_compare_to)

    # Cycle-PSNR-style: mean abs luminance gap across all overlaps,
    # plus per-pixel agreement before vs after.
    lum_gaps_before = [p["mean_abs_lum_gap_before"] for p in diag["pairs"]]
    lum_gaps_after = [p["mean_abs_lum_gap_after"] for p in diag["pairs"]]
    gap_before_mean = float(np.mean(lum_gaps_before)) if lum_gaps_before else 0.0
    gap_after_mean = float(np.mean(lum_gaps_after)) if lum_gaps_after else 0.0

    # Per-cam corrections serialized
    correction_blob = {}
    for i, cam in enumerate(cams):
        x = corrections[i]
        correction_blob[cam] = {
            "gain": [float(x[0]), float(x[1]), float(x[2])],
            "bias": [float(x[3]), float(x[4]), float(x[5])],
        }
    (out_dir / "correction_matrices.json").write_text(
        json.dumps(
            {
                "anchor_idx": int(anchor_idx),
                "per_cam": correction_blob,
                "overlap_diagnostics": diag,
                "overlap_gap_summary": {
                    "mean_abs_lum_gap_before": gap_before_mean,
                    "mean_abs_lum_gap_after": gap_after_mean,
                    "delta": gap_before_mean - gap_after_mean,
                    "n_pairs": n_pairs,
                },
            },
            indent=2,
        )
    )

    summary = {
        "anchor_idx": int(anchor_idx),
        "erp_hw": list(erp_hw),
        "num_bands": int(num_bands),
        "valid_threshold": float(valid_threshold),
        "huber_f_scale": float(huber_f_scale),
        "n_overlap_pairs": int(n_pairs),
        "mean_abs_lum_gap_before": gap_before_mean,
        "mean_abs_lum_gap_after": gap_after_mean,
        "mean_abs_lum_gap_delta": gap_before_mean - gap_after_mean,
        "timings_s": {
            "project": round(t_proj, 3),
            "ls_solve": round(t_solve, 3),
            "blend": round(t_blend, 3),
            "total": round(time.time() - t0, 3),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(
        f"[hdr] anchor {anchor_idx}: lum_gap "
        f"before={gap_before_mean:.2f} -> after={gap_after_mean:.2f} "
        f"(delta={gap_before_mean - gap_after_mean:+.2f}), "
        f"t_total={summary['timings_s']['total']:.2f}s",
        flush=True,
    )
    return summary


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument(
        "--input-mode", choices=["av2", "pi3-cache", "auto"], default="auto",
    )
    ap.add_argument(
        "--log-dir", type=Path, default=None,
        help="(av2 mode) AV2 sensor log root.",
    )
    ap.add_argument(
        "--pi3-cache", type=Path, default=None,
        help="(pi3-cache mode) Parent dir containing anchor_NNN/ subdirs. "
             "If --pi3-dir is set, that takes precedence and only one anchor "
             "is run regardless of --anchor-frames.",
    )
    ap.add_argument(
        "--pi3-dir", type=Path, default=None,
        help="(pi3-cache mode) Exact anchor dir; overrides --pi3-cache + --anchor.",
    )
    ap.add_argument(
        "--anchor", type=int, default=60,
        help="Single anchor index (used when --anchor-frames is empty).",
    )
    ap.add_argument(
        "--anchor-frames", type=int, nargs="*", default=None,
        help="One or more anchor indices to process.",
    )
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--valid-threshold", type=float, default=0.01)
    ap.add_argument("--huber-f-scale", type=float, default=10.0)
    ap.add_argument("--max-pixels-per-pair", type=int, default=4000)
    ap.add_argument(
        "--also-make-compare-png", type=Path, default=None,
        help="If set, write a stacked before/after compare PNG here. "
             "If multiple anchors are processed, only the FIRST anchor's "
             "compare PNG goes to this exact path; subsequent anchors get "
             "`<path>.anchor_<id>.png` siblings.",
    )
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    # Resolve input mode
    mode = args.input_mode
    if mode == "auto":
        if args.pi3_dir is not None or args.pi3_cache is not None:
            mode = "pi3-cache"
        elif args.log_dir is not None:
            mode = "av2"
        else:
            ap.error("provide either --pi3-cache/--pi3-dir or --log-dir")

    # Resolve anchor list
    if args.anchor_frames:
        anchors = list(args.anchor_frames)
    else:
        anchors = [int(args.anchor)]

    erp_hw = (args.erp_h, args.erp_w)
    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"[hdr] mode={mode} erp_hw={erp_hw} anchors={anchors}", flush=True)

    # Per-anchor loader closure
    av2_loader = None
    if mode == "av2":
        if args.log_dir is None:
            ap.error("--log-dir required for av2 mode")
        from waymo2panorama.data_io.av2_loader import AV2RingLoader  # noqa: E402
        av2_loader = AV2RingLoader(args.log_dir)

    def gather_data(anchor_idx: int) -> tuple[dict, int]:
        if mode == "pi3-cache":
            if args.pi3_dir is not None:
                anchor_dir = Path(args.pi3_dir)
                try:
                    a_idx = int(anchor_dir.name.split("_")[1])
                except (IndexError, ValueError):
                    a_idx = anchor_idx
            else:
                anchor_dir = Path(args.pi3_cache) / f"anchor_{anchor_idx:03d}"
                a_idx = anchor_idx
            if not anchor_dir.exists():
                raise FileNotFoundError(anchor_dir)
            data = {cam: _load_from_pi3_cache(anchor_dir, cam) for cam in RING_CAMS_7}
            return data, a_idx
        else:
            ts_all = av2_loader.anchor_timestamps_ns()
            if not (0 <= anchor_idx < len(ts_all)):
                raise IndexError(
                    f"anchor {anchor_idx} out of range [0, {len(ts_all)})"
                )
            frame = av2_loader.load_synced_frame(ts_all[anchor_idx])
            data = {
                cam: {
                    "image": frame.images[cam],
                    "K": frame.calibrations[cam].K,
                    "T_ego_cam": frame.calibrations[cam].T_ego_cam,
                }
                for cam in RING_CAMS_7
            }
            return data, anchor_idx

    # ---- run ----
    multi_summary: list[dict] = []
    for idx, a in enumerate(anchors):
        per_cam_data, a_idx = gather_data(a)
        anchor_out = out_root / f"anchor_{a_idx:03d}"
        compare_path: Optional[Path] = None
        if args.also_make_compare_png is not None:
            if idx == 0:
                compare_path = Path(args.also_make_compare_png)
            else:
                base = Path(args.also_make_compare_png)
                compare_path = base.with_name(f"{base.stem}.anchor_{a_idx:03d}{base.suffix}")
        s = run_one_anchor(
            anchor_idx=a_idx,
            per_cam_data=per_cam_data,
            out_dir=anchor_out,
            erp_hw=erp_hw,
            num_bands=args.num_bands,
            valid_threshold=args.valid_threshold,
            huber_f_scale=args.huber_f_scale,
            max_pixels_per_pair=args.max_pixels_per_pair,
            write_compare_to=compare_path,
        )
        multi_summary.append(s)

    # Aggregate
    if multi_summary:
        gaps_before = [s["mean_abs_lum_gap_before"] for s in multi_summary]
        gaps_after = [s["mean_abs_lum_gap_after"] for s in multi_summary]
        agg = {
            "anchors": [s["anchor_idx"] for s in multi_summary],
            "n_anchors": len(multi_summary),
            "mean_abs_lum_gap_before_avg": float(np.mean(gaps_before)),
            "mean_abs_lum_gap_after_avg": float(np.mean(gaps_after)),
            "mean_abs_lum_gap_delta_avg": float(np.mean(gaps_before) - np.mean(gaps_after)),
            "per_anchor": multi_summary,
        }
        (out_root / "summary_all_anchors.json").write_text(json.dumps(agg, indent=2))
        print(
            f"[hdr] AGG over {agg['n_anchors']} anchors: "
            f"lum_gap before={agg['mean_abs_lum_gap_before_avg']:.2f} -> "
            f"after={agg['mean_abs_lum_gap_after_avg']:.2f} "
            f"(delta={agg['mean_abs_lum_gap_delta_avg']:+.2f})",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
