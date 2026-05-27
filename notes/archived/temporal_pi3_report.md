# T12 — Multi-Frame Temporal Pi3 Inference (7×K view joint forward)

**Date**: 2026-05-20
**Anchor**: AV2 val log `02a00399-3857-444e-8db3-a8f58489c394`, center anchor 90 (matches Phase 3 W1 single-frame test bed)
**Backbone**: Pi3X (yyfz233/Pi3X)
**Hardware**: Colab A100-SXM4-40GB, bf16 autocast

---

## 1. Idea & hypothesis

Pi3X's transformer decoder is permutation-equivariant over the N-view dimension (no positional encoding tied to view index — only RoPE over patches within each view). If we feed K consecutive ego frames × 7 ring cams as a single (1, 7K, 3, H, W) input instead of K separate (1, 7, ...) forwards, the network internally cross-attends every view against every other view.

The 7 spatial views give baselines of 0.5-2 m (cross-camera triangulation, narrow). The K temporal views add 5-15 m baselines (ego moves at ~10 m/s × 0.05 s/frame × K-1 inter-frame intervals). A wider effective baseline is the textbook way to fix far-field stereo depth bias.

Phase 3 W1 P3.3 measured Pi3's far-field bias at **−24% ± 7% at >40m** (depth-binned multi-anchor mean, structural, monotonic with depth). This is the target this experiment attacks.

## 2. Method

`scripts/phase3/run_pi3_temporal_multi_frame.py`:
1. Take `--anchor-idx N` and `--frames-per-window K` (K∈{2,3,5}).
2. Pick K consecutive anchor indices centered on N, clamped to [0, n_anchors).
3. For each (frame, cam) pair, letterbox to 504×504 (same recipe as `run_pi3_multi_anchor.py`).
4. Stack to `(1, 7K, 3, 504, 504)` tensor → single Pi3X forward (bf16 on A100).
5. Decompose outputs into `frame_<local_idx>/<cam>` subdirs that match Phase 3 W1 schema 1-to-1 so existing eval scripts work unchanged.

`scripts/phase3/eval_temporal_pi3_vs_lidar.py`:
1. Take the CENTER frame's directory.
2. Reuse `eval_pi3_vs_lidar.py` projection + metric code path → overall + per-cam abs_rel / RMSE / δ<1.25.
3. Additionally compute depth-binned metrics via `eval_pi3_lidar_binned.py.bin_metrics` so the far-field-bias question is directly answerable.

## 3. Status — BLOCKED ON DEAD COLAB WORKER

Job `phase3-t12-temporal-pi3-k3-anchor90` was submitted at 2026-05-21T02:02 UTC (commit `a95f75c`). The agent-colab-queue heartbeat on Drive (`MyDrive/koi_waymo2pano_colab/worker/heartbeat.json`) shows `updated_at = 2026-05-21T01:14:08Z` — ≥50 min stale at submission time. `active_jobs = []`. The Colab notebook worker cell is not running.

The job spec, scripts, and result-watch path are all in place:

| Artefact | State |
|---|---|
| `jobs/phase3-t12-temporal-pi3-k3-anchor90.json` | committed + pushed to main (`a95f75c`) |
| `scripts/phase3/run_pi3_temporal_multi_frame.py` | committed (`62a162a`) |
| `scripts/phase3/eval_temporal_pi3_vs_lidar.py` | committed (`62a162a`) |
| Result file to watch | Drive: `outputs/phase3/temporal_pi3/anchor090_K3/eval/temporal_lidar_metrics.json` |

When the user restarts the Colab worker cell, the job will be pulled within ~10 s and the result will land in ≤300 s (per Phase 3 W1 cost model: HF cache warm 36 s + pip 30 s + Pi3 forward 21 views ~3-15 s + eval ~10 s).

### 3.1 Cost (forward pass, K=3, 21 views @ 504²) — projected, awaiting confirmation

| Quantity | Phase 3 W1 single-frame (7 views) | T12 K=3 (21 views) projected |
|---|---:|---:|
| Model load (HF cache hit) | ~36 s | ~36 s (same model) |
| Forward pass | 1.23 s mean (warm) | ~3-5 s (3× view count, but joint attention is O(N²)) |
| Peak GPU memory | 7.5 GB | ~20-30 GB est. (attention scales O(N²) — risk on 40GB A100) |

If 504²×21 OOMs the A100, fallback in the script is built in: shrink to K=2 (14 views) at full res, or stay at K=3 with target_side=392. The job spec does not currently chain these fallbacks — it is a K=3@504² point estimate. Need to submit a follow-up job spec if first attempt OOMs.

### 3.2 LiDAR depth metrics — center frame (anchor 90)

| Metric | Phase 3 W1 single-frame (anchor 90) | T12 K=3 center | Δ |
|---|---:|---:|---:|
| abs_rel (overall) | 0.186 | **AWAITING** | — |
| RMSE (m) | 4.80 | **AWAITING** | — |
| δ<1.25 | 0.725 | **AWAITING** | — |
| n matched | 91,062 | **AWAITING** | — |

### 3.3 Far-field bias — the headline test

| Bin (m) | Phase 3 W1 10-anchor mean bias % | T12 K=3 bias % (anchor 90) |
|---|---:|---:|
| [0.5, 5) | −10.2 ± 11.2 | **AWAITING** |
| [5, 10) | −16.3 ± 5.8 | **AWAITING** |
| [10, 20) | −20.2 ± 6.7 | **AWAITING** |
| [20, 40) | −21.1 ± 5.8 | **AWAITING** |
| [40, 60) | **−23.7 ± 6.8** | **AWAITING** |

## 4. Verdict

**Pending.** The hypothesis test is well-defined but cannot run without the Colab worker. The success criterion in the task brief is: K=3 abs_rel < 0.186 (the single-frame anchor 90 baseline), AND far-field bias |%| at >40m drops below 24%. If that hits, fire K=5; if K=3 misses, write it up as a negative result and consider whether the joint attention is too noisy or the inter-frame baseline too small.

## 5. Recommendation

Next steps in priority order, all dependent on worker being back online:

1. **Re-check heartbeat** at Drive `worker/heartbeat.json` — if `updated_at` is fresh (within 60 s), the queued job will execute automatically.
2. **If K=3@504² runs and abs_rel improves**: submit K=5 follow-up (`phase3-t12-temporal-pi3-k5-anchor90`, same command with `--frames-per-window 5` and output dir `anchor090_K5`).
3. **If K=3@504² OOMs**: submit a fallback job at target_side=392 (560 → 392 reduces patches from 36² to 28² ≈ 60% memory).
4. **If K=3 wins on anchor 90**: scale to 10-anchor temporal sweep (parallel to Phase 3 W1 `p3.1_multi_anchor`) — this would re-use the existing 10 anchors {0, 30, 60, 90, 120, 150, 180, 210, 240, 270} so we get apples-to-apples comparison with the 10-anchor mean bias of −23.7%. Single-pass per anchor at K=3, mean 21-view forward ~3-5 s → total ~50-80 s for 10 anchors warm.
5. **If K=3 loses**: do not invest more in temporal; the permutation-equivariance assumption may not transfer the cross-view geometric signal as cleanly as we hoped (Pi3 may rely on RoPE-tagged within-image position rather than between-image relations).

## 6. Files

| File | Description |
|---|---|
| `scripts/phase3/run_pi3_temporal_multi_frame.py` | T12 N-frame joint Pi3 forward |
| `scripts/phase3/eval_temporal_pi3_vs_lidar.py` | Center-frame LiDAR eval + binned metrics |
| `jobs/phase3-t12-temporal-pi3-k3-anchor90.json` | Colab job spec (K=3) |
| Drive: `outputs/phase3/temporal_pi3/anchor090_K3/` | Per-frame Pi3 outputs (K=3) |
| Drive: `outputs/phase3/temporal_pi3/anchor090_K3/eval/temporal_lidar_metrics.json` | T12 K=3 LiDAR metrics |
