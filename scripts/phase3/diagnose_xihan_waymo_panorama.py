"""Diagnose cross-cam brightness shift on Xihan's Waymo panorama.

Xihan handed us a pre-stitched ERP panorama (distance-to-boundary blending)
plus 8 individual cam jpgs. We DO NOT have intrinsics/extrinsics, so this
diagnoses purely from the stitched panorama:

  1) Find cam region boundaries from |dY/dx| spike (the visible seams).
  2) Per-region: median Y (BT.601 luminance), median Cr, Cb.
  3) Quantify pairwise neighbor luminance gap (dB ratio + delta Y).
  4) Annotate the panorama with detected seam positions.
  5) Print a Y / Cr / Cb table per region.

Input  : meeting/5.22_meeting with xihan/xihan/assets/xihan task/c4b1d01f...jpg
Output : deliverables/xihan/diagnose_waymo_*.png + diagnose_waymo.json
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
PANO = (REPO / "meeting" / "5.22_meeting with xihan" / "xihan" / "assets"
        / "xihan task" / "c4b1d01f8f6616e59a2d203b879db1d3.jpg")
OUT_DIR = REPO / "deliverables" / "xihan"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_pano_rgb(path: Path) -> np.ndarray:
    bgr = cv2.imread(str(path))
    assert bgr is not None, f"cannot read {path}"
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def find_vertical_seams(
    rgb: np.ndarray,
    band_top_frac: float = 0.30,
    band_bot_frac: float = 0.55,
    smooth_kernel: int = 21,
    expected_n_seams: int = 7,
    min_separation_frac: float = 0.04,
) -> list[int]:
    """Detect vertical cam seams from |dY/dx| spikes in a horizontal band that
    contains sky+road (avoids the top/bottom black regions).

    Returns x-positions of seams in pixel coords, sorted left-to-right.
    """
    H, W = rgb.shape[:2]
    ycrcb = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    Y = ycrcb[..., 0].astype(np.float32)

    # Use a horizontal band that's likely non-black
    r0 = int(H * band_top_frac)
    r1 = int(H * band_bot_frac)
    band = Y[r0:r1, :]

    # |dY/dx| then column-aggregate (median over rows is robust to texture)
    # use sobel for noise resilience
    sob = cv2.Sobel(band, cv2.CV_32F, 1, 0, ksize=3)
    abs_sob = np.abs(sob)
    col_score = np.median(abs_sob, axis=0)

    # smooth so we pick one peak per seam, not per pixel
    if smooth_kernel > 1:
        k = np.ones(smooth_kernel, dtype=np.float32) / smooth_kernel
        col_score = np.convolve(col_score, k, mode="same")

    # mask out outermost ~5% (panorama edges with strong black-to-content step)
    mask_edge = int(W * 0.04)
    col_score[:mask_edge] = 0.0
    col_score[-mask_edge:] = 0.0

    # peak picking with non-max suppression: greedy by score, enforce min spacing
    min_sep = max(1, int(W * min_separation_frac))
    candidates = np.argsort(col_score)[::-1]
    picked: list[int] = []
    for c in candidates:
        if col_score[c] <= 0:
            break
        if all(abs(int(c) - p) >= min_sep for p in picked):
            picked.append(int(c))
        if len(picked) >= expected_n_seams:
            break

    return sorted(picked)


def region_stats(rgb: np.ndarray, x0: int, x1: int) -> dict:
    """Median Y/Cr/Cb over the central band (skip black top/bottom)."""
    H, W = rgb.shape[:2]
    r0 = int(H * 0.30)
    r1 = int(H * 0.55)
    crop = rgb[r0:r1, x0:x1]
    # ignore near-black pixels (Y < 8) — they're the alpha holes of distance blending
    ycrcb = cv2.cvtColor(crop, cv2.COLOR_RGB2YCrCb).astype(np.float32)
    Y = ycrcb[..., 0]
    Cr = ycrcb[..., 1]
    Cb = ycrcb[..., 2]
    valid = Y >= 8
    if valid.sum() < 100:
        return {"x0": x0, "x1": x1, "valid_px": int(valid.sum()),
                "Y_med": None, "Cr_med": None, "Cb_med": None}
    return {
        "x0": int(x0), "x1": int(x1),
        "valid_px": int(valid.sum()),
        "Y_med":  float(np.median(Y[valid])),
        "Cr_med": float(np.median(Cr[valid])),
        "Cb_med": float(np.median(Cb[valid])),
    }


def annotate_seams(rgb: np.ndarray, seams: list[int], regions: list[dict]) -> np.ndarray:
    """Draw seam lines + region Y values."""
    H, W = rgb.shape[:2]
    out = rgb.copy()
    for x in seams:
        cv2.line(out, (x, 0), (x, H - 1), (255, 255, 0), 2)
    for r in regions:
        if r["Y_med"] is None:
            continue
        cx = (r["x0"] + r["x1"]) // 2
        # caption near the top of the content band
        cv2.putText(
            out, f"Y={r['Y_med']:.0f}",
            (max(cx - 70, 0), int(H * 0.32)),
            cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3, cv2.LINE_AA,
        )
        cv2.putText(
            out, f"Y={r['Y_med']:.0f}",
            (max(cx - 70, 0), int(H * 0.32)),
            cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 1, cv2.LINE_AA,
        )
    return out


def main() -> None:
    rgb = load_pano_rgb(PANO)
    H, W = rgb.shape[:2]
    print(f"loaded panorama: {W}x{H}")

    # Xihan said 8 cams → expect up to 7 interior seams (plus wrap)
    seams = find_vertical_seams(rgb, expected_n_seams=7)
    print(f"detected {len(seams)} interior seams at x = {seams}")

    # Build region list: x=0 → first seam → second seam → ... → last seam → W
    boundaries = [0] + seams + [W]
    regions = []
    for i in range(len(boundaries) - 1):
        x0 = boundaries[i]
        x1 = boundaries[i + 1]
        if x1 - x0 < 20:
            continue
        stats = region_stats(rgb, x0, x1)
        stats["region_idx"] = i
        regions.append(stats)

    # Y-gap analysis
    Y_vals = [r["Y_med"] for r in regions if r["Y_med"] is not None]
    if Y_vals:
        y_min, y_max = float(min(Y_vals)), float(max(Y_vals))
        ratio = (y_max + 1) / (y_min + 1)
        gap_db = 20.0 * np.log10(ratio)
        print(f"Y range: {y_min:.1f} - {y_max:.1f}  ratio={ratio:.2f}x  gap={gap_db:.2f} dB")
    else:
        y_min, y_max, gap_db = None, None, None

    # neighbor pair Y diffs
    pair_diffs = []
    for i in range(len(regions) - 1):
        a, b = regions[i], regions[i + 1]
        if a["Y_med"] is not None and b["Y_med"] is not None:
            pair_diffs.append({
                "left_region": a["region_idx"], "right_region": b["region_idx"],
                "left_Y": a["Y_med"], "right_Y": b["Y_med"],
                "delta_Y": b["Y_med"] - a["Y_med"],
            })
            print(f"  seam {a['region_idx']}->{b['region_idx']}: "
                  f"Y {a['Y_med']:.1f} -> {b['Y_med']:.1f}  delta={b['Y_med']-a['Y_med']:+.1f}")

    # annotate
    bgr_annot = cv2.cvtColor(annotate_seams(rgb, seams, regions), cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(OUT_DIR / "diagnose_waymo_annotated.png"), bgr_annot)

    # save raw input as PNG for stable Markdown embedding
    cv2.imwrite(str(OUT_DIR / "diagnose_waymo_raw.png"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))

    summary = {
        "pano_path": str(PANO.relative_to(REPO)),
        "pano_size_wh": [int(W), int(H)],
        "detected_seams_x": seams,
        "regions": regions,
        "Y_range": [y_min, y_max] if Y_vals else None,
        "Y_gap_db": gap_db,
        "neighbor_pair_diffs": pair_diffs,
    }
    out_json = OUT_DIR / "diagnose_waymo.json"
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"wrote {out_json}")


if __name__ == "__main__":
    main()
