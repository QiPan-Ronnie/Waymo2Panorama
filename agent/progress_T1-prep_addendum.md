### 2026-05-21 — [T1-prep] AV2 val UUID candidate strategy
- Result: AV2 paper/docs don't publish val UUID list. Strategy: 4 buckets (Miami urban / Pittsburgh highway / Detroit-DC dense ped / DC night) filtered by AV2 metadata heuristics (brightness, ped:veh ratio, LiDAR depth). Wrote `scripts/phase3/find_av2_val_candidates.py` for in-situ scanning when Colab worker back.
- Deliverable: `notes/av2_log_candidates.md` (strategy + Phase B plan), `scripts/phase3/find_av2_val_candidates.py` (CPU scan script, ~18 min on Colab to scan all 150 val logs).
- Next: run `find_av2_val_candidates.py` on Colab once worker restarts → pick 4 UUIDs → download via s5cmd (~40 GB) → fire T1 (multi-log replication).
