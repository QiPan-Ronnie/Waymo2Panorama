"""
N1 Phase A — Build visual A/B panel from r-sweep outputs.

Loads the ERP PNGs produced by run_l1_finite_radius.py (one per r value), crops
the Porsche-wheel and BMW-wheel zoom regions, and stacks them into multi-row
panels for the visual gate review.

Output PNGs (written to --out-dir):
    porsche_zoom_n1_phase_a.png  — N-row stack (one row per r), each row labeled
    bmw_zoom_n1_phase_a.png      — same for BMW
    full_erp_n1_phase_a.png      — N-row stack of full ERPs (downsampled)
    porsche_diff_n1_phase_a.png  — porsche zoom diff vs 'inf' baseline (amp 4x)
    bmw_diff_n1_phase_a.png      — same for BMW

Crop coords (fractional, calibrated to log 02a00399 anchor 0):
    Porsche: col ~0.366 * W (center), row ~0.488-0.635 * H, half-w 200/4096 * W
    BMW:     col ~0.854 * W (center), row ~0.439-0.635 * H, half-w 250/4096 * W

These fractions are derived from the Stage 3 v5 audit at ERP res 2048x4096 and
will rescale correctly to any 2:1 ERP.

Usage:
    python scripts/phase3/make_n1_phase_a_panel.py \\
        --in-dir outputs/phase3/n1_phase_a/02a00399/anchor_0 \\
        --out-dir deliverables/n1_phase_a
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


# Fractional crop coords (calibrated to 2048x4096 from Stage 3 v5 audit)
PORSCHE_COL_CENTER_FRAC = 1500 / 4096
PORSCHE_ROW_TOP_FRAC = 1000 / 2048
PORSCHE_ROW_BOT_FRAC = 1300 / 2048
PORSCHE_HALF_W_FRAC = 200 / 4096

BMW_COL_CENTER_FRAC = 3500 / 4096
BMW_ROW_TOP_FRAC = 900 / 2048
BMW_ROW_BOT_FRAC = 1300 / 2048
BMW_HALF_W_FRAC = 250 / 4096


def crop_zoom(
    erp_img: np.ndarray,
    col_center_frac: float,
    row_top_frac: float,
    row_bot_frac: float,
    half_w_frac: float,
) -> np.ndarray:
    """Crop a zoom region from an ERP using fractional coords (so it scales
    with ERP resolution)."""
    h, w = erp_img.shape[:2]
    col_c = int(col_center_frac * w)
    col_lo = max(0, int(col_c - half_w_frac * w))
    col_hi = min(w, int(col_c + half_w_frac * w))
    row_lo = max(0, int(row_top_frac * h))
    row_hi = min(h, int(row_bot_frac * h))
    return erp_img[row_lo:row_hi, col_lo:col_hi]


def add_label(img: np.ndarray, text: str, font_size: int = 22) -> np.ndarray:
    """Add a text label in the top-left corner (black outline + white fill)."""
    pil = Image.fromarray(img)
    draw = ImageDraw.Draw(pil)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except (OSError, IOError):
        font = ImageFont.load_default()
    x, y = 8, 6
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((x + dx, y + dy), text, font=font, fill="black")
    draw.text((x, y), text, font=font, fill="white")
    return np.array(pil)


def stack_rows(rows: list[np.ndarray]) -> np.ndarray:
    """Vertically stack rows; pad narrower rows to the widest width (black)."""
    if not rows:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    max_w = max(r.shape[1] for r in rows)
    padded = []
    for r in rows:
        if r.shape[1] < max_w:
            pad = np.zeros((r.shape[0], max_w - r.shape[1], 3), dtype=r.dtype)
            r = np.concatenate([r, pad], axis=1)
        padded.append(r)
    return np.concatenate(padded, axis=0)


def amp_diff(img: np.ndarray, ref: np.ndarray, amp: float = 4.0) -> np.ndarray:
    """|img - ref| * amp, clipped to uint8."""
    diff = np.abs(img.astype(np.float32) - ref.astype(np.float32)) * amp
    return np.clip(diff, 0, 255).astype(np.uint8)


def main() -> int:
    ap = argparse.ArgumentParser(description="N1 Phase A — build visual A/B panel")
    ap.add_argument("--in-dir", type=Path, required=True,
                    help="Dir containing run_l1_finite_radius.py outputs (with summary.json)")
    ap.add_argument("--out-dir", type=Path, required=True,
                    help="Dir for panel PNGs")
    ap.add_argument("--full-erp-max-h", type=int, default=384,
                    help="Max row height for the full-ERP stack panel (downsample)")
    ap.add_argument("--diff-amp", type=float, default=4.0,
                    help="Amplification factor for diff panels (default 4x)")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = args.in_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.json missing: {summary_path}")
    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    print(f"[panel] {len(summary['results'])} r value(s) found", flush=True)

    # Load all ERPs first (keep in memory; ERPs are ~6 MB each at 1024x2048).
    erps: dict[str, np.ndarray] = {}
    labels: list[str] = []
    rs: dict[str, object] = {}
    for entry in summary["results"]:
        label = entry["label"]
        path = args.in_dir / entry["out_file"]
        if not path.exists():
            print(f"[panel] WARN: missing {path}", flush=True)
            continue
        erps[label] = np.asarray(Image.open(path).convert("RGB"))
        rs[label] = entry["r"]
        labels.append(label)

    # Pick 'inf' as the diff reference baseline (legacy L1, identical to plain).
    ref_label = "inf" if "inf" in erps else labels[0]
    print(f"[panel] diff reference: {ref_label}", flush=True)
    ref_erp = erps[ref_label]

    # Build the per-row crops.
    porsche_rows: list[np.ndarray] = []
    bmw_rows: list[np.ndarray] = []
    full_rows: list[np.ndarray] = []
    porsche_diff_rows: list[np.ndarray] = []
    bmw_diff_rows: list[np.ndarray] = []

    ref_porsche = crop_zoom(ref_erp, PORSCHE_COL_CENTER_FRAC, PORSCHE_ROW_TOP_FRAC,
                            PORSCHE_ROW_BOT_FRAC, PORSCHE_HALF_W_FRAC)
    ref_bmw = crop_zoom(ref_erp, BMW_COL_CENTER_FRAC, BMW_ROW_TOP_FRAC,
                        BMW_ROW_BOT_FRAC, BMW_HALF_W_FRAC)

    for label in labels:
        erp = erps[label]
        r = rs[label]
        text = f"{label} (r={r})"

        porsche = crop_zoom(erp, PORSCHE_COL_CENTER_FRAC, PORSCHE_ROW_TOP_FRAC,
                            PORSCHE_ROW_BOT_FRAC, PORSCHE_HALF_W_FRAC)
        bmw = crop_zoom(erp, BMW_COL_CENTER_FRAC, BMW_ROW_TOP_FRAC,
                        BMW_ROW_BOT_FRAC, BMW_HALF_W_FRAC)

        porsche_rows.append(add_label(porsche, text))
        bmw_rows.append(add_label(bmw, text))

        if porsche.shape == ref_porsche.shape:
            p_diff = amp_diff(porsche, ref_porsche, amp=args.diff_amp)
        else:
            p_diff = np.zeros_like(porsche)
        if bmw.shape == ref_bmw.shape:
            b_diff = amp_diff(bmw, ref_bmw, amp=args.diff_amp)
        else:
            b_diff = np.zeros_like(bmw)

        diff_label = f"{label} diff vs {ref_label} (x{args.diff_amp:g})"
        porsche_diff_rows.append(add_label(p_diff, diff_label))
        bmw_diff_rows.append(add_label(b_diff, diff_label))

        # Full ERP (downsample for size)
        if erp.shape[0] > args.full_erp_max_h:
            scale = args.full_erp_max_h / erp.shape[0]
            new_w = int(erp.shape[1] * scale)
            full = np.asarray(Image.fromarray(erp).resize((new_w, args.full_erp_max_h),
                                                          resample=Image.LANCZOS))
        else:
            full = erp
        full_rows.append(add_label(full, text))

    if porsche_rows:
        out = args.out_dir / "porsche_zoom_n1_phase_a.png"
        Image.fromarray(stack_rows(porsche_rows)).save(out)
        print(f"[panel] porsche zoom -> {out}", flush=True)
    if bmw_rows:
        out = args.out_dir / "bmw_zoom_n1_phase_a.png"
        Image.fromarray(stack_rows(bmw_rows)).save(out)
        print(f"[panel] bmw zoom -> {out}", flush=True)
    if porsche_diff_rows:
        out = args.out_dir / "porsche_diff_n1_phase_a.png"
        Image.fromarray(stack_rows(porsche_diff_rows)).save(out)
        print(f"[panel] porsche diff -> {out}", flush=True)
    if bmw_diff_rows:
        out = args.out_dir / "bmw_diff_n1_phase_a.png"
        Image.fromarray(stack_rows(bmw_diff_rows)).save(out)
        print(f"[panel] bmw diff -> {out}", flush=True)
    if full_rows:
        out = args.out_dir / "full_erp_n1_phase_a.png"
        Image.fromarray(stack_rows(full_rows)).save(out)
        print(f"[panel] full erp stack -> {out}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
