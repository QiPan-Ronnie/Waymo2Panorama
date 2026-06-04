"""DB-38: Bosch-ready candidate handoff board.

This is a visual/evidence packaging script only. It compares current candidates
under the Bosch/world-model constraint: source-faithful objects/ground are more
important than cosmetically hiding seams with fake geometry.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


H, W = 1024, 2048
LONG_ROI = (850, 420, 1650, 720)
RIGHT_ROI = (1440, 360, 2048, 720)
SKY_ROI = (520, 170, 1480, 430)
OBJECT_ROI = (1370, 300, 2048, 720)


def read_bgr(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    if img.shape[:2] != (H, W):
        img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
    return img


def crop(img: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = roi
    return img[y0:y1, x0:x1]


def label(img: np.ndarray, text: str, h: int = 30) -> np.ndarray:
    bar = np.zeros((h, img.shape[1], 3), np.uint8)
    cv2.putText(bar, text[:96], (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, img])


def fit(img: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = img.shape[:2]
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((h, w, 3), np.uint8)
    y0, x0 = (h - nh) // 2, (w - nw) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = resized
    return out


def draw_rois(img: np.ndarray) -> np.ndarray:
    out = img.copy()
    for roi, color in [
        (LONG_ROI, (0, 0, 255)),
        (RIGHT_ROI, (0, 255, 255)),
        (SKY_ROI, (255, 0, 0)),
        (OBJECT_ROI, (0, 255, 0)),
    ]:
        x0, y0, x1, y1 = roi
        cv2.rectangle(out, (x0, y0), (x1, y1), color, 3)
    return out


def diff_heat(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    d = cv2.absdiff(a, b).max(axis=2)
    heat = cv2.applyColorMap(np.clip(d * 3, 0, 255).astype(np.uint8), cv2.COLORMAP_JET)
    heat[d == 0] = (0, 0, 0)
    return heat


def make_row(name: str, img: np.ndarray, ref: np.ndarray | None = None) -> np.ndarray:
    panels = [
        label(fit(draw_rois(img), 340, 170), f"{name} full"),
        label(fit(crop(img, LONG_ROI), 340, 170), "long seam ROI"),
        label(fit(crop(img, RIGHT_ROI), 340, 170), "right white-line ROI"),
        label(fit(crop(img, SKY_ROI), 340, 170), "sky/panel ROI"),
        label(fit(crop(img, OBJECT_ROI), 340, 170), "object ROI"),
    ]
    if ref is not None:
        panels.append(label(fit(crop(diff_heat(ref, img), RIGHT_ROI), 340, 170), "right diff vs ref"))
    return np.hstack(panels)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    candidates = [
        {
            "name": "G_bmw_pano",
            "path": Path("deliverables/ghostkill/G_bmw_pano.jpg"),
            "decision": "reject-as-handoff seam residual",
            "note": "user-nearest original but has long red-line/right white-line seam",
        },
        {
            "name": "DB19_G_sky_only",
            "path": Path("deliverables/dit360_v2/db19_G_bmw_pano_sky_t50_s0_postcompose_thr45.png"),
            "decision": "presentation-only with G seam caveat",
            "note": "sky improved, original G ground seam preserved",
        },
        {
            "name": "DB28_a200_source",
            "path": Path("deliverables/dit360_v2/db28_clean_subset_refine/SR_bmw_db28_a200_final_1024x2048.png"),
            "decision": "accepted source sidestep base",
            "note": "cleaner source, no original G red-line seam, but sky/black bands remain",
        },
        {
            "name": "DB32_s40_current_best",
            "path": Path("deliverables/dit360_v2/db32_generated_sky_harmonize_v2/db32_generated_sky_harmonize_s40.png"),
            "decision": "current best Bosch handoff candidate",
            "note": "DB28 source plus object-gated sky fill/harmonization; source pixels preserved",
        },
        {
            "name": "DB36_G_DiT_redline_rejected",
            "path": Path(
                "deliverables/dit360_v2/db36_user_redline_mask/G_bmw_pano_user_redline_tau5_fetch/"
                "G_bmw_pano_user_redline_tau5/db36_user_redline_tau5/db36_user_redline_tau5_corecompose.png"
            ),
            "decision": "reject fake ground",
            "note": "object gate passed but generated fake ground slabs/holes",
        },
    ]

    images = [(c, read_bgr(c["path"])) for c in candidates]
    ref = images[0][1]
    rows = [make_row(c["name"], img, ref=ref) for c, img in images]
    board = np.vstack(rows)
    board_path = args.out_dir / "db38_bosch_handoff_board.jpg"
    cv2.imwrite(str(board_path), board, [cv2.IMWRITE_JPEG_QUALITY, 95])

    db34 = load_json(Path("deliverables/dit360_v2/db34_current_best_qa/db34_current_best_manifest.json"))
    db36 = load_json(Path("deliverables/dit360_v2/db36_user_redline_mask/db36_reject_review_manifest.json"))
    manifest = {
        "board": str(board_path),
        "rois": {
            "long_roi": list(LONG_ROI),
            "right_roi": list(RIGHT_ROI),
            "sky_roi": list(SKY_ROI),
            "object_roi": list(OBJECT_ROI),
        },
        "bosch_constraints": [
            "world-model input should preserve real objects/road/curb where possible",
            "fake generated ground is worse than an honest black/out-of-FOV or source-selection caveat",
            "object gate is necessary but not sufficient; vision review decides seam/geometry quality",
        ],
        "candidate_decisions": [
            {
                "name": c["name"],
                "path": str(c["path"]),
                "decision": c["decision"],
                "note": c["note"],
            }
            for c, _img in images
        ],
        "current_best": {
            "name": "DB32_s40_current_best",
            "path": "deliverables/dit360_v2/db32_generated_sky_harmonize_v2/db32_generated_sky_harmonize_s40.png",
            "not_a_claim": "does not fix original G seam; it is a cleaner source sidestep",
            "source_preservation": db34.get("source_preservation", {}),
            "object_gate": db34.get("object_gate", {}),
            "accepted_caveats": db34.get("accepted_caveats", []),
        },
        "negative_control": {
            "name": "DB36_G_DiT_redline_rejected",
            "reason": db36.get("vision_verdict"),
            "outside_mask_max_abs_diff": db36.get("outside_mask_max_abs_diff"),
            "core_mean_abs_diff": db36.get("core_mean_abs_diff"),
        },
        "vision_verdict": "TBD",
    }
    (args.out_dir / "db38_bosch_handoff_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(board_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
