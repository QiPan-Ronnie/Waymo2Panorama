"""Evaluate DiT360-as-oracle source selection.

This consumes existing DiT360 raw seam-completion images and uses them only as
an appearance target for choosing between real L1 ERP camera slabs.  The output
is source-faithful: every pixel is copied from an AV2 camera slab, never from
the DiT image.
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

from waymo2panorama.blending.dit_oracle_source import (  # noqa: E402
    DiTOracleConfig,
    blend_dit_oracle_source,
    hard_label_map,
    compose_from_labels,
    oracle_overlay,
)
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS  # noqa: E402
from waymo2panorama.blending.multiband import multiband_blend  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402
from measure_overlap_ncc import score_one_anchor  # noqa: E402


DRIVE = Path("/content/drive/MyDrive/koi_waymo2pano_colab")
DEFAULT_CASES = {
    "02a00399_a000_bmw": {
        "log": "02a00399-3857-444e-8db3-a8f58489c394",
        "anchor": 0,
        "summary": DRIVE / "results/dit360_seam_completion/runs_v10_adaptive_tau5/batch_summary.json",
        "run": "adapt_color_r008_tau5",
    },
    "fbee355f_a095_ped_obj": {
        "log": "fbee355f-8878-31fa-8ac8-b9a45a3f130a",
        "anchor": 95,
        "summary": DRIVE
        / "results/dit360_seam_completion/runs_v11_adaptive_tau5_generalize/fbee355f_a095/batch_summary.json",
        "run": "fbee355f_a095_adapt_color_r008_tau5",
    },
    "0bae3b5e_a030_clean_far": {
        "log": "0bae3b5e-417d-3b03-abaa-806b433233b8",
        "anchor": 30,
        "summary": DRIVE
        / "results/dit360_seam_completion/runs_v11_adaptive_tau5_generalize/0bae3b5e_a030/batch_summary.json",
        "run": "0bae3b5e_a030_adapt_color_r008_tau5",
    },
}


def _save_rgb(path: Path, rgb: np.ndarray, quality: int = 88) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        img.save(path, quality=quality)
    else:
        img.save(path)


def _load_rgb(path: Path, size: tuple[int, int] | None = None) -> np.ndarray:
    img = Image.open(path).convert("RGB")
    if size is not None and img.size != size:
        img = img.resize(size, Image.Resampling.BICUBIC)
    return np.array(img)


def _load_mask(path: Path, size: tuple[int, int]) -> np.ndarray:
    img = Image.open(path).convert("L")
    if img.size != size:
        img = img.resize(size, Image.Resampling.NEAREST)
    return np.array(img)


def _resize_w(rgb: np.ndarray, width: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    height = max(1, round(h * width / w))
    return cv2.resize(np.clip(rgb, 0, 255).astype(np.uint8), (width, height), interpolation=cv2.INTER_AREA)


def _label_panel(rgb: np.ndarray, label: str, label_h: int = 38) -> np.ndarray:
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    band = np.zeros((label_h, rgb.shape[1], 3), dtype=np.uint8)
    cv2.putText(band, label, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (255, 255, 255), 2, cv2.LINE_AA)
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
            row = np.hstack([row, np.zeros((row.shape[0], width - row.shape[1], 3), dtype=np.uint8)])
        padded.append(row)
    return np.vstack(padded)


def _default_crops(h: int, w: int) -> dict[str, tuple[int, int, int, int]]:
    return {
        "left_lane": (int(0.38 * h), int(0.66 * h), int(0.03 * w), int(0.30 * w)),
        "mid_lane": (int(0.36 * h), int(0.66 * h), int(0.20 * w), int(0.50 * w)),
        "right_suv": (int(0.20 * h), int(0.65 * h), int(0.66 * w), int(0.94 * w)),
        "center_front": (int(0.18 * h), int(0.68 * h), int(0.39 * w), int(0.70 * w)),
    }


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


def _find_run(summary_path: Path, run_name: str) -> dict:
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)
    for run in summary.get("runs", []):
        if run.get("name") == run_name:
            return run
    raise KeyError(f"run {run_name!r} not found in {summary_path}")


def _json_diag(diag: dict) -> dict:
    out = dict(diag)
    for key in ["label_map", "selected_mask", "raw_change_mask", "interest_mask", "gain"]:
        out.pop(key, None)
    return out


def _score_methods(methods: dict[str, np.ndarray], slabs, weights, args) -> dict:
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


def run_case(case_name: str, cfg: dict, args) -> dict:
    out_dir = Path(args.out_dir)
    erp_hw = (args.erp_h, args.erp_w)
    log_dir = Path(args.av2_root) / str(cfg["log"])
    anchor = int(cfg["anchor"])
    run = _find_run(Path(cfg["summary"]), str(cfg["run"]))
    print(f"[case] {case_name} log={log_dir} anchor={anchor}", flush=True)
    print(f"[dit] {run['name']} output={run['output']}", flush=True)

    t0 = time.time()
    slabs, weights = _project_frame(log_dir, anchor, erp_hw)
    project_s = round(time.time() - t0, 3)
    labels, _winner = hard_label_map(weights)
    hard = compose_from_labels(slabs, labels)
    multiband = multiband_blend(slabs, weights)

    size = (args.erp_w, args.erp_h)
    dit_raw = _load_rgb(Path(run["output"]), size=size)
    mask = _load_mask(Path(run["mask"]), size=size)

    variants = {
        "oracle_safe": DiTOracleConfig(
            mask_dilate_px=6,
            weight_penalty=30.0,
            stay_penalty=4.0,
            switch_margin=10.0,
            min_component_area=160,
            min_component_gain=12.0,
            max_changed_frac=0.035,
        ),
        "oracle_balanced": DiTOracleConfig(
            mask_dilate_px=10,
            weight_penalty=22.0,
            stay_penalty=3.0,
            switch_margin=7.0,
            min_component_area=96,
            min_component_gain=8.5,
            max_changed_frac=0.08,
        ),
        "oracle_loose": DiTOracleConfig(
            mask_dilate_px=14,
            weight_penalty=14.0,
            stay_penalty=2.0,
            switch_margin=4.5,
            min_component_area=48,
            min_component_gain=5.5,
            max_changed_frac=0.14,
        ),
    }

    methods: dict[str, np.ndarray] = {
        "multiband": multiband,
        "hard_select": hard,
        "dit_raw": dit_raw,
    }
    diagnostics: dict[str, dict] = {
        "run": run,
        "runtime_s": {"project": project_s},
    }
    for name, oracle_cfg in variants.items():
        started = time.time()
        out, diag = blend_dit_oracle_source(
            slabs,
            weights,
            dit_raw,
            preserve_mask=mask,
            config=oracle_cfg,
            return_diagnostics=True,
        )
        diagnostics["runtime_s"][name] = round(time.time() - started, 3)
        methods[name] = out
        methods[f"{name}_overlay"] = oracle_overlay(out, diag["selected_mask"], diag["interest_mask"])
        diagnostics[name] = _json_diag(diag)
        print(
            f"[oracle] {name}: selected={diag['selected_change_pixels']} "
            f"gain={diag['mean_gain_selected']:.2f} "
            f"target_core {diag['target_mae_hard_core']:.2f}->{diag['target_mae_oracle_core']:.2f}",
            flush=True,
        )

    scores = _score_methods(methods, slabs, weights, args)
    diagnostics["overlap_ncc"] = scores
    for name, agg in scores.items():
        if agg:
            print(
                f"[metric] {name}: NCC={agg.get('mean_ncc_pano_vs_winner', 0):.4f} "
                f"SSD={agg.get('mean_ssd_pano_vs_winner', 0):.2f}",
                flush=True,
            )

    labels_text = {
        "multiband": "L1 multiband",
        "hard_select": "L1 hard_select",
        "dit_raw": "DiT360 raw target (not source-faithful)",
        "oracle_safe": "DiT-oracle source select safe",
        "oracle_safe_overlay": "safe selected switches",
        "oracle_balanced": "DiT-oracle source select balanced",
        "oracle_balanced_overlay": "balanced selected switches",
        "oracle_loose": "DiT-oracle source select loose",
        "oracle_loose_overlay": "loose selected switches",
    }
    review_order = [
        "multiband",
        "hard_select",
        "dit_raw",
        "oracle_safe",
        "oracle_safe_overlay",
        "oracle_balanced",
        "oracle_balanced_overlay",
        "oracle_loose",
        "oracle_loose_overlay",
    ]
    review_methods = {k: _resize_w(methods[k], args.review_w) for k in review_order}
    for name, rgb in review_methods.items():
        _save_rgb(out_dir / f"{case_name}_{name}_w{args.review_w}.jpg", rgb, quality=args.jpg_quality)
    _save_rgb(out_dir / f"{case_name}_review_stack_w{args.review_w}.jpg", _stack_named(review_methods, labels_text), args.jpg_quality)

    crop_methods = {k: methods[k] for k in review_order}
    for crop_name, crop in _default_crops(args.erp_h, args.erp_w).items():
        crop_stack = _stack_named(crop_methods, labels_text, crop=crop)
        crop_stack = _resize_w(crop_stack, args.crop_w)
        _save_rgb(out_dir / f"{case_name}_{crop_name}_crop_stack_w{args.crop_w}.jpg", crop_stack, args.jpg_quality)

    if args.save_full:
        for name in ["hard_select", "dit_raw", "oracle_safe", "oracle_balanced", "oracle_loose"]:
            _save_rgb(out_dir / f"{case_name}_{name}.png", methods[name])

    diagnostics.update(
        {
            "case_name": case_name,
            "log_dir": str(log_dir),
            "anchor_idx": anchor,
            "erp_hw": [args.erp_h, args.erp_w],
            "mask_convention": "white/255 preserves source; black/0 generates",
        }
    )
    with open(out_dir / f"{case_name}_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, ensure_ascii=False)
    return diagnostics


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", default=str(DRIVE / "data/argoverse2/val"))
    ap.add_argument("--out-dir", default=str(DRIVE / "results/dit360_oracle_source/three_anchor_v1"))
    ap.add_argument("--case", action="append", choices=sorted(DEFAULT_CASES.keys()))
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--review-w", type=int, default=1400)
    ap.add_argument("--crop-w", type=int, default=1600)
    ap.add_argument("--jpg-quality", type=int, default=84)
    ap.add_argument("--ncc-win", type=int, default=15)
    ap.add_argument("--max-sample-per-pair", type=int, default=40000)
    ap.add_argument("--save-full", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = args.case or list(DEFAULT_CASES.keys())
    all_diag = []
    for case_name in cases:
        all_diag.append(run_case(case_name, DEFAULT_CASES[case_name], args))
    with open(out_dir / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump({"cases": all_diag}, f, indent=2, ensure_ascii=False)
    print(f"[done] wrote {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
