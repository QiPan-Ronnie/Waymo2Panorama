#!/usr/bin/env python
"""Build DB45 geometry/depth/flow evidence-audit v0 artifacts.

DB45 v0 is evidence-only. It does not run geometry foundation models, render
new panoramas, or repair pixels. Its job is to pin the fixed 8-control set and
make permission-state changes impossible without explicit raw/depth/flow/model
evidence in a later DB45 sub-brief.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DB44_MANIFEST = (
    ROOT
    / "deliverables"
    / "dit360_v2"
    / "db44_layer_aware_dispatcher"
    / "db44_layer_aware_dispatcher_manifest.json"
)
DB41_MANIFEST = (
    ROOT
    / "deliverables"
    / "dit360_v2"
    / "db41_rightline_evidence_gate"
    / "db41_rightline_evidence_manifest.json"
)
DB25_SUMMARY = (
    ROOT
    / "deliverables"
    / "dit360_v2"
    / "db25_longline_evidence_fetch"
    / "db25_longline_summary.json"
)
DEPTH_VIS_SUMMARY = ROOT / "deliverables" / "depth_visibility_seam_probe" / "batch_summary.json"
DENSE_DEPTH_SUMMARY = ROOT / "deliverables" / "dense_depth_edge_seam_probe" / "batch_summary.json"
PARALLAX_SUBSET = ROOT / "data" / "parallax_subset.json"
E2_PARETO = ROOT / "deliverables" / "e2_seam_depth" / "pareto_table.json"
PI3_CACHE = ROOT / "outputs" / "phase3" / "pi3_cache"
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db45_geometry_evidence_audit"
MANIFEST = OUT_DIR / "db45_geometry_evidence_audit_manifest.json"
PERMISSION_BOARD = OUT_DIR / "db45_evidence_permission_board.jpg"
NEGATIVE_BOARD = OUT_DIR / "db45_negative_controls_board.jpg"
PREFLIGHT_BOARD = OUT_DIR / "db45_preflight_and_gate_board.jpg"


EVIDENCE_COLORS = {
    "GREEN": (40, 125, 70),
    "YELLOW": (145, 115, 35),
    "RED": (150, 45, 45),
}

CLAIM_COLORS = {
    "source-faithful": (40, 125, 70),
    "source-sidestep": (115, 105, 40),
    "abstain": (125, 70, 25),
    "reject": (150, 45, 45),
    "diagnostic": (95, 95, 95),
}


A100_PREFLIGHT = {
    "status_endpoint": {
        "runtime_type": "colab-gpu",
        "gpu_name": "NVIDIA A100-SXM4-40GB",
        "gpu_mem_free_gb": 39.49,
        "active_jobs": 0,
        "shell_alive": False,
        "version": "0.1.0a1",
    },
    "exec_preflight": {
        "job_id": "f98f06aa4c9f4c95b9249bb1ecbda4f0",
        "exit_code": 0,
        "repo_found_in_checked_paths": False,
        "drive_mounted": True,
        "python": "3.12.13",
        "modules": {
            "torch": True,
            "transformers": True,
            "cv2": True,
            "av2": False,
            "PIL": True,
        },
        "cache_hits": [
            "/content/drive/MyDrive/koi_waymo2pano_colab/cache/new_f_vggt",
            "/content/drive/MyDrive/koi_waymo2pano_colab/cache/new_f_vggt/vggt-repo.tar.zst",
        ],
        "model_download": False,
        "model_inference": False,
        "panorama_generation": False,
    },
    "verdict": "A100 is live, but DB45 v0 only records env/cache readiness. No foundation model evidence is accepted yet.",
}


@dataclass(frozen=True)
class AuditSegment:
    segment_id: str
    source_component_id: str
    title: str
    artifact: str
    role: str
    layer: str
    evidence_state_before: str
    evidence_state_after: str
    claim_before: str
    claim_after: str
    existing_evidence: dict[str, object]
    foundation_model_evidence: dict[str, object]
    permission_delta: str
    required_future_evidence: list[str]
    kill_guard: str
    verdict: str


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_optional(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_img(rel_path: str) -> Image.Image:
    path = ROOT / rel_path
    if not path.exists():
        raise FileNotFoundError(path)
    return Image.open(path).convert("RGB")


def fit(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGB", size, (16, 16, 16))
    work = img.copy()
    work.thumbnail(size, Image.Resampling.LANCZOS)
    canvas.paste(work, ((size[0] - work.width) // 2, (size[1] - work.height) // 2))
    return canvas


def text_lines(text: str, width: int) -> list[str]:
    return wrap(str(text), width=width, break_long_words=False, break_on_hyphens=False)


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    width: int,
    line_h: int,
    fill: tuple[int, int, int],
    fnt: ImageFont.ImageFont,
) -> int:
    x, y = xy
    for line in text_lines(text, width):
        draw.text((x, y), line, fill=fill, font=fnt)
        y += line_h
    return y


def component_map(db44: dict) -> dict[str, dict]:
    return {c["component_id"]: c for c in db44["components"]}


def flow_metric(summary: dict, pair: str) -> float | None:
    stats = summary.get("flow_pair_stats", {}).get(pair)
    if not stats:
        return None
    val = stats.get("fb_reliable_frac")
    return float(val) if val is not None else None


def fmt_frac(x: object) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x):.3f}"
    except (TypeError, ValueError):
        return str(x)


def first_bmw_case(summary: dict | list | None) -> dict | None:
    if not isinstance(summary, dict):
        return None
    cases = summary.get("cases")
    if not isinstance(cases, list):
        return None
    for case in cases:
        if "02a00399" in str(case.get("case", "")) or case.get("log_short") == "02a00399":
            return case
    return cases[0] if cases else None


def artifact_status(path: Path, kind: str, action: str, note: str, summary: dict[str, object] | None = None) -> dict[str, object]:
    if path.exists():
        status = "structured_json" if path.suffix.lower() == ".json" else "present"
    else:
        status = "missing"
    return {
        "path": str(path.relative_to(ROOT)) if path.exists() else str(path.relative_to(ROOT)),
        "kind": kind,
        "status": status,
        "db45_action": action,
        "note": note,
        "summary": summary or {},
    }


def build_evidence_registry() -> list[dict[str, object]]:
    depth = read_json_optional(DEPTH_VIS_SUMMARY)
    dense = read_json_optional(DENSE_DEPTH_SUMMARY)
    e2 = read_json_optional(E2_PARETO)
    parallax = read_json_optional(PARALLAX_SUBSET)
    depth_bmw = first_bmw_case(depth)
    dense_bmw = first_bmw_case(dense)

    depth_summary: dict[str, object] = {}
    if depth_bmw:
        dv = depth_bmw.get("depth_visibility_global", {})
        prx = dv.get("parallax_px", {})
        depth_summary = {
            "bmw_lidar_supported_frac_of_band": dv.get("lidar_supported_frac_of_band"),
            "bmw_unknown_frac_of_band": dv.get("unknown_frac_of_band"),
            "bmw_high_depth_risk_frac_supported": dv.get("high_depth_risk_frac_supported"),
            "bmw_parallax_p90_px": prx.get("p90"),
        }

    dense_summary: dict[str, object] = {}
    if dense_bmw:
        dd = dense_bmw.get("dense_depth_global", {})
        corr = dense_bmw.get("dense_source_correlations_on_seam", {})
        dense_summary = {
            "model_id": dense_bmw.get("model_id"),
            "bmw_high_dense_depth_frac_of_band": dd.get("high_dense_depth_frac_of_band"),
            "dense_vs_source_risk": corr.get("dense_vs_source_risk"),
            "dense_vs_structure_risk": corr.get("dense_vs_structure_risk"),
        }

    e2_summary: dict[str, object] = {}
    if isinstance(e2, dict):
        e2_summary = {
            "E2_dense_p90_px_vs_L1": e2.get("axis_geometry_cost_M_p90_px_vs_L1", {}).get("E2-dense"),
            "E2_sparse_p90_px_vs_L1": e2.get("axis_geometry_cost_M_p90_px_vs_L1", {}).get("E2-sparse"),
            "E1_5_p90_px_vs_L1": e2.get("axis_geometry_cost_M_p90_px_vs_L1", {}).get("E1.5-cut5"),
            "note": e2.get("note"),
        }

    pi3_dirs = []
    if PI3_CACHE.exists():
        pi3_dirs = sorted(p.name for p in PI3_CACHE.iterdir() if p.is_dir())

    parallax_summary: dict[str, object] = {}
    if isinstance(parallax, list):
        parallax_summary = {"records": len(parallax), "type": "list"}
    elif isinstance(parallax, dict):
        parallax_summary = {"keys": sorted(parallax.keys())[:12], "type": "dict"}

    return [
        artifact_status(
            DB25_SUMMARY,
            "roi_flow_lidar_summary",
            "reuse",
            "Long-line ROI has structured raw/LiDAR/flow evidence and remains abstain under current summary.",
        ),
        artifact_status(
            DB41_MANIFEST,
            "rightline_roi_gate",
            "reuse",
            "Right/lower-right ROIs have structured thresholds; both fail DB41 gate and remain abstain.",
        ),
        artifact_status(
            DB44_MANIFEST,
            "dispatcher_manifest",
            "reuse",
            "DB44 component states are the source for DB45 permission deltas.",
        ),
        artifact_status(
            DEPTH_VIS_SUMMARY,
            "lidar_depth_visibility_summary",
            "reuse_case_level_only",
            "Case-level seam-band depth visibility is reusable, but it is not DB41 target-ROI-specific.",
            depth_summary,
        ),
        artifact_status(
            DENSE_DEPTH_SUMMARY,
            "dense_depth_edge_summary",
            "reuse_case_level_only",
            "Depth-Anything-V2 evidence exists; it is diagnostic metadata, not DAC/DAP and not a renderer.",
            dense_summary,
        ),
        artifact_status(
            PARALLAX_SUBSET,
            "parallax_ranking_or_subset",
            "index_only",
            "Useful for case prioritization; not enough to promote DB41 ROIs without target-surface support.",
            parallax_summary,
        ),
        artifact_status(
            E2_PARETO,
            "depth_fusion_negative_control",
            "reuse_negative_control",
            "E2 depth fusion is a negative control showing depth warp distortion escalates.",
            e2_summary,
        ),
        {
            "path": str(PI3_CACHE.relative_to(ROOT)),
            "kind": "pi3_cache_index",
            "status": "present" if PI3_CACHE.exists() else "missing",
            "db45_action": "index_only",
            "note": "Pi3 cache can be evidence metadata only; do not treat cached geometry as seam truth without ROI checks.",
            "summary": {"anchor_dirs": pi3_dirs[:12], "anchor_dir_count": len(pi3_dirs)},
        },
    ]


def build_model_routes() -> list[dict[str, object]]:
    return [
        {
            "route": "VGGT",
            "local_script": (ROOT / "scripts" / "phase3" / "run_vggt_multi_anchor.py").exists(),
            "remote_cache_hint": "new_f_vggt/vggt-repo.tar.zst",
            "current_evidence_state": "script_or_cache_only_no_db45_outputs",
            "db45_action": "do_not_run_until_scoped_evidence_job",
        },
        {
            "route": "Fast3R",
            "local_script": False,
            "remote_cache_hint": None,
            "current_evidence_state": "no_local_script_or_output",
            "db45_action": "do_not_run",
        },
        {
            "route": "CUT3R",
            "local_script": False,
            "remote_cache_hint": None,
            "current_evidence_state": "no_local_script_or_output",
            "db45_action": "do_not_run",
        },
        {
            "route": "DAC/DAP",
            "local_script": False,
            "remote_cache_hint": None,
            "current_evidence_state": "no_structured_output; DA-V2 probe is separate existing diagnostic",
            "db45_action": "do_not_claim_as_dac_dap",
        },
        {
            "route": "PriOr-Flow/FlowSeek",
            "local_script": False,
            "remote_cache_hint": None,
            "current_evidence_state": "no_local_tool_or_output; DB25/DB41 flow is DIS-style reliability only",
            "db45_action": "do_not_run",
        },
        {
            "route": "DepthPro/Metric3D",
            "local_script": (ROOT / "scripts" / "phase3" / "run_depth_backbone_swap.py").exists(),
            "remote_cache_hint": None,
            "current_evidence_state": "route_script_only_no_db45_structured_output",
            "db45_action": "do_not_run_until_followup_brief",
        },
    ]


def build_segments() -> list[AuditSegment]:
    db44 = read_json(DB44_MANIFEST)
    db41 = read_json(DB41_MANIFEST)
    db25 = read_json(DB25_SUMMARY)
    comps = component_map(db44)

    db41_right = db41["summaries"]["right_roi"]
    db41_lower = db41["summaries"]["lower_right_roi"]
    db41_gate = db41["threshold_results"]

    base_no_model = {
        "state": "not_run_in_db45_v0",
        "reason": "preflight-only; no model download, inference, rendering, or repaired ERP",
        "permission_effect": "no_promotion",
    }

    def comp(cid: str) -> dict:
        if cid not in comps:
            raise KeyError(cid)
        return comps[cid]

    specs = [
        {
            "segment_id": "db45_clean_source_preservation",
            "cid": "db44_db34_source_preservation",
            "role": "positive-control",
            "existing": {
                "source_preservation": True,
                "noncore_byte_exact": True,
                "generated_mask_excluded": True,
            },
            "future": [
                "source_id_map stays consistent",
                "future model confidence must not relabel full DB32 as source-faithful",
            ],
            "verdict": "Keep GREEN positive control only for source-preserved non-core pixels.",
        },
        {
            "segment_id": "db45_bev_planar_road_control",
            "cid": "db44_bevfinal_source_faithful",
            "role": "positive-caveated-control",
            "existing": {
                "source_faithful_route": True,
                "planar_road_allowed": True,
                "curb_floor": True,
                "out_of_fov_caveat": True,
            },
            "future": [
                "plane validity or LiDAR support on the target surface",
                "curb/off-plane mask proving the operator does not bleed into DB41-like regions",
            ],
            "verdict": "Keep YELLOW/source-faithful caveat: road evidence cannot upgrade curb/right-line.",
        },
        {
            "segment_id": "db45_db32_source_sidestep_control",
            "cid": "db44_db32_s40_long_roi",
            "role": "claim-control",
            "existing": {
                "source_sidestep": True,
                "not_original_g_repair": True,
                "generated_sky_caveat_in_full_candidate": True,
            },
            "future": [
                "same-ROI source comparison must keep sidestep language",
                "DB49 generated/unknown masks if used for Bosch handoff",
            ],
            "verdict": "Keep YELLOW/source-sidestep; no local seam repair claim.",
        },
        {
            "segment_id": "db45_db25_longline_abstain",
            "cid": "db44_db25_longline_evidence",
            "role": "negative-evidence-control",
            "existing": {
                "roi": db25["roi"],
                "near_ground_frac": db25["near_ground_frac"],
                "lidar_support_frac": db25["lidar_support_frac"],
                "best_flow_pair": db25["best_flow_pair"],
                "best_flow_reliable_frac": db25["best_flow_reliable_frac"],
                "key_pair_6_5_flow_reliable_frac": flow_metric(db25, "6-5"),
                "top_camera_labels": db25["top_camera_labels"],
            },
            "future": [
                "target-surface-aware tracks, not only best-pair flow",
                "raw-camera/LiDAR evidence on the right dark-wall/near-ground target surface",
            ],
            "verdict": "Remain RED/evidence-only abstain; best pair does not cover key right dark-wall pair.",
        },
        {
            "segment_id": "db45_db41_right_roi_abstain",
            "cid": "db44_db41_right_roi",
            "role": "negative-evidence-control",
            "existing": {
                "roi": db41_right["roi"],
                "near_ground_frac": db41_right["near_ground_frac"],
                "lidar_support_frac": db41_right["lidar_support_frac"],
                "best_flow_pair": db41_right["best_flow_pair"],
                "best_flow_reliable_frac": db41_right["best_flow_reliable_frac"],
                "key_pair_5_4_flow_reliable_frac": flow_metric(db41_right, "5-4"),
                "passes_db41_gate": db41_gate["right_roi"]["passes_db41_gate"],
                "top_camera_labels": db41_right["top_camera_labels"],
            },
            "future": [
                "prove flow/LiDAR support lands on the white-line or curb target surface",
                "continuous source-faithful geometry across the right ROI",
            ],
            "verdict": "Remain RED/abstain; high best-flow patch cannot override sparse LiDAR target evidence.",
        },
        {
            "segment_id": "db45_db41_lower_right_abstain",
            "cid": "db44_db41_lower_right",
            "role": "hard-negative-evidence-control",
            "existing": {
                "roi": db41_lower["roi"],
                "near_ground_frac": db41_lower["near_ground_frac"],
                "lidar_support_frac": db41_lower["lidar_support_frac"],
                "best_flow_pair": db41_lower["best_flow_pair"],
                "best_flow_reliable_frac": db41_lower["best_flow_reliable_frac"],
                "key_pair_5_4_flow_reliable_frac": flow_metric(db41_lower, "5-4"),
                "passes_db41_gate": db41_gate["lower_right_roi"]["passes_db41_gate"],
                "top_camera_labels": db41_lower["top_camera_labels"],
            },
            "future": [
                "new raw/depth/correspondence evidence on the actual lower-right target surface",
                "must not be upgraded by generic dense confidence or edge-attached flow",
            ],
            "verdict": "Remain RED/abstain; LiDAR support is zero in the all-near-ground target ROI.",
        },
        {
            "segment_id": "db45_db36_fake_redline_reject",
            "cid": "db44_db36_user_redline",
            "role": "fake-geometry-negative",
            "existing": {
                "generated_output": True,
                "object_gate_pass_is_insufficient": True,
                "fake_geometry_reasons": ["fake_slab", "black_hole", "fake_ground"],
            },
            "future": [
                "future model confidence must be low or contradicted on fake generated geometry",
                "do not accept outside-mask exactness as a seam repair proof",
            ],
            "verdict": "Remain RED/reject; detector-clean generated red-line output is fake geometry.",
        },
        {
            "segment_id": "db45_db40_longsrc_fake_pole_reject",
            "cid": "db44_db40_longsrc_raw",
            "role": "fake-geometry-negative",
            "existing": {
                "generated_output": True,
                "object_gate_pass_is_insufficient": True,
                "fake_geometry_reasons": ["vertical_slice", "pole_like_artifact", "long_source_band"],
            },
            "future": [
                "future model confidence must reject pole/slice artifacts",
                "BMW keepout root-cause success must not become a seam repair claim",
            ],
            "verdict": "Remain RED/reject; longsrc replay creates pole-like generated geometry.",
        },
    ]

    segments: list[AuditSegment] = []
    for spec in specs:
        c = comp(spec["cid"])
        segments.append(
            AuditSegment(
                segment_id=spec["segment_id"],
                source_component_id=spec["cid"],
                title=c["title"],
                artifact=c["artifact"],
                role=spec["role"],
                layer=c["layer"],
                evidence_state_before=c["evidence_state"],
                evidence_state_after=c["evidence_state"],
                claim_before=c["claim_level"],
                claim_after=c["claim_level"],
                existing_evidence=spec["existing"],
                foundation_model_evidence=dict(base_no_model),
                permission_delta="unchanged",
                required_future_evidence=spec["future"],
                kill_guard=c["kill_guard"],
                verdict=spec["verdict"],
            )
        )
    return segments


def segment_tile(seg: AuditSegment, size: tuple[int, int]) -> Image.Image:
    tile = Image.new("RGB", size, (24, 24, 24))
    draw = ImageDraw.Draw(tile)
    draw.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(72, 72, 72), width=1)
    color = EVIDENCE_COLORS.get(seg.evidence_state_after, (100, 100, 100))
    draw.rectangle([0, 0, size[0], 7], fill=color)
    draw.text((12, 14), seg.segment_id, fill=(255, 255, 255), font=font(14))
    draw.text((12, 36), f"{seg.role} | {seg.evidence_state_after} | {seg.claim_after}", fill=(230, 230, 210), font=font(13))

    img = fit(load_img(seg.artifact), (size[0] - 24, 165))
    tile.paste(img, (12, 60))

    y = 236
    draw.text((12, y), "Evidence:", fill=(255, 255, 255), font=font(13))
    y += 20
    metric_bits = []
    for k, v in seg.existing_evidence.items():
        if isinstance(v, float):
            metric_bits.append(f"{k}={v:.3f}")
        elif isinstance(v, list) and len(metric_bits) < 5:
            metric_bits.append(f"{k}={v}")
        elif len(metric_bits) < 6 and not isinstance(v, list):
            metric_bits.append(f"{k}={v}")
    y = draw_wrapped(draw, (12, y), "; ".join(metric_bits), 52, 16, (220, 220, 220), font(11))
    y += 6
    draw.text((12, y), "DB45 v0 model evidence: not run -> no promotion", fill=(255, 215, 160), font=font(12))
    y += 22
    y = draw_wrapped(draw, (12, y), seg.verdict, 52, 17, (235, 235, 235), font(12))
    y += 4
    draw_wrapped(draw, (12, y), f"Guard: {seg.kill_guard}", 52, 16, (255, 190, 190), font(11))
    return tile


def build_permission_board(segments: list[AuditSegment]) -> None:
    board = Image.new("RGB", (1760, 1120), (16, 16, 16))
    draw = ImageDraw.Draw(board)
    draw.text((22, 16), "DB45 geometry foundation evidence audit v0: fixed 8 controls, no repair", fill=(255, 255, 255), font=font(26))
    draw.text(
        (22, 50),
        "Existing DB25/DB41/DB43/DB44 evidence only. A100 preflight only. No model inference, no renderer, no repaired ERP.",
        fill=(225, 225, 225),
        font=font(15),
    )

    tile_w, tile_h = 420, 500
    x0, y0 = 22, 90
    for idx, seg in enumerate(segments):
        x = x0 + (idx % 4) * (tile_w + 18)
        y = y0 + (idx // 4) * (tile_h + 22)
        board.paste(segment_tile(seg, (tile_w, tile_h)), (x, y))
    board.save(PERMISSION_BOARD, quality=92)


def build_negative_board(segments: list[AuditSegment]) -> None:
    negs = [s for s in segments if s.evidence_state_after == "RED"]
    board = Image.new("RGB", (1720, 1540), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((22, 16), "DB45 hard negatives: no-evidence and fake-geometry controls must not be promoted", fill=(255, 255, 255), font=font(25))
    draw.text(
        (22, 50),
        "Red controls remain abstain/reject unless a later brief brings target-surface raw/depth/flow/model evidence that passes these guards.",
        fill=(225, 225, 225),
        font=font(14),
    )

    y = 86
    for seg in negs:
        panel = Image.new("RGB", (1680, 270), (24, 24, 24))
        d = ImageDraw.Draw(panel)
        d.rectangle([0, 0, 1679, 269], outline=(90, 55, 55), width=2)
        d.rectangle([0, 0, 1679, 8], fill=EVIDENCE_COLORS["RED"])
        panel.paste(fit(load_img(seg.artifact), (460, 235)), (12, 24))
        d.text((490, 24), f"{seg.segment_id} | {seg.claim_after}", fill=(255, 255, 255), font=font(18))
        d.text((490, 52), seg.layer, fill=(230, 230, 210), font=font(14))
        yy = 84
        for k, v in seg.existing_evidence.items():
            if yy > 182:
                break
            if isinstance(v, float):
                val = f"{v:.3f}"
            else:
                val = str(v)
            d.text((500, yy), f"{k}: {val}", fill=(220, 220, 220), font=font(12))
            yy += 18
        d.text((1040, 84), "Required future evidence", fill=(255, 255, 255), font=font(14))
        yy = 112
        for item in seg.required_future_evidence:
            yy = draw_wrapped(d, (1040, yy), f"- {item}", 58, 17, (220, 220, 220), font(12))
        draw_wrapped(d, (1040, 202), f"Kill guard: {seg.kill_guard}", 58, 17, (255, 190, 190), font(12))
        board.paste(panel, (22, y))
        y += 294
    board.save(NEGATIVE_BOARD, quality=92)


def build_preflight_board(
    segments: list[AuditSegment],
    kill_checks: list[dict[str, object]],
    gate_pass: bool,
    evidence_registry: list[dict[str, object]],
    model_routes: list[dict[str, object]],
) -> None:
    board = Image.new("RGB", (1500, 1360), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((24, 18), "DB45 A100 preflight and gate summary", fill=(255, 255, 255), font=font(26))
    draw.text((24, 52), "No model download, no model inference, no panorama repair in DB45 v0.", fill=(225, 225, 225), font=font(15))

    y = 92
    draw.text((24, y), "A100 status", fill=(255, 255, 255), font=font(20))
    y += 32
    status = A100_PREFLIGHT["status_endpoint"]
    exec_pf = A100_PREFLIGHT["exec_preflight"]
    lines = [
        f"runtime={status['runtime_type']}, gpu={status['gpu_name']}, free={status['gpu_mem_free_gb']} GB, jobs={status['active_jobs']}",
        f"exec job={exec_pf['job_id']}, exit={exec_pf['exit_code']}, drive_mounted={exec_pf['drive_mounted']}, repo_found={exec_pf['repo_found_in_checked_paths']}",
        f"python={exec_pf['python']}, modules={exec_pf['modules']}",
        f"cache_hits={exec_pf['cache_hits']}",
        "preflight verdict: live GPU exists, but no accepted geometry foundation evidence yet.",
    ]
    for line in lines:
        y = draw_wrapped(draw, (40, y), line, 118, 22, (225, 225, 225), font(13))
        y += 4

    y += 12
    draw.text((24, y), "Segment counts", fill=(255, 255, 255), font=font(20))
    y += 32
    state_counts = Counter(s.evidence_state_after for s in segments)
    claim_counts = Counter(s.claim_after for s in segments)
    draw.text((40, y), f"evidence_state={dict(state_counts)}", fill=(225, 225, 225), font=font(14))
    y += 26
    draw.text((40, y), f"claim_level={dict(claim_counts)}", fill=(225, 225, 225), font=font(14))
    y += 42

    draw.text((24, y), "Reusable evidence registry", fill=(255, 255, 255), font=font(20))
    y += 32
    for item in evidence_registry:
        if y > 590:
            break
        line = f"{Path(str(item['path'])).name}: {item['status']} | {item['db45_action']}"
        draw.text((40, y), line, fill=(225, 225, 225), font=font(12))
        y += 18
    y += 12

    draw.text((24, y), "Model route state", fill=(255, 255, 255), font=font(20))
    y += 32
    for item in model_routes:
        if y > 760:
            break
        line = f"{item['route']}: {item['current_evidence_state']} -> {item['db45_action']}"
        y = draw_wrapped(draw, (40, y), line, 114, 18, (225, 225, 225), font(11))
    y += 12

    draw.text((24, y), f"Gate pass: {gate_pass}", fill=(160, 255, 180) if gate_pass else (255, 120, 120), font=font(22))
    y += 38
    for chk in kill_checks:
        fill = (160, 255, 180) if chk["pass"] else (255, 120, 120)
        draw.text((44, y), f"{'PASS' if chk['pass'] else 'FAIL'}  {chk['id']}", fill=fill, font=font(14))
        y += 22
        y = draw_wrapped(draw, (70, y), chk["description"], 105, 18, (220, 220, 220), font(12))
        y += 8

    board.save(PREFLIGHT_BOARD, quality=92)


def kill_checks_for(segments: list[AuditSegment]) -> list[dict[str, object]]:
    seg_by_id = {s.segment_id: s for s in segments}

    def pass_check(check_id: str, ok: bool, desc: str) -> dict[str, object]:
        return {"id": check_id, "pass": bool(ok), "description": desc}

    red = [s for s in segments if s.evidence_state_before == "RED"]
    db41_right = seg_by_id["db45_db41_right_roi_abstain"]
    db41_lower = seg_by_id["db45_db41_lower_right_abstain"]
    db36 = seg_by_id["db45_db36_fake_redline_reject"]
    db40 = seg_by_id["db45_db40_longsrc_fake_pole_reject"]
    db32 = seg_by_id["db45_db32_source_sidestep_control"]

    checks = [
        pass_check(
            "max_8_segments",
            len(segments) == 8,
            "DB45 v0 must stay within the first-pass max scope of exactly 8 fixed controls.",
        ),
        pass_check(
            "no_repair_or_model_inference",
            not A100_PREFLIGHT["exec_preflight"]["model_inference"] and not A100_PREFLIGHT["exec_preflight"]["panorama_generation"],
            "A100 was used only for live/env/cache preflight; no model inference or repaired ERP was produced.",
        ),
        pass_check(
            "no_red_promoted",
            all(s.evidence_state_after == "RED" and s.permission_delta == "unchanged" for s in red),
            "Every RED control remains RED with unchanged permission state.",
        ),
        pass_check(
            "db41_right_remains_abstain",
            db41_right.claim_after == "abstain"
            and db41_right.existing_evidence.get("passes_db41_gate") is False
            and float(db41_right.existing_evidence.get("lidar_support_frac", 1.0)) < 0.2,
            "DB41 right ROI remains abstain because LiDAR support is below gate even with local flow patches.",
        ),
        pass_check(
            "db41_lower_right_zero_lidar",
            db41_lower.claim_after == "abstain"
            and db41_lower.existing_evidence.get("passes_db41_gate") is False
            and float(db41_lower.existing_evidence.get("lidar_support_frac", 1.0)) == 0.0,
            "DB41 lower-right remains abstain with zero LiDAR support in the all-near-ground target ROI.",
        ),
        pass_check(
            "fake_geometry_rejects",
            db36.claim_after == "reject" and db40.claim_after == "reject",
            "DB36 fake red-line slabs/holes and DB40 pole-like longsrc raw remain rejected.",
        ),
        pass_check(
            "db32_not_fully_source_faithful",
            db32.claim_after == "source-sidestep" and db32.evidence_state_after == "YELLOW",
            "DB32 long ROI remains source-sidestep/caveated and is not promoted to fully source-faithful or original-G repair.",
        ),
        pass_check(
            "no_foundation_confidence_claim",
            all(s.foundation_model_evidence["state"] == "not_run_in_db45_v0" for s in segments),
            "No VGGT/Fast3R/CUT3R/DAC/DAP/PriOr-Flow confidence is claimed before a scoped model run.",
        ),
    ]
    return checks


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    segments = build_segments()
    evidence_registry = build_evidence_registry()
    model_routes = build_model_routes()
    kill_checks = kill_checks_for(segments)
    gate_pass = all(chk["pass"] for chk in kill_checks)

    build_permission_board(segments)
    build_negative_board(segments)
    build_preflight_board(segments, kill_checks, gate_pass, evidence_registry, model_routes)

    manifest = {
        "db": "DB-45",
        "status": "running_phase0_output",
        "purpose": "Geometry/depth/flow foundation evidence audit v0 over fixed controls.",
        "scope": {
            "evidence_only": True,
            "fixed_segments": len(segments),
            "max_segments": 8,
            "new_panorama_generation": False,
            "panorama_repair": False,
            "source_replacement": False,
            "diffusion_or_refiner": False,
            "model_download": False,
            "model_inference": False,
            "a100_used": "status/env/cache preflight only",
        },
        "outputs": {
            "manifest": str(MANIFEST.relative_to(ROOT)),
            "permission_board": str(PERMISSION_BOARD.relative_to(ROOT)),
            "negative_board": str(NEGATIVE_BOARD.relative_to(ROOT)),
            "preflight_board": str(PREFLIGHT_BOARD.relative_to(ROOT)),
        },
        "refs": {
            "db44_manifest": str(DB44_MANIFEST.relative_to(ROOT)),
            "db41_manifest": str(DB41_MANIFEST.relative_to(ROOT)),
            "db25_summary": str(DB25_SUMMARY.relative_to(ROOT)),
        },
        "a100_preflight": A100_PREFLIGHT,
        "evidence_registry": evidence_registry,
        "model_routes": model_routes,
        "segments": [asdict(s) for s in segments],
        "counts": {
            "evidence_state_after": dict(Counter(s.evidence_state_after for s in segments)),
            "claim_after": dict(Counter(s.claim_after for s in segments)),
            "permission_delta": dict(Counter(s.permission_delta for s in segments)),
        },
        "kill_checks": kill_checks,
        "gate_pass": gate_pass,
        "decision": {
            "permission_state_changes": "none",
            "red_promotions": [],
            "accepted_as_final_db45": False,
            "db45_remains_running": True,
            "next": "If continuing DB45, open a sub-scope for an actual foundation-model evidence run. It must compare confidence/tracks/depth/flow against these eight controls and must kill immediately if RED controls receive high confidence without target-surface evidence.",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {MANIFEST}")
    print(f"gate_pass={gate_pass} segments={len(segments)}")


if __name__ == "__main__":
    main()
