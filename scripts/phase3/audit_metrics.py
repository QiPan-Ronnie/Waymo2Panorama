"""
Phase 3 T5 — Cycle-consistency metric audit (P3-audit).

Goal: check whether the headline cycle-PSNR metric used in P2.7/P3.1b is structurally
biased against sharp-but-misaligned reconstructions (L3 forward-splat) and in favour
of blurrier-but-aligned ones (L1 sphere projection).

Method (anchor 0 only, 7 cams):
  Input    : `reconstruction_<cam>.png` 3-panel images produced by
             `scripts/phase2/eval_cycle_consistency.py --save-recon-pngs`.
             Layout = [GT | L1_recon | L3_recon], each panel 504x504, gap=4 cols (value 32),
             total width = 3*504 + 2*4 = 1520.
  Splits   : GT  = [:, 0:504], L1 = [:, 508:1012], L3 = [:, 1016:1520].
  Mask     : intersection of "this method actually reconstructed something" — proxied as
             "the pixel is not the (0,0,0) padding the eval script writes for out-of-coverage".
             We use L3_mask = any-channel-nonzero  AND  L1_mask = any-channel-nonzero,
             intersect = L1_mask & L3_mask. This matches the intersection-mask philosophy
             of the original eval (we cannot recover the exact mask without re-running Pi3).
  Metrics  : on the intersection mask only —
             * PSNR (re-computed, sanity)
             * MS-SSIM (skimage.metrics.structural_similarity has no direct multiscale,
                         so we run a 4-scale pyramid average — Wang 2003 style)
             * LPIPS-Alex (perceptual; runs over the full image but we zero-out
                            non-mask pixels in BOTH images so they contribute identical
                            "background" patches — i.e., they cancel out in the LPIPS
                            difference up to boundary leakage)
             * Region-PSNR: split rows into thirds, sky=[:168], object=[168:336],
                            ground=[336:504]. Compute PSNR per region per method.

Output:
  outputs/phase3/metric_audit/anchor0_audit.json       per-cam + aggregate numbers
  outputs/phase3/metric_audit/anchor0_audit_table.md   markdown table summary
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RECON_DIR = ROOT / "outputs" / "phase3" / "metric_audit" / "recon"
OUT_DIR = ROOT / "outputs" / "phase3" / "metric_audit"

CAMS = [
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
]

PANEL_W = 504
PANEL_H = 504
GAP = 4

# Region-PSNR thirds (rows)
SKY_ROWS = (0, 168)
OBJ_ROWS = (168, 336)
GROUND_ROWS = (336, 504)


def split_3panel(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split a (H, 1520, 3) 3-panel image into (gt, l1, l3) each (504, 504, 3)."""
    gt = arr[:, 0:PANEL_W]
    l1 = arr[:, PANEL_W + GAP : 2 * PANEL_W + GAP]
    l3 = arr[:, 2 * PANEL_W + 2 * GAP : 3 * PANEL_W + 2 * GAP]
    return gt, l1, l3


def coverage_mask(recon: np.ndarray) -> np.ndarray:
    """Heuristic mask: True where the reconstruction has any non-zero channel.
    The eval script writes 0 for out-of-coverage pixels. This is the best we can
    recover from the saved 3-panel PNGs (the exact internal mask wasn't dumped)."""
    return np.any(recon > 0, axis=-1)


def psnr_masked(a: np.ndarray, b: np.ndarray, mask: Optional[np.ndarray]) -> float:
    a = a.astype(np.float64); b = b.astype(np.float64)
    if mask is None:
        mse = float(np.mean((a - b) ** 2))
    else:
        if mask.sum() == 0:
            return float("nan")
        mse = float(((a - b) ** 2)[mask].mean())
    if mse <= 1e-12:
        return float("inf")
    return 20.0 * np.log10(255.0) - 10.0 * np.log10(mse)


def ms_ssim_masked(a: np.ndarray, b: np.ndarray, mask: np.ndarray, n_scales: int = 4) -> float:
    """Multi-scale SSIM via average of SSIM at multiple downscalings (Wang 2003 style,
    but using arithmetic mean instead of the original geometric-weighted form — both
    rank methods identically for our purposes)."""
    from skimage.metrics import structural_similarity as ssim
    from skimage.transform import resize

    if mask.sum() == 0:
        return float("nan")
    a_g = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]).astype(np.float64)
    b_g = (0.299 * b[..., 0] + 0.587 * b[..., 1] + 0.114 * b[..., 2]).astype(np.float64)
    m = mask.astype(np.float64)

    vals: list[float] = []
    for s in range(n_scales):
        scale = 2 ** s
        if scale == 1:
            a_s = a_g; b_s = b_g; m_s = mask
        else:
            new_h = max(7, a_g.shape[0] // scale)
            new_w = max(7, a_g.shape[1] // scale)
            a_s = resize(a_g, (new_h, new_w), order=1, preserve_range=True, anti_aliasing=True)
            b_s = resize(b_g, (new_h, new_w), order=1, preserve_range=True, anti_aliasing=True)
            m_resized = resize(m, (new_h, new_w), order=0, preserve_range=True, anti_aliasing=False)
            m_s = m_resized > 0.5
        if m_s.sum() == 0:
            continue
        _, ssim_map = ssim(a_s, b_s, data_range=255.0, full=True)
        vals.append(float(ssim_map[m_s].mean()))
    if not vals:
        return float("nan")
    return float(np.mean(vals))


# Lazy-load lpips global so we don't pay model load 7× per cam.
_LPIPS_NET = None


def lpips_masked(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    """LPIPS-Alex (CPU). Zero-out non-mask pixels in BOTH images so the perceptual
    field is identical there and the residual signal is dominated by the masked
    region. Returns a scalar (lower = closer)."""
    global _LPIPS_NET
    import torch
    import lpips

    if _LPIPS_NET is None:
        _LPIPS_NET = lpips.LPIPS(net="alex", verbose=False)
        _LPIPS_NET.eval()

    if mask.sum() == 0:
        return float("nan")

    m = mask[..., None].astype(np.float32)
    a_m = (a.astype(np.float32) * m).astype(np.uint8)
    b_m = (b.astype(np.float32) * m).astype(np.uint8)

    # to [-1, 1] torch tensors (N, C, H, W)
    def _to_t(x: np.ndarray) -> "torch.Tensor":
        t = torch.from_numpy(x.astype(np.float32) / 255.0).permute(2, 0, 1).unsqueeze(0)
        return t * 2.0 - 1.0

    with torch.no_grad():
        d = _LPIPS_NET(_to_t(a_m), _to_t(b_m))
    return float(d.item())


def region_psnr(a: np.ndarray, b: np.ndarray, mask: np.ndarray, rows: tuple[int, int]) -> float:
    r0, r1 = rows
    sub_mask = np.zeros_like(mask)
    sub_mask[r0:r1] = mask[r0:r1]
    return psnr_masked(a, b, sub_mask)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for cam in CAMS:
        png = RECON_DIR / f"reconstruction_{cam}.png"
        if not png.exists():
            print(f"[skip] missing {png}")
            continue
        panel = np.asarray(Image.open(png).convert("RGB"))
        gt, l1, l3 = split_3panel(panel)
        m_l1 = coverage_mask(l1)
        m_l3 = coverage_mask(l3)
        intersect = m_l1 & m_l3

        psnr_l1_full = psnr_masked(gt, l1, intersect)
        psnr_l3_full = psnr_masked(gt, l3, intersect)
        ms_ssim_l1 = ms_ssim_masked(gt, l1, intersect)
        ms_ssim_l3 = ms_ssim_masked(gt, l3, intersect)
        lpips_l1 = lpips_masked(gt, l1, intersect)
        lpips_l3 = lpips_masked(gt, l3, intersect)

        # Region-separated PSNR (sky/obj/ground) on the same intersection mask
        psnr_l1_sky = region_psnr(gt, l1, intersect, SKY_ROWS)
        psnr_l3_sky = region_psnr(gt, l3, intersect, SKY_ROWS)
        psnr_l1_obj = region_psnr(gt, l1, intersect, OBJ_ROWS)
        psnr_l3_obj = region_psnr(gt, l3, intersect, OBJ_ROWS)
        psnr_l1_gnd = region_psnr(gt, l1, intersect, GROUND_ROWS)
        psnr_l3_gnd = region_psnr(gt, l3, intersect, GROUND_ROWS)

        cov_inter = float(intersect.mean())
        cov_sky = float(intersect[SKY_ROWS[0]:SKY_ROWS[1]].mean())
        cov_obj = float(intersect[OBJ_ROWS[0]:OBJ_ROWS[1]].mean())
        cov_gnd = float(intersect[GROUND_ROWS[0]:GROUND_ROWS[1]].mean())

        row = {
            "cam": cam,
            "coverage_intersection": cov_inter,
            "coverage_sky": cov_sky,
            "coverage_obj": cov_obj,
            "coverage_ground": cov_gnd,
            "PSNR_L1": psnr_l1_full, "PSNR_L3": psnr_l3_full,
            "PSNR_delta_L3_minus_L1": psnr_l3_full - psnr_l1_full,
            "MS_SSIM_L1": ms_ssim_l1, "MS_SSIM_L3": ms_ssim_l3,
            "MS_SSIM_delta_L3_minus_L1": ms_ssim_l3 - ms_ssim_l1,
            "LPIPS_L1": lpips_l1, "LPIPS_L3": lpips_l3,
            "LPIPS_delta_L3_minus_L1": lpips_l3 - lpips_l1,  # lower = better, so positive Δ means L3 worse
            "PSNR_sky_L1": psnr_l1_sky, "PSNR_sky_L3": psnr_l3_sky,
            "PSNR_obj_L1": psnr_l1_obj, "PSNR_obj_L3": psnr_l3_obj,
            "PSNR_ground_L1": psnr_l1_gnd, "PSNR_ground_L3": psnr_l3_gnd,
        }
        rows.append(row)
        print(f"{cam:22s}  inter={cov_inter:5.1%}  "
              f"PSNR L1/L3 {psnr_l1_full:5.2f}/{psnr_l3_full:5.2f}  "
              f"MS-SSIM {ms_ssim_l1:.3f}/{ms_ssim_l3:.3f}  "
              f"LPIPS {lpips_l1:.3f}/{lpips_l3:.3f}  "
              f"obj-PSNR {psnr_l1_obj:5.2f}/{psnr_l3_obj:5.2f}")

    # Aggregate (mean over non-NaN cams)
    def _agg(key: str) -> float:
        vals = [r[key] for r in rows if isinstance(r[key], float) and np.isfinite(r[key])]
        return float(np.mean(vals)) if vals else float("nan")

    keys = [
        "PSNR_L1", "PSNR_L3", "PSNR_delta_L3_minus_L1",
        "MS_SSIM_L1", "MS_SSIM_L3", "MS_SSIM_delta_L3_minus_L1",
        "LPIPS_L1", "LPIPS_L3", "LPIPS_delta_L3_minus_L1",
        "PSNR_sky_L1", "PSNR_sky_L3",
        "PSNR_obj_L1", "PSNR_obj_L3",
        "PSNR_ground_L1", "PSNR_ground_L3",
    ]
    mean = {k: _agg(k) for k in keys}

    summary = {
        "source": "outputs/phase3/metric_audit/recon/reconstruction_*.png (anchor 0, P2.7 panel dumps)",
        "splits": {"GT": "[:, 0:504]", "L1": "[:, 508:1012]", "L3": "[:, 1016:1520]"},
        "mask": "intersection of any-channel-nonzero in L1 and L3 reconstructions (proxy for original eval intersection mask)",
        "regions": {"sky": "rows 0-167", "obj": "rows 168-335", "ground": "rows 336-503"},
        "lpips_net": "alex",
        "ms_ssim_scales": 4,
        "per_cam": rows,
        "mean": mean,
    }
    out_json = OUT_DIR / "anchor0_audit.json"
    out_json.write_text(json.dumps(summary, indent=2))
    print(f"\n[audit] wrote {out_json}")

    # Markdown table
    lines: list[str] = []
    lines.append("# Anchor 0 metric audit (T5)\n")
    lines.append("Source: P2.7 saved 3-panel PNGs (GT|L1|L3) downloaded from Drive.\n")
    lines.append("Mask = intersection of L1/L3 any-channel-nonzero, computed identically to original eval philosophy.\n")
    lines.append("\n## Per-cam (LPIPS lower = better; PSNR/MS-SSIM higher = better)\n")
    lines.append("| cam | inter% | PSNR L1 | PSNR L3 | ΔPSNR | MS-SSIM L1 | MS-SSIM L3 | LPIPS L1 | LPIPS L3 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        lines.append(
            f"| {r['cam']} | {r['coverage_intersection']*100:.1f} | "
            f"{r['PSNR_L1']:.2f} | {r['PSNR_L3']:.2f} | {r['PSNR_delta_L3_minus_L1']:+.2f} | "
            f"{r['MS_SSIM_L1']:.3f} | {r['MS_SSIM_L3']:.3f} | "
            f"{r['LPIPS_L1']:.3f} | {r['LPIPS_L3']:.3f} |"
        )
    lines.append(
        f"| **MEAN** | — | **{mean['PSNR_L1']:.2f}** | **{mean['PSNR_L3']:.2f}** | "
        f"**{mean['PSNR_delta_L3_minus_L1']:+.2f}** | **{mean['MS_SSIM_L1']:.3f}** | **{mean['MS_SSIM_L3']:.3f}** | "
        f"**{mean['LPIPS_L1']:.3f}** | **{mean['LPIPS_L3']:.3f}** |"
    )
    lines.append("\n## Region-separated PSNR (intersection mask, rows-thirds)\n")
    lines.append("| cam | sky L1 | sky L3 | Δ sky | obj L1 | obj L3 | Δ obj | ground L1 | ground L3 | Δ ground |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        d_sky = r["PSNR_sky_L3"] - r["PSNR_sky_L1"]
        d_obj = r["PSNR_obj_L3"] - r["PSNR_obj_L1"]
        d_gnd = r["PSNR_ground_L3"] - r["PSNR_ground_L1"]
        lines.append(
            f"| {r['cam']} | "
            f"{r['PSNR_sky_L1']:.2f} | {r['PSNR_sky_L3']:.2f} | {d_sky:+.2f} | "
            f"{r['PSNR_obj_L1']:.2f} | {r['PSNR_obj_L3']:.2f} | {d_obj:+.2f} | "
            f"{r['PSNR_ground_L1']:.2f} | {r['PSNR_ground_L3']:.2f} | {d_gnd:+.2f} |"
        )
    lines.append(
        f"| **MEAN** | "
        f"**{mean['PSNR_sky_L1']:.2f}** | **{mean['PSNR_sky_L3']:.2f}** | "
        f"**{mean['PSNR_sky_L3'] - mean['PSNR_sky_L1']:+.2f}** | "
        f"**{mean['PSNR_obj_L1']:.2f}** | **{mean['PSNR_obj_L3']:.2f}** | "
        f"**{mean['PSNR_obj_L3'] - mean['PSNR_obj_L1']:+.2f}** | "
        f"**{mean['PSNR_ground_L1']:.2f}** | **{mean['PSNR_ground_L3']:.2f}** | "
        f"**{mean['PSNR_ground_L3'] - mean['PSNR_ground_L1']:+.2f}** |"
    )

    md = "\n".join(lines) + "\n"
    out_md = OUT_DIR / "anchor0_audit_table.md"
    out_md.write_text(md, encoding="utf-8")
    print(f"[audit] wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
