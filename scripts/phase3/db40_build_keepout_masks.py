from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "deliverables" / "dit360_v2" / "db40_v14_mask_alignment" / "masks"

H, W = 1024, 2048
HALO_PX = 16

# Manual ERP keepouts from the user's marked right-BMW artifact area.
# These are intentionally larger than the white BMW body: include wheel/body,
# the bright generated slab location, and adjacent building edge.
RIGHT_BMW_KEEPOUT = (1340, 305, 1868, 705)
LOWER_RIGHT_GROUND_KEEPOUT = (1520, 585, 2048, 815)

CASES = [
    {
        "name": "A1_view_none_bmw",
        "prefix": "a1keep",
        "init": ROOT / "deliverables" / "a1_streetview_pipeline" / "A1_view_none_L1_vs_result.jpg",
        "extract": "a1_result",
        "model_mask": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_a1_view_none_fetch"
        / "A1_view_none_bmw"
        / "a1view_r008_h016_w025_tau5"
        / "a1view_r008_h016_w025_tau5_model_mask_far_preserve.png",
    },
    {
        "name": "G_bmw_pano",
        "prefix": "gkeep",
        "init": ROOT / "deliverables" / "ghostkill" / "G_bmw_pano.jpg",
        "model_mask": ROOT
        / "deliverables"
        / "dit360_v2"
        / "db14_g_bmw_pano_fetch"
        / "G_bmw_pano"
        / "g_r008_h016_w025_tau5"
        / "g_r008_h016_w025_tau5_model_mask_far_preserve.png",
    },
]


def load_rgb(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def resize_erp(arr: np.ndarray) -> np.ndarray:
    if arr.shape[:2] == (H, W):
        return arr
    return cv2.resize(arr, (W, H), interpolation=cv2.INTER_AREA)


def extract_a1_result(arr: np.ndarray) -> np.ndarray:
    h, w = arr.shape[:2]
    panel_h = round(w * H / W)
    y0 = 30 + panel_h + 30
    return cv2.resize(arr[y0 : y0 + panel_h, :], (W, H), interpolation=cv2.INTER_AREA)


def load_init(case: dict) -> np.ndarray:
    arr = load_rgb(case["init"])
    if case.get("extract") == "a1_result":
        arr = extract_a1_result(arr)
    return resize_erp(arr)


def load_generate_from_model_mask(path: Path) -> np.ndarray:
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise FileNotFoundError(path)
    gray = cv2.resize(gray, (W, H), interpolation=cv2.INTER_NEAREST)
    return gray < 128


def erode(mask: np.ndarray, radius: int) -> np.ndarray:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    return cv2.erode(mask.astype(np.uint8), k).astype(bool)


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    return cv2.dilate(mask.astype(np.uint8), k).astype(bool)


def rect_mask(box: tuple[int, int, int, int]) -> np.ndarray:
    out = np.zeros((H, W), dtype=bool)
    x0, y0, x1, y1 = box
    out[y0:y1, x0:x1] = True
    return out


def preview(init: np.ndarray, core: np.ndarray, keepout: np.ndarray) -> np.ndarray:
    halo = dilate(core, HALO_PX) & ~core
    out = init.astype(np.float32).copy()
    red = np.zeros_like(out)
    red[..., 0] = 255
    yellow = np.zeros_like(out)
    yellow[..., 0] = 255
    yellow[..., 1] = 210
    cyan = np.zeros_like(out)
    cyan[..., 1] = 230
    cyan[..., 2] = 255
    out[halo] = 0.55 * out[halo] + 0.45 * yellow[halo]
    out[core] = 0.50 * out[core] + 0.50 * red[core]
    out[keepout] = 0.50 * out[keepout] + 0.50 * cyan[keepout]
    return np.clip(out, 0, 255).astype(np.uint8)


def label(arr: np.ndarray, text: str, width: int = 560) -> np.ndarray:
    h, w = arr.shape[:2]
    nh = max(1, round(h * width / w))
    img = Image.fromarray(arr).resize((width, nh), Image.Resampling.BICUBIC)
    band_h = 34
    out = Image.new("RGB", (width, nh + band_h), (0, 0, 0))
    out.paste(img, (0, band_h))
    draw = ImageDraw.Draw(out)
    draw.text((8, 10), text, fill=(255, 255, 255), font=ImageFont.load_default())
    return np.array(out)


def pad_h(arr: np.ndarray, height: int) -> np.ndarray:
    if arr.shape[0] >= height:
        return arr
    return np.vstack([arr, np.zeros((height - arr.shape[0], arr.shape[1], 3), dtype=np.uint8)])


def crop(arr: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    return arr[y0:y1, x0:x1]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    keepout_raw = rect_mask(RIGHT_BMW_KEEPOUT) | rect_mask(LOWER_RIGHT_GROUND_KEEPOUT)
    keepout_expanded = dilate(keepout_raw, HALO_PX)

    board_rows = []
    rows = []
    for case in CASES:
        init = load_init(case)
        old_gen = load_generate_from_model_mask(case["model_mask"])
        # The saved model mask is core+halo generate. Erode by the known halo radius
        # to recover the approximate original core strip used by run_dit360_trimap_clamp.
        old_core = erode(old_gen, HALO_PX)
        keep_core = old_core & ~keepout_expanded
        preserve = np.where(keep_core, 0, 255).astype(np.uint8)
        stem = f"{case['prefix']}_r008_h016_w025_tau5_rightbmw_keepout"
        Image.fromarray(preserve, mode="L").save(OUT / f"{stem}_preserve_nonseam.png")

        old_prev = preview(init, old_core, keepout_raw)
        new_prev = preview(init, keep_core, keepout_raw)
        Image.fromarray(old_prev).save(OUT / f"{stem}_oldcore_keepout_overlay.jpg", quality=92)
        Image.fromarray(new_prev).save(OUT / f"{stem}_preview.jpg", quality=92)

        parts = [
            label(init, f"{case['name']} init"),
            label(crop(old_prev, RIGHT_BMW_KEEPOUT), "old core over right BMW"),
            label(crop(new_prev, RIGHT_BMW_KEEPOUT), "keepout-carved core"),
            label(crop(new_prev, LOWER_RIGHT_GROUND_KEEPOUT), "lower-right keepout"),
        ]
        max_h = max(p.shape[0] for p in parts)
        row = np.hstack([pad_h(p, max_h) for p in parts])
        board_rows.append(row)
        rows.append(
            {
                "case": case["name"],
                "mask": str((OUT / f"{stem}_preserve_nonseam.png").relative_to(ROOT)),
                "preview": str((OUT / f"{stem}_preview.jpg").relative_to(ROOT)),
                "old_core_fraction": float(old_core.mean()),
                "new_core_fraction": float(keep_core.mean()),
                "removed_core_fraction": float((old_core & ~keep_core).mean()),
                "keepout_fraction": float(keepout_raw.mean()),
                "keepout_expanded_fraction": float(keepout_expanded.mean()),
                "right_bmw_keepout": RIGHT_BMW_KEEPOUT,
                "lower_right_ground_keepout": LOWER_RIGHT_GROUND_KEEPOUT,
                "halo_px": HALO_PX,
                "derivation": "old model_mask black region eroded by halo_px to approximate v14 core, then right-BMW/lower-right keepout expanded by halo_px is removed from core",
            }
        )

    board = np.vstack(board_rows)
    Image.fromarray(board).save(OUT / "db40_keepout_mask_preview_board.jpg", quality=92)
    manifest = {
        "db": "DB-20260604-40",
        "purpose": "Candidate-specific right-BMW hard-preserve masks for bounded A100 test.",
        "mask_convention": "white/255 preserve source; black/0 generate core",
        "rows": rows,
        "a100_candidate_cases": [
            "A1 first: run the keepout mask with old v14 tau5/guidance2.8 prompt and a stricter prompt; proceed to G only if A1 improves visibly.",
        ],
    }
    with (OUT / "db40_keepout_mask_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(json.dumps({"out": str(OUT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
