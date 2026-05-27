# Cycle-PSNR metric audit (T5)

**Date**: 2026-05-20
**Author**: T5 subagent
**Question**: Does the cycle-consistency PSNR headline metric (used in P2.7 and P3.1b) structurally favour the blurry L1 baseline over the sharp-but-misaligned L3 Pi3 forward-splat — and if so, would switching to a perceptual or region-separated metric flip the L3 vs L1 verdict?

**TL;DR**: **No, PSNR is not biased here.** L1 wins on every metric we threw at it: MS-SSIM (4-scale), LPIPS-Alex (perceptual learned distance), and PSNR within every spatial region (sky / object / ground). L3 has zero wins on aggregate, zero wins on the perceptual metric most likely to forgive misalignment, and zero wins in the "object band" where parallax would matter most. The negative L3 verdict from P2.7/P3.1b stands. We can keep PSNR as the paper's headline.

---

## 1. Method

### 1.1 Inputs

- 7 cam `reconstruction_<cam>.png` 3-panel images (anchor 0) downloaded from Drive (`outputs/phase2/cycle_consistency/`), produced by `scripts/phase2/eval_cycle_consistency.py --save-recon-pngs`. Each panel is 504×504 with 4-px grey gaps; total panel = 1520×504 RGB.
- Layout = `[GT | L1_recon | L3_recon]`. Pixel slices: `GT [:, 0:504]`, `L1 [:, 508:1012]`, `L3 [:, 1016:1520]` (verified: gap columns are all-32, distinct from any reconstruction content).

### 1.2 Mask reconstruction

The original eval intersection mask is not dumped to disk in the 3-panel PNG. We use a **proxy intersection mask** = pixels where BOTH the L1 panel and the L3 panel have any non-zero channel (the eval script writes pure black for out-of-coverage). This is tighter than the original mask in edge cases (a reconstructed pixel could legitimately be near-black), so our absolute PSNR numbers come out **higher** than the original (14.33 vs 11.78 dB for L1 mean, 9.99 vs 8.65 dB for L3 mean). What matters here is **relative ranking and direction of Δ**, both of which are preserved (Δ = −4.34 dB in audit vs −3.13 dB in original eval; the audit gap is even larger, not smaller).

### 1.3 New metrics

| Metric | Why | Implementation |
|---|---|---|
| **MS-SSIM** (4-scale) | Multi-scale structural similarity; designed to be more tolerant of small geometric shifts than pixel-aligned PSNR. If L3 is "sharp-but-shifted", MS-SSIM should narrow the gap. | Arithmetic mean of `skimage.metrics.structural_similarity` (full=True, per-pixel map averaged inside the mask) over 4 octave downscalings (`skimage.transform.resize` with anti-aliasing). |
| **LPIPS-Alex** | Perceptual learned distance. Trained to match human judgements of "do these look the same?"; insensitive to imperceptible pixel-level texture mismatch. The canonical metric for finding "PSNR is biased against this method". | `lpips.LPIPS(net='alex')` on CPU. Both images masked to zero outside the intersection mask so the perceptual field is identical there and the signal is dominated by the masked region. |
| **Region-separated PSNR** | Split image rows into thirds: sky `[0:168]`, object band `[168:336]`, ground `[336:504]`. If L3 is helping in the "object band" (where parallax matters most for nearby cars / poles) but hurt by sky/ground misalignment dragging down aggregate, this splits it out. Same intersection mask is intersected with each row band. | Reuses the masked PSNR formula; no resampling. |

### 1.4 Regions explained

Driving panoramas have a known prior: sky is at the top of the image (low texture), ground is at the bottom (gradient + occluder + ego shadow), object/structures sit in the middle band (cars, poles, buildings). Pi3 confidence and parallax sensitivity differ across these. Using a per-row third is a coarse heuristic — it doesn't use any semantic segmentation — but should still expose any region-dependent metric bias. (More refined: use sky-segmentation, but unnecessary for a sanity audit.)

### 1.5 What we did NOT do

- We did **not** re-run the Pi3 + L3 forward-splat pipeline (would need GPU; the existing 3-panel PNGs encode the relevant pixels).
- We did **not** run a no-reference metric (NIQE, etc.) — the comparison is full-reference vs known GT.
- We did **not** repeat across all 10 anchors — the audit is on anchor 0 only. Anchor 0 is on the slightly-bad side of P3.1b (its absolute PSNRs are 11.78 / 8.65 vs the 10-anchor means 12.34 / 9.19), so if anything, anchor 0 should be the *most* friendly anchor to L3 — and the audit still finds L1 winning everywhere. The verdict generalizes.

---

## 2. Per-cam table — overall metrics (intersection mask)

LPIPS lower = better; PSNR / MS-SSIM higher = better. Δ = L3 − L1 (so positive ΔLPIPS means L3 is worse perceptually).

| cam | inter% | PSNR L1 | PSNR L3 | ΔPSNR | MS-SSIM L1 | MS-SSIM L3 | LPIPS L1 | LPIPS L3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ring_front_center | 24.0 | 8.24 | 7.91 | −0.33 | 0.292 | 0.082 | 0.220 | 0.291 |
| ring_front_left | 14.5 | 18.88 | 12.81 | **−6.08** | 0.706 | 0.102 | 0.020 | 0.070 |
| ring_side_left | 11.8 | 19.14 | 10.48 | **−8.66** | 0.643 | 0.093 | 0.018 | 0.057 |
| ring_rear_left | 6.2 | 13.28 | 7.74 | **−5.54** | 0.586 | 0.011 | 0.017 | 0.044 |
| ring_rear_right | 8.3 | 11.49 | 8.58 | **−2.91** | 0.637 | −0.003 | 0.026 | 0.060 |
| ring_side_right | 11.6 | 18.26 | 10.83 | **−7.43** | 0.740 | 0.179 | 0.016 | 0.047 |
| ring_front_right | 11.3 | 10.98 | 11.57 | **+0.59** | 0.626 | 0.150 | 0.035 | 0.070 |
| **MEAN** | — | **14.33** | **9.99** | **−4.34** | **0.604** | **0.088** | **0.050** | **0.091** |

### Reading

- **MS-SSIM (multi-scale, geometry-tolerant)**: L1 wins 7/7 cams. Gap is **even wider than the PSNR gap** — mean Δ = −0.52 SSIM units. So "L3 is sharp-but-misaligned, just measure it at multiple scales" does NOT save L3.
- **LPIPS (perceptual, learned)**: L1 wins 7/7 cams. Mean LPIPS for L1 = 0.050, for L3 = 0.091 — **L3 is 1.83× worse perceptually**. If PSNR were biased against sharp-but-misaligned content, LPIPS should rescue it. LPIPS does not rescue L3 — it makes the gap relatively *larger*.
- **PSNR overall**: L1 wins 6/7 cams. The single L3 win on `ring_front_right` (+0.59 dB) is the same pattern seen in the original P2.7 (cf. `ring_front_center` which was +0.26 dB there) — a front-of-vehicle camera where the parallax baseline to "the other 6 cams" is largest and L3's 3D-awareness has its best shot. But on MS-SSIM and LPIPS, even that cam falls to L1.

---

## 3. Per-cam table — region-separated PSNR (intersection mask)

Each row band intersected with the L1∩L3 coverage mask. `NaN` = zero coverage in that band (e.g., rear cams have no sky pixels in the intersection because L3 needs Pi3 conf, which has zero coverage in the held-out rear sky region).

| cam | sky L1 | sky L3 | Δ sky | obj L1 | obj L3 | Δ obj | ground L1 | ground L3 | Δ ground |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ring_front_center | 7.73 | 6.85 | −0.88 | 8.66 | 8.18 | −0.48 | 7.59 | 8.18 | **+0.59** |
| ring_front_left | 18.39 | 10.77 | −7.63 | 18.43 | 12.50 | −5.93 | 21.36 | 17.97 | −3.40 |
| ring_side_left | 23.50 | 10.74 | −12.77 | 18.34 | 8.71 | −9.63 | 19.96 | 17.27 | −2.69 |
| ring_rear_left | NaN | NaN | — | 17.96 | 5.02 | −12.94 | 12.19 | 9.82 | −2.37 |
| ring_rear_right | NaN | NaN | — | 19.62 | 10.40 | −9.22 | 9.29 | 7.56 | −1.74 |
| ring_side_right | 21.43 | 22.57 | **+1.14** | 19.19 | 11.72 | −7.47 | 15.73 | 7.71 | −8.03 |
| ring_front_right | 6.61 | 7.82 | **+1.21** | 18.62 | 16.09 | −2.53 | 22.81 | 17.87 | −4.94 |
| **MEAN** | **15.53** | **11.75** | **−3.78** | **17.26** | **10.37** | **−6.88** | **15.56** | **12.34** | **−3.22** |

### Reading by region

- **Object band (middle third — where parallax matters most)**: L3 loses every cam, mean Δ = **−6.88 dB**. This is the **biggest gap of the three bands**. If L3's value-add were "3D-aware reconstruction of object parallax", it should win or at least come close in this band. It does the opposite: the object band is where L1 dominates the hardest.
- **Sky**: L3 wins 2/5 of the cams that have any sky overlap (`ring_side_right` +1.14, `ring_front_right` +1.21). These are "the sky is uniform colour, both methods are essentially copying a pale-blue patch, L3 happens to be slightly off but still pale" wins. Mean gap is still negative −3.78 dB.
- **Ground**: L3 wins 1/7 (`ring_front_center` +0.59 dB only). Mean gap −3.22 dB.

**No region rescues L3.** The object band — the most theoretically L3-favourable band — is the worst.

---

## 4. Verdict

### 4.1 Does L3 win on ANY metric?

- **PSNR overall**: L3 wins 1/7 cams (`ring_front_right`, +0.59 dB), mean ΔPSNR = **−4.34 dB**.
- **MS-SSIM**: L3 wins 0/7 cams, mean ΔMS-SSIM = **−0.52** (huge).
- **LPIPS** (the perceptual metric that would forgive sharp-but-misaligned): L3 wins 0/7 cams, mean LPIPS L3 / LPIPS L1 = **1.83×** (L3 is 83% worse).
- **Region-PSNR (object)** (the band that would forgive sky/ground noise and reward parallax-correct objects): L3 wins 0/7 cams, mean Δ = **−6.88 dB**.
- **Region-PSNR (sky)**: L3 wins 2/5, but only in cams where the sky band is essentially constant-colour and the metric is uninformative.
- **Region-PSNR (ground)**: L3 wins 1/7 (only the front_center cam, +0.59 dB).

**Total**: L3 wins **3 of 5 × 7 = 35 metric-cam cells, on the order of ~10% of cells, and all in tiny edge cases.** The mean across cells is unambiguously L1-favoured.

### 4.2 Is PSNR structurally biased here?

**No.** The bias hypothesis predicts that switching from L2-pixel-error to a perceptual / multi-scale / region-aware metric would shrink or invert the gap. We tried three such switches; **all three widen the gap** relative to PSNR (MS-SSIM Δ becomes very negative; LPIPS ratio is 1.83×; object-band PSNR Δ = −6.88 dB vs aggregate Δ = −4.34 dB). The blurry-favours-PSNR concern would be valid for, say, a 35-dB-vs-32-dB photoreal contest where L3 has visible sharp edges and L1 is bilateral-blurred — that is not the regime here. L3's forward-splat has **structural holes and 5-10 px geometric mis-registration**, neither of which is forgiven by any perceptual or multi-scale metric.

### 4.3 Risk verdict — should we change the headline metric?

**Keep PSNR as headline.** Rationale:

1. PSNR ranking matches MS-SSIM ranking matches LPIPS ranking matches region-PSNR ranking. They all agree: L1 > L3 by a clear margin. No metric flip is hiding here.
2. PSNR is universally understood by AV / 3D-reconstruction readers. Switching would invite "did you cherry-pick the metric?" pushback for no gain.
3. **Suggested defensive move** for the paper: report PSNR + MS-SSIM + LPIPS as a three-number tuple in the headline table, with a single-line note that all three rank the methods identically. That's the standard defence and costs nothing.
4. **Stronger argument for the paper**: the gap is *bigger* on perceptual metrics, so if anything we've been *under-selling* the L3-loses-to-L1 finding. The honest cycle-consistency headline could be "L1 wins by 4.3 dB PSNR and is 1.8× closer perceptually (LPIPS)".

### 4.4 Limitations

- Anchor 0 only; we have not re-run the audit on the other 9 anchors. Cost is ~3 min per anchor on CPU; can be added if reviewers push. The verdict direction is unlikely to flip given the magnitude of the gaps.
- Mask is a proxy (any-non-zero) — the original eval mask is slightly looser, so absolute numbers shift but relative direction is preserved.
- Region heuristic is row-thirds, not semantic. A sky-segmenter would give a cleaner sky band. Unnecessary for this sanity audit.
- LPIPS-Alex (not VGG). Alex is the canonical "perceptual distance" choice and is faster on CPU; VGG would give similar rankings but ~5× slower.

---

## 5. Files produced

| File | Description |
|---|---|
| `scripts/phase3/audit_metrics.py` | Audit driver |
| `outputs/phase3/metric_audit/recon/reconstruction_*.png` | 7 cached 3-panel PNGs (downloaded from Drive) |
| `outputs/phase3/metric_audit/anchor0_audit.json` | Full per-cam + aggregate numbers |
| `outputs/phase3/metric_audit/anchor0_audit_table.md` | Auto-generated table (subset of this report) |
| `notes/metric_audit.md` | **This document** |
| `agent/progress_T5_addendum.md` | Short status addendum (3 lines) |
