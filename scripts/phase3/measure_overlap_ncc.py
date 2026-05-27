"""
Pixel-level ghost metric: overlap-zone NCC between rendered ERP and the
winning ring cam's ERP slab.

Motivation
----------
The YOLO-bbox metrics (score_panorama_yolo.py, score_panorama_doubled.py) do
not cleanly isolate doubled-feature ghost — see
`deliverables/doubled_metric_negative_finding.md`. The seam-gap luminance Y
metric (measure_seam_gap.py) measures HDR effectiveness, not ghost geometry.

This script implements a TRUE pixel-level ghost detector. It re-projects the
original 7 ring cams to per-cam ERP slabs (legacy infinity-depth, identical
to the pipeline default) and then, at every overlap pixel of every adjacent
ring pair, asks:

    "Does the rendered panorama look like the WINNING cam's content, or like
     some chimera blend of both cams?"

We answer with windowed Normalized Cross-Correlation (NCC) between the
rendered ERP and the winning cam's slab, computed in a small box around each
overlap pixel. We additionally report a simpler per-pixel SSD against the
winning cam's slab (lower = better preservation).

Interpretation
--------------
- High NCC (close to 1.0): the rendered panorama agrees structurally with the
  winning cam in the overlap zone — no view-mixing chimera ghost.
- Low NCC: the rendered panorama is a blend of cam_i and cam_j with shifted
  edges — i.e. doubled-feature ghost.

Expected ordering (best to worst, NCC-high to NCC-low):
    hard_hdr_of  >  hard_select  >  multiband
    (aligned)    >  (one-cam)    >  (chimera blend)

The L3 OF chain warp in hard_hdr_of should yield the highest NCC because
slabs are aligned BEFORE selection, so even at sub-pixel scale the rendered
ERP matches the winning cam well.

Usage
-----
    python scripts/phase3/measure_overlap_ncc.py \\
        --pano-dirs \\
            multiband=/content/.../multiband_baseline_v1 \\
            hard_hdr_of_v1=/content/.../full_pipeline_v1 \\
        --log-root /content/drive/.../av2_logs \\
        --output-json /content/.../overlap_ncc.json

The script expects rendered panoramas to live under
    {pano-dir}/{log_short}/anchor_NNNN.png
where `log_short = log_dir.name.split("-")[0]` (matches
render_log_with_hard_hdr_of.py's convention).

The original AV2 log dirs are located beneath `--log-root` by matching the
same `log_dir.name.split("-")[0]` prefix.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


# --------------------------------------------------------------------------
# NCC kernel (windowed per-pixel NCC via box filter)
# --------------------------------------------------------------------------


def _box_blur(x: np.ndarray, win: int) -> np.ndarray:
    """Box-mean filter, mean over a `win`x`win` window centered on each pixel."""
    return cv2.boxFilter(x, ddepth=-1, ksize=(win, win), normalize=True,
                         borderType=cv2.BORDER_REFLECT)


def per_pixel_ncc(a: np.ndarray, b: np.ndarray, win: int) -> np.ndarray:
    """Per-pixel windowed NCC between two single-channel float images.

    For each pixel p, returns the Pearson correlation coefficient between the
    `win`x`win` patches of a and b centered at p. Uses the standard
    box-filter NCC formulation:

        NCC = ( E[a*b] - E[a]*E[b] ) / ( std(a) * std(b) )

    Output is clipped to [-1, 1] to absorb floating-point overshoot.
    """
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    mu_a = _box_blur(a, win)
    mu_b = _box_blur(b, win)
    mu_ab = _box_blur(a * b, win)
    var_a = np.maximum(_box_blur(a * a, win) - mu_a * mu_a, 0.0)
    var_b = np.maximum(_box_blur(b * b, win) - mu_b * mu_b, 0.0)
    denom = np.sqrt(var_a * var_b)
    cov = mu_ab - mu_a * mu_b
    ncc = np.where(denom > 1e-6, cov / np.maximum(denom, 1e-6), 0.0)
    return np.clip(ncc, -1.0, 1.0)


def rgb_to_gray_f32(rgb: np.ndarray) -> np.ndarray:
    """Convert an RGB image (uint8 or float) to a single-channel float32 grayscale."""
    if rgb.dtype != np.uint8:
        rgb_u8 = np.clip(rgb, 0, 255).astype(np.uint8)
    else:
        rgb_u8 = rgb
    return cv2.cvtColor(rgb_u8, cv2.COLOR_RGB2GRAY).astype(np.float32)


# --------------------------------------------------------------------------
# Anchor / log mapping
# --------------------------------------------------------------------------


ANCHOR_RE = re.compile(r"anchor_(\d+)\.png$")


def anchor_idx_from_filename(name: str) -> int | None:
    """Extract anchor index N from `anchor_NNNN.png`. Returns None on miss."""
    m = ANCHOR_RE.search(name)
    return int(m.group(1)) if m else None


def find_log_dir(log_root: Path, log_short: str) -> Path | None:
    """Locate the original AV2 log dir whose split-by-hyphen prefix matches log_short."""
    if not log_root.exists():
        return None
    for child in log_root.iterdir():
        if not child.is_dir():
            continue
        if child.name.split("-")[0] == log_short:
            return child
    return None


# --------------------------------------------------------------------------
# Re-project the 7 ring cams to ERP slabs at one anchor
# --------------------------------------------------------------------------


def render_slabs_for_anchor(loader, ts_ns: int, erp_hw: tuple[int, int],
                            render_camera_to_erp, RING_CAMS_7,
                            build_ego_masks=None):
    """Re-project all 7 ring cams onto ERP at this anchor.

    Returns (slabs, weights): lists indexed by RING_CAMS_7 order.
    Uses the pipeline default (legacy infinity-depth, convergence_distance_m=None)
    so the slabs exactly match what the rendered ERP was produced from.
    Ego masks are applied when available (matches eval_parallax_seam_metric.py).
    """
    frame = loader.load_synced_frame(ts_ns)
    image_shapes = {cam: frame.images[cam].shape[:2] for cam in RING_CAMS_7}
    ego_masks = {}
    if build_ego_masks is not None:
        try:
            ego_masks = build_ego_masks(list(RING_CAMS_7), image_shapes, enabled=True)
        except Exception:
            ego_masks = {}

    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for cam in RING_CAMS_7:
        c = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=frame.images[cam], K=c.K, T_ego_cam=c.T_ego_cam,
            erp_hw=erp_hw, ego_mask=ego_masks.get(cam),
            convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w)
    return slabs, weights


# --------------------------------------------------------------------------
# Per-anchor NCC + SSD scoring
# --------------------------------------------------------------------------


def score_one_anchor(pano_rgb: np.ndarray,
                     slabs: list[np.ndarray],
                     weights: list[np.ndarray],
                     RING_PAIRS,
                     win: int,
                     overlap_thresh: float = 1e-6,
                     min_overlap_px: int = 200,
                     max_sample_per_pair: int = 20000) -> dict:
    """Compute mean overlap NCC and SSD for one anchor's rendered panorama.

    For each ring pair (i, j):
      1. Identify overlap mask where both weights > thresh.
      2. Compute per-pixel windowed NCC between rendered ERP and winning cam's slab
         (winning = argmax of (w_i, w_j) at each overlap pixel).
      3. Compute per-pixel grayscale SSD between rendered ERP and winning cam's slab.
      4. Average NCC and SSD over overlap pixels.

    Returns dict with per-pair stats + aggregate (overlap-area-weighted) mean.
    """
    # Pre-compute grayscale renderings once
    pano_gray = rgb_to_gray_f32(pano_rgb)
    # Per-cam grayscale on demand inside the loop to keep peak RAM manageable
    H, W = pano_rgb.shape[:2]
    if pano_rgb.shape[:2] != slabs[0].shape[:2]:
        raise ValueError(
            f"rendered panorama shape {pano_rgb.shape[:2]} does not match "
            f"re-projected slab shape {slabs[0].shape[:2]}. Check --erp-h/-w."
        )

    per_pair: dict[str, dict] = {}
    total_overlap_px = 0
    sum_ncc_pano_vs_win = 0.0
    sum_ssd_pano_vs_win = 0.0
    # Reference (sanity-check) metric: NCC of the winning cam's slab against
    # the LOSING cam's slab in overlap. This is a method-independent "natural
    # chimera floor" — how aligned the two source cams are with each other
    # before any blending. Useful to see whether a method's pano-vs-winning
    # NCC is meaningfully above the raw parallax floor.
    sum_ncc_winner_vs_loser = 0.0

    for (i, j) in RING_PAIRS:
        overlap = (weights[i] > overlap_thresh) & (weights[j] > overlap_thresh)
        n_overlap = int(overlap.sum())
        if n_overlap < min_overlap_px:
            continue

        # Winning cam per pixel (within this pair only) by larger weight
        wins_i = weights[i] >= weights[j]  # True → cam_i wins, False → cam_j wins

        slab_i_gray = rgb_to_gray_f32(slabs[i])
        slab_j_gray = rgb_to_gray_f32(slabs[j])
        # The "winning cam" slab at each pixel — values from slab_i where wins_i
        # else from slab_j. We need this as a single grayscale image so we can
        # box-filter NCC against pano_gray in one shot over the whole overlap.
        winner_gray = np.where(wins_i, slab_i_gray, slab_j_gray)
        loser_gray = np.where(wins_i, slab_j_gray, slab_i_gray)

        # Per-pixel windowed NCC between rendered ERP and winning cam.
        # Important: NCC is local — at overlap pixels far from the seam this is
        # almost always ~1.0 because the winning cam is the only contributor
        # in hard_select. Near the seam (where multiband would chimera-blend)
        # NCC drops. So we DO sample uniformly over the overlap; the mean
        # captures both effects.
        ncc_pano_vs_win = per_pixel_ncc(pano_gray, winner_gray, win)
        ncc_win_vs_lose = per_pixel_ncc(winner_gray, loser_gray, win)

        # Grayscale SSD (per-pixel squared diff). Lower = closer to winning cam.
        diff = pano_gray - winner_gray
        ssd_per_px = diff * diff

        # Reduce to overlap pixels only
        ovl_idx = np.where(overlap)
        n = len(ovl_idx[0])
        if n > max_sample_per_pair:
            sel = np.random.default_rng(0).choice(n, size=max_sample_per_pair, replace=False)
            rr = ovl_idx[0][sel]
            cc = ovl_idx[1][sel]
        else:
            rr, cc = ovl_idx[0], ovl_idx[1]

        ncc_vals = ncc_pano_vs_win[rr, cc]
        wvl_vals = ncc_win_vs_lose[rr, cc]
        ssd_vals = ssd_per_px[rr, cc]

        mean_ncc = float(ncc_vals.mean())
        mean_wvl = float(wvl_vals.mean())
        mean_ssd = float(ssd_vals.mean())

        pair_key = f"{i}-{j}"
        per_pair[pair_key] = {
            "n_overlap_px": n_overlap,
            "n_sampled_px": int(len(ncc_vals)),
            "mean_ncc_pano_vs_winner": mean_ncc,
            "mean_ncc_winner_vs_loser": mean_wvl,
            "mean_ssd_pano_vs_winner": mean_ssd,
        }
        # Overlap-area weighting for the aggregate
        total_overlap_px += n_overlap
        sum_ncc_pano_vs_win += mean_ncc * n_overlap
        sum_ncc_winner_vs_loser += mean_wvl * n_overlap
        sum_ssd_pano_vs_win += mean_ssd * n_overlap

    if total_overlap_px == 0:
        return {"per_pair": per_pair, "aggregate": None}
    agg = {
        "total_overlap_px": int(total_overlap_px),
        "mean_ncc_pano_vs_winner": sum_ncc_pano_vs_win / total_overlap_px,
        "mean_ncc_winner_vs_loser": sum_ncc_winner_vs_loser / total_overlap_px,
        "mean_ssd_pano_vs_winner": sum_ssd_pano_vs_win / total_overlap_px,
    }
    return {"per_pair": per_pair, "aggregate": agg}


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pano-dirs", type=str, nargs="+", required=True,
                    help="space-list of name=path, e.g. multiband=/foo hard_hdr_of=/bar")
    ap.add_argument("--log-root", type=Path, required=True,
                    help="parent dir containing the original AV2 log dirs (full UUID names)")
    ap.add_argument("--output-json", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--ncc-win", type=int, default=9,
                    help="NCC window size in pixels (odd; default 9)")
    ap.add_argument("--max-anchors", type=int, default=0,
                    help="if >0, limit per-method to first N common anchors (debug)")
    ap.add_argument("--max-sample-per-pair", type=int, default=20000,
                    help="cap on overlap pixels sampled per ring pair (for speed)")
    ap.add_argument("--w2p-code", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent / "code")
    args = ap.parse_args()

    if args.ncc_win % 2 == 0:
        print(f"warning: --ncc-win {args.ncc_win} is even; using {args.ncc_win + 1}")
        args.ncc_win += 1

    sys.path.insert(0, str(args.w2p_code))
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    from waymo2panorama.blending.hard_hdr_of import RING_PAIRS
    try:
        from waymo2panorama.data_io.ego_mask import build_ego_masks
    except Exception:
        build_ego_masks = None  # type: ignore

    # Parse pano dirs
    method_dirs: dict[str, Path] = {}
    for spec in args.pano_dirs:
        name, path = spec.split("=", 1)
        method_dirs[name] = Path(path)

    # Find common anchors across methods (by log + anchor png name)
    method_anchors: dict[str, set[tuple[str, str]]] = {}
    for method, root in method_dirs.items():
        method_anchors[method] = set()
        for log_dir in sorted(root.iterdir()):
            if not log_dir.is_dir():
                continue
            for png in sorted(log_dir.glob("anchor_*.png")):
                method_anchors[method].add((log_dir.name, png.name))

    common = sorted(set.intersection(*method_anchors.values()))
    print(f"common anchors across all methods: {len(common)}")
    if args.max_anchors > 0:
        common = common[: args.max_anchors]
        print(f"  limited to first {len(common)} anchors for debug")

    # Pre-resolve log dirs (we project once per anchor, reuse across methods).
    log_dir_cache: dict[str, Path | None] = {}
    loader_cache: dict[str, AV2RingLoader] = {}

    erp_hw = (args.erp_h, args.erp_w)

    per_anchor: dict[str, dict] = {}
    # Aggregates: sums weighted by total_overlap_px across anchors
    method_sums: dict[str, dict[str, float]] = {
        m: {"ncc": 0.0, "ncc_wvl": 0.0, "ssd": 0.0, "overlap_px": 0.0, "n_anchors": 0}
        for m in method_dirs.keys()
    }

    for log_short, png_name in common:
        anchor_idx = anchor_idx_from_filename(png_name)
        if anchor_idx is None:
            print(f"  skip {log_short}/{png_name}: cannot parse anchor index")
            continue

        # Resolve log dir
        if log_short not in log_dir_cache:
            log_dir_cache[log_short] = find_log_dir(args.log_root, log_short)
        log_dir = log_dir_cache[log_short]
        if log_dir is None:
            print(f"  skip {log_short}/{png_name}: no matching log dir under {args.log_root}")
            continue

        # Loader (cache per log)
        if log_short not in loader_cache:
            try:
                loader_cache[log_short] = AV2RingLoader(log_dir)
            except Exception as e:
                print(f"  skip {log_short}: loader failed: {e}")
                loader_cache[log_short] = None  # type: ignore
                continue
        loader = loader_cache[log_short]
        if loader is None:
            continue

        ts_all = loader.anchor_timestamps_ns()
        if anchor_idx >= len(ts_all):
            print(f"  skip {log_short}/{png_name}: anchor {anchor_idx} >= "
                  f"available {len(ts_all)}")
            continue

        # Re-project cams once per anchor — reused across methods.
        try:
            slabs, weights = render_slabs_for_anchor(
                loader, ts_all[anchor_idx], erp_hw,
                render_camera_to_erp, RING_CAMS_7,
                build_ego_masks=build_ego_masks,
            )
        except Exception as e:
            print(f"  skip {log_short}/{png_name}: render_slabs failed: {e}")
            continue

        key = f"{log_short}/{png_name}"
        per_anchor[key] = {}
        msg_parts = []
        for method, root in method_dirs.items():
            pano_path = root / log_short / png_name
            try:
                pano_rgb = np.asarray(Image.open(pano_path).convert("RGB"))
            except Exception as e:
                print(f"  skip {key} method={method}: load failed: {e}")
                continue
            # Resize pano if shape doesn't match (e.g. rendered at different ERP res)
            if pano_rgb.shape[:2] != erp_hw:
                pano_rgb = cv2.resize(
                    pano_rgb, (erp_hw[1], erp_hw[0]), interpolation=cv2.INTER_AREA,
                )

            scores = score_one_anchor(
                pano_rgb, slabs, weights, RING_PAIRS,
                win=args.ncc_win,
                max_sample_per_pair=args.max_sample_per_pair,
            )
            per_anchor[key][method] = scores
            agg = scores.get("aggregate")
            if agg is not None:
                method_sums[method]["ncc"] += agg["mean_ncc_pano_vs_winner"] * agg["total_overlap_px"]
                method_sums[method]["ncc_wvl"] += agg["mean_ncc_winner_vs_loser"] * agg["total_overlap_px"]
                method_sums[method]["ssd"] += agg["mean_ssd_pano_vs_winner"] * agg["total_overlap_px"]
                method_sums[method]["overlap_px"] += agg["total_overlap_px"]
                method_sums[method]["n_anchors"] += 1
                msg_parts.append(f"{method}=NCC{agg['mean_ncc_pano_vs_winner']:.3f}")
            else:
                msg_parts.append(f"{method}=NCC--")
        print(f"  {key}: " + ", ".join(msg_parts))

    # Method-level aggregates
    aggregate: dict[str, dict] = {}
    for m, sums in method_sums.items():
        opx = sums["overlap_px"]
        if opx > 0:
            aggregate[m] = {
                "n_anchors_scored": int(sums["n_anchors"]),
                "mean_ncc_pano_vs_winner": sums["ncc"] / opx,
                "mean_ncc_winner_vs_loser_floor": sums["ncc_wvl"] / opx,
                "mean_ssd_pano_vs_winner": sums["ssd"] / opx,
            }
        else:
            aggregate[m] = {
                "n_anchors_scored": 0,
                "mean_ncc_pano_vs_winner": None,
                "mean_ncc_winner_vs_loser_floor": None,
                "mean_ssd_pano_vs_winner": None,
            }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "metric": "overlap-zone windowed NCC: rendered ERP vs winning cam's slab",
        "ncc_win_px": args.ncc_win,
        "erp_hw": [args.erp_h, args.erp_w],
        "n_common_anchors": len(common),
        "n_scored_anchors": len(per_anchor),
        "log_root": str(args.log_root),
        "per_anchor": per_anchor,
        "aggregate": aggregate,
    }
    with open(args.output_json, "w") as f:
        json.dump(summary, f, indent=2)

    print("\nAGGREGATE per method (higher NCC = better, lower SSD = better):")
    print(f"  {'method':<22s}  {'NCC pano vs win':>16s}  "
          f"{'NCC cam vs cam':>16s}  {'SSD pano vs win':>16s}  {'#anch':>6s}")
    for m in method_dirs.keys():
        a = aggregate[m]
        if a["mean_ncc_pano_vs_winner"] is None:
            print(f"  {m:<22s}  (no overlap data)")
            continue
        print(f"  {m:<22s}  {a['mean_ncc_pano_vs_winner']:>16.4f}  "
              f"{a['mean_ncc_winner_vs_loser_floor']:>16.4f}  "
              f"{a['mean_ssd_pano_vs_winner']:>16.2f}  "
              f"{a['n_anchors_scored']:>6d}")
    print(f"saved {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
