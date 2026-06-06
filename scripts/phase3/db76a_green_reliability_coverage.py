"""DB-76a battery 1+2: Calibrated GREEN reliability + abstain mass (measurement-only).

Render-free CPU batteries for DB-76a (see agent/decision_briefs.md DB-76a and
agent/2026-06-06-leader-strategy-synthesis.md). This script writes NO repaired RGB
panorama. It builds the source-owned skeleton (hard_select owner map + per-pixel
visibility count) and measures:

  Battery 1 - GREEN reliability via leave-one-camera-out (LOO) render-back.
    The source-owned skeleton uses convergence_distance_m=None (rotation-only /
    infinite-radius sphere), so each camera's ERP slab already IS "what that camera
    sees along an ERP direction". Forbidding the held-out camera and reprojecting a
    GREEN (raw_copy) pixel back into it therefore reduces, at this geometry, to
    comparing the owner camera's slab vs the held-out (co-observing) camera's slab
    at the same ERP pixel. That cross-camera disagreement on co-observed GREEN IS
    the false-GREEN signal of a no-depth single-source copy. Channels: CIELab dE
    (per-pixel) + tile phase-correlation displacement in px (the measured parallax
    the no-depth copy fails to account for) + structure buckets. LOO validates
    co-observed GREEN only; truly-marginal pixels are abstained, not claimed GREEN.

  Battery 2 - abstain mass on the task-valid band, downstream-weighted.
    Three numbers (NOT on the full black ERP): abstain_full_erp, abstain on the
    ring task-valid band, and downstream-weighted abstain. Plus the raw-source
    coverage histogram (0 / 1 / 2 / 3+ cameras) and per-marked-ROI breakdown.

Pre-registered thresholds + kill criteria are fixed BEFORE the run (PRE_REGISTERED
_THRESHOLDS below, mirrored into the brief) and are not relaxed by the outcome.

One bounded secure /status + /exec on a CPU Colab runtime. Runtime URL/token are
read only by the local executor client from process env or a non-repo secret file;
the remote script receives no token. Strict secret scan over repo artifacts must be 0.

Fixed cases: BMW 02a00399:0 + clean control 0bae3b5e:30. No RGB repair, no DB75
tuning, no model inference, no VGGT/Pi3/DiT/FLUX/3DGS, no dataset scan beyond the
fixed cases, no RED promotion.
"""
from __future__ import annotations

import argparse
import base64
import json
import py_compile
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db64_ltr_v0_phase4b_z_visibility_cause import (
    ColabClient,
    rel,
    safe_status,
    sanitize,
    secret_hits,
)

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "db76a_green_reliability_coverage"
FETCH_DIR = OUT_DIR / "fetch"
REMOTE_OUT = "/content/drive/MyDrive/koi_waymo2pano_colab/results/db76a_green_reliability_coverage"
REMOTE_RESULT = REMOTE_OUT + "/DB76a_remote_result.json"

LOCAL_REMOTE_RESULT = OUT_DIR / "DB76a_remote_result.json"
LOCAL_SUMMARY = OUT_DIR / "DB76a_batch_summary.json"
MANIFEST = OUT_DIR / "DB76a_manifest.json"
BOARD = OUT_DIR / "DB76a_review_board.jpg"
THRESHOLDS_FILE = OUT_DIR / "DB76a_pre_registered_thresholds.json"

CASES = [
    ("02a00399:0:bmw", "02a00399_a000_bmw"),
    ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far"),
]

# Pre-registered BEFORE the run; failure does NOT relax these (DB-76a brief).
PRE_REGISTERED_THRESHOLDS = {
    "high_error_definition": {
        "geometry_reproj_px_normal": 3.0,
        "geometry_reproj_px_protected": 2.0,
        "color_dE_lab_cv2_units": 25.0,
        "color_dE_note": "cv2 LAB units (~2.55x CIE dE76); ~ dE76 10. Secondary channel.",
        "textureless_note": "textureless regions: photometric unreliable -> bucketed by visibility/coverage, NOT scored photometric",
        "high_error_rule": "co-observed GREEN pixel is high-error if tile_disp_px > px_threshold(structure) OR dE_lab > color_dE",
    },
    "green_accepted": {
        "overall_high_error_max": 0.03,
        "critical_structure_high_error_max": 0.01,
        "p95_residual_px_max": 3.0,
        "critical_structure_def": "lane | curb | wall_base proxy",
    },
    "stereo_temporal_build_worthy": {
        "abstain_band_converted_min": 0.25,
        "task_valid_validated_green_min": 0.08,
        "require_no_protected_failure": True,
    },
    "contract_as_main_deliverable": {
        "task_valid_abstain_gt": 0.10,
        "driving_critical_abstain_gt": 0.05,
        "continuous_seam_abstain_gt": 0.25,
        "green_high_error_gt_without_risk_calibration": 0.03,
    },
    "kill_criteria": {
        "green_false_rate_gt": 0.05,
        "stereo_band_coverage_lt": 0.10,
        "temporal_validated_coverage_lt": 0.05,
        "risk_not_loo_calibrated_means_heuristic": True,
    },
}

FETCH_ITEMS = {
    "summary": ("DB76a_batch_summary.json", 16),
    "remote_result": ("DB76a_remote_result.json", 16),
    "board": ("DB76a_review_board.jpg", 60),
    "bmw_board": ("02a00399_a000_bmw_battery_board.jpg", 60),
    "bmw_loo_overlay": ("02a00399_a000_bmw_loo_high_error_overlay.png", 30),
    "bmw_disp_heat": ("02a00399_a000_bmw_loo_disp_px_heat.png", 30),
    "bmw_green_abstain": ("02a00399_a000_bmw_green_abstain_overlay.png", 30),
    "bmw_owner": ("02a00399_a000_bmw_owner_viz.png", 16),
    "bmw_viscount": ("02a00399_a000_bmw_visibility_count.png", 16),
    "bmw_roi_report": ("02a00399_a000_bmw_marked_roi_report.json", 16),
    "clean_board": ("0bae3b5e_a030_clean_far_battery_board.jpg", 60),
    "clean_loo_overlay": ("0bae3b5e_a030_clean_far_loo_high_error_overlay.png", 30),
    "clean_green_abstain": ("0bae3b5e_a030_clean_far_green_abstain_overlay.png", 30),
    "clean_roi_report": ("0bae3b5e_a030_clean_far_marked_roi_report.json", 16),
    "claim": ("DB76a_claim.json", 16),
}


# --------------------------------------------------------------------------- #
# Remote (CPU Colab) source. Self-contained; receives no token.
# --------------------------------------------------------------------------- #
def remote_python() -> str:
    code = r'''
import json, math, pathlib, subprocess, sys, time, traceback
import numpy as np

REMOTE_OUT = pathlib.Path("__REMOTE_OUT__")
REMOTE_RESULT = pathlib.Path("__REMOTE_RESULT__")
DATA_ROOT = pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
WORKDIR_CANDIDATES = [
    pathlib.Path("/content/waymo2panorama"),
    pathlib.Path("/content/drive/MyDrive/koi_waymo2pano_colab/Waymo2Panorama"),
]
H, W = 1024, 2048
EPS = 1e-6
CASES = [("02a00399:0:bmw", "02a00399_a000_bmw"), ("0bae3b5e:30:clean_far", "0bae3b5e_a030_clean_far")]
MARKED_ROIS = {
    "left_road_patch": (250, 515, 460, 715),
    "lower_center_road_patch": (740, 595, 1035, 745),
    "center_lane_marking": (1030, 515, 1325, 735),
    "right_curb_sidewalk_wall_base": (1300, 500, 1575, 760),
}
THRESHOLDS = json.loads(r"""__THRESHOLDS_JSON__""")
PX_NORMAL = float(THRESHOLDS["high_error_definition"]["geometry_reproj_px_normal"])
PX_PROT = float(THRESHOLDS["high_error_definition"]["geometry_reproj_px_protected"])
COLOR_DE = float(THRESHOLDS["high_error_definition"]["color_dE_lab_cv2_units"])
TILE = 24
SEARCH = 18  # phase-correlation magnitude is clamped to this for robustness

OUT = {
    "phase": "db76a_battery1_battery2_measurement_only",
    "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "scope": {
        "cases": [c[0] for c in CASES],
        "rgb_repair": False, "db75_tuning": False, "blend_or_warp_final_pano": False,
        "model_inference": False, "vggt_pi3": False, "dit_flux_3dgs": False,
        "generation_inpaint": False, "source_replacement": False, "red_promotion": False,
        "measurement_only": True,
    },
    "secret_policy": "runtime secret used only by local executor client; remote receives no token",
}


def run(cmd, timeout=600):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
    return {"returncode": int(p.returncode), "tail": p.stdout[-1200:]}


def import_ok(name):
    try:
        __import__(name); return True
    except Exception:
        return False


def ensure_deps():
    rows = {}
    if not import_ok("av2"):
        rows["av2_install"] = run([sys.executable, "-m", "pip", "install", "-q", "av2>=0.3"], timeout=600)
    rows["av2_import_after"] = import_ok("av2")
    rows["cv2"] = import_ok("cv2")
    return rows


def find_workdir():
    for c in WORKDIR_CANDIDATES:
        if (c / "scripts" / "phase3" / "run_a1_streetview_pipeline.py").exists():
            return c
    return None


def json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return v if math.isfinite(v) else None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def rgb_to_y(rgb):
    x = rgb.astype(np.float32)
    return 0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2]


def normalize_u8(x, valid=None):
    arr = x.astype(np.float32)
    vals = arr[valid] if valid is not None and bool(np.any(valid)) else arr.reshape(-1)
    if vals.size == 0:
        return np.zeros_like(arr, dtype=np.uint8)
    lo, hi = np.percentile(vals, [2, 98])
    if hi <= lo:
        return np.zeros_like(arr, dtype=np.uint8)
    return np.clip((arr - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def save_u8(path, arr):
    import cv2
    cv2.imwrite(str(path), arr.astype("uint8"))


def save_rgb(path, arr, quality=92):
    import cv2
    a = np.clip(arr, 0, 255).astype("uint8")
    if path.suffix.lower() in (".jpg", ".jpeg"):
        cv2.imwrite(str(path), cv2.cvtColor(a, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    else:
        cv2.imwrite(str(path), cv2.cvtColor(a, cv2.COLOR_RGB2BGR))


def heat(x, valid=None):
    import cv2
    u = normalize_u8(x, valid)
    return cv2.cvtColor(cv2.applyColorMap(u, cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)


def overlay(rgb, mask, color, alpha=0.6):
    out = rgb.astype(np.float32).copy()
    m = mask.astype(bool)
    for c in range(3):
        out[..., c][m] = (1 - alpha) * out[..., c][m] + alpha * color[c]
    return np.clip(out, 0, 255).astype(np.uint8)


def structure_components(rgb, valid):
    import cv2
    y = rgb_to_y(rgb)
    gx = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3)
    edge = np.sqrt(gx * gx + gy * gy)
    edge_n = normalize_u8(edge, valid).astype(np.float32) / 255.0
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    sat = hsv[..., 1].astype(np.float32); val = hsv[..., 2].astype(np.float32); hue = hsv[..., 0].astype(np.float32)
    yy = np.arange(rgb.shape[0])[:, None]
    road_band = (yy > 390) & (yy < 780)
    lane = (((val > 168) & (sat < 98) & road_band) | ((hue > 15) & (hue < 45) & (sat > 58) & (val > 88) & road_band))
    lane = cv2.dilate(lane.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))) > 0
    thr = (np.percentile(np.abs(gy[valid]), 88) if bool(np.any(valid)) else 45)
    wall_base = (np.abs(gy) > thr) & (yy > 430) & (yy < 780)
    curb = (edge_n > 0.34) & road_band & (yy > 500)
    low_texture = (edge_n < 0.06) & valid & road_band
    saturated = (val > 250) | (val < 6)
    return {
        "edge": edge_n.astype(np.float32),
        "lane": lane.astype(bool),
        "wall_base": np.asarray(wall_base, bool),
        "curb": np.asarray(curb, bool),
        "low_texture": np.asarray(low_texture, bool),
        "saturated": np.asarray(saturated, bool),
    }


def pct(arr, ps):
    if arr.size == 0:
        return {f"p{p}": None for p in ps}
    q = np.percentile(arr, ps)
    return {f"p{p}": float(v) for p, v in zip(ps, q)}


def build_skeleton(case_spec, workdir):
    from depth_visibility_seam_probe import _parse_case
    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp
    short, log_dir, anchor_idx, tag = _parse_case(case_spec, DATA_ROOT)
    run_name = f"{short}_a{anchor_idx:03d}_{tag}"
    loader = AV2RingLoader(log_dir)
    ts = loader.anchor_timestamps_ns()[anchor_idx]
    frame = loader.load_synced_frame(ts)
    slabs, weights, labs, ys = [], [], [], []
    import cv2
    for cam in RING_CAMS_7:
        calib = frame.calibrations[cam]
        rgb, _a, w = render_camera_to_erp(image=frame.images[cam], K=calib.K, T_ego_cam=calib.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        rgb = rgb.astype(np.uint8)
        slabs.append(rgb)
        weights.append(w.astype(np.float32))
        labs.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32))
        ys.append(rgb_to_y(rgb))
    slabs = np.stack(slabs, 0)            # (N,H,W,3) u8
    weights = np.stack(weights, 0)        # (N,H,W) f32
    labs = np.stack(labs, 0)             # (N,H,W,3) f32
    ys = np.stack(ys, 0)                 # (N,H,W) f32
    valid_stack = weights > EPS
    viscount = valid_stack.sum(0).astype(np.int32)
    valid_any = viscount > 0
    order = np.argsort(-weights, axis=0)  # (N,H,W) desc by weight
    owner = order[0].astype(np.int32)
    owner_safe = np.where(valid_any, owner, 0)
    base = np.take_along_axis(slabs, owner_safe[None, :, :, None], axis=0)[0]
    base[~valid_any] = 0
    return run_name, slabs, weights, labs, ys, valid_stack, viscount, valid_any, order, owner, base


def battery1_loo(slabs, labs, ys, valid_stack, viscount, valid_any, order, owner, base, comps):
    """Leave-one-camera-out render-back over co-observed GREEN pixels."""
    import cv2
    N = slabs.shape[0]
    # GREEN raw_copy skeleton = every owned pixel. Co-observed subset = viscount>=2.
    green = valid_any.copy()
    co_observed = green & (viscount >= 2)

    # Per-pixel held-out camera = 2nd strongest weight where co-observed.
    second_idx = order[1].astype(np.int32)
    second_safe = np.where(co_observed, second_idx, 0)
    second_valid = np.take_along_axis(valid_stack, second_safe[None], axis=0)[0]
    co_observed = co_observed & second_valid

    lab_owner = np.take_along_axis(labs, np.where(valid_any, owner, 0)[None, :, :, None], axis=0)[0]
    lab_second = np.take_along_axis(labs, second_safe[None, :, :, None], axis=0)[0]
    dE = np.sqrt(((lab_owner - lab_second) ** 2).sum(-1)).astype(np.float32)
    dE[~co_observed] = 0.0

    # Tile phase-correlation displacement (the measured parallax of the no-depth copy).
    # v1.1: gate by phase-corr response + reject out-of-range magnitudes (do NOT clamp
    # spurious huge shifts to SEARCH, which inflated the tail and locked onto repetitive
    # lane texture). Tiles failing the gate are 'disp_unreliable' (rank-deficient) and are
    # excluded from the geometry false-GREEN denominator, not scored as high-error.
    disp = np.zeros((H, W), np.float32)
    disp_valid = np.zeros((H, W), bool)
    disp_unreliable = np.zeros((H, W), bool)
    y_owner = np.take_along_axis(ys, np.where(valid_any, owner, 0)[None], axis=0)[0]
    y_second = np.take_along_axis(ys, second_safe[None], axis=0)[0]
    han = cv2.createHanningWindow((TILE, TILE), cv2.CV_32F)
    ny, nx = H // TILE, W // TILE
    for ty in range(ny):
        y0 = ty * TILE
        for tx in range(nx):
            x0 = tx * TILE
            sl = (slice(y0, y0 + TILE), slice(x0, x0 + TILE))
            cov = co_observed[sl]
            if cov.mean() < 0.5:
                continue
            a = y_owner[sl].astype(np.float32); b = y_second[sl].astype(np.float32)
            if a.std() < 6.0 and b.std() < 6.0:
                continue  # textureless tile: displacement unreliable -> skip (rank-deficiency)
            try:
                (sx, sy), resp = cv2.phaseCorrelate(a, b, han)
            except Exception:
                continue
            mag = math.hypot(sx, sy)
            if (resp < 0.18) or (mag > SEARCH):
                disp_unreliable[sl] = cov  # low-confidence / out-of-range -> not scored
                continue
            disp[sl] = float(mag)
            disp_valid[sl] = True

    protected = comps["lane"] | comps["curb"] | comps["wall_base"]
    px_thr = np.where(protected, PX_PROT, PX_NORMAL).astype(np.float32)
    measurable = co_observed & disp_valid  # co-observed AND displacement reliably measured
    geom_high = measurable & (disp > px_thr)               # HEADLINE: geometric falseness
    color_high = co_observed & (dE > COLOR_DE)             # SEPARATE: exposure/tone seam (tone_only-fixable)
    high_error = geom_high | color_high                   # reference only (over-counts)

    co_n = int(co_observed.sum())
    meas_n = int(measurable.sum())
    def frac(mask, sub):
        d = int(sub.sum())
        return (float(int((mask & sub).sum()) / d) if d else None)

    structures = {
        "lane": comps["lane"], "curb": comps["curb"], "wall_base": comps["wall_base"],
        "low_texture": comps["low_texture"], "saturated": comps["saturated"],
    }
    by_structure_geom = {k: {"co_observed_px": int((v & co_observed).sum()),
                             "measurable_px": int((v & measurable).sum()),
                             "geom_false_rate": frac(geom_high, v & measurable),
                             "tone_seam_rate": frac(color_high, v & co_observed)} for k, v in structures.items()}
    critical_meas = (comps["lane"] | comps["curb"] | comps["wall_base"]) & measurable
    disp_co = disp[measurable]
    dE_co = dE[co_observed]
    green_n = int(green.sum())

    metrics = {
        "version": "v1.1",
        "green_pixels": green_n,
        "co_observed_green_pixels": co_n,
        "co_observed_fraction_of_green": (float(co_n / green_n) if green_n else None),
        "measurable_fraction_of_co_observed": (float(meas_n / co_n) if co_n else None),
        "disp_unreliable_fraction_of_co_observed": (float(int((disp_unreliable & co_observed).sum()) / co_n) if co_n else None),
        # HEADLINE keystone = geometry-only over the measurable co-observed subset
        "green_false_rate_geom": frac(geom_high, measurable),
        "critical_structure_geom_false_rate": frac(geom_high, critical_meas),
        # SEPARATE tone channel (exposure/color seam; tone_only-fixable, NOT geometric falseness)
        "photometric_tone_seam_rate": frac(color_high, co_observed),
        # reference only (geom OR tone): over-counts, do not headline
        "green_false_rate_combined_reference": frac(high_error, co_observed),
        "disp_px_percentiles": pct(disp_co, [50, 90, 95, 99]),
        "dE_lab_percentiles": pct(dE_co, [50, 90, 95, 99]),
        "by_structure_geom": by_structure_geom,
        "measurable_tiles_fraction_full_erp": float(disp_valid.sum() / (H * W)),
    }
    maps = {"geom_high": geom_high, "color_high": color_high, "high_error": high_error,
            "disp": disp, "disp_valid": disp_valid, "disp_unreliable": disp_unreliable, "dE": dE,
            "green": green, "co_observed": co_observed, "measurable": measurable, "critical": critical_meas}
    return metrics, maps


def task_weight_map(comps, valid_any):
    """Downstream-importance weight: near-ground prior + lane/curb/wall_base boost."""
    yy = np.arange(H)[:, None].astype(np.float32) * np.ones((1, W), np.float32)
    # near-ground prior: rises toward the lower-center ring band (rows ~430..820).
    ground = np.clip((yy - 410.0) / 360.0, 0.0, 1.0)
    ground[yy > 830] = np.clip((1.0 - (yy[yy > 830] - 830.0) / 180.0), 0.0, 1.0)
    w = 0.35 + 0.65 * ground
    w = w + 0.8 * comps["lane"].astype(np.float32) + 0.7 * comps["curb"].astype(np.float32) + 0.6 * comps["wall_base"].astype(np.float32)
    return np.clip(w, 0.0, 2.0).astype(np.float32)


def battery2_abstain(viscount, valid_any, comps):
    abstain = ~valid_any  # no raw-camera source sees this ERP direction
    col_cov = valid_any.mean(axis=1)  # per-row coverage fraction
    band_rows = np.where(col_cov >= 0.5)[0]
    if band_rows.size:
        y_top, y_bot = int(band_rows.min()), int(band_rows.max())
    else:
        y_top, y_bot = 0, H - 1
    band = np.zeros((H, W), bool); band[y_top:y_bot + 1, :] = True
    wmap = task_weight_map(comps, valid_any)

    def m(mask, sub):
        d = float(sub.sum())
        return (float((mask & sub).sum() / d) if d else None)

    abstain_band = m(abstain, band)
    wsub = wmap * band
    abstain_weighted = float((abstain.astype(np.float32) * wsub).sum() / max(wsub.sum(), 1e-6))
    cov_hist = {
        "count0_abstain": float((viscount == 0).mean()),
        "count1_single_source": float((viscount == 1).mean()),
        "count2": float((viscount == 2).mean()),
        "count3plus_co_observed": float((viscount >= 3).mean()),
    }
    band_hist = {
        "count0_abstain": m(viscount == 0, band),
        "count1_single_source": m(viscount == 1, band),
        "count2plus_co_observed": m(viscount >= 2, band),
    }
    metrics = {
        "task_valid_band_rows": [y_top, y_bot],
        "abstain_full_erp": float(abstain.mean()),
        "abstain_task_valid_band": abstain_band,
        "abstain_weighted_task_band": abstain_weighted,
        "coverage_histogram_full_erp": cov_hist,
        "coverage_histogram_task_band": band_hist,
    }
    return metrics, {"abstain": abstain, "band": band, "wmap": wmap}


def roi_report(b1_maps, b2_maps, viscount):
    rows = {}
    co = b1_maps["co_observed"]; ge = b1_maps["geom_high"]; ce = b1_maps["color_high"]
    meas = b1_maps["measurable"]; disp = b1_maps["disp"]; ab = b2_maps["abstain"]
    for name, (x0, y0, x1, y1) in MARKED_ROIS.items():
        sl = (slice(y0, y1), slice(x0, x1))
        co_r = co[sl]; meas_r = meas[sl]; vc = viscount[sl]
        co_n = int(co_r.sum()); meas_n = int(meas_r.sum())
        d_r = disp[sl][meas_r]
        rows[name] = {
            "box": [x0, y0, x1, y1],
            "co_observed_green_px": co_n,
            "measurable_px": meas_n,
            "geom_false_rate": (float(int((ge[sl] & meas_r).sum()) / meas_n) if meas_n else None),
            "tone_seam_rate": (float(int((ce[sl] & co_r).sum()) / co_n) if co_n else None),
            "abstain_fraction": float(ab[sl].mean()),
            "single_source_fraction": float((vc == 1).mean()),
            "co_observed_fraction": float((vc >= 2).mean()),
            "disp_px_p95": (float(np.percentile(d_r, 95)) if d_r.size else None),
        }
    return rows


def evaluate_go_no_go(b1, b2):
    g = THRESHOLDS["green_accepted"]; k = THRESHOLDS["kill_criteria"]; c = THRESHOLDS["contract_as_main_deliverable"]
    fr = b1["green_false_rate_geom"]  # geometry-only headline keystone
    crit = b1["critical_structure_geom_false_rate"]
    p95 = (b1["disp_px_percentiles"] or {}).get("p95")
    green_accepted = bool(
        fr is not None and fr <= g["overall_high_error_max"]
        and (crit is None or crit <= g["critical_structure_high_error_max"])
        and (p95 is None or p95 <= g["p95_residual_px_max"])
    )
    kill_green = bool(fr is not None and fr > k["green_false_rate_gt"])
    contract_main = bool(
        (b2["abstain_task_valid_band"] or 0) > c["task_valid_abstain_gt"]
        or (fr is not None and fr > c["green_high_error_gt_without_risk_calibration"])
    )
    return {
        "green_accepted": green_accepted,
        "kill_green_false_rate_hit": kill_green,
        "contract_as_main_indicated": contract_main,
        "risk_label": "heuristic_uncalibrated",  # v1 risk is not LOO/held-out conformal-calibrated
        "green_false_rate_geom": fr,
        "critical_structure_geom_false_rate": crit,
        "photometric_tone_seam_rate": b1["photometric_tone_seam_rate"],
        "disp_px_p95": p95,
        "proceed_to_batteries_3_4": bool((not green_accepted) or contract_main or (b2["abstain_weighted_task_band"] or 0) > 0.05),
    }


def build_case_board(run_name, base, b1m, b2m, b1maps, b2maps, viscount, owner, valid_any, gng):
    import cv2
    from PIL import Image, ImageDraw, ImageFont
    save_rgb(REMOTE_OUT / f"{run_name}_hard_select_base.png", base)
    save_u8(REMOTE_OUT / f"{run_name}_owner_viz.png", (owner.astype(np.float32) / 6.0 * 255).astype(np.uint8))
    save_u8(REMOTE_OUT / f"{run_name}_visibility_count.png", np.clip(viscount * 60, 0, 255).astype(np.uint8))
    green_abstain = overlay(overlay(base, b1maps["co_observed"], (60, 220, 90), 0.40), b2maps["abstain"], (35, 35, 35), 0.75)
    save_rgb(REMOTE_OUT / f"{run_name}_green_abstain_overlay.png", green_abstain)
    loo = overlay(overlay(base, b1maps["color_high"], (255, 200, 40), 0.55), b1maps["geom_high"], (255, 40, 40), 0.82)
    save_rgb(REMOTE_OUT / f"{run_name}_loo_high_error_overlay.png", loo)
    disp_heat = heat(b1maps["disp"], b1maps["disp_valid"])
    save_rgb(REMOTE_OUT / f"{run_name}_loo_disp_px_heat.png", disp_heat)

    # composite battery board
    def tile_img(title, arr):
        a = np.clip(arr, 0, 255).astype(np.uint8)
        im = Image.fromarray(a)
        sc = 760 / im.width
        im = im.resize((760, int(im.height * sc)))
        bar = Image.new("RGB", (im.width, 26), (15, 15, 22)); d = ImageDraw.Draw(bar)
        try:
            f = ImageFont.truetype("DejaVuSans.ttf", 16)
        except Exception:
            f = ImageFont.load_default()
        d.text((6, 4), title, (235, 235, 245), font=f)
        out = Image.new("RGB", (im.width, im.height + 26)); out.paste(bar, (0, 0)); out.paste(im, (0, 26))
        return out

    tiles = [
        tile_img(f"{run_name} hard_select base", base),
        tile_img("GREEN(co-obs) green / abstain dark", green_abstain),
        tile_img(f"LOO geom-false red / tone-seam amber  geom={b1m['green_false_rate_geom']}", loo),
        tile_img(f"LOO disp px (parallax)  p95={(b1m['disp_px_percentiles'] or {}).get('p95')}", disp_heat),
    ]
    wmax = max(t.width for t in tiles); hsum = sum(t.height for t in tiles) + 90
    board = Image.new("RGB", (wmax, hsum), (10, 10, 14)); d = ImageDraw.Draw(board)
    try:
        f = ImageFont.truetype("DejaVuSans.ttf", 15)
    except Exception:
        f = ImageFont.load_default()
    lines = [
        f"{run_name}  MEASUREMENT-ONLY (no RGB repair)  v1.1",
        f"geom_false={b1m['green_false_rate_geom']} crit_geom={b1m['critical_structure_geom_false_rate']} tone_seam={b1m['photometric_tone_seam_rate']} p95_disp={(b1m['disp_px_percentiles'] or {}).get('p95')}",
        f"abstain band={b2m['abstain_task_valid_band']} weighted={b2m['abstain_weighted_task_band']} | green_accepted={gng['green_accepted']} kill={gng['kill_green_false_rate_hit']}",
    ]
    for i, ln in enumerate(lines):
        d.text((8, 6 + i * 26), ln, (240, 240, 250), font=f)
    yo = 90
    for t in tiles:
        board.paste(t, (0, yo)); yo += t.height
    board.save(REMOTE_OUT / f"{run_name}_battery_board.jpg", quality=90)


def run_one_case(case_spec, run_name, workdir):
    _name, slabs, weights, labs, ys, valid_stack, viscount, valid_any, order, owner, base = build_skeleton(case_spec, workdir)
    comps = structure_components(base, valid_any)
    b1m, b1maps = battery1_loo(slabs, labs, ys, valid_stack, viscount, valid_any, order, owner, base, comps)
    b2m, b2maps = battery2_abstain(viscount, valid_any, comps)
    gng = evaluate_go_no_go(b1m, b2m)
    rois = roi_report(b1maps, b2maps, viscount)
    (REMOTE_OUT / f"{run_name}_marked_roi_report.json").write_text(json.dumps(json_safe(rois), indent=2), encoding="utf-8")
    build_case_board(run_name, base, b1m, b2m, b1maps, b2maps, viscount, owner, valid_any, gng)
    return {
        "case": run_name,
        "battery1_green_reliability": b1m,
        "battery2_abstain_mass": b2m,
        "go_no_go": gng,
        "marked_rois": rois,
    }


def build_batch_board(summary):
    from PIL import Image, ImageDraw, ImageFont
    paths = [REMOTE_OUT / "02a00399_a000_bmw_battery_board.jpg", REMOTE_OUT / "0bae3b5e_a030_clean_far_battery_board.jpg"]
    imgs = [Image.open(p) for p in paths if p.exists()]
    if not imgs:
        return
    w = max(i.width for i in imgs); h = sum(i.height for i in imgs) + 70
    board = Image.new("RGB", (w, h), (8, 8, 12)); d = ImageDraw.Draw(board)
    try:
        f = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception:
        f = ImageFont.load_default()
    d.text((8, 8), "DB-76a battery 1 (LOO false-GREEN) + battery 2 (abstain mass) - measurement-only", (240, 240, 250), font=f)
    d.text((8, 36), f"status={summary.get('status')}  proceed_to_3_4={summary.get('proceed_to_batteries_3_4')}", (210, 230, 210), font=f)
    yo = 70
    for im in imgs:
        board.paste(im, (0, yo)); yo += im.height
    board.save(REMOTE_OUT / "DB76a_review_board.jpg", quality=90)


try:
    t0 = time.time()
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    OUT["dependency"] = ensure_deps()
    workdir = find_workdir()
    OUT["workdir_found"] = bool(workdir)
    if workdir is None:
        raise RuntimeError("Waymo2Panorama workdir not found on remote")
    sys.path.insert(0, str(workdir / "scripts" / "phase3"))
    sys.path.insert(0, str(workdir / "code"))
    reports = [run_one_case(cs, rn, workdir) for cs, rn in CASES]
    by_case = {r["case"]: r for r in reports}
    proceed = any(r["go_no_go"]["proceed_to_batteries_3_4"] for r in reports)
    any_kill = any(r["go_no_go"]["kill_green_false_rate_hit"] for r in reports)
    summary = {
        "status": "db76a_battery1_battery2_complete",
        "measurement_only": True,
        "source_faithful_repair_claim_allowed": False,
        "pre_registered_thresholds": THRESHOLDS,
        "by_case": by_case,
        "proceed_to_batteries_3_4": bool(proceed),
        "kill_green_false_rate_hit_any": bool(any_kill),
        "risk_label": "heuristic_uncalibrated",
        "scope": OUT["scope"],
        "runtime_s": round(time.time() - t0, 2),
    }
    build_batch_board(summary)
    (REMOTE_OUT / "DB76a_batch_summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    claim = {
        "brief": "DB-76a",
        "classification": "measurement_only_green_reliability_and_abstain_mass",
        "rgb_repair": False, "db75_tuning": False, "source_faithful_repair": False,
        "bosch_training_ready": False, "red_promotion": False,
        "loo_validates_co_observed_green_only": True,
        "risk_is": "heuristic_uncalibrated_not_conformal",
    }
    (REMOTE_OUT / "DB76a_claim.json").write_text(json.dumps(claim, indent=2), encoding="utf-8")
    OUT["status"] = "db76a_completed"
    OUT["summary"] = summary
except Exception as exc:
    OUT["status"] = "db76a_failed_or_blocked"
    OUT["error"] = {"type": type(exc).__name__, "message": str(exc), "trace_tail": traceback.format_exc()[-3000:]}
finally:
    OUT["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    REMOTE_OUT.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULT.write_text(json.dumps(json_safe(OUT), indent=2), encoding="utf-8")
    print("DB76A_JSON_BEGIN")
    print(json.dumps(json_safe(OUT), sort_keys=True, separators=(",", ":")))
    print("DB76A_JSON_END")
'''
    code = code.replace("__REMOTE_OUT__", REMOTE_OUT).replace("__REMOTE_RESULT__", REMOTE_RESULT)
    code = code.replace("__THRESHOLDS_JSON__", json.dumps(PRE_REGISTERED_THRESHOLDS))
    return code


def remote_bash() -> str:
    code_b64 = base64.b64encode(remote_python().encode("utf-8")).decode("ascii")
    return (
        "set +x\n"
        "python - <<'PY'\n"
        "import base64\n"
        f"code = base64.b64decode('{code_b64}').decode('utf-8')\n"
        "exec(compile(code, '<db76a_green_reliability_coverage_remote>', 'exec'))\n"
        "PY"
    )


def parse_json_from_log(log_tail: str) -> dict[str, Any] | None:
    if "DB76A_JSON_BEGIN" not in log_tail or "DB76A_JSON_END" not in log_tail:
        return None
    body = log_tail.split("DB76A_JSON_BEGIN", 1)[1].split("DB76A_JSON_END", 1)[0].strip()
    try:
        return json.loads(body)
    except Exception:
        return None


def poll_job(client: ColabClient, job_id: str, timeout_s: int) -> dict[str, Any]:
    t0 = time.time()
    last: dict[str, Any] = {}
    while time.time() - t0 < timeout_s + 120:
        time.sleep(7)
        last = client.get(f"/jobs/{urllib.parse.quote(job_id)}", timeout=180)
        if last.get("state") != "running":
            return sanitize(last)
    return sanitize(last or {"state": "poll_timeout", "job_id": job_id})


def fetch_outputs(client: ColabClient) -> dict[str, Any]:
    fetched: dict[str, Any] = {}
    for key, (remote_name, max_mb) in FETCH_ITEMS.items():
        local = (OUT_DIR if key in {"summary", "remote_result", "board"} else FETCH_DIR) / remote_name
        local.parent.mkdir(parents=True, exist_ok=True)
        raw = client.read_file(REMOTE_OUT + "/" + remote_name, max_size_mb=max_mb)
        if raw is None:
            fetched[key] = {"path": rel(local), "exists": False}
        else:
            local.write_bytes(raw)
            fetched[key] = {"path": rel(local), "exists": True, "bytes": len(raw)}
    return fetched


def write_thresholds_file() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    THRESHOLDS_FILE.write_text(json.dumps(PRE_REGISTERED_THRESHOLDS, indent=2), encoding="utf-8")


def run_remote(timeout_s: int) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_thresholds_file()
    client = ColabClient()
    status = client.get("/status", timeout=180)
    submit = client.post(
        "/exec",
        {"cmd": ["bash", "-lc", remote_bash()], "cwd": "/content", "timeout_s": timeout_s},
        timeout=180,
    )
    job = poll_job(client, submit["job_id"], timeout_s=timeout_s)
    remote_result: dict[str, Any] | None = None
    raw = client.read_file(REMOTE_RESULT, max_size_mb=24)
    if raw is not None:
        try:
            remote_result = json.loads(raw.decode("utf-8"))
        except Exception:
            remote_result = None
    if remote_result is None:
        remote_result = parse_json_from_log(job.get("log_tail", ""))
    if remote_result is None:
        remote_result = {"status": "remote_result_missing", "log_tail_sanitized": sanitize(job.get("log_tail", ""))}
    remote_result = sanitize(remote_result)
    LOCAL_REMOTE_RESULT.write_text(json.dumps(remote_result, indent=2, ensure_ascii=False), encoding="utf-8")
    fetched = fetch_outputs(client)
    summary: dict[str, Any] = {}
    if LOCAL_SUMMARY.exists():
        try:
            summary = json.loads(LOCAL_SUMMARY.read_text(encoding="utf-8"))
        except Exception:
            summary = {}
    if not summary:
        summary = remote_result.get("summary") or {}

    run_ok = bool(remote_result.get("status") == "db76a_completed")
    manifest: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "db76a_green_reliability_coverage",
        "brief": "DB-76a (battery 1+2 measurement-only)",
        "scope": {
            "remote_status_used": True,
            "remote_exec_used": True,
            "exec_count": 1,
            "runtime_type": status.get("runtime_type"),
            "fixed_cases_only": [c[0] for c in CASES],
            "measurement_only": True,
            "rgb_repair": False,
            "db75_tuning": False,
            "blend_or_warp_final_pano": False,
            "model_inference": False,
            "vggt_pi3_dit_flux_3dgs": False,
            "a100_used_or_needed": False,
            "red_promotion": False,
        },
        "runtime": {
            "secret_source_kind": "process_env" if client.source == "process_env" else "non_repo_file",
            "status": safe_status(status),
        },
        "job": sanitize({k: v for k, v in job.items() if k not in {"log_tail", "cmd"}}),
        "remote_status": remote_result.get("status"),
        "summary": summary,
        "fetched_outputs": fetched,
        "pre_registered_thresholds": rel(THRESHOLDS_FILE),
        "output_location": rel(OUT_DIR),
        "drive_output_location": "results/db76a_green_reliability_coverage/",
        "decision": {
            "run_ok": run_ok,
            "proceed_to_batteries_3_4": bool(summary.get("proceed_to_batteries_3_4")),
            "kill_green_false_rate_hit_any": bool(summary.get("kill_green_false_rate_hit_any")),
            "vision_check_required": True,
            "accepted_as_source_faithful_repair": False,
        },
        "claim_boundary": [
            "DB-76a battery 1+2 is measurement-only: source-owned skeleton + GREEN/abstain gate + LOO render-back false-GREEN rate + abstain mass.",
            "No RGB repair, no DB75 tuning, no blend/warp final pano, no model inference, no RED promotion.",
            "LOO validates co-observed GREEN only; truly-marginal pixels are abstained, not claimed GREEN.",
            "risk is heuristic/uncalibrated in v1 (not conformal); thresholds were pre-registered before the run.",
            "Vision review decides go/no-go for batteries 3-4 (forward-stereo / ring-temporal).",
        ],
    }
    scan_text = json.dumps(manifest, ensure_ascii=False) + "\n" + json.dumps(remote_result, ensure_ascii=False)
    hits = secret_hits(scan_text)
    manifest["strict_secret_scan"] = {"hit_count": sum(h["count"] for h in hits), "hits": hits}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "status": manifest["status"],
        "run_ok": run_ok,
        "remote_status": remote_result.get("status"),
        "proceed_to_batteries_3_4": bool(summary.get("proceed_to_batteries_3_4")),
        "secret_hits": manifest["strict_secret_scan"]["hit_count"],
        "manifest": rel(MANIFEST),
        "board": rel(BOARD),
    }


def check_compile() -> None:
    # 1) wrapper compiles (this very file)
    py_compile.compile(str(Path(__file__).resolve()), doraise=True)
    # 2) embedded remote source compiles
    compile(remote_python(), "<db76a_remote>", "exec")
    print(json.dumps({"status": "compile_ok", "wrapper": True, "remote_source": True}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-remote", action="store_true")
    parser.add_argument("--check", action="store_true", help="compile-check wrapper + embedded remote source, no remote")
    parser.add_argument("--write-thresholds", action="store_true", help="write pre-registered thresholds JSON locally, no remote")
    parser.add_argument("--timeout-s", type=int, default=2400)
    args = parser.parse_args()
    if args.check:
        check_compile(); return
    if args.write_thresholds:
        write_thresholds_file(); print(json.dumps({"status": "thresholds_written", "path": rel(THRESHOLDS_FILE)}, indent=2)); return
    if not args.run_remote:
        print(json.dumps({"status": "ready", "message": "Use --check, --write-thresholds, or --run-remote (DB-76a active)."}, indent=2))
        return
    result = run_remote(args.timeout_s)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
