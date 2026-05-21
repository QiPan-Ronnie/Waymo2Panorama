# Paper Angle Decision Pack v0 (T7 preliminary)

**Date**: 2026-05-21 (Phase 3 W2 mid-run, T12/T16 still pending)
**Author**: T7 preliminary subagent
**Status**: Preliminary call — defensible starting position before Koi v0 review.

---

## 1. TL;DR (3 sentences)

**Recommended angle: B-with-C-as-motivation hybrid** — a method paper anchored on the IPM ground-prior + sphere hybrid (T14: ground-only ΔPSNR = +0.20 ± 0.11 dB across 3 anchors, +1.0–1.7 dB on rear cams, full-image ΔPSNR = +0.04 dB drop-in safe), with the L3 forward-splat negative result (P3.1b ΔPSNR = -3.15 ± 0.72 dB, L3 loses 10/10) and the T5 metric-robust audit (LPIPS 1.83×, MS-SSIM 0/7, object-band PSNR -6.88 dB) serving as the motivating analysis that justifies a hybrid 2D/3D pipeline instead of a pure-3D lift. The submission target is **3DV 2026** (~Aug 2026 deadline, 12-week runway, exact fit for 3D-focused B-with-C story) with **CVPR 2027** as the upgrade target if T9/T10 downstream lands. If pending T12 (multi-frame Pi3) closes ≥1 dB of the L3 gap on anchor 60 OR T16 (Bayesian fusion) gives an additional ≥+0.1 dB on top of IPM, the angle stays B-with-C; if both null, we fall back to pure-C negative-result analysis at a workshop venue.

---

## 2. Status of each angle (A / B / C / D)

### Angle A — Dataset paper

**Pitch**: "AV2 → 360° benchmark + L1 baseline + L3 .ply"

**Evidence supporting**: Reproducible AV2 → 360° ERP pipeline (Phase 1, tag `v0.1-l1-mvp`), L3 .ply export format (690K colored points / frame), per-view depth maps, cycle-consistency eval harness, LiDAR-anchored depth eval, 10-anchor sweep over one log, depth-binned bias characterization. Eval protocol itself (multi-anchor + multi-metric + depth-binned + region-separated) is publishable methodology.

**Concrete gaps**: One log only (`02a00399-...`). NeurIPS D&B requires "diverse, multi-scenario, statistically meaningful" datasets — typically 10+ scenes with cross-condition coverage. P3.2 multi-log scheduled but not started; ETA 3-5 days per Drive-budgeted log. No Dur360BEV cross-dataset eval (T21 deferred). Release infra (HF datasets card, Croissant metadata, license for AV2 re-distribution) unbuilt.

**Verdict**: insufficient ground in time budget. Revisit at Phase 5 if T21 cross-dataset succeeds.

### Angle B — Method paper

**Pitch**: "Hybrid 2D/3D pipeline for AV → 360 stitching"

**Evidence supporting**: T14 IPM ground hybrid is the **first positive method contribution**. Ground-only ΔPSNR = +0.20 ± 0.11 dB (3 anchors), rear cams +1.0–1.7 dB, full-image ΔPSNR = +0.04 ± 0.07 dB (no regression). Visually documented: crosswalks/lane markings align across cam boundaries; 5-20 cm ghost-shifts removed. Analytically grounded (closed-form IPM at ego ground plane is parallax-free by construction). T16 Bayesian fusion may add bump. The "hybrid" frame is well-supported by negative L3 finding — pure 3D lift fails, hybrid wins.

**Concrete gaps**: Win magnitude small (+0.2 dB ground-only is publishable but not headline-grabbing without story). Front-cam regression (-0.3 to -0.8 dB ground-only, dynamic shadow/pedestrian content) needs mitigation or honest discussion. Only 3 anchors so far; needs 10-anchor sweep to match Phase 3 W1 N. **No head-to-head against OmniStitch** (only published AV-360 baseline) — P3.5 scheduled but not run.

**Verdict**: Strength: real positive result with clear story. Weakness: small magnitude needs framing.

### Angle C — Negative-result analysis

**Pitch**: "Why naive 3D lift fails for AV panorama"

**Evidence supporting**: **Most statistically robust finding**. P2.7 single-frame + P3.1b 10-anchor + T5 metric audit form triangulated negative case: L3 loses on PSNR (-3.15 ± 0.72 dB, 10/10 anchors), MS-SSIM (0/7 cams, ΔSSIM -0.52), LPIPS (1.83× worse, 0/7 cams), every spatial region including parallax-critical object band (-6.88 dB). Audit explicitly tests/rejects "PSNR is biased toward blurry L1" hypothesis. Combined with depth-binned Pi3 bias (-10% near → -24% far, monotonic across 10/10 anchors), clean story: Pi3 is geometrically usable as .ply consumer but not as forward-splat 2D ERP renderer. Five decoded failure modes (depth variance ±0.3m, multi-cam blend doubling, conf-cliff sky holes, dynamic content, far-field bias compression) are publishable diagnostic.

**Concrete gaps**: Pure negative results face venue friction at top conferences. Best fit is workshop (CVPR W on AV / 3D-VR workshop) or systems-paper venue. Alone, lacks positive contribution reviewers expect.

**Verdict**: Best used as motivation for B, not standalone.

### Angle D — System integration

**Pitch**: "Pi3 + L1 ERP as 3D-cache for AV → 360° video diffusion"

**Evidence supporting**: Pi3 vs LiDAR (abs_rel 0.202 ± 0.042, near-field δ<1.25 = 0.711 ≈ Monodepth2 KITTI SOTA) establishes Pi3 as viable 3D cache producer. L1 ERP production-grade (12.34 ± 1.31 dB). Koi paper-stack alignment real (Pi3 → ViPE → GEN3C → Pantheon360). Handoff PDF already reframes our work this way.

**Concrete gaps**: T9 (ViPE on L1 ERP), T10 (Pantheon360 spike), T17 (Panacea+ baseline) all GPU-blocked (Colab worker offline) and not started. **Zero downstream diffusion evidence** (no FVD, no consumer of our .ply). System-integration paper without working downstream is position paper.

**Verdict**: not deliverable in 4-6 weeks. Revisit at Phase 4 end.

---

## 3. Recommended angle + reasoning

**Pick: B-with-C-as-motivation** — "A hybrid 2D/3D pipeline for autonomous-vehicle 360° panorama stitching, with analysis of why naive 3D-lift fails."

**Reasoning**:

1. **We have the positive result we needed.** T14 IPM hybrid is the first method contribution that produces a numerical win against fair baseline (+0.20 dB ground-only, +0.04 dB full-image, zero regression — drop-in safe). Method papers require this; we have it. Win is *consistent* (3/3 anchors positive on ground-only) and failure modes are understood and isolated to dynamic content + front-cam shadows — honest discussion items, not deal-breakers.

2. **The negative result is the motivation, not the contribution.** P3.1b + T5 together justify *why* we built a hybrid: "naive Pi3 forward-splat fails by 3.15 dB across all metrics and regions; we engineered around this by using Pi3 only for ground-prior generation and keeping sphere projection for the rest." This is the strongest narrative structure: converts biggest negative finding into the paper's *raison d'être* instead of an embarrassment.

3. **Metric robustness is in hand.** T5 forecloses the "you cherry-picked PSNR" critique by showing every metric agrees. Publish headline as (PSNR, MS-SSIM, LPIPS) tuple per T5's recommendation.

4. **Multi-anchor statistical power is in hand.** Phase 3 W1 gives N=10 anchors with explicit ±σ on headline numbers. Pre-empts "single-frame anecdote" reviewer concern. T14 still needs extension from 3 to 10 anchors, ~30s CPU once Colab back — low risk.

5. **Workable scoping.** One log + 10 anchors + IPM hybrid + L3 negative + metric audit + depth-binned Pi3 characterization = 6-8 page paper in 4-6 weeks. Percep360 4-6 week scoop window (T8) aligns. We avoid dataset paper trap (need 10 logs) and system integration trap (need downstream diffusion).

6. **Reframe-friendly.** If Koi's downstream Pantheon360 integration succeeds, the same paper extends to "and our .ply / depth maps feed Pantheon360" as future work or Section 6 demo, without restructuring.

---

## 4. Decision triggers (what would flip the angle)

| Pending track | Result | Angle change |
|---|---|---|
| **T12 (multi-frame Pi3 K=3, anchor 60)** | Closes L3 gap to ≤ -0.5 dB on anchor 60 | Stay B-with-C, expand: add "multi-frame Pi3 + IPM hybrid" as full method. Strengthens. |
| **T12** | Closes gap to >+0.5 dB (L3 beats L1 on parallax-rich anchor) | **Promote to B-headline**: "multi-frame Pi3 forward-splat with IPM ground prior beats L1 in parallax-rich regime." |
| **T12** | No improvement (still ≤ -1 dB) | Stay B-with-C unchanged; document as Section 5 ablation. |
| **T16 (Bayesian fusion at ERP overlap)** | +0.1 dB or more on top of IPM hybrid full-image | Expand B: hybrid becomes 3-component. Strengthens. |
| **T16** | Null or negative | Stay B-with-C; drop T16 from paper or move to ablation. |
| **T13 (self-sup cycle finetune of Pi3)** | Recovers ≥1 pp on Pi3 abs_rel | Add backbone-improvement section to B; still B angle. |
| **T17 (Panacea+ baseline) on AV2** | Works and FVD-comparable to our pipeline output | Add Section 6 demo to B; angle unchanged. |
| **T9/T10 (ViPE + Pantheon360 integration)** | Both succeed and produce 360° video from our L1 + .ply | Optional shift to D-headline for venue upgrade; risky given GPU dependencies. Keep B fallback. |
| **P3.5 (OmniStitch baseline)** | OmniStitch significantly outperforms L1 | Force B-pivot: re-frame as "we identify failure modes of both stitching and forward-splat, propose hybrid that fixes both"; still B-with-C. |
| **P3.5** | OmniStitch comparable or worse than L1 | Strengthen B headline: "Hybrid beats both prior published baseline and naive 3D lift." |

**No trigger flips us to A.** Angle A requires multi-log breadth we don't have a path to in 4-6 weeks.

---

## 5. Hybrid possibilities

**B-with-C-as-motivation (chosen)**: Method paper. Section 1 motivates with negative result. Section 3 presents method. Section 4 evaluates with (PSNR, MS-SSIM, LPIPS) tuple. Section 5 ablates and discusses failure modes. Section 6 positions for downstream consumers.

**B-with-D-as-future-work**: Same paper, but Section 6 includes "downstream consumer" subsection demonstrating Pantheon360 consumption of our outputs (depends on T9/T10 GPU unblock).

**C-with-A-as-benchmark**: Workshop alternative if T14 extension to 10 anchors regresses. Negative-result analysis paper, with 10-anchor / multi-metric / depth-binned eval protocol pitched as benchmark contribution. Lower-tier venue but safe landing.

**Not recommended**: A+D fusion (overstated; needs both 10 logs and downstream).

---

## 6. Submission target

| Venue | Deadline (best estimate) | Fit | Verdict |
|---|---|---|---|
| NeurIPS 2026 D&B | abstract ~mid-May 2026 / full ~late-May (closed for 2026; target 2027) | Good for A; mediocre for B | Skip 2026 cycle; 2027 if pivoting to A |
| **CVPR 2027 main** | ~Nov 2026 full paper | **Best fit for B-with-C** — vision+3D combined, hybrid story | **Primary target (upgrade path)** |
| ICCV 2027 | ~Mar 2027 | Same as CVPR; backup | Secondary |
| **3DV 2026** | ~Aug 2026 abstract / Sep full | **Excellent fit for 3D-focused B-with-C**, lower acceptance pressure | **Primary target (W3-W4 path)** |
| CVPR 2026 workshop (AV / 360°) | ~Feb-Mar 2026 | Pure-C fallback if T14 regresses | Backup |
| ICRA 2027 | ~Sep 2026 | Possible if we add robot-relevant demo | Tertiary |

**Recommended primary**: **3DV 2026** (~Aug 2026 deadline, exact fit for hybrid 2D/3D story, ~12 weeks runway). **CVPR 2027** as upgrade target if T9/T10 land downstream evidence.

---

## 7. Risk register (top 3 for next 2 weeks)

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **T14 10-anchor extension regresses to mean ≤ +0.05 dB ground-only with overlapping σ** | Medium (current 3-anchor σ = 0.11 dB; 10-anchor σ could be larger) | High — undermines only positive result | Pre-commit T14 10-anchor run to top priority once Colab worker back; if regress, run front-cam mitigation (semantic shadow mask) before re-eval |
| **Percep360 (ICRA 2026) drops code in early June 2026 with hybrid 2D/3D pipeline overlapping ours** | Low-Medium (T8 says diffusion-only, but actual code may surprise) | Medium — partial scoop, requires re-positioning | Watch GitHub repo weekly (T8 continued); if scoop materializes, pivot framing to "hybrid + analysis" emphasizing negative-result + metric-audit contributions which are orthogonal |
| **OmniStitch baseline (P3.5) significantly beats both L1 and our hybrid** | Low (OmniStitch is GV360-synthetic-trained, not AV2-tuned) | High — kills B-headline | Run P3.5 in week 1; if OmniStitch wins on full-image PSNR, re-frame as "we identify the components that work + propose IPM ground prior as drop-in addition to any stitching base" |

---

**Sign-off**: This is a preliminary T7 call. Re-issue v1 after T12 + T16 + T14-10anchor + P3.5 complete (estimated W3 D3).
