from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "deliverables" / "dit360_v2" / "db39_v14_trimap_replay"

ROIS = {
    "long_source": (850, 360, 1680, 735),
    "right_white": (1400, 330, 2048, 735),
    "lower_right": (1580, 560, 2048, 790),
}

CASES = [
    {
        "label": "G input",
        "path": ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg",
        "init": None,
        "diag": None,
        "gate": None,
        "verdict": "baseline; user-marked long/right seam remains",
    },
    {
        "label": "G v14 raw tau5",
        "path": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_g_bmw_pano_fetch"
        / "G_bmw_pano"
        / "g_r008_h016_w025_tau5"
        / "g_r008_h016_w025_tau5_raw.png",
        "init": ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg",
        "diag": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_g_bmw_pano_fetch"
        / "G_bmw_pano"
        / "g_r008_h016_w025_tau5"
        / "g_r008_h016_w025_tau5_diagnostics.json",
        "gate": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_g_bmw_pano_fetch"
        / "G_bmw_pano"
        / "g_r008_h016_w025_tau5"
        / "g_r008_h016_w025_tau5_gate_gate.json",
        "verdict": "reject candidate: right ROI vertical generated slice/pole-like artifact",
    },
    {
        "label": "G v14 soft tau5",
        "path": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_g_bmw_pano_fetch"
        / "G_bmw_pano"
        / "g_r008_h016_w025_tau5"
        / "g_r008_h016_w025_tau5_softcompose.png",
        "init": ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg",
        "diag": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_g_bmw_pano_fetch"
        / "G_bmw_pano"
        / "g_r008_h016_w025_tau5"
        / "g_r008_h016_w025_tau5_diagnostics.json",
        "gate": None,
        "verdict": "diagnostic only; seam softened but source-band/slice still visible",
    },
    {
        "label": "G v14 core tau5",
        "path": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_g_bmw_pano_fetch"
        / "G_bmw_pano"
        / "g_r008_h016_w025_tau5"
        / "g_r008_h016_w025_tau5_corecompose.png",
        "init": ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg",
        "diag": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_g_bmw_pano_fetch"
        / "G_bmw_pano"
        / "g_r008_h016_w025_tau5"
        / "g_r008_h016_w025_tau5_diagnostics.json",
        "gate": None,
        "verdict": "diagnostic only; preserves outside core but exposes hard paste/slice",
    },
    {
        "label": "BEST input",
        "path": ROOT / "deliverables" / "ghostkill" / "BEST_bmw_pano.jpg",
        "init": None,
        "diag": None,
        "gate": None,
        "verdict": "baseline; building/car ghosting remains",
    },
    {
        "label": "BEST v14 raw tau5",
        "path": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_best_bmw_pano_fetch"
        / "BEST_bmw_pano"
        / "best_r008_h016_w025_tau5"
        / "best_r008_h016_w025_tau5_raw.png",
        "init": ROOT / "deliverables" / "ghostkill" / "BEST_bmw_pano.jpg",
        "diag": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_best_bmw_pano_fetch"
        / "BEST_bmw_pano"
        / "best_r008_h016_w025_tau5"
        / "best_r008_h016_w025_tau5_diagnostics.json",
        "gate": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_best_bmw_pano_fetch"
        / "BEST_bmw_pano"
        / "best_r008_h016_w025_tau5"
        / "best_r008_h016_w025_tau5_gate_gate.json",
        "verdict": "reject candidate: inherits BEST ghosting and adds generated vertical slabs",
    },
    {
        "label": "A1 input",
        "path": ROOT / "deliverables" / "a1_streetview_pipeline" / "A1_view_none_L1_vs_result.jpg",
        "extract": "a1_result",
        "init": None,
        "diag": None,
        "gate": None,
        "verdict": "baseline; parallax artifacts remain",
    },
    {
        "label": "A1 v14 raw tau5",
        "path": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_a1_view_none_fetch"
        / "A1_view_none_bmw"
        / "a1view_r008_h016_w025_tau5"
        / "a1view_r008_h016_w025_tau5_raw.png",
        "init": ROOT / "deliverables" / "a1_streetview_pipeline" / "A1_view_none_L1_vs_result.jpg",
        "init_extract": "a1_result",
        "diag": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_a1_view_none_fetch"
        / "A1_view_none_bmw"
        / "a1view_r008_h016_w025_tau5"
        / "a1view_r008_h016_w025_tau5_diagnostics.json",
        "gate": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_a1_view_none_fetch"
        / "A1_view_none_bmw"
        / "a1view_r008_h016_w025_tau5"
        / "a1view_r008_h016_w025_tau5_gate_gate.json",
        "verdict": "reject candidate: right seam becomes a visible vertical slice",
    },
    {
        "label": "old v14 raw hardselect",
        "path": ROOT
        / "deliverables"
        / "dit360_seam_completion"
        / "runs_v14_trimap_clamp_bmw"
        / "trimap_r008_h016_w025_tau5"
        / "trimap_r008_h016_w025_tau5_raw_fullres_1024x2048.png",
        "init": None,
        "diag": ROOT
        / "deliverables"
        / "dit360_seam_completion"
        / "runs_v14_trimap_clamp_bmw"
        / "trimap_r008_h016_w025_tau5"
        / "trimap_r008_h016_w025_tau5_diagnostics.json",
        "gate": None,
        "verdict": "reference method; visually closer in places but still shows generated vertical seam material",
    },
    {
        "label": "DB36 redline core",
        "path": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db36_user_redline_mask"
        / "G_bmw_pano_user_redline_tau5_fetch"
        / "G_bmw_pano_user_redline_tau5"
        / "db36_user_redline_tau5"
        / "db36_user_redline_tau5_corecompose.png",
        "init": ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg",
        "diag": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db36_user_redline_mask"
        / "G_bmw_pano_user_redline_tau5_fetch"
        / "G_bmw_pano_user_redline_tau5"
        / "db36_user_redline_tau5"
        / "db36_user_redline_tau5_diagnostics.json",
        "gate": None,
        "verdict": "negative control; fake pale ground slabs/holes",
    },
]


def read_json(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_rgb(path: Path) -> np.ndarray:
    if not path.exists():
        raise FileNotFoundError(path)
    return np.array(Image.open(path).convert("RGB"))


def extract_a1_result(arr: np.ndarray) -> np.ndarray:
    h, w = arr.shape[:2]
    panel_h = round(w * 1024 / 2048)
    res_y0 = 30 + panel_h + 30
    res = arr[res_y0 : res_y0 + panel_h, :]
    return resize_to(res, (2048, 1024))


def load_case_rgb(path: Path, extract: str | None = None) -> np.ndarray:
    arr = load_rgb(path)
    if extract == "a1_result":
        return extract_a1_result(arr)
    return arr


def resize_to(arr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return np.array(Image.fromarray(arr).resize(size, Image.Resampling.BICUBIC))


def crop(arr: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    h, w = arr.shape[:2]
    if (w, h) != (2048, 1024):
        arr = resize_to(arr, (2048, 1024))
    x0, y0, x1, y1 = box
    return arr[y0:y1, x0:x1]


def fit_width(arr: np.ndarray, width: int) -> np.ndarray:
    h, w = arr.shape[:2]
    height = max(1, int(round(h * width / w)))
    return np.array(Image.fromarray(arr).resize((width, height), Image.Resampling.BICUBIC))


def add_label(arr: np.ndarray, text: str, width: int | None = None) -> np.ndarray:
    if width is not None:
        arr = fit_width(arr, width)
    img = Image.fromarray(arr)
    band_h = 34
    out = Image.new("RGB", (img.width, img.height + band_h), (0, 0, 0))
    out.paste(img, (0, band_h))
    draw = ImageDraw.Draw(out)
    font = ImageFont.load_default()
    draw.text((8, 10), text[:110], fill=(255, 255, 255), font=font)
    return np.array(out)


def pad_h(arr: np.ndarray, height: int) -> np.ndarray:
    if arr.shape[0] >= height:
        return arr
    pad = np.zeros((height - arr.shape[0], arr.shape[1], 3), dtype=np.uint8)
    return np.vstack([arr, pad])


def roi_mae(candidate: np.ndarray, init: np.ndarray | None, box: tuple[int, int, int, int]) -> float | None:
    if init is None:
        return None
    a = crop(candidate, box).astype(np.float32)
    b = crop(init, box).astype(np.float32)
    return float(np.mean(np.abs(a - b)))


def build_board() -> tuple[list[dict], np.ndarray]:
    rows = []
    manifest_rows = []
    for case in CASES:
        arr = load_case_rgb(case["path"], case.get("extract"))
        init = load_case_rgb(case["init"], case.get("init_extract")) if case.get("init") else None
        diag = read_json(case.get("diag"))
        gate = read_json(case.get("gate"))

        full_panel = add_label(fit_width(arr, 360), case["label"])
        roi_panels = []
        for name, box in ROIS.items():
            roi = crop(arr, box)
            label = f"{name}"
            mae = roi_mae(arr, init, box)
            if mae is not None:
                label += f" | mae {mae:.1f}"
            roi_panels.append(add_label(roi, label, width=360))

        row_parts = [full_panel, *roi_panels]
        max_h = max(part.shape[0] for part in row_parts)
        rows.append(np.hstack([pad_h(part, max_h) for part in row_parts]))

        manifest_rows.append(
            {
                "label": case["label"],
                "path": str(case["path"].relative_to(ROOT)),
                "exists": case["path"].exists(),
                "verdict": case["verdict"],
                "diagnostics_path": str(case["diag"].relative_to(ROOT)) if case.get("diag") and case["diag"].exists() else None,
                "gate_path": str(case["gate"].relative_to(ROOT)) if case.get("gate") and case["gate"].exists() else None,
                "gate_pass": gate.get("PASS", gate.get("pass")),
                "netnew_count": gate.get("netnew_count"),
                "core_fraction": diag.get("core_fraction"),
                "halo_fraction": diag.get("halo_fraction"),
                "far_fraction": diag.get("far_fraction"),
                "raw_core_mae_vs_init": diag.get("raw_core_mae_vs_init"),
                "raw_halo_mae_vs_init": diag.get("raw_halo_mae_vs_init"),
                "raw_far_mae_vs_init": diag.get("raw_far_mae_vs_init"),
                "method": diag.get("method"),
                "roi_mae_vs_init": {
                    name: roi_mae(arr, init, box) for name, box in ROIS.items()
                }
                if init is not None
                else None,
            }
        )

    max_w = max(row.shape[1] for row in rows)
    rows = [np.hstack([row, np.zeros((row.shape[0], max_w - row.shape[1], 3), dtype=np.uint8)]) if row.shape[1] < max_w else row for row in rows]
    board = np.vstack(rows)
    return manifest_rows, board


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows, board = build_board()
    board_path = OUT / "db39_v14_trimap_replay_board.jpg"
    Image.fromarray(board).save(board_path, quality=92)

    existing_matrix = {
        "G_bmw_pano": sorted(
            str(p.relative_to(ROOT))
            for p in (
                ROOT
                / "deliverables"
                / "dit360_v2"
                / "db14_g_bmw_pano_fetch"
                / "G_bmw_pano"
            ).glob("g_r008_h016_w025_tau*/g_r008_h016_w025_tau*_raw.png")
        ),
        "BEST_bmw_pano": sorted(
            str(p.relative_to(ROOT))
            for p in (
                ROOT
                / "deliverables"
                / "dit360_v2"
                / "db14_best_bmw_pano_fetch"
                / "BEST_bmw_pano"
            ).glob("best_r008_h016_w025_tau*/best_r008_h016_w025_tau*_raw.png")
        ),
        "A1_view_none": sorted(
            str(p.relative_to(ROOT))
            for p in (
                ROOT
                / "deliverables"
                / "dit360_v2"
                / "db14_a1_view_none_fetch"
                / "A1_view_none_bmw"
            ).glob("a1view_r008_h016_w025_tau*/a1view_r008_h016_w025_tau*_raw.png")
        ),
    }

    manifest = {
        "db": "DB-20260604-39",
        "question": "Does exact v14 trimap-clamp raw/soft/core replay solve the user-marked G-family seam?",
        "rois_2048x1024": ROIS,
        "method_summary": {
            "runner": "scripts/phase3/run_dit360_trimap_clamp.py",
            "mask_convention": "white/255 preserves source; black/0 generates",
            "old_setting": "r008_h016_w025_tau5",
            "tri_map": "core free generation, halo weak latent source clamp, far strong source clamp",
        },
        "vision_verdict": "reject as seam solution: existing exact v14 replay results reduce some local texture breaks but repeatedly introduce vertical slice/slab artifacts or retain the original long/right seam. DB36 remains a separate negative control, not the full v14 method.",
        "a100_needed": False,
        "a100_reason": "Existing fetched DB14 outputs already cover G tau5/8/12, BEST tau5, and A1 tau5/8/12 with the requested r008/h016/w025 trimap-clamp family. Re-running the same matrix would spend A100 without new evidence.",
        "existing_v14_replay_matrix": existing_matrix,
        "rows": rows,
    }
    with (OUT / "db39_v14_trimap_replay_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(json.dumps({"board": str(board_path), "manifest": str(OUT / "db39_v14_trimap_replay_manifest.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
