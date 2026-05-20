# Phase 3 — Partial Report (waiting for A100)

**Status as of 2026-05-20 ~23:14 UTC**: P3.3 done (CPU); P3.1 (multi-anchor Pi3) blocked on A100. P3.1b / P3.4 blocked on P3.1.

---

## P3.3 — Depth-binned Pi3 vs LiDAR (CPU, done)

**Question**: P2.11 showed Pi3 underestimates depth by ~25% overall. Is that a real depth-dependent compression, or a *selection bias* artifact (conf-filter kicks out far/uncertain points → matched set is artificially closer)?

**Answer**: **Real depth-dependent compression.** Bias % grows monotonically with depth even within fixed bins, so it's not the filtering.

### Results (anchor 0, single frame, all 7 cams)

| LiDAR bin (m) | n | abs_rel | RMSE (m) | δ<1.25 | LiDAR μ | Pi3 μ | bias |
|---|---:|---:|---:|---:|---:|---:|---:|
| [0.5, 5) | 15,612 | **0.176** | 0.87 | **0.884** | 4.10 | 3.57 | **-12.8%** |
| [5, 10) | 16,554 | 0.191 | 1.57 | 0.675 | 6.86 | 5.66 | -17.5% |
| [10, 20) | 33,702 | 0.190 | 3.73 | 0.689 | 16.11 | 13.05 | -19.0% |
| [20, 40) | 22,119 | 0.238 | 7.48 | 0.541 | 26.41 | 19.98 | -24.3% |
| [40, 60) | 11,028 | **0.333** | 19.30 | **0.408** | 48.08 | 31.84 | **-33.8%** |

### Implications

1. **Near-field (<5m) Pi3 is SOTA-class** (δ<1.25 = 0.88, abs_rel = 0.18). For downstream consumers focused on near-ego perception (Pantheon360, lane-level diffusion training), Pi3 is reliable.

2. **Far-field (>20m) Pi3 is unusable as-is** — δ<1.25 = 0.41 means 60% of points are >25% off. For ego-frame scene reconstruction beyond ~20m, need either LiDAR fusion or a depth-tuned backbone.

3. **The "compress everything closer" pattern is a known monocular failure mode** (see ZoeDepth, NeWCRFs papers). Scale + shift correction (one-off per scene) might fix the ratio but won't fix variance.

4. **This is paper-worthy negative finding** — adds to the P2.7 story: "we tried to use monocular 3D for 360 panorama and found three specific failure modes (forward-splat ERP, depth compression, conf cliff)."

### Follow-ups this confirms we *don't* need to do

- ❌ Adjusting Sim(3) global scale to fix the underestimation. The bias is non-uniform, a single scalar can't fix it.
- ❌ Per-cam scale calibration. The bias is depth-dependent, not cam-dependent.

### Follow-ups this opens

- ✅ Per-bin scale-shift correction (if we wanted to remap Pi3 → calibrated depth): would need both s(d) and t(d) per scene. Could be done as post-process for Pantheon360 if accuracy >15m matters.
- ✅ **Pi3 + LiDAR fusion at >20m** as a Phase 4 quick win: trivial weighted average if both available, ~2 days of work.

---

## Phase 3 task status (current)

| ID | Task | Status | Blocker |
|---|---|---|---|
| P3.3 | Depth-binned metrics on anchor 0 | ✅ DONE | — |
| P3.1 | Pi3 multi-anchor (10 anchors) | ⏸️ HOLD | A100 not active (CPU runtime detected) |
| P3.1b-cycle | Batch P2.7 over 10 anchors | ⏸️ HOLD | needs P3.1 |
| P3.1b-lidar | Batch P2.11 over 10 anchors | ⏸️ HOLD | needs P3.1 |
| P3.4 | L1 ERP temporal smoothness (5s) | 🟡 next CPU candidate | — |
| P3.2 | Multi-log extension | ⏸️ later | needs more AV2 downloads |
| P3.5 | OmniStitch baseline | ⏸️ later | Week 4 |
| P3.7 | Pantheon360 integration spike | ⏸️ later | Week 4 |

---

## Runtime probe result

```
python: 3.12.13
torch:  2.10.0+cpu
cuda_available: false
device_name: null
```

Colab session needs to be switched to A100 (Runtime → Change runtime type → A100 GPU) before P3.1 can proceed.

When A100 is on, P3.1 multi-anchor (10 anchors × Pi3 7-cam forward) should take ~2 minutes:
- Model load: 36s (one-time)
- Per-anchor forward: 8.35s × 10 = 83.5s
- Drive I/O: ~30s
- **Total ~2.5 min**

Then P3.1b LiDAR-eval batch over 10 anchors: ~5 min CPU. Total Phase 3 wave 1: under 10 min wall-clock from "A100 on" to "report ready".
