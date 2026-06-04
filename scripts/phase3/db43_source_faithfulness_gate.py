#!/usr/bin/env python
"""Build DB43 source-faithfulness / fake-geometry gate artifacts.

DB43 is a gate and triage pass over existing artifacts only. It does not
generate a new panorama and does not run model inference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "deliverables" / "dit360_v2" / "db43_source_faithfulness_gate"
BOARD = OUT_DIR / "db43_known_case_board.jpg"
ROI_BOARD = OUT_DIR / "db43_canonical_roi_board.jpg"
RECT_BOARD = OUT_DIR / "db43_rectilinear_review_board.jpg"
SUMMARY_BOARD = OUT_DIR / "db43_reason_code_summary.jpg"
MANIFEST = OUT_DIR / "db43_source_faithfulness_gate_manifest.json"


LABEL_COLORS = {
    "source-faithful": (45, 120, 70),
    "caveated-handoff": (110, 100, 35),
    "source-sidestep": (95, 90, 45),
    "presentation-only": (75, 80, 130),
    "generated": (70, 75, 145),
    "diagnostic": (80, 80, 80),
    "abstain": (125, 65, 25),
    "reject": (140, 45, 45),
}


@dataclass(frozen=True)
class Case:
    case_id: str
    title: str
    artifact: str
    claim_label: str
    segment_type: str
    evidence_state: str
    branch: str
    operator: str
    reason_codes: list[str]
    vision_verdict: str
    next_action: str
    board_note: str
    roi_xyxy: tuple[int, int, int, int] | None = None
    source_artifact: str | None = None


CASES: list[Case] = [
    Case(
        "db32_s40_full",
        "DB32 s40 current handoff",
        "deliverables/dit360_v2/db32_generated_sky_harmonize_v2/db32_generated_sky_harmonize_s40.png",
        "caveated-handoff",
        "T8 sky + T12 source-sidestep",
        "YELLOW",
        "Bosch-facing handoff",
        "O9 sky-only + O10 source/frame selection",
        ["caveated_handoff", "source_sidestep", "sky_generated_caveat", "generated_region", "not_original_g_repair"],
        "Use as Bosch-facing presentation/handoff candidate with caveats; not fully source-faithful and not a G/A1/BEST repair.",
        "Keep as current handoff candidate; expose generated sky/source-sidestep masks in later DB49.",
        "handoff candidate",
    ),
    Case(
        "db34_source_preservation",
        "DB34 DB32 source preservation QA",
        "deliverables/dit360_v2/db34_current_best_qa/db34_current_best_review_board.jpg",
        "source-faithful",
        "T0 non-core source preservation",
        "GREEN",
        "source-preservation QA",
        "O0 keep source pixels",
        ["source_preserved", "noncore_byte_exact", "positive_control"],
        "Positive control for DB32 non-core source pixels: buildings, road, vehicles, and skyline are byte-exact outside generated sky core.",
        "Use as source-preservation control only; it does not make the full DB32 panorama fully source-faithful.",
        "non-core exact",
    ),
    Case(
        "db32_top_sky",
        "DB32 top sky generated/captured mix",
        "deliverables/dit360_v2/db32_generated_sky_harmonize_v2/db32_top_montage.jpg",
        "presentation-only",
        "T8 generated sky",
        "YELLOW",
        "generated sky review",
        "O9 sky-only diffusion/harmonization",
        ["generated_region", "sky_generated_caveat", "residual_sky_panel", "not_ground_truth"],
        "Generated sky can be accepted only with explicit caveat and mask; residual captured-sky panel remains visible.",
        "Keep as sky-only/generated control; do not transfer acceptance logic to ground/curb/lane.",
        "sky caveat",
    ),
    Case(
        "db32_s40_long_roi",
        "DB32 long seam ROI",
        "deliverables/dit360_v2/db35_seam_first/DB32_s40_current_long_roi.jpg",
        "source-sidestep",
        "T12 source/frame candidate",
        "YELLOW",
        "Bosch-facing handoff",
        "O10 source/frame selection",
        ["source_sidestep", "not_original_g_repair"],
        "Cleaner than classic G because the source base changed; cannot be claimed as a repaired original-G seam.",
        "Use only with source-sidestep language.",
        "source changed",
    ),
    Case(
        "db28_a200_source_long",
        "DB28 a200 source long ROI",
        "deliverables/dit360_v2/db35_seam_first/DB28_a200_source_long_roi.jpg",
        "source-sidestep",
        "T12 source/frame candidate",
        "YELLOW",
        "source selection",
        "O10 source/frame selection",
        ["source_sidestep", "candidate_not_final"],
        "Accepted as better source candidate; still not proof that a local seam was repaired.",
        "Feed only source-selection or data-contract branches.",
        "source base",
    ),
    Case(
        "bevfinal_source_faithful",
        "BEV/seamroute source-faithful ceiling",
        "deliverables/dit360_v2/db23_gate_fetch/seamroute/SR_bmw_bevfinal_1024x2048.png",
        "source-faithful",
        "T3 planar road + T5 curb floor",
        "YELLOW",
        "source-faithful",
        "O4 BEV road atlas + source-only seamroute",
        ["source_faithful", "curb_floor", "out_of_fov_caveat"],
        "Source-faithful ceiling for the current BMW-style route: road can improve, curb/out-of-FOV remains bounded.",
        "Use as positive/caveated control for DB44, not as proof that DB41 right-line is repairable.",
        "source ceiling",
    ),
    Case(
        "g_bmw_long",
        "G_bmw_pano long seam",
        "deliverables/dit360_v2/db35_seam_first/G_bmw_pano_long_roi.jpg",
        "diagnostic",
        "T1/T2/T5 classic BMW seam",
        "RED",
        "diagnostic only",
        "O0 keep/mark risk",
        ["classic_bmw_failure_reference", "not_default_repair_base", "source_boundary"],
        "Classic BMW failure / diagnostic reference; visually judged unsuitable as default repair base.",
        "Do not use as default base; only compare against it.",
        "failure ref",
    ),
    Case(
        "g_bmw_right",
        "G_bmw_pano right-line",
        "deliverables/dit360_v2/db35_seam_first/G_bmw_pano_right_roi.jpg",
        "diagnostic",
        "T4/T5/T10 right line/curb",
        "RED",
        "diagnostic only",
        "O0 keep/abstain",
        ["classic_bmw_failure_reference", "right_line_failure", "no_source_evidence_pending_db41"],
        "Right-line/curb region remains visibly wrong and is not repairable under current DB41 evidence.",
        "Abstain unless a new evidence brief changes DB41.",
        "right-line fail",
    ),
    Case(
        "a1_view_none_right",
        "A1_view_none right ROI",
        "deliverables/dit360_v2/db35_seam_first/A1_view_none_right_roi.jpg",
        "diagnostic",
        "T6/T7 object-adjacent seam",
        "RED",
        "diagnostic only",
        "O0 keep/mark risk",
        ["diagnostic_only", "parallax_residual", "not_final"],
        "Smoother in places but still not accepted as long/right seam repair.",
        "Use for mask-alignment diagnosis only.",
        "diagnostic",
    ),
    Case(
        "best_bmw_right",
        "BEST right ROI",
        "deliverables/dit360_v2/db35_seam_first/BEST_bmw_pano_right_roi.jpg",
        "reject",
        "T6/T7 object/building ghost",
        "RED",
        "rejected repair/donor",
        "O0 reject",
        ["ghosting", "unsafe_donor", "not_source_faithful"],
        "Ghosted source/donor candidate; unsafe as production repair source.",
        "Do not use as donor unless a future brief proves otherwise.",
        "ghost donor",
    ),
    Case(
        "db19_g_sky_only",
        "DB19 G sky-only",
        "deliverables/dit360_v2/db19_G_bmw_pano_sky_t50_s0_postcompose_thr45.png",
        "presentation-only",
        "T8 generated sky",
        "YELLOW",
        "presentation / generated sky",
        "O9 sky-only diffusion",
        ["generated_region", "sky_generated_caveat", "ground_seam_unchanged"],
        "Sky-only generated output can be useful, but classic G ground/right seam remains.",
        "Keep generated sky masked; do not claim seam repair.",
        "sky only",
    ),
    Case(
        "db23_ground_core",
        "DB23 ground outpaint",
        "deliverables/dit360_v2/db23_d4b_fetch/ground_t50_s0/ground_t50_s0_corecompose.png",
        "reject",
        "T9 generated ground",
        "RED",
        "rejected generated",
        "O0 reject",
        ["fake_road", "fake_lane", "fake_curb", "generated_region", "detector_clean_but_geometry_fake"],
        "Detector-clean output invents lower road/lane/curb geometry; not Bosch training data.",
        "Do not reopen prompt-only ground outpaint.",
        "fake ground",
    ),
    Case(
        "db23_full_core",
        "DB23 full outpaint",
        "deliverables/dit360_v2/db23_d4b_fetch/full_t50_s0/full_t50_s0_corecompose.png",
        "reject",
        "T8/T9 full generated out-of-FOV",
        "RED",
        "rejected generated",
        "O0 reject",
        ["generated_region", "net_new_object", "traffic_light", "fake_scene"],
        "Full outpaint fails object gate and generates broad non-source scene content.",
        "Keep as negative generated-control only.",
        "net-new object",
    ),
    Case(
        "db36_user_redline",
        "DB36 user red-line DiT",
        "deliverables/dit360_v2/db36_user_redline_mask/G_bmw_pano_user_redline_tau5_fetch/G_bmw_pano_user_redline_tau5/db36_user_redline_tau5/db36_user_redline_tau5_corecompose.png",
        "reject",
        "T4/T5 ground/curb/right-line",
        "RED",
        "rejected generated repair",
        "O0 reject",
        ["fake_road", "fake_curb", "fake_slab", "black_hole_or_patchy_hole", "detector_clean_but_geometry_fake", "outside_mask_ok_but_core_fake"],
        "Ultra-narrow mask preserved outside pixels and passed object gate, but generated fake ground slabs/holes.",
        "Do not tune prompt-only right-line/curb masks.",
        "fake slabs",
    ),
    Case(
        "db39_g_v14_raw",
        "DB39 G v14 raw",
        "deliverables/dit360_v2/db14_g_bmw_pano_fetch/G_bmw_pano/g_r008_h016_w025_tau5/g_r008_h016_w025_tau5_raw.png",
        "reject",
        "T4/T5 v14 seam",
        "RED",
        "rejected generated repair",
        "O0 reject",
        ["vertical_slice", "pole_like_artifact", "detector_clean_but_geometry_fake", "not_source_faithful"],
        "Existing exact v14 G replay introduces a right-side vertical generated slice/pole-like artifact.",
        "Do not rerun same v14 matrix without new source/depth constraints.",
        "v14 slice",
    ),
    Case(
        "db39_best_v14_raw",
        "DB39 BEST v14 raw",
        "deliverables/dit360_v2/db14_best_bmw_pano_fetch/BEST_bmw_pano/best_r008_h016_w025_tau5/best_r008_h016_w025_tau5_raw.png",
        "reject",
        "T6/T7 donor ghost + v14 seam",
        "RED",
        "rejected generated repair",
        "O0 reject",
        ["ghosting", "vertical_slice", "fake_slab", "net_new_object"],
        "Inherits BEST ghosting and adds slab/slice artifacts; object gate fails.",
        "Keep as negative donor/v14 control.",
        "ghost + slab",
    ),
    Case(
        "db39_a1_v14_raw",
        "DB39 A1 v14 raw",
        "deliverables/dit360_v2/db14_a1_view_none_fetch/A1_view_none_bmw/a1view_r008_h016_w025_tau5/a1view_r008_h016_w025_tau5_raw.png",
        "reject",
        "T6/T7 object-adjacent v14 seam",
        "RED",
        "rejected generated repair",
        "O0 reject",
        ["vertical_slice", "object_shape_changed", "mask_source_mismatch"],
        "Turns A1 right seam into a visible vertical slice; supports DB40 mask/source mismatch diagnosis.",
        "Use for diagnosis only.",
        "A1 slice",
    ),
    Case(
        "db40_keepout_core",
        "DB40 A1 keepout",
        "deliverables/dit360_v2/db40_v14_mask_alignment/a1_keepout_strict_fetch/a1_keepout_strict/a1_keepout_strict_tau5/a1_keepout_strict_tau5_corecompose.png",
        "diagnostic",
        "T6/T7 object keepout",
        "YELLOW",
        "diagnostic only",
        "O0 keep/diagnose",
        ["root_cause_supported", "partial_pass", "vertical_edit_bands", "not_final"],
        "Explains and locally reduces the A1 right-BMW slab, but leaves visible vertical edit bands.",
        "Use as root-cause evidence, not final repair.",
        "partial only",
    ),
    Case(
        "db40_longsrc_raw",
        "DB40 A1 longsrc raw",
        "deliverables/dit360_v2/db40_v14_mask_alignment/a1_longsrc_only_fetch/a1_longsrc_only_tau5/a1_longsrc_only_tau5_raw.png",
        "reject",
        "T1/T2 long-source seam",
        "RED",
        "rejected generated repair",
        "O0 reject",
        ["pole_like_artifact", "vertical_slice", "detector_clean_but_geometry_fake"],
        "Narrower long-source mask produces a conspicuous pole-like vertical object despite object-gate PASS.",
        "Stop v14 A1/G route unless new evidence appears.",
        "pole artifact",
    ),
    Case(
        "db35_best_donor",
        "DB35 BEST donor patch",
        "deliverables/dit360_v2/db35_seam_first/db35_rightline_best_donor_patch.png",
        "reject",
        "T4/T5 donor right-line",
        "RED",
        "rejected donor repair",
        "O0 reject",
        ["unsafe_donor", "ghosting", "minimal_improvement"],
        "BEST donor barely changes the line and carries ghost risk.",
        "Do not use BEST donor patching as production repair.",
        "donor fail",
    ),
    Case(
        "db35_a1_donor",
        "DB35 A1 donor patch",
        "deliverables/dit360_v2/db35_seam_first/db35_rightline_a1_donor_patch.png",
        "reject",
        "T4/T5 donor right-line",
        "RED",
        "rejected donor repair",
        "O0 reject",
        ["blur", "soft_ground", "not_source_faithful"],
        "A1 donor softens/blurs lower-right ground and still does not straighten the seam.",
        "Do not use A1 donor patching as production repair.",
        "donor blur",
    ),
    Case(
        "db25_longline_evidence",
        "DB25 long-line evidence",
        "deliverables/dit360_v2/db25_longline_evidence_fetch/db25_longline_evidence_montage.jpg",
        "abstain",
        "T10 low-support dark-wall/near-ground",
        "RED",
        "evidence-only",
        "O0 abstain",
        ["no_source_evidence", "low_lidar_support", "low_key_pair_flow", "low_flow_reliability", "multi_camera_source_boundary", "near_ground_low_texture", "unknown_or_abstain", "red_region_no_repair"],
        "Long-line ROI lacks reliable full-line correspondence; geometry warp should abstain.",
        "Do not repair without stronger raw/depth/correspondence evidence.",
        "abstain evidence",
    ),
    Case(
        "db24_source_boundary",
        "DB24 long-line source-boundary diagnosis",
        "deliverables/dit360_v2/db24_google_meta_line_diag/db24_longline_source_diag_montage.jpg",
        "abstain",
        "T10 source-boundary low-evidence seam",
        "RED",
        "evidence-only",
        "O0 abstain",
        ["source_boundary", "multi_camera_source_boundary", "near_ground_low_texture", "production_overlap_missing", "unknown_or_abstain", "red_region_no_repair"],
        "Explains why Google/Meta-style repair is blocked: the line is a source/camera-id boundary through near-ground/dark-wall low-texture content.",
        "Use as production-mechanism abstain control.",
        "source boundary",
    ),
    Case(
        "db26_photometric_smudge",
        "DB26 photometric attenuation",
        "deliverables/dit360_v2/db26_photometric_fetch/db26_attenuated_roi_montage.jpg",
        "reject",
        "T1 photometric-only seam attempt",
        "YELLOW",
        "rejected source-safe polish",
        "O0 reject",
        ["low_frequency_smudge", "donor_blur_or_smudge", "geometry_unchanged", "line_not_fixed"],
        "Even source-safe low-frequency attenuation fails the seam claim and risks dark-wall color wash/smudge.",
        "Do not accept source-safe color edits unless the vision gate passes.",
        "smudge fail",
    ),
    Case(
        "db41_right_roi",
        "DB41 right ROI",
        "deliverables/dit360_v2/db41_rightline_evidence_gate/right_roi/db25_longline_evidence_montage.jpg",
        "abstain",
        "T4/T5 right-line/curb",
        "RED",
        "evidence-only",
        "O0 abstain",
        ["no_source_evidence", "low_lidar_support", "right_line_boundary", "unknown_or_abstain", "red_region_no_repair"],
        "Flow has local reliable patches but LiDAR support is too sparse for continuous white-line/curb repair.",
        "Keep DB41 boundary until new evidence changes it.",
        "right abstain",
    ),
    Case(
        "db41_lower_right",
        "DB41 lower-right ROI",
        "deliverables/dit360_v2/db41_rightline_evidence_gate/lower_right_roi/db25_longline_evidence_montage.jpg",
        "abstain",
        "T4/T5/T10 lower-right line/curb",
        "RED",
        "evidence-only",
        "O0 abstain",
        ["no_source_evidence", "zero_lidar_support", "near_ground", "unknown_or_abstain", "red_region_no_repair"],
        "All near-ground ROI has zero LiDAR support on the actual target surface; source-faithful repair must abstain.",
        "Mandatory negative control for DB44.",
        "lower abstain",
    ),
    Case(
        "db30_sky_mask_preview",
        "DB30 sky-panel mask",
        "deliverables/dit360_v2/db30_sky_panel_a200/opmask_sky_panel_preview.jpg",
        "reject",
        "T8 sky mask with foreground bleed",
        "RED",
        "rejected generated mask",
        "O0 reject",
        ["mask_leaks_to_buildings", "mask_leaks_to_objects", "generated_region"],
        "Rejected before DiT because automatic sky-panel mask included buildings/vehicle/road-adjacent pixels.",
        "Do not generate through polluted sky masks.",
        "mask leak",
    ),
    Case(
        "db33_s50_sky_halo",
        "DB33 local sky s50",
        "deliverables/dit360_v2/db33_local_sky_boundary_harmonize/db33_local_sky_boundary_harmonize_s50.png",
        "reject",
        "T8 sky harmonization",
        "YELLOW",
        "rejected sky polish",
        "O0 reject",
        ["sky_halo", "diagonal_color_band", "source_safe_but_visual_fail"],
        "Source pixels are preserved, but local sky halos/diagonal bands make it worse than DB32.",
        "Keep rectilinear review; do not continue local color-field tuning.",
        "sky halo",
    ),
    Case(
        "db31_nonbmw_montage",
        "DB31 relaxed scan failures",
        "deliverables/dit360_v2/db31_multilog_candidate_scan/db31_full_montage.jpg",
        "diagnostic",
        "T12 dataset mining",
        "YELLOW",
        "source selection evidence",
        "O10 source/frame selection",
        ["dataset_mining_control", "no_successor_found", "not_top10_only"],
        "Bounded scan did not find a better non-BMW successor; useful as source-selection accounting.",
        "Future DB47 must report acceptance/reject statistics, not only pretty examples.",
        "scan control",
    ),
]


RECTILINEAR_CONTROLS = [
    {
        "title": "DB22 rectilinear right-line diagnostic",
        "artifact": "deliverables/dit360_v2/db22_rectilinear_diag/db22_rect_bmw_rightline_montage.jpg",
        "verdict": "Mask placement was not the root problem; DiT ground/curb semantics redraws fake geometry.",
    },
    {
        "title": "DB33 rectilinear sky review",
        "artifact": "deliverables/dit360_v2/db33_local_sky_boundary_harmonize/db33_rect_sky_montage.jpg",
        "verdict": "Rectilinear review catches sky halos/bands that ERP can hide.",
    },
    {
        "title": "DB36 reject review",
        "artifact": "deliverables/dit360_v2/db36_user_redline_mask/db36_reject_review_board.jpg",
        "verdict": "Object gate PASS is insufficient when seam-local fake slabs/holes appear.",
    },
    {
        "title": "DB40 longsrc reject review",
        "artifact": "deliverables/dit360_v2/db40_v14_mask_alignment/db40_a1_longsrc_review_board.jpg",
        "verdict": "Narrow mask still hallucinates pole-like vertical geometry.",
    },
]

CANONICAL_ROIS = {
    "long_source": (850, 360, 1680, 735),
    "right_line": (1400, 330, 2048, 735),
    "lower_right": (1580, 560, 2048, 790),
}

CANONICAL_ROI_CASES = [
    {
        "title": "G diagnostic reference",
        "artifact": "deliverables/ghostkill/G_bmw_pano.jpg",
        "label": "diagnostic",
        "note": "classic BMW failure reference; not default repair base",
    },
    {
        "title": "DB32 handoff candidate",
        "artifact": "deliverables/dit360_v2/db32_generated_sky_harmonize_v2/db32_generated_sky_harmonize_s40.png",
        "label": "caveated-handoff",
        "note": "source-sidestep + generated-sky caveat, not original-G repair",
    },
    {
        "title": "DB23 ground outpaint",
        "artifact": "deliverables/dit360_v2/db23_d4b_fetch/ground_t50_s0/ground_t50_s0_corecompose.png",
        "label": "reject",
        "note": "fake road/lane/curb generated geometry",
    },
    {
        "title": "DB36 red-line DiT",
        "artifact": "deliverables/dit360_v2/db36_user_redline_mask/G_bmw_pano_user_redline_tau5_fetch/G_bmw_pano_user_redline_tau5/db36_user_redline_tau5/db36_user_redline_tau5_corecompose.png",
        "label": "reject",
        "note": "outside-mask exact but core fake slabs/holes",
    },
    {
        "title": "DB39 G v14 raw",
        "artifact": "deliverables/dit360_v2/db14_g_bmw_pano_fetch/G_bmw_pano/g_r008_h016_w025_tau5/g_r008_h016_w025_tau5_raw.png",
        "label": "reject",
        "note": "vertical slice / pole-like artifact",
    },
    {
        "title": "DB39 A1 v14 raw",
        "artifact": "deliverables/dit360_v2/db14_a1_view_none_fetch/A1_view_none_bmw/a1view_r008_h016_w025_tau5/a1view_r008_h016_w025_tau5_raw.png",
        "label": "reject",
        "note": "A1 candidate/mask mismatch slice",
    },
    {
        "title": "DB40 A1 keepout",
        "artifact": "deliverables/dit360_v2/db40_v14_mask_alignment/a1_keepout_strict_fetch/a1_keepout_strict/a1_keepout_strict_tau5/a1_keepout_strict_tau5_corecompose.png",
        "label": "diagnostic",
        "note": "root cause evidence only; vertical bands remain",
    },
    {
        "title": "DB40 longsrc raw",
        "artifact": "deliverables/dit360_v2/db40_v14_mask_alignment/a1_longsrc_only_fetch/a1_longsrc_only_tau5/a1_longsrc_only_tau5_raw.png",
        "label": "reject",
        "note": "pole-like hallucinated geometry despite object gate PASS",
    },
    {
        "title": "DB35 A1 donor",
        "artifact": "deliverables/dit360_v2/db35_seam_first/db35_rightline_a1_donor_patch.png",
        "label": "reject",
        "note": "soft/blurred ground, line not fixed",
    },
]


def font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def load(path_text: str) -> Image.Image:
    path = ROOT / path_text
    if not path.exists():
        raise FileNotFoundError(path)
    return Image.open(path).convert("RGB")


def fit(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGB", size, (0, 0, 0))
    work = img.copy()
    work.thumbnail(size, Image.Resampling.LANCZOS)
    canvas.paste(work, ((size[0] - work.width) // 2, (size[1] - work.height) // 2))
    return canvas


def text_lines(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for part in text.split("\n"):
        lines.extend(wrap(part, width=width) or [""])
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    width: int,
    fill: tuple[int, int, int],
    size: int,
    max_lines: int | None = None,
) -> int:
    x, y = xy
    lines = text_lines(text, width)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [lines[max_lines - 1][: max(0, width - 3)] + "..."]
    line_h = size + 5
    for line in lines:
        draw.text((x, y), line, fill=fill, font=font(size))
        y += line_h
    return y


def case_image(case: Case, size: tuple[int, int]) -> Image.Image:
    img = load(case.artifact)
    if case.roi_xyxy is not None:
        img = img.crop(case.roi_xyxy)
    return fit(img, size)


def draw_case_tile(case: Case, size: tuple[int, int] = (330, 320)) -> Image.Image:
    label_color = LABEL_COLORS.get(case.claim_label, (90, 90, 90))
    tile = Image.new("RGB", size, (24, 24, 24))
    draw = ImageDraw.Draw(tile)
    draw.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=label_color, width=4)
    draw.rectangle([0, 0, size[0], 52], fill=label_color)
    draw.text((10, 8), case.case_id, fill=(255, 255, 255), font=font(14))
    draw.text((10, 29), case.claim_label, fill=(255, 255, 255), font=font(13))
    img = case_image(case, (size[0] - 16, 135))
    tile.paste(img, (8, 60))
    y = 202
    y = draw_wrapped(draw, (10, y), case.title, 38, (255, 245, 210), 12, max_lines=2)
    y += 2
    draw.text((10, y), f"{case.evidence_state} | {case.operator}", fill=(210, 210, 210), font=font(11))
    y += 20
    codes = ", ".join(case.reason_codes[:4])
    draw_wrapped(draw, (10, y), codes, 42, (230, 230, 230), 10, max_lines=3)
    return tile


def build_case_board() -> None:
    cols = 4
    tile_size = (330, 320)
    gap = 16
    header_h = 118
    rows = (len(CASES) + cols - 1) // cols
    board_w = cols * tile_size[0] + (cols + 1) * gap
    board_h = header_h + rows * tile_size[1] + (rows + 1) * gap
    board = Image.new("RGB", (board_w, board_h), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((18, 14), "DB43 Source-Faithfulness Eval v2 / Fake-Geometry Gate", fill=(255, 255, 255), font=font(24))
    draw.text(
        (18, 47),
        "Known cases only. CPU-only triage; no new panorama generation, no model inference, no A100.",
        fill=(220, 220, 220),
        font=font(14),
    )
    draw.text(
        (18, 72),
        "DB32=sidestep+generated-sky caveated handoff. G=classic failure reference. DB41 right-line/lower-right=abstain.",
        fill=(255, 225, 170),
        font=font(14),
    )

    for i, case in enumerate(CASES):
        row = i // cols
        col = i % cols
        x = gap + col * (tile_size[0] + gap)
        y = header_h + gap + row * (tile_size[1] + gap)
        board.paste(draw_case_tile(case, tile_size), (x, y))
    board.save(BOARD, quality=92)


def build_roi_board() -> None:
    label_w = 250
    roi_size = (340, 180)
    gap = 12
    header_h = 118
    row_h = roi_size[1] + 58
    board_w = label_w + len(CANONICAL_ROIS) * roi_size[0] + (len(CANONICAL_ROIS) + 2) * gap
    board_h = header_h + len(CANONICAL_ROI_CASES) * row_h + gap
    board = Image.new("RGB", (board_w, board_h), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((18, 14), "DB43 canonical BMW same-ROI review", fill=(255, 255, 255), font=font(24))
    draw.text(
        (18, 48),
        "Fixed ROIs across canonical cases. This is the visual falsification set for fake geometry and DB32/G claim boundaries.",
        fill=(220, 220, 220),
        font=font(14),
    )
    x = label_w + gap
    for roi_name in CANONICAL_ROIS:
        draw.text((x, header_h - 28), roi_name, fill=(255, 245, 190), font=font(15))
        x += roi_size[0] + gap

    for i, row in enumerate(CANONICAL_ROI_CASES):
        y = header_h + i * row_h
        color = LABEL_COLORS.get(row["label"], (90, 90, 90))
        draw.rectangle([gap, y + 8, label_w - 4, y + row_h - 8], fill=(30, 30, 30), outline=color, width=3)
        draw.text((gap + 10, y + 18), row["title"], fill=(255, 255, 255), font=font(14))
        draw.text((gap + 10, y + 40), row["label"], fill=(255, 220, 180), font=font(13))
        draw_wrapped(draw, (gap + 10, y + 62), row["note"], 32, (225, 225, 225), 11, max_lines=4)
        img = load(row["artifact"])
        x = label_w + gap
        for roi_name, box in CANONICAL_ROIS.items():
            crop = fit(img.crop(box), roi_size)
            board.paste(crop, (x, y + 18))
            draw.rectangle([x, y + 18, x + roi_size[0] - 1, y + 18 + roi_size[1] - 1], outline=color, width=2)
            x += roi_size[0] + gap
    board.save(ROI_BOARD, quality=92)


def build_rect_board() -> None:
    cols = 2
    tile_img_size = (720, 380)
    gap = 18
    header_h = 96
    tile_h = 500
    tile_w = tile_img_size[0] + 18
    rows = 2
    board = Image.new("RGB", (cols * tile_w + (cols + 1) * gap, header_h + rows * tile_h + (rows + 1) * gap), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((18, 14), "DB43 rectilinear / crop controls", fill=(255, 255, 255), font=font(24))
    draw.text((18, 48), "Use these controls before accepting seam-local fake geometry. ERP smoothness alone is insufficient.", fill=(220, 220, 220), font=font(14))

    for i, control in enumerate(RECTILINEAR_CONTROLS):
        row = i // cols
        col = i % cols
        x = gap + col * (tile_w + gap)
        y = header_h + gap + row * tile_h
        draw.rectangle([x, y, x + tile_w, y + tile_h - 1], outline=(80, 80, 80), width=2)
        draw.text((x + 10, y + 10), control["title"], fill=(255, 245, 190), font=font(16))
        img = fit(load(control["artifact"]), tile_img_size)
        board.paste(img, (x + 9, y + 42))
        draw_wrapped(draw, (x + 10, y + 432), control["verdict"], 88, (235, 235, 235), 13, max_lines=4)
    board.save(RECT_BOARD, quality=92)


def build_summary_board(manifest: dict) -> None:
    counts = manifest["counts"]
    board = Image.new("RGB", (1700, 980), (18, 18, 18))
    draw = ImageDraw.Draw(board)
    draw.text((18, 14), "DB43 gate summary", fill=(255, 255, 255), font=font(24))
    draw.text((18, 48), "Reason-coded triage is the gate. It is not a scalar score and not an automatic repair permission.", fill=(220, 220, 220), font=font(14))

    x = 24
    y = 92
    for label, count in sorted(counts["by_claim_label"].items()):
        color = LABEL_COLORS.get(label, (90, 90, 90))
        draw.rectangle([x, y, x + 330, y + 48], fill=color)
        draw.text((x + 12, y + 13), f"{label}: {count}", fill=(255, 255, 255), font=font(16))
        y += 58

    x = 410
    y = 92
    draw.text((x, y), "Mandatory kill controls", fill=(255, 245, 190), font=font(18))
    y += 40
    for check in manifest["gate_checks"]:
        color = (60, 130, 75) if check["pass"] else (150, 50, 50)
        draw.rectangle([x, y, x + 1250, y + 66], fill=color)
        draw.text((x + 12, y + 8), check["name"], fill=(255, 255, 255), font=font(14))
        draw_wrapped(draw, (x + 440, y + 8), check["detail"], 108, (255, 255, 255), 12, max_lines=3)
        y += 80

    y += 18
    draw.text((x, y), "Red-team synthesis", fill=(255, 245, 190), font=font(18))
    y += 38
    for item in manifest["red_team_audit"]:
        draw_wrapped(draw, (x + 6, y), f"- {item}", 120, (235, 235, 235), 13, max_lines=2)
        y += 48

    board.save(SUMMARY_BOARD, quality=92)


def validate_cases() -> dict:
    case_ids = {case.case_id for case in CASES}
    if len(case_ids) != len(CASES):
        raise ValueError("duplicate case_id in DB43 cases")

    missing = [case.artifact for case in CASES if not (ROOT / case.artifact).exists()]
    missing += [c["artifact"] for c in RECTILINEAR_CONTROLS if not (ROOT / c["artifact"]).exists()]
    if missing:
        raise FileNotFoundError("\n".join(missing))

    by_label: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for case in CASES:
        by_label[case.claim_label] = by_label.get(case.claim_label, 0) + 1
        for reason in case.reason_codes:
            by_reason[reason] = by_reason.get(reason, 0) + 1

    def has(case_id: str, label: str | None = None, reason: str | None = None) -> bool:
        for case in CASES:
            if case.case_id == case_id:
                if label is not None and case.claim_label != label:
                    return False
                if reason is not None and reason not in case.reason_codes:
                    return False
                return True
        return False

    reject_controls = ["db23_ground_core", "db36_user_redline", "db39_g_v14_raw", "db40_longsrc_raw"]
    gate_checks = [
        {
            "name": "DB32 is not fully source-faithful",
            "pass": has("db32_s40_full", "caveated-handoff", "source_sidestep")
            and "sky_generated_caveat" in next(c.reason_codes for c in CASES if c.case_id == "db32_s40_full"),
            "detail": "DB32 must remain Bosch-facing handoff with source-sidestep + generated-sky caveats.",
        },
        {
            "name": "DB41 right-line/lower-right abstains",
            "pass": has("db41_right_roi", "abstain", "no_source_evidence")
            and has("db41_lower_right", "abstain", "zero_lidar_support"),
            "detail": "Right-line and lower-right must not be marked repairable under current evidence.",
        },
        {
            "name": "Known fake geometry is rejected",
            "pass": all(has(case_id, "reject") for case_id in reject_controls),
            "detail": "DB23/DB36/DB39/DB40 style fake road/slab/slice/pole artifacts cannot pass.",
        },
        {
            "name": "Reason codes are mandatory",
            "pass": all(case.reason_codes for case in CASES),
            "detail": "Every case carries reason codes; no scalar-only verdict.",
        },
        {
            "name": "Generated sky and generated ground are separated",
            "pass": has("db19_g_sky_only", "presentation-only", "sky_generated_caveat")
            and has("db23_ground_core", "reject", "fake_road"),
            "detail": "Sky-only generated caveat is not treated like fake generated ground/curb/lane.",
        },
        {
            "name": "G_bmw_pano is not a default repair base",
            "pass": has("g_bmw_long", "diagnostic", "not_default_repair_base"),
            "detail": "G is only a classic BMW failure / diagnostic reference.",
        },
    ]

    gate_pass = all(check["pass"] for check in gate_checks)
    return {
        "by_claim_label": by_label,
        "by_reason_code": dict(sorted(by_reason.items())),
        "gate_checks": gate_checks,
        "gate_pass": gate_pass,
    }


def build_manifest(validation: dict) -> dict:
    red_team_audit = [
        "Smoothness is never a pass condition by itself; generated road/curb/lane artifacts stay rejected even when object gate passes.",
        "DB32 is deliberately caveated: handoff candidate, source-sidestep, generated sky; not source-faithful ceiling.",
        "G/A1/BEST cases remain diagnostic or rejected unless a future brief brings new evidence and survives its kill criteria.",
        "DB44 cannot start by repairing DB41 RED regions; it must first prove abstain behavior on those controls.",
        "Any brief that hits kill criteria must stop and be archived to progress.md instead of continuing parameter patches.",
    ]
    manifest = {
        "db": "DB-20260604-43",
        "status": "running-output",
        "purpose": "Source-Faithfulness Eval v2 / Fake-Geometry Gate + EGSR triage over known artifacts.",
        "scope": {
            "cases": len(CASES),
            "max_cases": 30,
            "new_panorama_generation": False,
            "model_inference": False,
            "a100_used": False,
            "dataset_scan": False,
            "source_scope": "Current Waymo2Panorama repo artifacts/calibration/ROIs only; do not generalize AV2 assumptions automatically.",
        },
        "outputs": {
            "known_case_board": rel(BOARD),
            "canonical_roi_board": rel(ROI_BOARD),
            "rectilinear_review_board": rel(RECT_BOARD),
            "summary_board": rel(SUMMARY_BOARD),
            "manifest": rel(MANIFEST),
        },
        "claim_label_definitions": {
            "source-faithful": "Pixels/operation remain source-derived with explicit caveats for residual risk.",
            "caveated-handoff": "Bosch-facing candidate with explicit non-source/generated/source-sidestep caveats.",
            "source-sidestep": "Accepted only as source/frame selection; not local repair of the original seam.",
            "presentation-only": "May be shown as visual/demo output only with generated/edit masks.",
            "generated": "Contains generated content and is not sensor truth.",
            "diagnostic": "Failure/reference/control case; not a production base.",
            "abstain": "Current evidence does not justify source-faithful repair.",
            "reject": "Fails gate or vision verdict; do not continue this direction without new brief/evidence.",
        },
        "cases": [asdict(case) for case in CASES],
        "rectilinear_controls": RECTILINEAR_CONTROLS,
        "counts": {
            "by_claim_label": validation["by_claim_label"],
            "by_reason_code": validation["by_reason_code"],
        },
        "gate_checks": validation["gate_checks"],
        "gate_pass": validation["gate_pass"],
        "red_team_audit": red_team_audit,
        "db44_preconditions": [
            "DB43 gate checks pass.",
            "DB41 right-line/lower-right controls remain RED/abstain.",
            "DB32 remains caveated-handoff, not fully source-faithful.",
            "DB44 starts as layer/evidence/operator dispatcher dry run; no diffusion and no RED-region repair.",
        ],
    }
    return manifest


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    validation = validate_cases()
    build_case_board()
    build_roi_board()
    build_rect_board()
    manifest = build_manifest(validation)
    build_summary_board(manifest)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {BOARD}")
    print(f"wrote {ROI_BOARD}")
    print(f"wrote {RECT_BOARD}")
    print(f"wrote {SUMMARY_BOARD}")
    print(f"wrote {MANIFEST}")
    print(f"gate_pass={manifest['gate_pass']} cases={len(CASES)}")


if __name__ == "__main__":
    main()
