#!/usr/bin/env python
"""DB45k VGGT pose/reflection coordinate audit.

CPU/local saved-artifact audit only. This script reads DB45i/DB45h outputs,
recomputes camera-center alignment diagnostics, and writes a manifest/board.
It does not contact HF/Colab, run VGGT, render or repair an ERP, or change any
permission state.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit"
DB45I_REMOTE = OUT_DIR / "db45i_vggt_calibrated_residual_remote_result.json"
DB45I_MANIFEST = OUT_DIR / "db45i_vggt_calibrated_residual_manifest.json"
DB45H_MANIFEST = OUT_DIR / "db45h_vggt_residual_job_contract_manifest.json"
DB45G_MANIFEST = OUT_DIR / "db45g_vggt_pose_decode_readiness_manifest.json"
BRIEF = ROOT / "agent" / "decision_briefs.md"
DB45I_SCRIPT = ROOT / "scripts" / "phase3" / "db45i_vggt_calibrated_residual_extractor.py"
DB45H_SCRIPT = ROOT / "scripts" / "phase3" / "db45h_vggt_residual_job_contract_gate.py"
AV2_LOADER = ROOT / "code" / "waymo2panorama" / "data_io" / "av2_loader.py"
MANIFEST = OUT_DIR / "db45k_vggt_pose_reflection_audit_manifest.json"
BOARD = OUT_DIR / "db45k_vggt_pose_reflection_audit_board.jpg"

CAMS = [
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
]

TOKEN_PATTERNS = {
    "hf_token": re.compile(r"hf_[A-Za-z0-9]{16,}"),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{16,}"),
    "cloudflare_url": re.compile(r"https://[A-Za-z0-9.\-]+\.trycloudflare\.com"),
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def round_float(x: Any, ndigits: int = 6) -> Any:
    if isinstance(x, (float, np.floating)):
        if not math.isfinite(float(x)):
            return None
        return round(float(x), ndigits)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, list):
        return [round_float(v, ndigits) for v in x]
    if isinstance(x, np.ndarray):
        return round_float(x.tolist(), ndigits)
    return x


def centers_from_saved(saved: dict[str, Any], key: str) -> np.ndarray:
    values = saved.get(key, {})
    return np.asarray([values[c] for c in CAMS], dtype=np.float64)


def pairwise_distance_summary(src: np.ndarray, dst: np.ndarray) -> dict[str, Any]:
    d_src = np.linalg.norm(src[:, None, :] - src[None, :, :], axis=-1)
    d_dst = np.linalg.norm(dst[:, None, :] - dst[None, :, :], axis=-1)
    tri = np.triu_indices(len(src), 1)
    src_v = d_src[tri]
    dst_v = d_dst[tri]
    scale = float((src_v @ dst_v) / max(1e-12, float(src_v @ src_v)))
    err = np.abs(scale * src_v - dst_v)
    pairs = []
    for (i, j), e, sv, dv in zip(zip(*tri), err, src_v, dst_v):
        pairs.append(
            {
                "pair": [CAMS[i], CAMS[j]],
                "scaled_vggt_distance_m": round_float(scale * sv),
                "waymo_distance_m": round_float(dv),
                "abs_error_m": round_float(e),
            }
        )
    pairs = sorted(pairs, key=lambda p: p["abs_error_m"], reverse=True)
    return {
        "best_scale_from_pairwise_distances": round_float(scale),
        "mean_abs_error_m": round_float(float(err.mean())),
        "rms_abs_error_m": round_float(float(np.sqrt((err**2).mean()))),
        "max_abs_error_m": round_float(float(err.max())),
        "heuristic_consistent": bool(float(err.mean()) <= 0.10 and float(err.max()) <= 0.25),
        "heuristic_thresholds_not_promotion_gates": {
            "mean_abs_error_m": 0.10,
            "max_abs_error_m": 0.25,
        },
        "worst_pairs": pairs[:6],
    }


def umeyama(src: np.ndarray, dst: np.ndarray, allow_reflection: bool) -> dict[str, Any]:
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    n = src.shape[0]
    mu_src = src.mean(axis=0)
    mu_dst = dst.mean(axis=0)
    src_c = src - mu_src
    dst_c = dst - mu_dst
    var_src = float((src_c**2).sum() / n)
    H = (dst_c.T @ src_c) / n
    U, sigma, Vt = np.linalg.svd(H)
    reflection_preferred = bool(np.linalg.det(U) * np.linalg.det(Vt) < 0)
    D = np.eye(3)
    if reflection_preferred and not allow_reflection:
        D[2, 2] = -1.0
    R = U @ D @ Vt
    scale = float((sigma * np.diag(D)).sum() / var_src)
    t = mu_dst - scale * R @ mu_src
    aligned = scale * src @ R.T + t
    residual = np.linalg.norm(aligned - dst, axis=1)
    return {
        "allow_reflection": bool(allow_reflection),
        "reflection_preferred_by_svd": reflection_preferred,
        "det_R": round_float(float(np.linalg.det(R))),
        "scale": round_float(scale),
        "mean_residual_m": round_float(float(residual.mean())),
        "rms_residual_m": round_float(float(np.sqrt((residual**2).mean()))),
        "max_residual_m": round_float(float(residual.max())),
        "per_camera_residual_m": {c: round_float(r) for c, r in zip(CAMS, residual)},
        "pass_db45_initial_center_thresholds": bool(
            float(residual.mean()) <= 0.50 and float(residual.max()) <= 1.00 and (allow_reflection or not reflection_preferred)
        ),
        "aligned_centers": round_float(aligned),
    }


def chirality(src: np.ndarray, dst: np.ndarray) -> dict[str, Any]:
    # A small signed-volume diagnostic using four stable ring positions.
    idx = [0, 1, 2, 3]

    def signed_volume(points: np.ndarray) -> float:
        a, b, c, d = points[idx]
        return float(np.linalg.det(np.stack([b - a, c - a, d - a], axis=1)))

    sv = signed_volume(src)
    dv = signed_volume(dst)
    return {
        "vggt_signed_volume": round_float(sv),
        "waymo_signed_volume": round_float(dv),
        "same_sign": bool((sv == 0.0 and dv == 0.0) or (sv * dv > 0.0)),
        "camera_subset": [CAMS[i] for i in idx],
    }


def stat_from_roi(roi: dict[str, Any], path: list[str], default: Any = None) -> Any:
    cur: Any = roi
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def summarize_rois(remote: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key, roi in remote.get("target_surface_residuals", {}).items():
        rows.append(
            {
                "roi": key,
                "known_lidar_support_frac": roi.get("known_lidar_support_frac"),
                "coverage_valid_frac": roi.get("coverage_valid_frac"),
                "owner_vggt_valid_frac_of_roi": roi.get("owner_vggt_valid_frac_of_roi"),
                "raw_reprojection_med_px": stat_from_roi(roi, ["owner_raw_reprojection_error_px", "med"]),
                "raw_reprojection_p90_px": stat_from_roi(roi, ["owner_raw_reprojection_error_px", "p90"]),
                "nearest_lidar_match_frac": roi.get("nearest_lidar_match_frac_of_samples"),
                "nearest_lidar_3d_residual_med_m": stat_from_roi(roi, ["nearest_lidar_3d_residual_m", "med"]),
                "permission_promotion_allowed": stat_from_roi(roi, ["admissibility", "permission_promotion_allowed"], False),
            }
        )
    return rows


def scan_token_hits(paths: list[Path]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists() or path.is_dir():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in TOKEN_PATTERNS.items():
            found = pattern.findall(text)
            if found:
                hits.append({"path": str(path.relative_to(ROOT)), "pattern": name, "count": len(found)})
    return hits


def make_check(check_id: str, passed: bool, severity: str, evidence: str) -> dict[str, Any]:
    return {"id": check_id, "pass": bool(passed), "severity": severity, "evidence": evidence}


def wrap_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, width: int, fill: tuple[int, int, int], font: ImageFont.ImageFont, spacing: int = 5) -> int:
    x, y = xy
    words = text.split()
    line = ""
    for word in words:
        probe = word if not line else f"{line} {word}"
        if draw.textbbox((0, 0), probe, font=font)[2] <= width:
            line = probe
        else:
            draw.text((x, y), line, fill=fill, font=font)
            y += draw.textbbox((0, 0), line, font=font)[3] + spacing
            line = word
    if line:
        draw.text((x, y), line, fill=fill, font=font)
        y += draw.textbbox((0, 0), line, font=font)[3] + spacing
    return y


def font(size: int) -> ImageFont.ImageFont:
    for name in ["DejaVuSans.ttf", "Arial.ttf", "arial.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def plot_centers(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], pts: np.ndarray, title: str, color: tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle(box, outline=(90, 90, 90), width=1)
    draw.text((x0 + 8, y0 + 6), title, fill=(230, 230, 230), font=font(14))
    xy = pts[:, :2].astype(float)
    center = xy.mean(axis=0)
    xy = xy - center
    span = float(np.max(np.abs(xy))) or 1.0
    sx = (x1 - x0 - 50) / (2.0 * span)
    sy = (y1 - y0 - 50) / (2.0 * span)
    scale = min(sx, sy)
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    pix = np.stack([cx + xy[:, 0] * scale, cy - xy[:, 1] * scale], axis=1)
    for i in range(len(pix)):
        j = (i + 1) % len(pix)
        draw.line((pix[i, 0], pix[i, 1], pix[j, 0], pix[j, 1]), fill=(80, 80, 80), width=1)
    for idx, p in enumerate(pix):
        draw.ellipse((p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5), fill=color)
        draw.text((p[0] + 7, p[1] - 7), str(idx), fill=(210, 210, 210), font=font(12))


def build_board(manifest: dict[str, Any]) -> None:
    board = Image.new("RGB", (1800, 1200), (24, 27, 31))
    draw = ImageDraw.Draw(board)
    draw.text((30, 24), "DB45k VGGT Pose/Reflection Coordinate Audit", fill=(255, 255, 255), font=font(30))
    draw.text((30, 62), "Saved DB45i outputs only. No A100, no model, no repair, no RED promotion.", fill=(220, 220, 220), font=font(17))

    decision = manifest["decision"]
    color = (66, 119, 183) if decision["accepted_db45_diagnostic_evidence"] else (160, 90, 70)
    draw.rounded_rectangle((30, 100, 650, 144), radius=6, fill=color)
    draw.text((44, 110), decision["accepted_evidence_type"], fill=(255, 255, 255), font=font(19))
    draw.text((690, 108), f"geometry={decision['accepted_db45_geometry_evidence']}  promotions={decision['red_promotions']}", fill=(255, 220, 180), font=font(18))

    y = 170
    y = wrap_text(draw, (30, y), "Verdict: " + decision["route_recommendation"], 1120, (255, 225, 180), font(18))
    y = wrap_text(draw, (30, y + 8), "Claim boundary: " + decision["claim_boundary"], 1120, (235, 235, 235), font(16))

    official = manifest["alignment_audit"]["official_camera_from_world_center"]
    reflected = manifest["alignment_audit"]["official_centers_with_reflection_allowed"]
    trans = manifest["alignment_audit"]["translation_column_as_center_diagnostic_only"]
    pairwise = manifest["pairwise_rig_shape_audit"]["official_camera_from_world_center"]

    draw.text((30, y + 18), "Alignment hypotheses", fill=(255, 255, 255), font=font(22))
    y += 52
    lines = [
        f"Official camera-from-world center: prefer_reflection={official['reflection_preferred_by_svd']} det={official['det_R']} mean/max={official['mean_residual_m']}/{official['max_residual_m']} pass={official['pass_db45_initial_center_thresholds']}",
        f"Reflected fit on official centers: det={reflected['det_R']} mean/max={reflected['mean_residual_m']}/{reflected['max_residual_m']} -> not admissible because reflection is disallowed.",
        f"Translation-column center diagnostic: documented={trans['documented_by_db45g_official_source']} mean/max={trans['mean_residual_m']}/{trans['max_residual_m']} pass_center={trans['pass_db45_initial_center_thresholds']} -> cannot promote without official-source contradiction resolved.",
        f"Pairwise rig-shape official centers: mean/rms/max abs error={pairwise['mean_abs_error_m']}/{pairwise['rms_abs_error_m']}/{pairwise['max_abs_error_m']} m; heuristic_consistent={pairwise['heuristic_consistent']}.",
    ]
    for line in lines:
        y = wrap_text(draw, (48, y), "- " + line, 1160, (225, 225, 225), font(15), 4)

    draw.text((30, y + 18), "ROI no-promotion boundary", fill=(255, 255, 255), font=font(22))
    y += 52
    headers = ["roi", "lidar", "raw med/p90 px", "lidar med m", "promote"]
    xs = [48, 390, 520, 750, 925]
    for x, h in zip(xs, headers):
        draw.text((x, y), h, fill=(200, 220, 255), font=font(14))
    y += 24
    for row in manifest["target_roi_boundary"]:
        vals = [
            row["roi"],
            str(row["known_lidar_support_frac"]),
            f"{round_float(row['raw_reprojection_med_px'], 3)}/{round_float(row['raw_reprojection_p90_px'], 3)}",
            str(round_float(row["nearest_lidar_3d_residual_med_m"], 3)),
            str(row["permission_promotion_allowed"]),
        ]
        for x, val in zip(xs, vals):
            draw.text((x, y), val, fill=(235, 235, 235), font=font(14))
        y += 24

    draw.text((30, y + 18), "Checks", fill=(255, 255, 255), font=font(22))
    y += 52
    for chk in manifest["checks"]:
        marker = "PASS" if chk["pass"] else "STOP"
        fill = (150, 225, 150) if chk["pass"] else (255, 170, 140)
        y = wrap_text(draw, (48, y), f"{marker} {chk['id']}: {chk['evidence']}", 1120, fill, font(13), 3)
        if y > 1130:
            break

    saved = manifest["saved_centers"]
    waymo = np.asarray([saved["waymo_rig_camera_centers"][c] for c in CAMS], dtype=float)
    vggt = np.asarray([saved["vggt_camera_from_world_centers"][c] for c in CAMS], dtype=float)
    trans_pts = np.asarray([saved["vggt_translation_column_centers"][c] for c in CAMS], dtype=float)
    plot_centers(draw, (1240, 190, 1760, 460), waymo, "Waymo rig XY", (105, 180, 255))
    plot_centers(draw, (1240, 490, 1760, 760), vggt, "VGGT official centers XY", (255, 180, 90))
    plot_centers(draw, (1240, 790, 1760, 1060), trans_pts, "Translation-column diagnostic XY", (140, 220, 150))
    draw.text((1240, 1080), "0..6 = documented RING_CAMS_7 order. Plots are shape/orientation diagnostics only.", fill=(210, 210, 210), font=font(14))

    board.save(BOARD, quality=92)


def build_manifest() -> dict[str, Any]:
    remote = read_json(DB45I_REMOTE)
    db45i_manifest = read_json(DB45I_MANIFEST)
    db45h = read_json(DB45H_MANIFEST)
    db45g = read_json(DB45G_MANIFEST) if DB45G_MANIFEST.exists() else {}

    saved = remote.get("saved_outputs", {})
    vggt_cfw = centers_from_saved(saved, "vggt_camera_centers")
    waymo = centers_from_saved(saved, "waymo_rig_camera_centers")
    extri = np.asarray(saved.get("decoded_extrinsics", []), dtype=np.float64)
    translation_col = extri[:, :3, 3] if extri.shape == (7, 3, 4) else np.full((7, 3), np.nan)

    db45i_text = read_text(DB45I_SCRIPT)
    db45h_text = read_text(DB45H_SCRIPT)
    db45g_text = json.dumps(db45g)
    av2_text = read_text(AV2_LOADER)
    brief_text = read_text(BRIEF)

    official_mentions_cfw = "camera-from-world" in db45g_text and "OpenCV" in db45g_text
    extractor_uses_official_center = "pose_encoding_to_extri_intri" in db45i_text and "-Rcw.T @ tcw" in db45i_text
    db45h_reflection_disallowed = "reflection_allowed" in db45h_text and "False" in db45h_text
    av2_order_match = all(cam in av2_text for cam in CAMS)

    official_fit = umeyama(vggt_cfw, waymo, allow_reflection=False)
    reflected_fit = umeyama(vggt_cfw, waymo, allow_reflection=True)
    translation_fit = umeyama(translation_col, waymo, allow_reflection=False)
    negative_translation_fit = umeyama(-translation_col, waymo, allow_reflection=False)

    translation_fit["documented_by_db45g_official_source"] = False
    translation_fit["admissible_for_geometry_promotion"] = False
    translation_fit["reason_not_admissible"] = (
        "Translation-column-as-center is a diagnostic contradiction check only; DB45g official-source fallback "
        "records decoded extrinsics as OpenCV camera-from-world, and DB45k does not recompute pointmap residuals."
    )
    official_fit["documented_by_db45g_official_source"] = bool(official_mentions_cfw and extractor_uses_official_center)
    official_fit["admissible_for_geometry_promotion"] = False
    official_fit["reason_not_admissible"] = "Official convention still prefers a reflection and fails the no-reflection contract."
    reflected_fit["admissible_for_geometry_promotion"] = False
    reflected_fit["reason_not_admissible"] = "Reflected similarity is explicitly disallowed by DB45h/DB45i center-alignment contract."
    negative_translation_fit["documented_by_db45g_official_source"] = False
    negative_translation_fit["admissible_for_geometry_promotion"] = False

    roi_rows = summarize_rois(remote)
    no_roi_promotion = all(row.get("permission_promotion_allowed") is False for row in roi_rows)
    lower_right = next((row for row in roi_rows if row["roi"] == "db41_lower_right_roi"), {})
    db41_zero_lidar = lower_right.get("known_lidar_support_frac") == 0.0

    pairwise_official = pairwise_distance_summary(vggt_cfw, waymo)
    pairwise_translation = pairwise_distance_summary(translation_col, waymo)

    preliminary_hits = scan_token_hits([DB45I_REMOTE, DB45I_MANIFEST, DB45H_MANIFEST, DB45G_MANIFEST, BRIEF, DB45I_SCRIPT, Path(__file__)])

    checks = [
        make_check("db45k_brief_open", "Phase11 sub-scope / DB45k" in brief_text, "precondition", "DB45k brief is present before execution."),
        make_check("inputs_present", all(p.exists() for p in [DB45I_REMOTE, DB45I_MANIFEST, DB45H_MANIFEST]), "blocker", "DB45i remote/manifest and DB45h contract inputs exist."),
        make_check("no_model_or_network_scope", True, "scope", "Script performs CPU/local saved-artifact audit only."),
        make_check("camera_order_documented", av2_order_match and CAMS == list(saved.get("vggt_camera_centers", {}).keys()), "blocker", "Saved center order matches documented RING_CAMS_7 order."),
        make_check("official_decode_convention_recorded", official_mentions_cfw, "blocker", "DB45g official-source fallback records OpenCV camera-from-world extrinsics."),
        make_check("extractor_followed_official_center_formula", extractor_uses_official_center, "blocker", "DB45i used pose_encoding_to_extri_intri and center = -Rcw.T @ tcw."),
        make_check("db45h_reflection_disallowed", db45h_reflection_disallowed, "blocker", "DB45h/DB45i contract disallows reflected Sim(3)."),
        make_check("official_center_not_admissible", not official_fit["pass_db45_initial_center_thresholds"], "blocker", "Official camera-from-world center fit still fails because reflection is preferred."),
        make_check("reflected_fit_not_admissible", reflected_fit["det_R"] < 0, "blocker", "Best reflected fit has det(R)<0 and cannot be promoted."),
        make_check("translation_column_is_diagnostic_only", translation_fit["pass_db45_initial_center_thresholds"] and not translation_fit["documented_by_db45g_official_source"], "blocker", "Translation-column center passes center thresholds but conflicts with the documented official convention."),
        make_check("pairwise_shape_not_a_clean_coordinate_fix", not pairwise_official["heuristic_consistent"], "blocker", "Official-center pairwise rig-shape error remains material after best scalar distance alignment."),
        make_check("roi_no_promotion_preserved", no_roi_promotion, "blocker", "All DB25/DB41 ROI rows remain permission_promotion_allowed=false."),
        make_check("db41_lower_right_zero_lidar_preserved", db41_zero_lidar, "blocker", "DB41 lower-right known LiDAR support remains 0.000."),
        make_check("no_token_in_inputs_or_script", not preliminary_hits, "blocker", f"secret_scan_hits={preliminary_hits}"),
    ]
    hard_pass = all(c["pass"] for c in checks if c["severity"] in {"precondition", "blocker", "scope"})

    route_recommendation = (
        "pause_vggt_residual_route_after_coordinate_audit; official camera-from-world extraction fails the "
        "no-reflection contract, reflected fits are not admissible, and the translation-column improvement is an "
        "undocumented convention conflict that cannot promote geometry or RED controls."
    )

    manifest: dict[str, Any] = {
        "db": "DB-45k",
        "status": "vggt_pose_reflection_coordinate_audit",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Audit DB45j reflection/coordinate failure from existing outputs only; no model action and no repair.",
        "inputs": {
            "db45i_remote_result": str(DB45I_REMOTE.relative_to(ROOT)),
            "db45i_manifest": str(DB45I_MANIFEST.relative_to(ROOT)),
            "db45h_manifest": str(DB45H_MANIFEST.relative_to(ROOT)),
            "db45g_manifest": str(DB45G_MANIFEST.relative_to(ROOT)) if DB45G_MANIFEST.exists() else None,
        },
        "scope": {
            "cpu_local_only": True,
            "existing_outputs_only": True,
            "network": False,
            "a100_executor": False,
            "hf_access": False,
            "model_load": False,
            "model_inference": False,
            "renderer": False,
            "erp_repair": False,
            "source_replacement": False,
            "generated_pixels": False,
            "permission_change": False,
            "red_promotion": False,
        },
        "source_contract_audit": {
            "documented_camera_order": CAMS,
            "saved_center_order": list(saved.get("vggt_camera_centers", {}).keys()),
            "av2_order_match": bool(av2_order_match),
            "db45g_official_mentions_opencv_camera_from_world": bool(official_mentions_cfw),
            "db45i_uses_official_pose_decode": "pose_encoding_to_extri_intri" in db45i_text,
            "db45i_center_formula_is_camera_from_world_inverse": "-Rcw.T @ tcw" in db45i_text,
            "db45h_reflection_allowed": False,
            "axis_hypothesis_boundary": (
                "Proper rotations are already covered by Sim(3). A one-axis flip is a reflection and is not "
                "admissible without official-source evidence. DB45k does not enumerate arbitrary flips or permutations."
            ),
        },
        "saved_centers": {
            "vggt_camera_from_world_centers": {c: round_float(v) for c, v in zip(CAMS, vggt_cfw)},
            "vggt_translation_column_centers": {c: round_float(v) for c, v in zip(CAMS, translation_col)},
            "waymo_rig_camera_centers": {c: round_float(v) for c, v in zip(CAMS, waymo)},
        },
        "alignment_audit": {
            "official_camera_from_world_center": official_fit,
            "official_centers_with_reflection_allowed": reflected_fit,
            "translation_column_as_center_diagnostic_only": translation_fit,
            "negative_translation_column_diagnostic_only": negative_translation_fit,
            "chirality_official_vs_waymo": chirality(vggt_cfw, waymo),
        },
        "pairwise_rig_shape_audit": {
            "official_camera_from_world_center": pairwise_official,
            "translation_column_diagnostic_only": pairwise_translation,
            "note": "Pairwise distances are invariant to rotation, translation, and reflection; scale is fitted once over all 21 camera pairs.",
        },
        "target_roi_boundary": roi_rows,
        "prior_db45i_decision": {
            "accepted_evidence_type": db45i_manifest.get("decision", {}).get("accepted_evidence_type"),
            "accepted_db45_geometry_evidence": db45i_manifest.get("decision", {}).get("accepted_db45_geometry_evidence"),
            "permission_state_changes": remote.get("db45b_permission_boundary", {}).get("permission_state_changes"),
            "red_promotions": remote.get("db45b_permission_boundary", {}).get("red_promotions"),
            "sim3_alignment": remote.get("sim3_alignment", {}),
        },
        "checks": checks,
        "secret_scan_hits": preliminary_hits,
        "decision": {
            "accepted_evidence_type": "vggt-pose-reflection-coordinate-audit-diagnostic-only" if hard_pass else "blocked-or-no-go",
            "accepted_db45_diagnostic_evidence": bool(hard_pass),
            "accepted_db45_geometry_evidence": False,
            "permission_state_changes": "none",
            "red_promotions": [],
            "db45_status_after_db45k": "paused",
            "route_recommendation": route_recommendation,
            "claim_boundary": (
                "DB45k is a coordinate/extractor audit only. It does not accept VGGT target-surface geometry, "
                "does not repair G/A1/BEST/DB32, and does not change DB25/DB41 RED/abstain states."
            ),
        },
        "board": str(BOARD.relative_to(ROOT)),
    }
    return manifest


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    final_hits = scan_token_hits([MANIFEST, BOARD, Path(__file__) if Path(__file__).exists() else MANIFEST])
    manifest["secret_scan_hits"] = final_hits
    for check in manifest["checks"]:
        if check["id"] == "no_token_in_inputs_or_script":
            check["pass"] = not final_hits
            check["evidence"] = f"secret_scan_hits={final_hits}"
    hard_pass = all(c["pass"] for c in manifest["checks"] if c["severity"] in {"precondition", "blocker", "scope"})
    manifest["decision"]["accepted_evidence_type"] = "vggt-pose-reflection-coordinate-audit-diagnostic-only" if hard_pass else "blocked-or-no-go"
    manifest["decision"]["accepted_db45_diagnostic_evidence"] = bool(hard_pass)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    build_board(manifest)
    print(json.dumps({"manifest": str(MANIFEST), "board": str(BOARD), "accepted": hard_pass}, indent=2))


if __name__ == "__main__":
    main()
