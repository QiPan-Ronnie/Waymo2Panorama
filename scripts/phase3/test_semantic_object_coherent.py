"""Semantic object-coherent hard_select probe.

This is an exploratory source-faithful seam experiment:

1. Render the usual L1 ERP slabs and hard_select panorama.
2. Run a COCO instance segmenter on the raw ring-camera images.
3. Project vehicle/person masks into ERP using the same calibration renderer.
4. If a projected object touches a hard_select seam, force the local object
   support to come from one camera instead of being split by the seam.

The output still copies pixels from original AV2 camera slabs. No diffusion,
no optical flow, no geometric warp, and no learned image generation is used.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(HERE))

from seam_confidence_map import _default_crops, _label_panel, _resize_w, _save_rgb  # noqa: E402
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select  # noqa: E402
from waymo2panorama.blending.multiband import multiband_blend  # noqa: E402
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band  # noqa: E402
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: E402
from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: E402


DEFAULT_LOGS = {
    "02a00399": "02a00399-3857-444e-8db3-a8f58489c394",
    "fbee355f": "fbee355f-8878-31fa-8ac8-b9a45a3f130a",
    "0bae3b5e": "0bae3b5e-417d-3b03-abaa-806b433233b8",
}
DEFAULT_CASES = ["02a00399:0:bmw", "fbee355f:95:ped_obj", "0bae3b5e:30:clean_far"]
COCO_KEEP = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}


@dataclass
class ObjectProposal:
    cam_idx: int
    cam_name: str
    cls_id: int
    cls_name: str
    conf: float
    erp_mask: np.ndarray
    near_seam_pixels: int
    mask_pixels: int
    mean_weight: float


def _stack_rows(rows: list[tuple[str, np.ndarray]]) -> np.ndarray:
    panels = [_label_panel(img, label) for label, img in rows]
    width = max(p.shape[1] for p in panels)
    padded = []
    for panel in panels:
        if panel.shape[1] < width:
            pad = np.zeros((panel.shape[0], width - panel.shape[1], 3), dtype=np.uint8)
            panel = np.hstack([panel, pad])
        padded.append(panel)
    return np.vstack(padded)


def _crop_stack(methods: dict[str, np.ndarray], crops: dict[str, tuple[int, int, int, int]]) -> dict[str, np.ndarray]:
    out = {}
    for crop_name, (y0, y1, x0, x1) in crops.items():
        rows = []
        for method_name, rgb in methods.items():
            rows.append((method_name, rgb[y0:y1, x0:x1]))
        out[crop_name] = _stack_rows(rows)
    return out


def _winner_label(weights: Sequence[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    stack = np.stack(weights, axis=0)
    label = stack.argmax(axis=0).astype(np.int16)
    valid = stack.max(axis=0) > 1e-6
    return label, valid


def _compose_from_label(slabs: Sequence[np.ndarray], label: np.ndarray, valid: np.ndarray) -> np.ndarray:
    out = np.zeros((*label.shape, 3), dtype=np.float32)
    for idx, slab in enumerate(slabs):
        m = (label == idx) & valid
        if m.any():
            out[m] = slab[m]
    return np.clip(out, 0, 255).astype(np.uint8)


def _seam_band(weights: Sequence[np.ndarray], band_half_width: int, core_half_width: int) -> tuple[np.ndarray, np.ndarray]:
    H, W = weights[0].shape
    band_all = np.zeros((H, W), dtype=bool)
    core_all = np.zeros((H, W), dtype=bool)
    for i, j in RING_PAIRS:
        wi = weights[i].astype(np.float32)
        wj = weights[j].astype(np.float32)
        overlap = (wi > 1e-6) & (wj > 1e-6)
        band, signed = build_voronoi_seam_band(wi, wj, band_half_width=band_half_width, threshold=1e-6)
        band &= overlap
        core = band & (np.abs(signed) <= core_half_width)
        band_all |= band
        core_all |= core
    return band_all, core_all


def _seam_gap_y(rgb: np.ndarray, label: np.ndarray, valid: np.ndarray) -> dict[str, float | int]:
    y = cv2.cvtColor(np.clip(rgb, 0, 255).astype(np.uint8), cv2.COLOR_RGB2YCrCb)[..., 0].astype(np.float32)
    seam = np.zeros(label.shape, dtype=bool)
    seam[:, :-1] = (label[:, :-1] != label[:, 1:]) & valid[:, :-1] & valid[:, 1:]
    rows, cols = np.where(seam[:, :-1])
    if rows.size == 0:
        return {"n": 0}
    gaps = np.abs(y[rows, cols] - y[rows, cols + 1])
    return {
        "n": int(gaps.size),
        "mean_delta_y": float(gaps.mean()),
        "median_delta_y": float(np.median(gaps)),
        "p90_delta_y": float(np.percentile(gaps, 90)),
        "p95_delta_y": float(np.percentile(gaps, 95)),
    }


def _project_mask_to_erp(mask: np.ndarray, K: np.ndarray, T_ego_cam: np.ndarray, erp_hw: tuple[int, int]) -> np.ndarray:
    mask_u8 = (mask.astype(np.uint8) * 255)
    rgb_mask = np.repeat(mask_u8[..., None], 3, axis=2)
    erp_rgb, _alpha, weight = render_camera_to_erp(rgb_mask, K, T_ego_cam, erp_hw=erp_hw)
    return (erp_rgb[..., 0] > 32.0) & (weight > 1e-6)


def _run_yolo_segmenter(frame, model_name: str, imgsz: int, conf_thresh: float, device: str):
    try:
        from ultralytics import YOLO
    except Exception as exc:  # pragma: no cover - exercised on Colab.
        raise RuntimeError("ultralytics is required: pip install ultralytics") from exc

    model = YOLO(model_name)
    images = [frame.images[cam] for cam in RING_CAMS_7]
    return model.predict(
        source=images,
        imgsz=imgsz,
        conf=conf_thresh,
        classes=sorted(COCO_KEEP),
        retina_masks=True,
        device=device,
        verbose=False,
    )


def _collect_object_proposals(
    results,
    frame,
    weights: Sequence[np.ndarray],
    seam_near: np.ndarray,
    erp_hw: tuple[int, int],
    min_mask_pixels: int,
    min_near_seam_pixels: int,
) -> tuple[list[ObjectProposal], list[dict[str, object]]]:
    proposals: list[ObjectProposal] = []
    raw_diags: list[dict[str, object]] = []
    for cam_idx, cam in enumerate(RING_CAMS_7):
        result = results[cam_idx]
        if result.masks is None or result.boxes is None:
            raw_diags.append({"cam": cam, "n_raw": 0, "n_kept": 0})
            continue
        masks = result.masks.data.detach().cpu().numpy()
        cls_ids = result.boxes.cls.detach().cpu().numpy().astype(int)
        confs = result.boxes.conf.detach().cpu().numpy().astype(float)
        h_img, w_img = frame.images[cam].shape[:2]
        n_kept = 0
        for k, (mask_small, cls_id, conf) in enumerate(zip(masks, cls_ids, confs)):
            if cls_id not in COCO_KEEP:
                continue
            if mask_small.shape[:2] != (h_img, w_img):
                mask = cv2.resize(mask_small.astype(np.float32), (w_img, h_img), interpolation=cv2.INTER_LINEAR) > 0.5
            else:
                mask = mask_small > 0.5
            if int(mask.sum()) < 256:
                continue
            calib = frame.calibrations[cam]
            erp_mask = _project_mask_to_erp(mask, calib.K, calib.T_ego_cam, erp_hw=erp_hw)
            mask_pixels = int(erp_mask.sum())
            near_pixels = int((erp_mask & seam_near).sum())
            if mask_pixels < min_mask_pixels or near_pixels < min_near_seam_pixels:
                continue
            mean_weight = float(weights[cam_idx][erp_mask].mean()) if mask_pixels else 0.0
            proposals.append(
                ObjectProposal(
                    cam_idx=cam_idx,
                    cam_name=cam,
                    cls_id=int(cls_id),
                    cls_name=COCO_KEEP[int(cls_id)],
                    conf=float(conf),
                    erp_mask=erp_mask,
                    near_seam_pixels=near_pixels,
                    mask_pixels=mask_pixels,
                    mean_weight=mean_weight,
                )
            )
            n_kept += 1
        raw_diags.append({"cam": cam, "n_raw": int(len(masks)), "n_kept": int(n_kept)})
    return proposals, raw_diags


def _apply_object_coherence(
    slabs: Sequence[np.ndarray],
    weights: Sequence[np.ndarray],
    base_label: np.ndarray,
    valid: np.ndarray,
    proposals: Sequence[ObjectProposal],
    seam_near: np.ndarray,
    min_owner_weight: float,
    protect_dilate: int,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, object]]]:
    out_label = base_label.copy()
    changed = np.zeros(base_label.shape, dtype=bool)
    diags: list[dict[str, object]] = []
    # Process high-confidence, high-weight proposals first. Later proposals only
    # overwrite if their source confidence is stronger at those pixels.
    score_map = np.zeros(base_label.shape, dtype=np.float32)
    ordered = sorted(
        proposals,
        key=lambda p: (p.mean_weight + 0.25 * p.conf, p.near_seam_pixels),
        reverse=True,
    )
    if protect_dilate > 1:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (protect_dilate, protect_dilate))
    else:
        kernel = None
    for p in ordered:
        mask = p.erp_mask
        if kernel is not None:
            mask = cv2.dilate(mask.astype(np.uint8), kernel, iterations=1).astype(bool)
            mask &= weights[p.cam_idx] > 1e-6
        # Keep the intervention local: protect the part of the object around
        # the seam, not the whole image projection.
        protect = mask & seam_near & valid & (weights[p.cam_idx] >= min_owner_weight)
        if not protect.any():
            diags.append(
                {
                    "cam": p.cam_name,
                    "class": p.cls_name,
                    "status": "no_valid_protect_pixels",
                    "mask_pixels": p.mask_pixels,
                    "near_seam_pixels": p.near_seam_pixels,
                }
            )
            continue
        score = float(p.mean_weight + 0.25 * p.conf)
        take = protect & (score > score_map)
        n_take = int(take.sum())
        if n_take == 0:
            diags.append(
                {
                    "cam": p.cam_name,
                    "class": p.cls_name,
                    "status": "lower_score_overlap",
                    "score": score,
                }
            )
            continue
        before_other = int(np.sum(take & (out_label != p.cam_idx)))
        out_label[take] = p.cam_idx
        score_map[take] = score
        changed |= take & (base_label != p.cam_idx)
        diags.append(
            {
                "cam": p.cam_name,
                "class": p.cls_name,
                "status": "applied",
                "score": score,
                "conf": p.conf,
                "mean_weight": p.mean_weight,
                "mask_pixels": p.mask_pixels,
                "near_seam_pixels": p.near_seam_pixels,
                "applied_pixels": n_take,
                "changed_from_other_cam": before_other,
            }
        )
    return out_label, changed, diags


def _overlay_objects(rgb: np.ndarray, seam_core: np.ndarray, proposals: Sequence[ObjectProposal], changed: np.ndarray) -> np.ndarray:
    out = np.clip(rgb, 0, 255).astype(np.uint8).copy()
    obj = np.zeros(seam_core.shape, dtype=bool)
    for p in proposals:
        obj |= p.erp_mask
    obj_near = obj & cv2.dilate(seam_core.astype(np.uint8), np.ones((45, 45), np.uint8), iterations=1).astype(bool)
    out[obj_near] = np.clip(0.65 * out[obj_near].astype(np.float32) + np.array([255, 180, 40]) * 0.35, 0, 255).astype(np.uint8)
    out[changed] = np.array([255, 70, 70], dtype=np.uint8)
    core = cv2.dilate(seam_core.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1).astype(bool)
    out[core] = np.array([255, 255, 255], dtype=np.uint8)
    return out


def _case_to_log(case: str, av2_root: Path) -> tuple[str, Path, int, str]:
    short, anchor_s, tag = case.split(":")
    if short not in DEFAULT_LOGS:
        raise KeyError(f"Unknown log short id {short}; known={sorted(DEFAULT_LOGS)}")
    return short, av2_root / DEFAULT_LOGS[short], int(anchor_s), tag


def run_case(args, case: str) -> dict[str, object]:
    short, log_dir, anchor_idx, tag = _case_to_log(case, Path(args.av2_root))
    run_name = f"{short}_a{anchor_idx:03d}_{tag}"
    out_dir = Path(args.out_dir)
    erp_hw = (args.erp_h, args.erp_w)

    print(f"[case] {run_name}", flush=True)
    loader = AV2RingLoader(log_dir)
    ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[anchor_idx])

    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    t0 = time.time()
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _alpha, w = render_camera_to_erp(
            frame.images[cam],
            calib.K,
            calib.T_ego_cam,
            erp_hw=erp_hw,
        )
        slabs.append(rgb)
        weights.append(w)
    project_s = time.time() - t0
    print(f"[project] {project_s:.1f}s", flush=True)

    base_label, valid = _winner_label(weights)
    hard = hard_select(slabs, weights)
    multiband = multiband_blend(slabs, weights, num_bands=5, wrap=True)
    seam_band, seam_core = _seam_band(weights, args.band_half_width, args.core_half_width)
    seam_near = cv2.dilate(
        seam_band.astype(np.uint8),
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (args.seam_dilate, args.seam_dilate)),
        iterations=1,
    ).astype(bool)

    t0 = time.time()
    yolo_results = _run_yolo_segmenter(frame, args.yolo_model, args.yolo_imgsz, args.yolo_conf, args.yolo_device)
    yolo_s = time.time() - t0
    proposals, raw_yolo_diags = _collect_object_proposals(
        yolo_results,
        frame,
        weights,
        seam_near=seam_near,
        erp_hw=erp_hw,
        min_mask_pixels=args.min_mask_pixels,
        min_near_seam_pixels=args.min_near_seam_pixels,
    )
    print(f"[objects] raw cams done in {yolo_s:.1f}s; near-seam proposals={len(proposals)}", flush=True)

    semantic_label, changed, object_diags = _apply_object_coherence(
        slabs,
        weights,
        base_label,
        valid,
        proposals,
        seam_near=seam_near,
        min_owner_weight=args.min_owner_weight,
        protect_dilate=args.protect_dilate,
    )
    semantic = _compose_from_label(slabs, semantic_label, valid)
    overlay = _overlay_objects(hard, seam_core, proposals, changed)

    methods = {
        "L1 multiband": _resize_w(multiband, args.review_w),
        "L1 hard_select": _resize_w(hard, args.review_w),
        "semantic object coherent": _resize_w(semantic, args.review_w),
        "object/seam diagnostic": _resize_w(overlay, args.review_w),
    }
    review = _stack_rows(list(methods.items()))
    _save_rgb(out_dir / f"{run_name}_review_stack_w{args.review_w}.jpg", review, quality=args.jpg_quality)
    _save_rgb(out_dir / f"{run_name}_hard_select_w{args.review_w}.jpg", methods["L1 hard_select"], quality=args.jpg_quality)
    _save_rgb(out_dir / f"{run_name}_semantic_w{args.review_w}.jpg", methods["semantic object coherent"], quality=args.jpg_quality)
    _save_rgb(out_dir / f"{run_name}_object_overlay_w{args.review_w}.jpg", methods["object/seam diagnostic"], quality=args.jpg_quality)

    crop_methods = {
        "hard_select": hard,
        "semantic_object": semantic,
        "changed_overlay": overlay,
    }
    for crop_name, crop_rgb in _crop_stack(crop_methods, _default_crops(args.erp_h, args.erp_w)).items():
        _save_rgb(out_dir / f"{run_name}_{crop_name}_crop_stack.jpg", crop_rgb, quality=args.jpg_quality)

    hard_gap = _seam_gap_y(hard, base_label, valid)
    semantic_gap = _seam_gap_y(semantic, semantic_label, valid)
    diag = {
        "run_name": run_name,
        "case": case,
        "log_dir": str(log_dir),
        "anchor_idx": anchor_idx,
        "erp_hw": [args.erp_h, args.erp_w],
        "project_s": round(project_s, 2),
        "yolo_s": round(yolo_s, 2),
        "yolo_model": args.yolo_model,
        "raw_yolo": raw_yolo_diags,
        "n_near_seam_proposals": len(proposals),
        "object_diagnostics": object_diags,
        "changed_fraction": float(changed.mean()),
        "changed_pixels": int(changed.sum()),
        "hard_seam_gap_y": hard_gap,
        "semantic_seam_gap_y": semantic_gap,
    }
    with open(out_dir / f"{run_name}_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(diag, f, indent=2)
    return diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--av2-root", default="/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--case", action="append", default=None)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--band-half-width", type=int, default=64)
    ap.add_argument("--core-half-width", type=int, default=2)
    ap.add_argument("--seam-dilate", type=int, default=81)
    ap.add_argument("--protect-dilate", type=int, default=9)
    ap.add_argument("--min-owner-weight", type=float, default=0.01)
    ap.add_argument("--min-mask-pixels", type=int, default=250)
    ap.add_argument("--min-near-seam-pixels", type=int, default=80)
    ap.add_argument("--yolo-model", default="yolov8x-seg.pt")
    ap.add_argument("--yolo-imgsz", type=int, default=1280)
    ap.add_argument("--yolo-conf", type=float, default=0.20)
    ap.add_argument("--yolo-device", default="0")
    ap.add_argument("--review-w", type=int, default=1200)
    ap.add_argument("--jpg-quality", type=int, default=86)
    args = ap.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    cases = args.case or DEFAULT_CASES
    all_diags = []
    for case in cases:
        all_diags.append(run_case(args, case))
    with open(Path(args.out_dir) / "batch_summary.json", "w", encoding="utf-8") as f:
        json.dump({"cases": cases, "diagnostics": all_diags}, f, indent=2)
    print(f"[saved] {args.out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
