#!/usr/bin/env python
"""Build DB44 layer-aware EGSR dispatcher dry-run artifacts.

DB44 is a CPU-only dispatcher pass over existing artifacts. It does not
generate, warp, diffuse, or locally repair any panorama. Its purpose is to
convert the DB43 fake-geometry gate into a layer/evidence/operator map.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
DB43_MANIFEST = (
    ROOT
    / "deliverables"
    / "dit360_v2"
    / "db43_source_faithfulness_gate"
    / "db43_source_faithfulness_gate_manifest.json"
)
DB41_MANIFEST = (
    ROOT
    / "deliverables"
    / "dit360_v2"
    / "db41_rightline_evidence_gate"
    / "db41_rightline_evidence_manifest.json"
)
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db44_layer_aware_dispatcher"
MANIFEST = OUT_DIR / "db44_layer_aware_dispatcher_manifest.json"
DISPATCH_BOARD = OUT_DIR / "db44_layer_dispatcher_board.jpg"
BMW_BOARD = OUT_DIR / "db44_bmw_roi_dispatch_board.jpg"
MATRIX_BOARD = OUT_DIR / "db44_operator_matrix_board.jpg"
LAYER_BOARD = OUT_DIR / "db44_layer_evidence_board.jpg"
NEGATIVE_BOARD = OUT_DIR / "db44_negative_controls_board.jpg"


EVIDENCE_COLORS = {
    "GREEN": (42, 120, 70),
    "YELLOW": (145, 115, 35),
    "RED": (150, 45, 45),
}

BRANCH_COLORS = {
    "source-faithful": (42, 120, 70),
    "source-sidestep": (110, 105, 45),
    "handoff-caveated": (115, 100, 35),
    "presentation-only": (75, 80, 130),
    "evidence-only": (75, 95, 120),
    "diagnostic-only": (95, 95, 95),
    "abstain": (125, 70, 25),
    "rejected": (145, 45, 45),
}


@dataclass(frozen=True)
class Component:
    component_id: str
    source_case_id: str
    title: str
    artifact: str
    roi_xyxy: list[int] | None
    layer: str
    segment_type: str
    evidence_state: str
    dispatch_operator: str
    operator_executed: bool
    allowed_branch: str
    claim_level: str
    source_faithful_action: str
    presentation_action: str
    protected_structures: list[str]
    evidence_metrics: dict[str, object]
    comparison_refs: list[str]
    generated_mask_required: bool
    unknown_or_abstain_mask_required: bool
    kill_check_ids: list[str]
    reason_codes: list[str]
    kill_guard: str
    vision_check: str


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def load_img(rel_path: str) -> Image.Image:
    path = ROOT / rel_path
    if not path.exists():
        raise FileNotFoundError(path)
    return Image.open(path).convert("RGB")


def fit(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGB", size, (18, 18, 18))
    work = img.copy()
    work.thumbnail(size, Image.Resampling.LANCZOS)
    canvas.paste(work, ((size[0] - work.width) // 2, (size[1] - work.height) // 2))
    return canvas


def text_lines(text: str, width: int) -> list[str]:
    return wrap(text, width=width, break_long_words=False, break_on_hyphens=False)


def component_specs() -> dict[str, dict[str, str]]:
    return {
        "db32_s40_full": {
            "layer": "sky + source-selection handoff",
            "operator": "O9 sky-only + O10 source/frame selection",
            "branch": "handoff-caveated",
            "source_action": "keep as caveated handoff; do not call fully source-faithful",
            "presentation_action": "allowed with generated-sky/source-sidestep caveats",
            "guard": "DB32 cannot become source-faithful ceiling or original-G repair",
        },
        "db34_source_preservation": {
            "layer": "low-risk source boundary",
            "operator": "O0 keep source pixels",
            "branch": "source-faithful",
            "source_action": "positive control for byte-exact non-core source pixels",
            "presentation_action": "not needed",
            "guard": "positive control does not upgrade full DB32 to source-faithful",
        },
        "db32_top_sky": {
            "layer": "out-of-FOV sky",
            "operator": "O9 sky-only generated fill",
            "branch": "presentation-only",
            "source_action": "not source-faithful; expose generated mask",
            "presentation_action": "allowed as sky-only generated region with mask",
            "guard": "sky-generation acceptance cannot transfer to ground/curb/lane",
        },
        "db32_s40_long_roi": {
            "layer": "source/frame selection",
            "operator": "O10 source/frame selection",
            "branch": "source-sidestep",
            "source_action": "valid only as source-sidestep, not local repair",
            "presentation_action": "show separately from classic-G diagnostics",
            "guard": "do not mix DB32 sidestep with original-G seam repair",
        },
        "db28_a200_source_long": {
            "layer": "source/frame selection",
            "operator": "O10 source/frame selection",
            "branch": "source-sidestep",
            "source_action": "candidate source base; no local seam repair claim",
            "presentation_action": "may support handoff lineage",
            "guard": "source selection must not be described as repaired seam",
        },
        "bevfinal_source_faithful": {
            "layer": "planar road + curb floor",
            "operator": "O4 BEV road atlas + O0 curb abstain",
            "branch": "source-faithful",
            "source_action": "allow BEV road only where planar-road evidence is valid",
            "presentation_action": "not needed",
            "guard": "BEV road does not make off-plane curb/right-line repairable",
        },
        "g_bmw_long": {
            "layer": "classic long source-boundary seam",
            "operator": "O0 keep/mark risk",
            "branch": "diagnostic-only",
            "source_action": "abstain from repair under current evidence",
            "presentation_action": "reference only; base choice requires separate brief",
            "guard": "G is diagnostic failure reference, not default repair base",
        },
        "g_bmw_right": {
            "layer": "right-line/curb/no-evidence",
            "operator": "O0 keep/abstain",
            "branch": "abstain",
            "source_action": "abstain unless new evidence changes DB41",
            "presentation_action": "presentation cleanup requires separate brief and masks",
            "guard": "right-line/curb cannot be repaired from current DB41 evidence",
        },
        "a1_view_none_right": {
            "layer": "object-adjacent seam",
            "operator": "O0 keep/mark risk",
            "branch": "diagnostic-only",
            "source_action": "diagnostic only; no accepted seam repair",
            "presentation_action": "possible base only after base-selection brief",
            "guard": "A1 smoother regions cannot override long/right seam failures",
        },
        "best_bmw_right": {
            "layer": "object/building ghost",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject as source repair/donor",
            "presentation_action": "do not use unless future brief re-proves donor safety",
            "guard": "donor ghosting is not a source-faithful improvement",
        },
        "db19_g_sky_only": {
            "layer": "sky-only generated region",
            "operator": "O9 sky-only generated fill",
            "branch": "presentation-only",
            "source_action": "not source-faithful; ground seam unchanged",
            "presentation_action": "allowed only with generated sky mask and ground caveat",
            "guard": "sky-only G output cannot become ground seam repair",
        },
        "db23_ground_core": {
            "layer": "generated ground/curb/lane",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject fake generated ground geometry",
            "presentation_action": "negative control only",
            "guard": "prompt-only ground outpaint remains blocked",
        },
        "db23_full_core": {
            "layer": "full generated scene",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject net-new scene/object content",
            "presentation_action": "negative generated-control only",
            "guard": "full outpaint cannot be Bosch training-data truth",
        },
        "db36_user_redline": {
            "layer": "right-line/curb generated seam",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject detector-clean fake slabs/holes",
            "presentation_action": "negative control only",
            "guard": "narrow prompt mask cannot bypass fake-geometry gate",
        },
        "db39_g_v14_raw": {
            "layer": "v14 generated right seam",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject vertical slice/pole-like artifact",
            "presentation_action": "negative v14 control only",
            "guard": "old v14 raw artifact cannot become default route",
        },
        "db39_best_v14_raw": {
            "layer": "v14 generated donor seam",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject ghost/slice donor path",
            "presentation_action": "negative v14 donor control only",
            "guard": "BEST donor cannot hide fake slice artifacts",
        },
        "db39_a1_v14_raw": {
            "layer": "v14 generated A1 seam",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject A1 vertical slice path",
            "presentation_action": "negative v14 A1 control only",
            "guard": "A1 v14 cannot reopen prompt-only ground seam",
        },
        "db40_keepout_core": {
            "layer": "object keepout diagnostic",
            "operator": "O7 object-aware ownership diagnostic",
            "branch": "diagnostic-only",
            "source_action": "use root-cause insight only; not final seam repair",
            "presentation_action": "possible mask-design evidence only",
            "guard": "keepout removing BMW slab does not solve long-source seam",
        },
        "db40_longsrc_raw": {
            "layer": "long-source generated seam",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject pole-like artifact despite object-gate pass",
            "presentation_action": "negative control only",
            "guard": "object gate pass is insufficient",
        },
        "db35_best_donor": {
            "layer": "right-line donor patch",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject unsafe donor blend",
            "presentation_action": "negative donor control only",
            "guard": "donor patch must not be patched-on-patch",
        },
        "db35_a1_donor": {
            "layer": "right-line donor patch",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject blur/slab donor blend",
            "presentation_action": "negative donor control only",
            "guard": "A1 donor softening is not line repair",
        },
        "db25_longline_evidence": {
            "layer": "low-support long-line evidence",
            "operator": "O0 evidence-only abstain",
            "branch": "evidence-only",
            "source_action": "keep/abstain; do not warp low-support dark-wall seam",
            "presentation_action": "diagnostic evidence only",
            "guard": "flow/LiDAR weakness blocks geometry warp",
        },
        "db24_source_boundary": {
            "layer": "source/camera-id boundary",
            "operator": "O0 explain/mark risk",
            "branch": "evidence-only",
            "source_action": "mark source-boundary risk; no blind production-style warp",
            "presentation_action": "diagnostic evidence only",
            "guard": "Google/Meta analogy requires evidence not present here",
        },
        "db26_photometric_smudge": {
            "layer": "photometric-only attempt",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject color wash/smudge path",
            "presentation_action": "negative low-frequency control only",
            "guard": "photometric polish cannot cross into smudge/no geometry fix",
        },
        "db41_right_roi": {
            "layer": "right-line/curb no-evidence",
            "operator": "O0 abstain",
            "branch": "abstain",
            "source_action": "RED abstain; no source-faithful repair",
            "presentation_action": "only a separately labeled presentation branch may edit",
            "guard": "DB41 right ROI must remain abstain",
        },
        "db41_lower_right": {
            "layer": "lower-right no-evidence near-ground",
            "operator": "O0 abstain",
            "branch": "abstain",
            "source_action": "RED abstain; zero-LiDAR target support",
            "presentation_action": "only a separately labeled presentation branch may edit",
            "guard": "DB41 lower-right must remain abstain",
        },
        "db30_sky_mask_preview": {
            "layer": "unsafe sky mask",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject mask that touches non-sky content",
            "presentation_action": "negative mask-control only",
            "guard": "sky branch must not touch buildings/vehicles/road",
        },
        "db33_s50_sky_halo": {
            "layer": "sky harmonization artifact",
            "operator": "O0 reject",
            "branch": "rejected",
            "source_action": "reject source-safe but visually worse sky halo",
            "presentation_action": "negative sky-polish control only",
            "guard": "source-safe numerically still needs vision gate",
        },
        "db31_nonbmw_montage": {
            "layer": "dataset/source selection failure",
            "operator": "O10 source/frame selection reject",
            "branch": "evidence-only",
            "source_action": "record rejected candidates; keep scan bounded",
            "presentation_action": "not a presentation base",
            "guard": "dataset mining must report failures, not cherry-pick",
        },
    }


def load_db43_cases() -> dict[str, dict]:
    if not DB43_MANIFEST.exists():
        raise FileNotFoundError(DB43_MANIFEST)
    manifest = json.loads(DB43_MANIFEST.read_text(encoding="utf-8"))
    if not manifest.get("gate_pass"):
        raise RuntimeError("DB43 gate must pass before DB44 can run")
    return {case["case_id"]: case for case in manifest["cases"]}


def load_db41_metrics() -> dict[str, dict]:
    if not DB41_MANIFEST.exists():
        return {}
    manifest = json.loads(DB41_MANIFEST.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for case_id, roi_id in {"db41_right_roi": "right_roi", "db41_lower_right": "lower_right_roi"}.items():
        summary = manifest.get("summaries", {}).get(roi_id, {})
        out[case_id] = {
            "roi_valid_frac": summary.get("roi_valid_frac"),
            "near_ground_frac": summary.get("near_ground_frac"),
            "lidar_support_frac": summary.get("lidar_support_frac"),
            "best_flow_pair": summary.get("best_flow_pair"),
            "best_flow_reliable_frac": summary.get("best_flow_reliable_frac"),
            "top_camera_labels": summary.get("top_camera_labels"),
            "passes_db41_gate": manifest.get("threshold_results", {}).get(roi_id, {}).get("passes_db41_gate"),
            "recommendation": summary.get("recommendation"),
        }
    return out


def protected_structures_for(case: dict, spec: dict[str, str]) -> list[str]:
    text = " ".join(
        [
            case["segment_type"],
            spec["layer"],
            " ".join(case["reason_codes"]),
            case["vision_verdict"],
        ]
    ).lower()
    structures: list[str] = []
    for key, label in [
        ("object", "object"),
        ("bmw", "vehicle"),
        ("car", "vehicle"),
        ("wheel", "wheel"),
        ("person", "person"),
        ("lane", "lane_marking"),
        ("line", "lane_or_right_line"),
        ("curb", "curb"),
        ("pole", "pole"),
        ("sign", "sign"),
        ("road", "road_topology"),
        ("facade", "facade"),
        ("building", "building"),
    ]:
        if key in text and label not in structures:
            structures.append(label)
    return structures


def kill_ids_for(case_id: str, case: dict, spec: dict[str, str]) -> list[str]:
    ids = ["manifest_completeness"]
    if case["evidence_state"] == "RED":
        ids.append("no_red_region_repair")
    if case_id == "db41_right_roi":
        ids.extend(["db41_right_roi_abstains", "protected_topology_guard"])
    if case_id == "db41_lower_right":
        ids.extend(["db41_lower_right_abstains", "protected_topology_guard"])
    if case_id == "db32_s40_full":
        ids.append("db32_not_fully_source_faithful")
    if case_id.startswith("g_bmw"):
        ids.append("g_is_diagnostic_only")
    if any(code in case["reason_codes"] for code in ["fake_road", "fake_curb", "fake_lane", "vertical_slice", "pole_like_artifact"]):
        ids.append("fake_geometry_controls_reject")
    if "generated_region" in case["reason_codes"]:
        ids.extend(["no_diffusion_source_branch", "no_ground_curb_lane_rightline_generation"])
    if "LPAM" in spec["operator"]:
        ids.append("lpam_green_only")
    if case_id in {"db34_source_preservation", "db32_s40_full", "db28_a200_source_long"}:
        ids.append("clean_control_no_overedit")
    return list(dict.fromkeys(ids))


def comparison_refs_for(case_id: str) -> list[str]:
    refs = {
        "db32_s40_full": ["db34_source_preservation", "db42_seam_decision_handoff"],
        "db32_s40_long_roi": ["g_bmw_long", "db28_a200_source_long"],
        "db28_a200_source_long": ["db32_s40_long_roi", "db31_nonbmw_montage"],
        "bevfinal_source_faithful": ["g_bmw_long", "db41_right_roi"],
        "g_bmw_long": ["db32_s40_long_roi", "db24_source_boundary", "db25_longline_evidence"],
        "g_bmw_right": ["db41_right_roi", "db41_lower_right", "a1_view_none_right", "best_bmw_right"],
        "a1_view_none_right": ["g_bmw_right", "db40_keepout_core", "db40_longsrc_raw"],
        "best_bmw_right": ["g_bmw_right", "db35_best_donor"],
        "db23_ground_core": ["db36_user_redline", "db39_g_v14_raw", "db40_longsrc_raw"],
        "db36_user_redline": ["db23_ground_core", "db41_lower_right"],
        "db39_g_v14_raw": ["g_bmw_right", "db40_longsrc_raw"],
        "db39_a1_v14_raw": ["a1_view_none_right", "db40_keepout_core"],
        "db40_keepout_core": ["a1_view_none_right", "db40_longsrc_raw"],
        "db40_longsrc_raw": ["db40_keepout_core", "db39_g_v14_raw"],
        "db41_right_roi": ["g_bmw_right", "db41_lower_right"],
        "db41_lower_right": ["g_bmw_right", "db41_right_roi"],
        "db30_sky_mask_preview": ["db32_top_sky", "db33_s50_sky_halo"],
        "db33_s50_sky_halo": ["db32_top_sky", "db32_s40_full"],
    }
    return refs.get(case_id, [])


def build_components(cases: dict[str, dict]) -> list[Component]:
    specs = component_specs()
    db41_metrics = load_db41_metrics()
    missing_specs = sorted(set(cases) - set(specs))
    if missing_specs:
        raise RuntimeError(f"DB44 specs missing DB43 cases: {missing_specs}")
    components: list[Component] = []
    for case_id, case in cases.items():
        spec = specs[case_id]
        components.append(
            Component(
                component_id=f"db44_{case_id}",
                source_case_id=case_id,
                title=case["title"],
                artifact=case["artifact"],
                roi_xyxy=case.get("roi_xyxy"),
                layer=spec["layer"],
                segment_type=case["segment_type"],
                evidence_state=case["evidence_state"],
                dispatch_operator=spec["operator"],
                operator_executed=False,
                allowed_branch=spec["branch"],
                claim_level=case["claim_label"],
                source_faithful_action=spec["source_action"],
                presentation_action=spec["presentation_action"],
                protected_structures=protected_structures_for(case, spec),
                evidence_metrics=db41_metrics.get(case_id, {}),
                comparison_refs=comparison_refs_for(case_id),
                generated_mask_required=(
                    "generated_region" in case["reason_codes"]
                    or spec["branch"] in {"presentation-only", "handoff-caveated"}
                ),
                unknown_or_abstain_mask_required=(
                    case["evidence_state"] == "RED"
                    or spec["branch"] in {"abstain", "evidence-only", "diagnostic-only"}
                ),
                kill_check_ids=kill_ids_for(case_id, case, spec),
                reason_codes=list(dict.fromkeys(case["reason_codes"] + [spec["guard"]])),
                kill_guard=spec["guard"],
                vision_check=case["vision_verdict"],
            )
        )
    return components


def validate_components(components: list[Component]) -> list[dict]:
    by_case = {c.source_case_id: c for c in components}
    checks = [
        {
            "name": "DB41 right-line/lower-right remain RED abstain",
            "pass": all(
                by_case[cid].evidence_state == "RED"
                and by_case[cid].allowed_branch == "abstain"
                and "abstain" in by_case[cid].dispatch_operator.lower()
                for cid in ("db41_right_roi", "db41_lower_right")
            ),
        },
        {
            "name": "No RED component is assigned a repair operator",
            "pass": all(
                not (
                    c.evidence_state == "RED"
                    and not (
                        "O0" in c.dispatch_operator
                        or c.allowed_branch in {"diagnostic-only", "rejected", "abstain"}
                    )
                )
                for c in components
            ),
        },
        {
            "name": "DB32 full is caveated handoff, not fully source-faithful",
            "pass": by_case["db32_s40_full"].allowed_branch == "handoff-caveated"
            and by_case["db32_s40_full"].claim_level == "caveated-handoff",
        },
        {
            "name": "G_bmw_pano is diagnostic only, not default repair base",
            "pass": by_case["g_bmw_long"].allowed_branch == "diagnostic-only"
            and by_case["g_bmw_right"].allowed_branch == "abstain",
        },
        {
            "name": "Generated ground/curb/lane/right-line cases reject",
            "pass": all(
                by_case[cid].allowed_branch == "rejected"
                for cid in (
                    "db23_ground_core",
                    "db36_user_redline",
                    "db39_g_v14_raw",
                    "db39_a1_v14_raw",
                    "db40_longsrc_raw",
                )
            ),
        },
        {
            "name": "Sky generation is presentation-only or handoff-caveated",
            "pass": all(
                by_case[cid].allowed_branch in {"presentation-only", "handoff-caveated", "rejected"}
                for cid in ("db32_top_sky", "db19_g_sky_only", "db30_sky_mask_preview", "db33_s50_sky_halo")
            ),
        },
        {
            "name": "LPAM is not executed in DB44 dry run",
            "pass": all("LPAM" not in c.dispatch_operator for c in components),
        },
        {
            "name": "No DB44 operator executes in dry run",
            "pass": all(c.operator_executed is False for c in components),
        },
        {
            "name": "Manifest completeness for every component",
            "pass": all(
                c.layer
                and c.evidence_state
                and c.dispatch_operator
                and c.claim_level
                and c.reason_codes
                and c.kill_check_ids
                for c in components
            ),
        },
        {
            "name": "Component count is within DB44 max scope",
            "pass": 20 <= len(components) <= 30,
        },
    ]
    return checks


def draw_tile(component: Component, size: tuple[int, int]) -> Image.Image:
    w, h = size
    tile = Image.new("RGB", size, (24, 24, 24))
    d = ImageDraw.Draw(tile)
    evidence_color = EVIDENCE_COLORS[component.evidence_state]
    branch_color = BRANCH_COLORS[component.allowed_branch]
    d.rectangle([0, 0, w - 1, h - 1], outline=(70, 70, 70), width=1)
    d.rectangle([0, 0, w - 1, 28], fill=evidence_color)
    d.rectangle([0, 28, w - 1, 52], fill=branch_color)
    d.text((8, 5), f"{component.evidence_state} | {component.allowed_branch}", fill=(255, 255, 255), font=font(13))
    d.text((8, 32), component.source_case_id, fill=(255, 255, 255), font=font(12))
    img = fit(load_img(component.artifact), (w, 142))
    tile.paste(img, (0, 54))
    y = 202
    d.text((8, y), component.layer[:42], fill=(255, 240, 190), font=font(12))
    y += 18
    for line in text_lines(component.dispatch_operator, 38)[:2]:
        d.text((8, y), line, fill=(225, 225, 225), font=font(11))
        y += 15
    for line in text_lines(component.source_faithful_action, 42)[:3]:
        d.text((8, y), line, fill=(205, 205, 205), font=font(10))
        y += 13
    return tile


def make_dispatch_board(components: list[Component]) -> None:
    cols = 5
    tile_size = (350, 300)
    margin = 18
    header_h = 94
    rows = (len(components) + cols - 1) // cols
    board = Image.new(
        "RGB",
        (cols * tile_size[0] + (cols + 1) * margin, header_h + rows * tile_size[1] + (rows + 1) * margin),
        (16, 16, 16),
    )
    d = ImageDraw.Draw(board)
    d.text((18, 16), "DB44 layer-aware EGSR dispatcher dry run", fill=(255, 255, 255), font=font(28))
    d.text(
        (18, 52),
        "Existing artifacts only. No new panorama repair. RED regions abstain/reject; YELLOW regions are caveated; GREEN keeps source.",
        fill=(220, 220, 220),
        font=font(15),
    )
    for idx, component in enumerate(components):
        row, col = divmod(idx, cols)
        x = margin + col * (tile_size[0] + margin)
        y = header_h + margin + row * (tile_size[1] + margin)
        board.paste(draw_tile(component, tile_size), (x, y))
    board.save(DISPATCH_BOARD, quality=92)


def make_bmw_board(components: list[Component]) -> None:
    by_case = {c.source_case_id: c for c in components}
    selected = [
        "g_bmw_long",
        "g_bmw_right",
        "db32_s40_long_roi",
        "bevfinal_source_faithful",
        "db41_right_roi",
        "db41_lower_right",
        "db40_keepout_core",
        "db40_longsrc_raw",
        "db35_best_donor",
        "db35_a1_donor",
        "db23_ground_core",
        "db36_user_redline",
    ]
    cols = 4
    tile_size = (430, 330)
    margin = 20
    header_h = 100
    rows = (len(selected) + cols - 1) // cols
    board = Image.new(
        "RGB",
        (cols * tile_size[0] + (cols + 1) * margin, header_h + rows * tile_size[1] + (rows + 1) * margin),
        (16, 16, 16),
    )
    d = ImageDraw.Draw(board)
    d.text((20, 18), "DB44 BMW classic seam dispatch controls", fill=(255, 255, 255), font=font(28))
    d.text(
        (20, 56),
        "G is diagnostic only. DB41 is RED/abstain. Donor/v14/DiT ground/right-line variants reject. DB32 is separate source-sidestep.",
        fill=(220, 220, 220),
        font=font(15),
    )
    for idx, case_id in enumerate(selected):
        row, col = divmod(idx, cols)
        x = margin + col * (tile_size[0] + margin)
        y = header_h + margin + row * (tile_size[1] + margin)
        board.paste(draw_tile(by_case[case_id], tile_size), (x, y))
    board.save(BMW_BOARD, quality=92)


def make_layer_board(components: list[Component]) -> None:
    by_case = {c.source_case_id: c for c in components}
    selected = [
        "db34_source_preservation",
        "db32_s40_full",
        "db32_top_sky",
        "db28_a200_source_long",
        "bevfinal_source_faithful",
        "g_bmw_long",
        "g_bmw_right",
        "db25_longline_evidence",
        "db24_source_boundary",
        "db41_right_roi",
        "db41_lower_right",
        "db31_nonbmw_montage",
    ]
    cols = 4
    tile_size = (430, 330)
    margin = 20
    header_h = 106
    rows = (len(selected) + cols - 1) // cols
    board = Image.new(
        "RGB",
        (cols * tile_size[0] + (cols + 1) * margin, header_h + rows * tile_size[1] + (rows + 1) * margin),
        (16, 16, 16),
    )
    d = ImageDraw.Draw(board)
    d.text((20, 18), "DB44 layer/evidence controls", fill=(255, 255, 255), font=font(28))
    d.text(
        (20, 56),
        "GREEN keeps source; YELLOW is caveated; RED abstains/rejects. This board is a label overlay, not a repaired ERP.",
        fill=(220, 220, 220),
        font=font(15),
    )
    for idx, case_id in enumerate(selected):
        row, col = divmod(idx, cols)
        x = margin + col * (tile_size[0] + margin)
        y = header_h + margin + row * (tile_size[1] + margin)
        board.paste(draw_tile(by_case[case_id], tile_size), (x, y))
    board.save(LAYER_BOARD, quality=92)


def make_negative_board(components: list[Component]) -> None:
    by_case = {c.source_case_id: c for c in components}
    selected = [
        "db23_ground_core",
        "db23_full_core",
        "db36_user_redline",
        "db39_g_v14_raw",
        "db39_best_v14_raw",
        "db39_a1_v14_raw",
        "db40_longsrc_raw",
        "db35_best_donor",
        "db35_a1_donor",
        "db30_sky_mask_preview",
        "db33_s50_sky_halo",
        "db26_photometric_smudge",
    ]
    cols = 4
    tile_size = (430, 330)
    margin = 20
    header_h = 100
    rows = (len(selected) + cols - 1) // cols
    board = Image.new(
        "RGB",
        (cols * tile_size[0] + (cols + 1) * margin, header_h + rows * tile_size[1] + (rows + 1) * margin),
        (16, 16, 16),
    )
    d = ImageDraw.Draw(board)
    d.text((20, 18), "DB44 locked negative controls", fill=(255, 255, 255), font=font(28))
    d.text(
        (20, 56),
        "Detector-clean or source-safe-looking outputs can still fail visually. These controls must stay rejected.",
        fill=(220, 220, 220),
        font=font(15),
    )
    for idx, case_id in enumerate(selected):
        row, col = divmod(idx, cols)
        x = margin + col * (tile_size[0] + margin)
        y = header_h + margin + row * (tile_size[1] + margin)
        board.paste(draw_tile(by_case[case_id], tile_size), (x, y))
    board.save(NEGATIVE_BOARD, quality=92)


def make_matrix_board(components: list[Component], checks: list[dict]) -> None:
    layer_counts: dict[str, Counter] = defaultdict(Counter)
    operator_counts: Counter = Counter()
    branch_counts: Counter = Counter()
    evidence_counts: Counter = Counter()
    for c in components:
        layer_counts[c.layer][c.evidence_state] += 1
        operator_counts[c.dispatch_operator.split(" + ")[0]] += 1
        branch_counts[c.allowed_branch] += 1
        evidence_counts[c.evidence_state] += 1

    board = Image.new("RGB", (1800, 2300), (18, 18, 18))
    d = ImageDraw.Draw(board)
    d.text((22, 18), "DB44 operator/evidence matrix", fill=(255, 255, 255), font=font(28))
    d.text(
        (22, 56),
        "Dispatcher objective: choose permission and weakest sufficient operator, not maximum visual smoothness.",
        fill=(220, 220, 220),
        font=font(15),
    )

    y = 98
    d.text((22, y), "Evidence counts", fill=(255, 240, 190), font=font(20))
    x = 22
    y += 34
    for state in ("GREEN", "YELLOW", "RED"):
        d.rectangle([x, y, x + 170, y + 56], fill=EVIDENCE_COLORS[state])
        d.text((x + 12, y + 12), f"{state}: {evidence_counts[state]}", fill=(255, 255, 255), font=font(18))
        x += 190

    y += 92
    d.text((22, y), "Branch counts", fill=(255, 240, 190), font=font(20))
    y += 34
    x = 22
    for branch, count in sorted(branch_counts.items()):
        d.rectangle([x, y, x + 245, y + 48], fill=BRANCH_COLORS[branch])
        d.text((x + 10, y + 12), f"{branch}: {count}", fill=(255, 255, 255), font=font(14))
        x += 260
        if x > 1500:
            x = 22
            y += 62

    y += 84
    d.text((22, y), "Layer -> evidence-state counts", fill=(255, 240, 190), font=font(20))
    y += 34
    d.rectangle([22, y, 1760, y + 34], fill=(42, 42, 42))
    d.text((32, y + 8), "Layer", fill=(255, 255, 255), font=font(14))
    d.text((820, y + 8), "GREEN", fill=(255, 255, 255), font=font(14))
    d.text((970, y + 8), "YELLOW", fill=(255, 255, 255), font=font(14))
    d.text((1120, y + 8), "RED", fill=(255, 255, 255), font=font(14))
    y += 38
    for layer, counts in sorted(layer_counts.items()):
        d.rectangle([22, y, 1760, y + 30], fill=(26, 26, 26), outline=(52, 52, 52))
        d.text((32, y + 7), layer[:88], fill=(230, 230, 230), font=font(12))
        d.text((835, y + 7), str(counts["GREEN"]), fill=(210, 255, 220), font=font(12))
        d.text((990, y + 7), str(counts["YELLOW"]), fill=(255, 240, 190), font=font(12))
        d.text((1135, y + 7), str(counts["RED"]), fill=(255, 205, 205), font=font(12))
        y += 32

    y += 26
    d.text((22, y), "Kill checks", fill=(255, 240, 190), font=font(20))
    y += 34
    for check in checks:
        color = (50, 120, 70) if check["pass"] else (160, 35, 35)
        d.rectangle([22, y, 1760, y + 42], fill=color)
        d.text((34, y + 11), f"{'PASS' if check['pass'] else 'FAIL'} - {check['name']}", fill=(255, 255, 255), font=font(14))
        y += 50

    y += 16
    d.text((22, y), "Operator first-token counts", fill=(255, 240, 190), font=font(20))
    y += 34
    for op, count in sorted(operator_counts.items()):
        d.text((34, y), f"{op}: {count}", fill=(225, 225, 225), font=font(13))
        y += 24
    board.save(MATRIX_BOARD, quality=92)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = load_db43_cases()
    components = build_components(cases)
    for component in components:
        if not (ROOT / component.artifact).exists():
            raise FileNotFoundError(component.artifact)

    checks = validate_components(components)
    gate_pass = all(check["pass"] for check in checks)
    make_dispatch_board(components)
    make_bmw_board(components)
    make_layer_board(components)
    make_negative_board(components)
    make_matrix_board(components, checks)

    manifest = {
        "db": "DB-44",
        "status": "accepted_dry_run_gate" if gate_pass else "failed_kill_check",
        "purpose": "Layer-aware EGSR dispatcher dry run over DB43 known cases.",
        "scope": {
            "cpu_only": True,
            "existing_artifacts_only": True,
            "new_panorama_generation": False,
            "diffusion_or_prompt_sweep": False,
            "a100_used": False,
            "component_count": len(components),
        },
        "outputs": {
            "manifest": str(MANIFEST.relative_to(ROOT)),
            "dispatcher_board": str(DISPATCH_BOARD.relative_to(ROOT)),
            "bmw_roi_dispatch_board": str(BMW_BOARD.relative_to(ROOT)),
            "layer_evidence_board": str(LAYER_BOARD.relative_to(ROOT)),
            "negative_controls_board": str(NEGATIVE_BOARD.relative_to(ROOT)),
            "operator_matrix_board": str(MATRIX_BOARD.relative_to(ROOT)),
        },
        "refs": {
            "db43_gate_ref": str(DB43_MANIFEST.relative_to(ROOT)),
            "db41_evidence_ref": str(DB41_MANIFEST.relative_to(ROOT)),
            "db42_handoff_ref": "deliverables/dit360_v2/db42_seam_decision_handoff/db42_seam_decision_handoff_manifest.json",
        },
        "dispatch_policy": {
            "GREEN": "source-faithful keep/source-only actions allowed when raw/source evidence supports them",
            "YELLOW": "caveated source-only/source-sidestep/photometric/sky actions only with explicit masks or caveats",
            "RED": "abstain or reject in source-faithful branch; no repair in DB44",
            "LPAM": "not executed in DB44; future use only as a gated GREEN far/static sub-operator",
            "DiT_FLUX": "not allowed for ground/curb/lane/right-line source-faithful repair",
        },
        "components": [asdict(c) for c in components],
        "counts": {
            "evidence_state": dict(Counter(c.evidence_state for c in components)),
            "allowed_branch": dict(Counter(c.allowed_branch for c in components)),
            "claim_level": dict(Counter(c.claim_level for c in components)),
        },
        "kill_checks": checks,
        "gate_pass": gate_pass,
        "next_recommendation": (
            "DB44 can be archived as dispatcher v0. A follow-up DB44 extension may implement a source-faithful operator "
            "only on GREEN/YELLOW non-RED segments with explicit raw/source evidence; DB45 evidence audit is the safer next main step "
            "if the question is whether any RED seam can become YELLOW/GREEN."
        ),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {MANIFEST.relative_to(ROOT)}")
    print(f"gate_pass={gate_pass} components={len(components)}")


if __name__ == "__main__":
    main()
