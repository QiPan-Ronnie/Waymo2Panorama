# T14 — IPM ground prior + sphere projection hybrid (addendum)

2026-05-20 — Added `code/waymo2panorama/projection/ipm_ground.py` (Method A ground detection via Pi3 ego-z + analytical IPM with virtual panorama center 1.5m above ground), plus orchestrator `scripts/phase3/run_ipm_hybrid.py` and cycle-eval `scripts/phase3/eval_ipm_hybrid_cycle.py`. Self-test passes.

Submitted Colab CPU job to run hybrid + cycle-eval on anchors 60, 0, 150 (top-3 parallax per T6). Results + per-anchor visual A/B and ground-only PSNR delta vs L1 written to `notes/ipm_hybrid_report.md`. Pi3 outputs read from Drive `outputs/phase3/p3.1_multi_anchor/anchor_{060,000,150}/`.

Outcome — see `notes/ipm_hybrid_report.md` § "TL;DR".
