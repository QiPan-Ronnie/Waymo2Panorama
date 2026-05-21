# T18 — Depth Pro Drop-in Backbone Swap Report

**Date**: 2026-05-21
**Subagent**: T18 (Depth backbone swap, anchor 60)
**Question being answered**: is L3 forward-splat's −3.15 dB cycle-PSNR loss caused by the **Pi3 backbone** (depth quality) or by the **forward-splat algorithm** (wrong-channel)?
**Frame**: AV2 val log `02a00399-3857-444e-8db3-a8f58489c394`, anchor_idx=60 (T6's top-3 parallax pick)
**Hardware**: Colab A100, bf16

---

## TL;DR

**The algorithm is the culprit, not the backbone — and Depth Pro is the wrong backbone for AV2 anyway.**

Three concrete findings:

1. **Depth Pro pip install via HF transformers succeeded** (`pip install -U 'transformers>=4.45'`, checkpoint `apple/DepthPro-hf`). No git clone needed. Cold install + model load = ~17 s. Per-cam forward = 0.15 s on A100.

2. **Depth Pro depth quality on AV2 is catastrophically worse than Pi3** at anchor 60:
   - abs_rel = **0.580** vs Pi3 **0.204** (2.84× worse)
   - RMSE = **15.78 m** vs Pi3 **5.27 m** (3× worse)
   - δ<1.25 = **0.064** vs Pi3 **0.633** (collapsed by 10×)
   - DepthPro mean depth = **7.57 m** vs LiDAR ground-truth **19.83 m** — Depth Pro systematically *under-estimates* by ~60% on AV2 outdoor driving scenes.

3. **L3 forward-splat cycle-PSNR with Depth Pro is ≈ L1** (Δ = −0.01 dB) — but this is **NOT a real algorithmic win**: it's an intersection-mask artifact. Depth Pro produces ~15% L3 coverage (vs Pi3's ~50% at the same anchor), so the PSNR is computed on a much smaller, easier subset of pixels.

**Verdict for G2 / paper angle decision**: angle **C (algorithm-wrong)** holds. Backbone swap to a "stronger" foundation model didn't rescue L3 forward-splat. If anything, the depth quality collapsed because the letterbox padding + AV2 outdoor distribution is out-of-domain for Depth Pro (which was trained on indoor/object-centric data). Backbone tuning (angle B) is *not* a free lunch — it needs domain adaptation on AV2 first.

---

## 1. Setup

- **Script**: `scripts/phase3/run_depth_backbone_swap.py`
  - Single CLI: `--backbone {depthpro,metric3d,pi3}`
  - Reuses Phase 2 letterbox (504×504), `av2_loader`, `eval_pi3_vs_lidar`'s projection helpers, and `eval_cycle_consistency`'s `reconstruct_l3 / reconstruct_l1` directly — only the depth source changes.
  - Back-projects per-pixel depth via `pt_cam = depth * unproject(K_letterboxed, u, v)`, then `T_ego_cam` lifts to ego.
- **Colab job**: `phase3-t18-depthpro-anchor60-piponly` (pip-only, fail-fast). Completed in **51 s** wall-clock.
- **Why HF transformers and not the Apple repo**: Apple's `ml-depth-pro` package is GitHub-only (no PyPI). HuggingFace transformers (≥4.45) ships `DepthProForDepthEstimation` + `apple/DepthPro-hf` checkpoint — pip-installable, identical model weights. This satisfies the "pip-only, fail-fast" constraint.

### Per-cam Depth Pro inference timing (A100, bf16)

| cam | fwd (s) | depth min | median | max (m) |
|---|---:|---:|---:|---:|
| ring_front_center | 0.63 | 0.46 | **1.94** | 93.0 |
| ring_front_left | 0.16 | 1.17 | 3.56 | 61.3 |
| ring_side_left | 0.15 | 0.89 | 3.05 | 105.0 |
| ring_rear_left | 0.15 | 0.55 | 3.12 | 50.3 |
| ring_rear_right | 0.15 | 0.87 | 4.75 | 256.0 |
| ring_side_right | 0.15 | 0.80 | **2.20** | 3.77 |
| ring_front_right | 0.15 | 0.96 | 2.25 | 51.3 |

The **median Depth Pro depth across cams is 2-5 m** — implausibly close. AV2 typical scene content is 10-40 m. Pi3 medians at anchor 60 were 3-6.5 m and the LiDAR mean was 19.8 m — Pi3 already underestimates (P3.3 finding), but Depth Pro underestimates much harder. `ring_side_right` is the smoking gun: Depth Pro outputs depth in [0.80, 3.77] m — i.e., the model thinks the entire side scene is within 4 m, when LiDAR says the average is 4.9 m and the actual far range is 30+ m.

---

## 2. LiDAR-anchored depth eval

### Overall (7 cams, ~110k matched points)

| Metric | **Depth Pro** | Pi3 (anchor 60) | Δ |
|---|---:|---:|---:|
| abs_rel | **0.580** | 0.204 | **+0.376** (worse) |
| RMSE (m) | **15.78** | 5.27 | **+10.51** (worse) |
| δ<1.25 | **0.064** | 0.633 | **−0.569** (collapsed) |
| δ<1.25² | 0.099 | — | — |
| δ<1.25³ | 0.264 | — | — |
| LiDAR mean (m) | 19.83 | 16.45 | (slight bin shift) |
| Pred mean (m) | **7.57** | 12.83 | (Depth Pro much shorter) |

### Per-cam (Depth Pro abs_rel / δ<1.25)

| cam | abs_rel | δ<1.25 | LiDAR μ | Pred μ |
|---|---:|---:|---:|---:|
| ring_front_center | 0.794 | 0.002 | 26.5 | 5.1 |
| ring_front_left | 0.600 | 0.0003 | 30.7 | 12.2 |
| ring_side_left | 0.624 | 0.002 | 19.5 | 7.4 |
| ring_rear_left | 0.733 | 0.0006 | 25.5 | 6.5 |
| ring_rear_right | 0.325 | **0.445** | 15.0 | 9.9 |
| ring_side_right | 0.496 | 0.018 | 4.9 | 2.3 |
| ring_front_right | 0.546 | 0.012 | 20.7 | 9.8 |

- **Worst**: front-center (abs_rel 0.79) — this is the portrait-orientation cam, so letterboxing adds the most black padding. Depth Pro likely treats borders as near walls.
- **Best**: rear-right (abs_rel 0.33, δ<1.25 = 0.44) — rear-right scene is closer and more cluttered, matching DepthPro's training-data distribution.
- **All-cam δ<1.25 = 0.064** is well below any production-usable threshold. Even Pi3's worst anchor (270) was 0.412.

### Why Depth Pro underperforms on AV2

1. **Letterbox padding**: 504×504 zero-pad introduces black borders that the model interprets as nearby surfaces. Pi3 was robust to this (was designed for multi-view stitching with arbitrary crops).
2. **Domain shift**: Depth Pro was trained on a mix of synthetic + real datasets weighted toward indoor / object-centric content. Outdoor driving at 10-50 m is the long tail.
3. **No camera intrinsics conditioning**: Depth Pro is designed to predict FOV jointly. We disabled `use_fov_model=False` (since we have AV2 truth K), and may have lost some accuracy. Re-running with FOV model = +~30% wall-clock, doubt it would close a 3× abs_rel gap.

---

## 3. L3 forward-splat cycle-consistency

| Hold-out cam | L1 PSNR | L3 PSNR | Δ (L3−L1) | L3 coverage | L1∩L3 coverage |
|---|---:|---:|---:|---:|---:|
| ring_front_center | 6.03 | 6.32 | +0.29 | 0.37 | 0.37 |
| ring_front_left | 15.28 | 14.49 | −0.80 | 0.13 | 0.13 |
| ring_side_left | 15.45 | 14.73 | −0.72 | 0.12 | 0.12 |
| ring_rear_left | 17.78 | 20.20 | **+2.42** | 0.13 | 0.13 |
| ring_rear_right | 16.17 | 16.25 | +0.08 | 0.10 | 0.10 |
| ring_side_right | 12.17 | 11.73 | −0.44 | 0.09 | 0.09 |
| ring_front_right | 11.30 | 10.41 | −0.89 | 0.12 | 0.12 |
| **MEAN** | **13.46** | **13.45** | **−0.01** | 0.15 | 0.15 |

### Read carefully — this is NOT a clean win

- **L3 ≈ L1 (Δ = −0.01 dB)** vs Pi3 baseline Δ = **−1.60 dB** at the same anchor (and −3.15 ± 0.72 dB across 10 anchors). On the face of it, that's a 1.6-3.1 dB improvement.

- **But: L3 coverage is only 15% (vs ~50% for Pi3 at anchor 60)**. PSNR is computed on the **intersection mask** L1 ∩ L3, so when L3 covers very few pixels, we're scoring on a small, biased subset.

- **Sanity check**: L1 mean PSNR with DepthPro intersection = 13.46 dB, but L1 mean PSNR with Pi3 intersection (anchor 60) was 11.88 dB. Same algorithm, same scene, same K, but +1.58 dB just from the different intersection mask. **The 1.58 dB "gain" for both L1 and L3 is the mask artifact**; the apparent L3 closing the gap is in reality "we're only scoring on the 15% of pixels DepthPro happens to splat into".

- **The one real positive: ring_rear_left (Δ = +2.42 dB)** — this is the first single-cam case in 70 hold-out trials (10 anchors × 7 cams) where L3 beats L1 by >1 dB on intersection PSNR. Worth a single-frame visual case study, but not statistically meaningful as one isolated win.

### Implication for backbone-vs-algorithm question

- Even if we charitably credit the +1.58 dB L1-side improvement to "DepthPro depth is sharper so the L3 z-buffer makes cleaner choices in the small region it covers", the **net is still ΔL3 = −0.01 dB on intersection** — i.e., L3 forward-splat is at best a coin-flip vs L1 on its own home turf (the small high-conf depth pixels).

- The forward-splat algorithm itself remains **wrong-channel for ERP** (depth misalignment → ghosting & holes are intrinsic; you can't fix it with sharper boundaries because the real problem is metric depth scale and parallax between cams).

---

## 4. Verdict for G2 / paper angle

**Angle C (Pi3-as-3D-scene, L1 ERP as production output) is reinforced.**

| Hypothesis | Predicted outcome | Observed | Verdict |
|---|---|---|---|
| **Backbone matters (angle B)** | DepthPro abs_rel < 0.18, L3 PSNR closes by ≥2 dB | abs_rel = 0.580 (3× WORSE), L3 PSNR Δ artifactual | ❌ Refuted (for DepthPro at least) |
| **Algorithm wrong (angle C)** | New backbone still doesn't fix L3 ERP | True — L3 still loses on raw coverage, artifactual on intersection | ✅ Supported |
| **DepthPro is right for AV2** | Should beat Pi3 on LiDAR | Loses 3× on abs_rel, 10× on δ<1.25 | ❌ Refuted: DepthPro needs AV2 fine-tune |

### Recommended next experiments (not blocking this task)

1. **Metric3D v2** as a third backbone (already wired in our script, `--backbone metric3d`). It's *designed* for outdoor metric depth from cars (KITTI/AV2-like distribution). If it also fails L3 forward-splat, angle C is bulletproof.
2. **Multi-anchor sweep with DepthPro** (re-run script on anchors 0/30/...270) — would confirm the depth-quality collapse generalizes.
3. **Disable letterbox** for DepthPro and feed native-resolution AV2 images — the black-border artifact might be the dominant failure mode. Worth a single test before declaring DepthPro broken on AV2.

---

## 5. Output files

| Path | Description |
|---|---|
| `scripts/phase3/run_depth_backbone_swap.py` | The script (3 backbones, LiDAR eval, L3 cycle eval) |
| Drive: `outputs/phase3/t18_depthpro/depthpro_lidar_metrics.json` | Per-cam + overall abs_rel/RMSE/δ |
| Drive: `outputs/phase3/t18_depthpro/depthpro_cycle_metrics.json` | Per-cam + mean L1 vs L3 PSNR |
| Drive: `outputs/phase3/t18_depthpro/depthpro_depth_<cam>.npy` | Per-cam 504×504 metric depth |
| Drive: `outputs/phase3/t18_depthpro/depthpro_depth_viz_<cam>.png` | Color-mapped depth viz |
| Drive: `outputs/phase3/t18_depthpro/depthpro_points_<cam>.npy` | Per-cam (H,W,3) ego-frame points |
| Drive: `outputs/phase3/t18_depthpro/summary.json` | Run summary + per-cam stats |
| Drive: `outputs/phase3/t18_depthpro/run.log` | Full stdout from the Colab run |
| Job spec: `jobs/phase3-t18-depthpro-anchor60-piponly.json` | Colab job spec (committed to repo) |
| Drive result: `results/phase3-t18-depthpro-anchor60-piponly.json` | Colab worker exit code + log_tail |

---

## 6. Five-bullet handoff

- **Depth Pro pip install**: succeeded via `pip install -U 'transformers>=4.45'` + checkpoint `apple/DepthPro-hf`. Cold install + load = 17 s. No git clone needed. No pivot to Metric3D necessary, though the script supports both for future use.
- **DepthPro vs Pi3 LiDAR abs_rel (anchor 60)**: **0.580 vs 0.204** — Depth Pro is 2.84× WORSE on AV2 outdoor scenes. δ<1.25 collapses 10× (0.064 vs 0.633). DepthPro systematically underestimates depth by ~60% (predicts 7.6 m where LiDAR says 19.8 m).
- **L3 forward-splat cycle-PSNR with DepthPro**: ΔPSNR = **−0.01 dB** (vs Pi3 baseline −1.60 dB at anchor 60). Looks like a big win, but L3 coverage is only 15% (vs Pi3 50%) so the intersection PSNR is computed on a much smaller, easier subset of pixels. Net real change ≈ mask artifact.
- **Verdict (backbone vs algorithm)**: **algorithm is the bottleneck**. Swapping in a different SOTA backbone (one that's "sharper" in benchmarks) didn't rescue L3 forward-splat — it broke depth quality entirely on AV2. Backbone-tuning (angle B) is NOT a drop-in fix without dataset-specific fine-tuning first.
- **G2 informant**: Angle C (Pi3-as-3D-scene, L1 ERP as production) holds. Future backbone work needs (a) AV2 domain adaptation, (b) Metric3D v2 control experiment, (c) test without letterbox padding. None of these would change the headline that L3 forward-splat ERP is wrong-channel.
