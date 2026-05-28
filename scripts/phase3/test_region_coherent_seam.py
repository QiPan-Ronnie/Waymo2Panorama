"""Evaluate region-coherent source selection against L1 hard_select.

This is a source-faithful no-DL experiment: no blending, no warp, no depth.
The new method uses DP seam routing, then prevents the seam from cutting
high-structure connected components by assigning each cut component to one
source camera.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))

from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.multiband import multiband_blend  # noqa: E402
from waymo2panorama.blending.region_coherent_seam import (  # noqa: E402
    blend_region_coherent_hard,
    blend_region_coherent_seam,
    seam_and_region_to_rgb,
)
from waymo2panorama.blending.seam_routing import blend_seam_routing, seam_mask_to_rgb  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402
from measure_overlap_ncc import score_one_anchor  # noqa: E402


DEFAULT_LOGS = {
    "02a00399": "02a00399-3857-444e-8db3-a8f58489c394",
    "fbee355f": "fbee355f-8878-31fa-8ac8-b9a45a3f130a",
    "0bae3b5e": "0bae3b5e-417d-3b03-abaa-806b433233b8",
}
DEFAULT_CASES = ["02a00399:0:bmw", "fbee355f:95:ped_obj", "0bae3b5e:30:clean_far"]


def _save_rgb(path: Path, rgb: np.ndarray, quality: int = 88) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        img.save(path, quality=quality)
    else:
        img.save(path)


def _resize_w(rgb: np.ndarray, width: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    height = max(1, round(h * width / w))
    return cv2.resize(np.clip(rgb, 0, 255).astype(np.uint8), (width, height), interpolation=cv2.INTER_AREA)


def _label_panel(rgb: np.ndarray, label: str, label_h: int = 40) -> np.ndarray:
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    band = np.zeros((label_h, rgb.shape[1], 3), dtype=np.uint8)
    cv2.putText(band, label, (12, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([band, rgb])


def _stack_named(methods: dict[str, np.ndarray], labels: dict[str, str], crop=None) -> np.ndarray:
    rows = []
    for name, rgb in methods.items():
        view = rgb
        if crop is not None:
            y0, y1, x0, x1 = crop
            view = view[y0:y1, x0:x1]
        rows.append(_label_panel(view, labels.get(name, name)))
    width = max(row.shape[1] for row in rows)
    padded = []
    for row in rows:
        if row.shape[1] < width:
            pad = np.zeros((row.shape[0], width - row.shape[1], 3), dtype=np.uint8)
            row = np.hstack([row, pad])
        padded.append(row)
    return np.vstack(padded)


def _default_crops(H: int, W: int) -> dict[str, tuple[int, int, int, int]]:
    return {
        "left_lane": (int(0.38 * H), int(0.66 * H), int(0.03 * W), int(0.30 * W)),
        "mid_lane": (int(0.36 * H), int(0.66 * H), int(0.20 * W), int(0.50 * W)),
        "right_suv": (int(0.20 * H), int(0.65 * H), int(0.66 * W), int(0.94 * W)),
        "center_front": (int(0.18 * H), int(0.68 * H), int(0.39 * W), int(0.70 * W)),
    }


def _jsonable_diag(diag: dict) -> dict:
    out = dict(diag)
    out.pop("label_map", None)
    out.pop("seam_mask", None)
    out.pop("protected_mask", None)
    return out


def _parse_case(case: str, av2_root: Path) -> tuple[Path, int, str]:
    parts = case.split(":")
    if len(parts) < 2:
        raise ValueError(f"case must be LOGSHORT:ANCHOR[:NAME], got {case!r}")
    log_key = parts[0]
    anchor = int(parts[1])
    name = parts[2] if len(parts) > 2 else f"a{anchor:03d}"
    if Path(log_key).exists():
        log_dir = Path(log_key)
        short = log_dir.name.split("-")[0]
    else:
        log_name = DEFAULT_LOGS.get(log_key, log_key)
        log_dir = av2_root / log_name
        short = log_key.split("-")[0]
    return log_dir, anchor, f"{short}_a{anchor:03d}_{name}"


def _project_frame(log_dir: Path, anchor_idx: int, erp_hw: tuple[int, int]) -> tuple[list[np.ndarray], list[np.ndarray]]:
    loader = AV2RingLoader(log_dir)
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[anchor_idx])
    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(
            image=frame.images[cam],
            K=calib.K,
            T_ego_cam=calib.T_ego_cam,
            erp_hw=erp_hw,
            convergence_distance_m=None,
        )
        slabs.append(rgb)
        weights.append(w)
    return slabs, weights


def _score_methods(methods: dict[str, np.ndarray], slabs, weights, args) -> dict[str, dict]:
    scores: dict[str, dict] = {}
    for name, rgb in methods.items():
        if name.endswith("_overlay"):
            continue
        one = score_one_anchor(
            rgb,
            slabs,
            weights,
            RING_PAIRS,
            win=args.ncc_win,
            max_sample_per_pair=args.max_sample_per_pair,
        )
        scores[name] = one.get("aggregate", {})
    return scores


def run_case(log_dir: Path, anchor_idx: int, run_name: str, args) -> dict:
    out_dir = Path(args.out_dir)
    erp_hw = (args.erp_h, args.erp_w)
    print(f"[case] {run_name} log={log_dir} anchor={anchor_idx}", flush=True)
    t0 = time.time()
    slabs, weights = _project_frame(log_dir, anchor_idx, erp_hw)
    project_s = time.time() - t0
    print(f"[project] {project_s:.1f}s", flush=True)

    labels = {
        "multiband": "L1 multiband",
        "hard_select": "L1 hard_select",
        "seam_routing": "DP seam routing v2",
        "region_coherent": "DP + region-coherent seam v3a",
        "component_coherent": "hard_select + component repair v3b",
        "seam_routing_overlay": "v2 seam path",
        "region_overlay": "v3a seam + protected regions",
        "component_overlay": "v3b original seam + protected regions",
    }
    methods: dict[str, np.ndarray] = {}
    runtime_s: dict[str, float] = {"project": round(project_s, 3)}

    def timed(name: str, fn):
        print(f"[run] {name}", flush=True)
        s0 = time.time()
        out = fn()
        runtime_s[name] = round(time.time() - s0, 3)
        print(f"[done] {name}: {runtime_s[name]:.2f}s", flush=True)
        return out

    methods["multiband"] = timed("multiband", lambda: multiband_blend(slabs, weights))
    methods["hard_select"] = timed("hard_select", lambda: hard_select(slabs, weights))
    seam_out, seam_diag = timed(
        "seam_routing",
        lambda: blend_seam_routing(
            slabs,
            weights,
            return_diagnostics=True,
            band_half_width=args.band_half_width,
            max_step=args.max_step,
        ),
    )
    methods["seam_routing"] = seam_out
    methods["seam_routing_overlay"] = seam_mask_to_rgb(seam_diag["seam_mask"], seam_out)

    rc_out, rc_diag = timed(
        "region_coherent",
        lambda: blend_region_coherent_seam(
            slabs,
            weights,
            return_diagnostics=True,
            band_half_width=args.band_half_width,
            max_step=args.max_step,
            seam_dilate=args.seam_dilate,
            min_component_area=args.min_component_area,
            max_component_area=args.max_component_area,
            source_weight=args.source_weight,
            change_weight=args.change_weight,
        ),
    )
    methods["region_coherent"] = rc_out
    methods["region_overlay"] = seam_and_region_to_rgb(rc_diag["seam_mask"], rc_diag["protected_mask"], rc_out)

    cc_out, cc_diag = timed(
        "component_coherent",
        lambda: blend_region_coherent_hard(
            slabs,
            weights,
            return_diagnostics=True,
            band_half_width=args.band_half_width,
            core_half_width=args.core_half_width,
            seam_dilate=args.seam_dilate,
            min_component_area=args.min_component_area,
            max_component_area=args.max_component_area,
            source_weight=args.source_weight,
            change_weight=args.change_weight,
        ),
    )
    methods["component_coherent"] = cc_out
    methods["component_overlay"] = seam_and_region_to_rgb(cc_diag["seam_mask"], cc_diag["protected_mask"], cc_out)

    scores = _score_methods(methods, slabs, weights, args)
    for name, agg in scores.items():
        if agg:
            print(
                f"[metric] {name}: NCC={agg.get('mean_ncc_pano_vs_winner', 0):.4f} "
                f"SSD={agg.get('mean_ssd_pano_vs_winner', 0):.2f}",
                flush=True,
            )

    review_methods = {k: _resize_w(v, args.review_w) for k, v in methods.items()}
    for name, rgb in review_methods.items():
        _save_rgb(out_dir / f"{run_name}_{name}_w{args.review_w}.jpg", rgb, quality=args.jpg_quality)
    _save_rgb(out_dir / f"{run_name}_review_stack_w{args.review_w}.jpg", _stack_named(review_methods, labels), args.jpg_quality)

    for crop_name, crop in _default_crops(args.erp_h, args.erp_w).items():
        _save_rgb(out_dir / f"{run_name}_{crop_name}_crop_stack.png", _stack_named(methods, labels, crop=crop))

    if args.save_full:
        for name in ["hard_select", "seam_routing", "region_coherent", "component_coherent"]:
            _save_rgb(out_dir / f"{run_name}_{name}.png", methods[name])

    diag = {
        "run_name": run_name,
        "log_dir": str(log_dir),
        "anchor_idx": anchor_idx,
        "erp_hw": list(erp_hw),
        "params": vars(args),
        "runtime_s": runtime_s,
        "overlap_ncc": scores,
        "seam_routing": _jsonable_diag(seam_diag),
        "region_coherent": _jsonable_diag(rc_diag),
        "component_coherent": _jsonable_diag(cc_diag),
    }
    with open(out_dir / f"{run_name}_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(diag, f, indent=2, ensure_ascii=False)
    return diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--av2-root",
        default="/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val",
    )
    ap.add_argument("--case", action="append", help="LOGSHORT:ANCHOR[:NAME]. Defaults to the three seam anchors.")
    ap.add_argument("--erp-h", type=int, default=2048)
    ap.add_argument("--erp-w", type=int, default=4096)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--band-half-width", type=int, default=72)
    ap.add_argument("--core-half-width", type=int, default=2)
    ap.add_argument("--max-step", type=int, default=3)
    ap.add_argument("--seam-dilate", type=int, default=9)
    ap.add_argument("--min-component-area", type=int, default=36)
    ap.add_argument("--max-component-area", type=int, default=18000)
    ap.add_argument("--source-weight", type=float, default=18.0)
    ap.add_argument("--change-weight", type=float, default=3.0)
    ap.add_argument("--review-w", type=int, default=1400)
    ap.add_argument("--jpg-quality", type=int, default=82)
    ap.add_argument("--save-full", action="store_true")
    ap.add_argument("--max-sample-per-pair", type=int, default=20000)
    ap.add_argument("--ncc-win", type=int, default=9)
    args = ap.parse_args()
    if args.ncc_win % 2 == 0:
        args.ncc_win += 1

    cases = args.case if args.case else DEFAULT_CASES
    av2_root = Path(args.av2_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_diags = []
    for case in cases:
        log_dir, anchor, run_name = _parse_case(case, av2_root)
        all_diags.append(run_case(log_dir, anchor, run_name, args))

    summary = {
        "cases": [
            {
                "run_name": d["run_name"],
                "anchor_idx": d["anchor_idx"],
                "runtime_s": d["runtime_s"],
                "overlap_ncc": d["overlap_ncc"],
                "region_protected_pixels": d["region_coherent"]["protected_pixels"],
                "region_changed_pixels": d["region_coherent"]["routed_pixels_changed"],
                "component_protected_pixels": d["component_coherent"]["protected_pixels"],
                "component_changed_pixels": d["component_coherent"]["routed_pixels_changed"],
            }
            for d in all_diags
        ]
    }
    with open(out_dir / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"[saved] {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
