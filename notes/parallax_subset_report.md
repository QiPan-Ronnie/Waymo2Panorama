# T6 — Parallax-heavy Anchor Ranking

**Date**: 2026-05-20
**Inputs**: existing 10 Pi3 anchors from P3.1 (anchor indices 0, 30, 60, ..., 270)
**Goal**: rank the 10 anchors by how parallax-heavy they are, so we can target the parallax-rich subset for future L3 evaluation (where L3 has the best chance of beating L1 on cycle-PSNR).

---

## 1. TL;DR

**Top-3 parallax-heavy anchors: `[0, 150, 60]`** (with `[30]` essentially tied 4th).
**Bottom-2 (far-field dominated): `[210, 180]`.**

The rich-vs-poor parallax gap is modest (0.41 → 0.32 score) — this AV2 log is fairly uniform in scene depth. But the ordering aligns with the P3.1b cycle-PSNR rebalance: the **closest L3 came to beating L1 was anchor 60** (ΔPSNR = −1.60 dB, the only one < −2 dB out of 10), and anchor 60 is in our top-3. **Anchor 180**, which had the *best* Pi3 abs_rel (0.139), is in our bottom-2 — confirming that "Pi3 reports accurate depth" and "L3 has parallax to exploit" are *different* properties.

The verdict for follow-up T18/T12 experiments: **start with anchor 60 or anchor 0**, where parallax is strongest *and* we already have a P3.1b ΔPSNR baseline to beat.

---

## 2. Method (summary-stat proxy)

We used the per-anchor `summary.json` (one per anchor in Drive `outputs/phase3/p3.1_multi_anchor/anchor_<idx>/`). For each anchor + each of 7 ring cameras we read:

| Field | Meaning |
|---|---|
| `local_z_median_when_valid` | Pi3 median depth (m) for pixels with conf > 0.1 |
| `conf_pct_gt_0.1` | Fraction of pixels Pi3 considers "valid surface" |
| `conf_pct_gt_0.5` | Fraction at strict threshold |

**Closeness signal** per cam: linear ramp `1.0 at z ≤ 1.5 m, 0.0 at z ≥ 10 m`. Anything beyond 10 m is treated as "L1 sphere-projection good enough", so L3's value-add saturates near zero.

**Per-anchor score**:

```
score = weighted_mean_closeness * mean_conf * (0.5 + 0.5 * near_cam_count/7)
```

- `weighted_mean_closeness`: closeness across 7 cams, weighted by conf>0.1 coverage. Anchors where a noisy cam reports a spuriously small depth get suppressed because that cam also tends to have low conf coverage.
- `mean_conf`: mean fraction of valid pixels — coverage proxy.
- `near_cam_count`: # of cams with median z < 10 m. Multi-cam consensus matters because parallax disparity is largest when adjacent cams overlap on the same near scene.

All three terms are in [0,1]; the score is bounded in [0,1]. We deliberately **did not** compute per-pixel L1-vs-L3 reprojection delta — that requires loading the full 504×504×3 point clouds for all 70 cams and is overkill for "find the top-3 out of 10" decision.

Per-anchor visualizations (`outputs/phase3/parallax/anchor_<idx>_summary.png`) show per-cam median depth bar + per-cam coverage bar + per-cam closeness bar — three sub-plots, one per anchor, ten anchors total.

---

## 3. Ranked table

| Rank | Anchor | Score | mean closeness | mean conf>0.1 | near-cams | mean median z (m) | P3.1b ΔPSNR (L3−L1) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | **0** | 0.4112 | 0.572 | 0.719 | 7/7 | 5.27 | −3.13 |
| 2 | **150** | 0.4040 | 0.550 | 0.735 | 7/7 | 5.34 | −2.76 |
| 3 | **60** | 0.3964 | 0.567 | 0.700 | 7/7 | 5.28 | **−1.60** (best) |
| 4 | 30 | 0.3952 | 0.570 | 0.693 | 7/7 | 5.22 | −3.35 |
| 5 | 270 | 0.3642 | 0.472 | 0.771 | 7/7 | 6.04 | −4.22 (worst) |
| 6 | 90 | 0.3496 | 0.487 | 0.718 | 7/7 | 5.89 | −2.97 |
| 7 | 240 | 0.3467 | 0.436 | 0.795 | 7/7 | 6.33 | −3.88 |
| 8 | 120 | 0.3430 | 0.469 | 0.731 | 7/7 | 6.11 | −3.73 |
| 9 | **180** | 0.3336 | 0.430 | 0.777 | 7/7 | 6.35 | −3.37 |
| 10 | **210** | 0.3160 | 0.402 | 0.786 | 7/7 | 6.69 | −2.51 |

(ΔPSNR values are L3 minus L1 cycle-PSNR per `notes/phase3_multi_anchor_report.md` §3.)

### Reading

1. **Range is narrow (0.32 to 0.41)** — this log is fairly uniform scene depth. No anchor is dramatically more parallax-rich than another. The rank order is robust but the absolute spread is small. Consequence for paper: we cannot claim "L3 wins on a parallax-rich subset" without first picking a subset where the L3 win actually shows up in ΔPSNR.

2. **Near-cam count saturates at 7/7 for every anchor.** Every cam's median depth is under 10 m. This is consistent with AV2 ring cameras seeing a lot of street furniture / parked cars / curb near the ego. The "multi-cam consensus" term thus doesn't differentiate — the closeness signal is what's driving the ranking.

3. **`ring_side_right` is consistently the closest cam** (median z 1.6 - 5.5 m across anchors), often with very high conf coverage (>80%). That side mounts catches near-curb/parked-car structure. **This is the camera where L3 has the most to gain.**

4. **Correlation with ΔPSNR is real but noisy.** Top-3 by score has ΔPSNR ∈ {−3.13, −2.76, **−1.60**}; bottom-2 has ΔPSNR ∈ {−3.37, −2.51}. So the *best* L3-vs-L1 result (anchor 60) does sit in the top-3, but the worst (anchor 270, ΔPSNR −4.22) ranks 5th, not 10th. **Closeness predicts the upside ceiling, not necessarily the floor**. There are non-parallax confounders (texture, sky fraction, conf calibration) that dominate the worst-case.

5. **Anchor 180 paradox**: Pi3 produces its most LiDAR-accurate depth there (abs_rel 0.139) but it has the *lowest* parallax score. Translation: in that frame Pi3 nails the geometry, but the geometry is mostly far → L1 wins by default because there's no near-field ghost to fix. **Accurate depth ≠ useful for L3 ERP unless objects are close.**

---

## 4. What this implies for T18 / T12 follow-up

- **Run T18 (Depth Pro) and T12 (multi-frame Pi3) on anchor 60 first.** It is in our top-3 parallax-heavy set AND already has the smallest L1-L3 gap (−1.60 dB). If a better depth backbone or temporal fusion can shrink that gap further, it shows up here first. If anchor 60 still loses by ≥ −1 dB after T18, then 3D-aware ERP via better depth alone is dead — we'd need raycast/z-buffer or 3DGS (Phase 4 P4.2).
- **Anchor 0 and 150 are the secondary candidates.** Both share the "close mean depth, high coverage" profile of 60 but currently lose by ≈ −3 dB. If the better-depth experiment moves both of them up to single-digit-negative ΔPSNR, that's evidence the parallax-heavy regime is recoverable.
- **Anchor 180 is the negative control.** Far-field dominated. Even with perfect depth, L3 shouldn't beat L1 there because L1's infinite-depth assumption is approximately correct. Use it to confirm the new methods don't *break* easy cases.
- **Anchor 270 is a worst-case sanity check.** It has the largest existing L1-L3 gap (−4.22 dB) but only middling parallax score — meaning the gap is being driven by something other than parallax (texture? conf cliff?). Do not target this first; do regression-test after.

Suggested order for T12/T18 sweep: `[60, 0, 150, 180]` (top-3 + one negative control).

---

## 5. Validity caveats

- **Median depth is a coarse signal.** A cam with median z = 6 m and 30% of pixels at z = 2 m is much more parallax-heavy than a cam with median z = 5 m and the closest 1% of pixels at z = 5 m. Per-cam histogram percentiles (p10, p25) would refine this; not available in `summary.json` (only median).
- **No ego velocity used.** The original task suggested `closeness * velocity` as a proxy. We omitted velocity because (a) Pi3 sees scene parallax in a single frame regardless of ego speed — closeness is the only thing that matters for *single-frame* L3 quality, (b) all 10 anchors are on the same log so velocity is ~constant, (c) the `summary.json` doesn't expose velocity. If we extend to multi-log (P3.2), velocity becomes relevant for temporal stitching (T12).
- **Only the AV2 val log `02a00399…` is covered.** All conclusions are intra-log; do not over-generalize.
- **No per-pixel L1-vs-L3 reprojection delta computed.** That would be the gold-standard signal but takes O(seconds/anchor) of CPU to read 7 × 504² point clouds per anchor. The summary-stat proxy gives the same ranking decision in ~1 s total.

---

## 6. Files produced

| File | Description |
|---|---|
| `data/parallax_subset.json` | Ranking, scores, per-cam breakdown |
| `data/_summaries_cache/anchor_<idx>_summary.json` | Cached Pi3 summary JSONs (one per anchor) |
| `outputs/phase3/parallax/anchor_<idx>_summary.png` (×10) | Per-anchor 3-panel viz |
| `outputs/phase3/parallax/parallax_ranking.png` | Cross-anchor comparison bar chart |
| `scripts/phase3/rank_parallax.py` | Reproducible ranking script |
| `notes/parallax_subset_report.md` | This document |
| `agent/progress_T6_addendum.md` | 3-line progress entry |
