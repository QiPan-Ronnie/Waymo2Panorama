from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment"

RIGHT_BMW = (1320, 300, 1960, 735)
RIGHT_SEAM = (1530, 300, 1900, 735)
LOWER_RIGHT = (1500, 555, 2048, 820)
LONG_SOURCE = (820, 360, 1680, 735)
ROIS = {
    "right_bmw": RIGHT_BMW,
    "right_seam": RIGHT_SEAM,
    "lower_right": LOWER_RIGHT,
    "long_source": LONG_SOURCE,
}

CASES = [
    {
        "label": "old_v14_reference",
        "input": ROOT
        / "deliverables"
        / "dit360_seam_completion"
        / "runs_v14_trimap_clamp_bmw"
        / "trimap_r008_h016_w025_tau5"
        / "trimap_r008_h016_w025_tau5_hard_select_fullres_1024x2048.png",
        "trimap": ROOT
        / "deliverables"
        / "dit360_seam_completion"
        / "runs_v14_trimap_clamp_bmw"
        / "trimap_r008_h016_w025_tau5"
        / "trimap_r008_h016_w025_tau5_trimap_preview.jpg",
        "mask": ROOT
        / "deliverables"
        / "dit360_seam_completion"
        / "runs_v14_trimap_clamp_bmw"
        / "trimap_r008_h016_w025_tau5"
        / "trimap_r008_h016_w025_tau5_model_mask_far_preserve.png",
        "raw": ROOT
        / "deliverables"
        / "dit360_seam_completion"
        / "runs_v14_trimap_clamp_bmw"
        / "trimap_r008_h016_w025_tau5"
        / "trimap_r008_h016_w025_tau5_raw_fullres_1024x2048.png",
    },
    {
        "label": "A1_view_none_v14_tau5",
        "input": ROOT / "deliverables" / "a1_streetview_pipeline" / "A1_view_none_L1_vs_result.jpg",
        "extract": "a1_result",
        "trimap": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_a1_view_none_fetch"
        / "A1_view_none_bmw"
        / "a1view_r008_h016_w025_tau5"
        / "a1view_r008_h016_w025_tau5_trimap_preview.jpg",
        "mask": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_a1_view_none_fetch"
        / "A1_view_none_bmw"
        / "a1view_r008_h016_w025_tau5"
        / "a1view_r008_h016_w025_tau5_model_mask_far_preserve.png",
        "raw": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_a1_view_none_fetch"
        / "A1_view_none_bmw"
        / "a1view_r008_h016_w025_tau5"
        / "a1view_r008_h016_w025_tau5_raw.png",
    },
    {
        "label": "G_bmw_pano_v14_tau5",
        "input": ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg",
        "trimap": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_g_bmw_pano_fetch"
        / "G_bmw_pano"
        / "g_r008_h016_w025_tau5"
        / "g_r008_h016_w025_tau5_trimap_preview.jpg",
        "mask": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_g_bmw_pano_fetch"
        / "G_bmw_pano"
        / "g_r008_h016_w025_tau5"
        / "g_r008_h016_w025_tau5_model_mask_far_preserve.png",
        "raw": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_g_bmw_pano_fetch"
        / "G_bmw_pano"
        / "g_r008_h016_w025_tau5"
        / "g_r008_h016_w025_tau5_raw.png",
    },
]


def load_rgb(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def resize_erp(arr: np.ndarray) -> np.ndarray:
    if arr.shape[:2] == (1024, 2048):
        return arr
    return cv2.resize(arr, (2048, 1024), interpolation=cv2.INTER_AREA)


def extract_a1_result(arr: np.ndarray) -> np.ndarray:
    h, w = arr.shape[:2]
    panel_h = round(w * 1024 / 2048)
    y0 = 30 + panel_h + 30
    return cv2.resize(arr[y0 : y0 + panel_h, :], (2048, 1024), interpolation=cv2.INTER_AREA)


def load_case_image(case: dict, key: str) -> np.ndarray:
    arr = load_rgb(case[key])
    if key == "input" and case.get("extract") == "a1_result":
        arr = extract_a1_result(arr)
    return resize_erp(arr)


def crop(arr: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    return arr[y0:y1, x0:x1]


def label(arr: np.ndarray, text: str, width: int = 340) -> np.ndarray:
    h, w = arr.shape[:2]
    nh = max(1, round(h * width / w))
    img = Image.fromarray(arr).resize((width, nh), Image.Resampling.BICUBIC)
    band_h = 34
    out = Image.new("RGB", (width, nh + band_h), (0, 0, 0))
    out.paste(img, (0, band_h))
    draw = ImageDraw.Draw(out)
    draw.text((8, 10), text[:90], fill=(255, 255, 255), font=ImageFont.load_default())
    return np.array(out)


def pad_h(arr: np.ndarray, height: int) -> np.ndarray:
    if arr.shape[0] >= height:
        return arr
    return np.vstack([arr, np.zeros((height - arr.shape[0], arr.shape[1], 3), dtype=np.uint8)])


def draw_rois(arr: np.ndarray) -> np.ndarray:
    out = arr.copy()
    colors = {
        "right_bmw": (255, 80, 80),
        "right_seam": (255, 220, 40),
        "lower_right": (80, 220, 255),
        "long_source": (180, 80, 255),
    }
    for name, box in ROIS.items():
        x0, y0, x1, y1 = box
        cv2.rectangle(out, (x0, y0), (x1, y1), colors[name], 3)
    return out


def mask_stats(mask: np.ndarray) -> dict[str, float]:
    if mask.ndim == 3:
        mask = mask[..., 0]
    mask = cv2.resize(mask, (2048, 1024), interpolation=cv2.INTER_NEAREST)
    generate = mask < 128
    out = {}
    for name, box in ROIS.items():
        x0, y0, x1, y1 = box
        out[f"{name}_generate_fraction"] = float(generate[y0:y1, x0:x1].mean())
    return out


def mean_abs(a: np.ndarray, b: np.ndarray, box: tuple[int, int, int, int]) -> float:
    aa = crop(a, box).astype(np.float32)
    bb = crop(b, box).astype(np.float32)
    return float(np.mean(np.abs(aa - bb)))


def build() -> tuple[np.ndarray, dict]:
    board_rows = []
    rows = []
    for case in CASES:
        inp = load_case_image(case, "input")
        trimap = load_case_image(case, "trimap")
        raw = load_case_image(case, "raw")
        if case["mask"].exists():
            mask = load_rgb(case["mask"])
            stats = mask_stats(mask)
            mask_path = str(case["mask"].relative_to(ROOT))
        else:
            stats = None
            mask_path = None

        parts = [
            label(draw_rois(inp), f"{case['label']} input", 300),
            label(crop(trimap, RIGHT_BMW), "trimap right_bmw", 340),
            label(crop(raw, RIGHT_BMW), "raw right_bmw", 340),
            label(crop(raw, LOWER_RIGHT), "raw lower_right", 340),
            label(crop(raw, LONG_SOURCE), "raw long_source", 340),
        ]
        max_h = max(p.shape[0] for p in parts)
        board_rows.append(np.hstack([pad_h(p, max_h) for p in parts]))
        rows.append(
            {
                "label": case["label"],
                "input": str(case["input"].relative_to(ROOT)),
                "raw": str(case["raw"].relative_to(ROOT)),
                "trimap": str(case["trimap"].relative_to(ROOT)),
                "model_mask": mask_path,
                "mask_stats": stats,
                "roi_mae_raw_vs_input": {name: mean_abs(raw, inp, box) for name, box in ROIS.items()},
            }
        )
    width = max(r.shape[1] for r in board_rows)
    board_rows = [
        np.hstack([r, np.zeros((r.shape[0], width - r.shape[1], 3), dtype=np.uint8)])
        if r.shape[1] < width
        else r
        for r in board_rows
    ]
    board = np.vstack(board_rows)
    manifest = {
        "db": "DB-20260604-40",
        "purpose": "Forensic check: whether v14 trimap/model mask overlays the same semantic right-BMW seam area for old reference, A1, and G.",
        "rois_2048x1024": ROIS,
        "preliminary_hypothesis": "A1/G replay reused the old hard-select seam mask coordinates on different candidate images; this can put the generate strip through the white BMW/building/sidewalk seam context and create vertical slice artifacts.",
        "rows": rows,
    }
    return board, manifest


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    board, manifest = build()
    Image.fromarray(board).save(OUT / "db40_mask_alignment_forensic_board.jpg", quality=92)
    with (OUT / "db40_mask_alignment_forensic_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(json.dumps({"out": str(OUT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
