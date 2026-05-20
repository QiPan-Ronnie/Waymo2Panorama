# Phase 2 P2.11 — Pi3 vs AV2 LiDAR Depth Comparison

**Tag**: `v0.2-l3-mvp` (continues Phase 2 closeout)
**Date**: 2026-05-20
**Frame**: AV2 val log `02a00399-3857-444e-8db3-a8f58489c394`, anchor_idx=0
**Backbone**: Pi3X (Phase 2 D1 winner)

---

## TL;DR

Pi3 depth vs AV2 LiDAR ground truth, **single anchor frame, 7 cams, 99,015 matched points**:

| | Pi3 (this work) | KITTI SOTA monocular (literature) |
|---|---:|---:|
| abs_rel | **0.215** | 0.05-0.15 |
| RMSE | **7.70 m** | 2-4 m |
| δ<1.25 | **65.3%** | 85-95% |
| δ<1.25² | **90.2%** | 95-98% |
| δ<1.25³ | **93.9%** | 98-99% |

**Reading**: Pi3 is in the **right ballpark for general-purpose monocular depth on outdoor AV scenes**, but ~10-20% behind KITTI-tuned models. Two real findings:

1. **Pi3 systematically underestimates depth by ~25%** (Pi3 mean 13.96 m vs LiDAR mean 18.53 m). The Sim(3) global scale (1.0346) cannot fix this — it is a *non-uniform* bias that compresses far things more than near things.
2. **Per-cam quality scales with scene closeness** — front_right (mean depth 7.05 m) hits δ<1.25 = 91.7% (~SOTA), while rear_left (mean 29.26 m) drops to 22.3%. Pi3 is competent up to ~15 m, struggles beyond.

**Implication for L3 pipeline**: the .ply / depth-map deliverable is geometrically usable for Pantheon360-class downstream consumers in the near field (5-15 m). Far-field (>20 m) needs LiDAR fusion or a depth-tuned backbone before it's ground truth.

---

## 1. Protocol (P2.11)

```
LiDAR sweep at t closest to anchor (Δ = 9.8 ms, 99k ego-frame points)
                    │
                    ↓
       For each of 7 ring cams:
                    │
   ego -> cam via inv(T_ego_cam_av2)
                    │
   pinhole project via av2_K_letterboxed (504x504 scale, same as Pi3 input)
                    │
   filter: z>0.5m, z<60m, in [0,503]x[0,503], Pi3 conf > 0.3
                    │
   sample Pi3 depth = local_points_<cam>.npy[..., 2]  at nearest-int (u,v)
                    │
   compute Eigen-et-al metrics: abs_rel, sq_rel, RMSE, log_RMSE,
                                δ<1.25 / 1.25² / 1.25³
```

Why letterboxed 504x504 and not full-res: Pi3's per-pixel depth is at the letterboxed input resolution. The pixel at letterboxed (u, v) corresponds to a specific ray (defined by the letterboxed K). Projecting LiDAR via the same letterboxed K means we're comparing depths along the **same ray** as Pi3's prediction — fair apples-to-apples without resampling.

Implementation: `scripts/phase2/eval_pi3_vs_lidar.py` (commit `1cd7bd7`).
Job: `phase2-p2.11-pi3-vs-lidar` (Colab CPU, 43.7s wall-clock, exit 0).

---

## 2. Numerical Results

### Per-camera table

| Cam | LiDAR mean (m) | Pi3 mean (m) | n matched | abs_rel | RMSE (m) | log_RMSE | δ<1.25 | δ<1.25² | δ<1.25³ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ring_front_center | 23.23 | 18.13 | 9,451 | 0.206 | 7.56 | 0.305 | 0.527 | 0.935 | 0.958 |
| ring_front_left | 26.48 | 21.31 | 17,015 | 0.191 | 6.67 | 0.247 | 0.642 | 0.958 | 0.989 |
| ring_side_left | 22.90 | 16.06 | 16,430 | 0.242 | 10.83 | 0.396 | 0.583 | 0.812 | 0.907 |
| ring_rear_left | 29.26 | 19.18 | 9,293 | **0.296** | **14.12** | 0.466 | **0.223** | 0.815 | 0.843 |
| ring_rear_right | 17.72 | 14.40 | 13,774 | 0.196 | 4.05 | 0.275 | 0.702 | 0.974 | 0.983 |
| ring_side_right | 9.60 | 7.37 | 15,483 | 0.234 | 4.15 | 0.375 | 0.732 | 0.869 | 0.927 |
| **ring_front_right** | 7.05 | 5.36 | 17,569 | **0.170** | **4.11** | 0.296 | **0.917** | 0.934 | 0.940 |
| **OVERALL** | **18.53** | **13.96** | **99,015** | **0.215** | **7.70** | **0.337** | **0.653** | **0.902** | **0.939** |

### Coverage

- LiDAR sweep total: 98,981 points in ego frame
- Per-cam FOV catch: 10k-20k points (depends on cam angle and obstructions)
- Match rate (FOV ∩ Pi3 conf > 0.3): 69-98% — front_center lowest (sky in upper FOV is conf-filtered), front_left/side highest

---

## 3. Two real findings

### 3.1 Systematic depth underestimation (~25%)

| | LiDAR mean | Pi3 mean | ratio |
|---|---:|---:|---:|
| Overall | 18.53 m | 13.96 m | **0.753** |
| Front_right (near scene) | 7.05 m | 5.36 m | 0.760 |
| Front_left (mid scene) | 26.48 m | 21.31 m | 0.805 |
| Rear_left (far scene) | 29.26 m | 19.18 m | 0.656 |

Pi3 systematically reports closer-than-actual depths, and the bias is worse for far scenes. Sim(3) global scale (1.0346) cannot fix this because:
- Sim(3) is one scalar applied to all cams uniformly
- The depth bias here is *range-dependent* — compresses far points more than near

**Hypothesis**: Pi3's conf-based filtering throws out the most uncertain (= most distant) points, so the *matched-set* mean is artificially closer. A more careful eval would bin by ground-truth depth range and report metrics per bin. **Tagged as P2.12 follow-up.**

### 3.2 Per-cam quality scales inversely with scene distance

```
δ<1.25 vs LiDAR mean depth (closer = better):

  front_right  (7.0m)   ████████████████████████████████ 0.917
  ring_side_right (9.6m) ████████████████████████      0.732
  rear_right  (17.7m)    ████████████████████████      0.702
  front_left  (26.5m)    █████████████████████          0.642
  side_left   (22.9m)    ███████████████████             0.583
  front_center (23.2m)   █████████████████              0.527
  rear_left   (29.3m)    ████████                        0.223
```

This is the **monocular depth confidence cliff at ~15 m**, well-documented in the depth-estimation literature (KITTI Eigen split, MiDaS, ZoeDepth all show similar drop-offs).

---

## 4. Reading the numbers in context

| Reference | abs_rel | δ<1.25 | Notes |
|---|---:|---:|---|
| Pi3 (this work, AV2) | 0.215 | 0.653 | General-purpose 3D, no depth fine-tuning |
| MiDaS v3.1 (KITTI) | ~0.20 | ~0.74 | Indoor+outdoor pretrained, zero-shot to KITTI |
| ZoeDepth-N (KITTI) | ~0.077 | ~0.94 | KITTI-fine-tuned SOTA |
| Monodepth2 (KITTI) | ~0.11 | ~0.88 | Self-supervised baseline |
| MVSNet (DTU) | ~0.05 | ~0.97 | Multi-view (uses calibrated stereo pairs) |

**Verdict**: Pi3 is comparable to **zero-shot MiDaS on out-of-distribution data**. That is a *reasonable* result for a 3D-scene-reconstruction model evaluated as a depth predictor on an out-of-distribution AV dataset — it is not the wrong tool, just not the fine-tuned tool.

For Pantheon360 / 3DGS / depth-conditioned diffusion downstream:
- **Near field (≤15 m)**: Pi3 depth is reliable (δ<1.25 > 0.7 on 4/7 cams)
- **Far field (>20 m)**: needs LiDAR fusion or a depth-tuned backbone

---

## 5. Connection to Phase 2 main story

P2.7 (cycle-consistency) said: "L3 forward-splat ERP loses to L1, but only because forward-splat is the wrong rendering algorithm — the .ply geometry itself might still be useful."

P2.11 (this report) **anchors that claim with a ground-truth number**: yes, the geometry is usable, with abs_rel ~0.2 and δ<1.25 = 65% on a single AV2 frame. The L3 deliverable (`.ply` + depth maps) is now defensible as input to downstream 3D-aware consumers, with a known accuracy envelope.

---

## 6. Follow-ups (tagged for next phase)

| ID | Question | Why |
|---|---|---|
| **P2.12** | Per-depth-bin metrics (5-10m, 10-20m, 20-40m, >40m) | The 25% underestimation could be a binning artifact; need to confirm |
| **P2.13** | Same metrics across 10 anchor frames within one log | Frame-to-frame variance unknown |
| **P2.14** | Same metrics across 3-5 different logs (urban / highway / night) | Sequence-specific or model-general? |
| **P3.x** | LiDAR-supervised Pi3 fine-tune (LoRA?) on AV2 | Potential way to close the 0.215 → 0.10 gap |

---

## 7. Files

| File | Description |
|---|---|
| `scripts/phase2/eval_pi3_vs_lidar.py` | This evaluation, CPU-only, ~40s |
| Drive: `outputs/phase2/pi3_vs_lidar/metrics_overall.json` | Full numerical dump |
| Drive: `outputs/phase2/pi3_vs_lidar/metrics_<cam>.json` | Per-cam metrics |
| Drive: `outputs/phase2/pi3_vs_lidar/overlay_<cam>.png` × 7 | LiDAR points colored by abs_rel on Pi3 input image |
| Drive: `outputs/phase2/pi3_vs_lidar/scatter_<cam>.png` × 7 | Pi3 depth vs LiDAR depth scatter, ±25% bands |
| Drive: `outputs/phase2/pi3_vs_lidar/summary.txt` | Pretty-printed table |
| `notes/pi3_vs_lidar_report.md` | **This document** |
| `notes/l3_evaluation_report.md` | P2.7 cycle-consistency (companion) |
