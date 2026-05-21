# T6 addendum — Parallax-heavy anchor ranking

**2026-05-20** — Ranked the 10 existing P3.1 Pi3 anchors by parallax score (mean closeness × conf coverage × multi-cam consensus, from summary.json per-cam median depths). Top-3 = anchors {0, 150, 60}; bottom-2 = {180, 210}. Anchor 60 (parallax rank #3) was P3.1b's least-bad L3 result (ΔPSNR −1.60 dB), and anchor 180 (parallax rank #9) had Pi3's best abs_rel — confirming "accurate depth" and "parallax-rich" are orthogonal axes for L3 vs L1.

**Recommendation for T18 (Depth Pro) / T12 (multi-frame Pi3)**: run on anchor 60 first (best chance of L3 winning), then 0 and 150; use 180 as negative control. Score range across the 10 anchors is narrow (0.32 → 0.41), so we cannot claim "L3 wins on parallax-rich subset" without a new ΔPSNR experiment — the ranking only identifies *where to look first*.

**Files**: `data/parallax_subset.json`, `notes/parallax_subset_report.md`, `outputs/phase3/parallax/anchor_*_summary.png` (×10) + `parallax_ranking.png`, `scripts/phase3/rank_parallax.py`.
