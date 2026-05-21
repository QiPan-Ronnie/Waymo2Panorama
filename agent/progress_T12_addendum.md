# T12 addendum — Multi-frame temporal Pi3

**2026-05-20 (UTC ~02:05)** — T12 implemented `scripts/phase3/run_pi3_temporal_multi_frame.py` (7K-view joint Pi3 forward, K∈{2,3,5}) + `eval_temporal_pi3_vs_lidar.py` (center-frame LiDAR + binned bias). Submitted Colab job `phase3-t12-temporal-pi3-k3-anchor90` (commit `a95f75c`) for K=3 anchor 90 vs Phase 3 W1 single-frame baseline (abs_rel 0.186, δ<1.25 0.725).

**Blocker**: Colab worker heartbeat stale (Drive shows last beat 2026-05-21T01:14:08 UTC, ~50 min before submission). Job is queued and committed; awaits user to restart worker cell. Numbers + verdict in `notes/temporal_pi3_report.md` (currently `TBD` placeholders) will fill in automatically once worker pulls and `temporal_lidar_metrics.json` lands at Drive `outputs/phase3/temporal_pi3/anchor090_K3/eval/`.
