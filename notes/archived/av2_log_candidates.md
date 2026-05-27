# AV2 Val Log Candidates for T1 Multi-Log Extension

**Date**: 2026-05-21
**Author**: T1-prep subagent + main thread
**Status**: Phase A (strategy) done; Phase B (in-situ UUID discovery) requires Colab worker

---

## Summary

AV2 paper / docs / GitHub do NOT publish an explicit val split UUID list. Strategy: use AV2 SDK to scan val split metadata + filter by city/scenario criteria.

AV2 val split composition (from Wilson et al. 2023, NeurIPS Datasets):
- Total: 1,000 logs (700 train / **150 val** / 150 test)
- Cities: Miami 354, Pittsburgh 350, Detroit 117, Washington DC 126, Austin 31, Palo Alto 22 (counts across all splits; val is ~15% of each city's allocation)
- Per-log: 15-16s @ 20Hz = ~300-320 frames, ~7-10 GB

**Currently downloaded**: `02a00399-3857-444e-8db3-a8f58489c394` (Miami urban daytime, val, confirmed working in Phase 0.5 spike)

---

## Selection criteria (4 + 1 backup)

| # | Type | City preference | Scenario heuristic | Why |
|---|---|---|---|---|
| 1 | Urban daytime baseline | Miami | medium ped count, mid-day timestamp | Compare to current 02a00399 — same conditions, intra-city variance |
| 2 | Highway / sparse | Pittsburgh | low ped count, mean LiDAR depth >30m | Tests Pi3 far-field bias |
| 3 | Dense intersection / peds | Detroit or DC | ped:vehicle ratio >0.1 | Tests forward-splat ghost zone (multi-class objects, mid-distance) |
| 4 | Night / low-light | DC | mean image brightness <80 (8-bit) | Tests Pi3 conf calibration under domain shift |
| 5 (backup) | Austin or Palo Alto | sparse cities | any scenario | Geographic diversity backup if downloads fail |

---

## Phase B execution plan (when Colab back)

Use `scripts/phase3/find_av2_val_candidates.py` (just written, see same dir) to:

1. Load AV2 val split via `av2.datasets.sensor.SensorDataloader`
2. For each val log: read `city_SE3_egovehicle.feather` (city metadata) + sample 5 frames per log for mean brightness + sample annotations for ped:vehicle ratio
3. Filter logs into 4 buckets:
   - **bucket 1**: city=Miami, ped/veh ratio 0.3-0.7, mean brightness 100-180
   - **bucket 2**: city=Pittsburgh, mean LiDAR depth >25m
   - **bucket 3**: city=Detroit OR DC, ped/veh ratio >1.0
   - **bucket 4**: city=DC, mean brightness <80 (night-like)
4. Pick 1 best UUID per bucket → write to `data/av2_val_picked.json`

Estimated runtime on Colab: 5-10 min (just reading metadata, no full image loading)

---

## Download plan

For each of 4 picked UUIDs:

```bash
s5cmd --no-sign-request cp \
  "s3://argoverse/datasets/av2/sensor/val/<UUID>/*" \
  "/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val/<UUID>/"
```

Expected total: 5 logs × ~8 GB = ~40 GB on Drive. Currently have ~10 GB used in val (1 log). Drive workspace has 50+ GB free, OK.

Per-log download time: ~5-10 min on Colab's network (s5cmd parallel).

---

## Open question

Once 4 UUIDs picked + downloaded, T1 (multi-log replication) fires P3.1b sweep (10 anchors × 4 logs = 40 anchor evals) via existing `scripts/phase3/run_pi3_multi_anchor.py` + `batch_eval_lidar.py` + `batch_eval_cycle.py`. Total Colab time: 4 × ~6 min = ~25 min.

Combined with `02a00399`, this gives N=5 logs × 10 anchors = 50 anchor evals — sufficient statistical power for paper-quality claims about Pi3 + L1 + L3 generalization.

---

## Confidence

**Phase A strategy: HIGH confidence** (based on AV2 paper + 6-city distribution + spike report).

**Phase B execution: requires Colab worker** — currently offline (heartbeat 50+ min stale as of T-Koi-1 / T12 fire time). When worker back, run `find_av2_val_candidates.py`.

---

## Files

- `notes/av2_log_candidates.md` — **this document**
- `scripts/phase3/find_av2_val_candidates.py` — in-situ UUID picker (CPU-only on Colab)
