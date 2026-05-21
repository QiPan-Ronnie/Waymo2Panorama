# T14 — IPM ground prior + sphere projection hybrid (addendum)

2026-05-20 — Implemented `code/waymo2panorama/projection/ipm_ground.py` (Method A ground detection via Pi3 ego-z + analytical IPM with virtual panorama center 1.5m above ground), orchestrator `scripts/phase3/run_ipm_hybrid.py`, and cycle-eval `scripts/phase3/eval_ipm_hybrid_cycle.py`. Ran locally on anchors 60, 0, 150 (Colab worker was offline so used local CPU + downloaded Pi3 outputs from Drive).

**Result**: ground-only cycle-PSNR `+0.20 ± 0.11 dB` vs L1 baseline averaged over 3 anchors, with rear cams hitting **+1.0 to +1.5 dB** consistently. Full-image PSNR unchanged (+0.04 dB), so safe to drop in. Visually: lane markings and crosswalks align across cam boundaries in the hybrid where L1 shows ghosting. **Versus L3 (Pi3 forward-splat) which was -3.15 dB, IPM hybrid is a structural improvement.** Runtime 3.3 s/anchor on CPU.

**Failure modes**: front-cam shadows / pedestrians regress (-0.5 dB ground-only); morphological gap-fill leaves magenta edge fringe. **Recommendation**: extend to 10-anchor sweep when Colab worker is back; add temporal stability check to fix front-cam regression. See `notes/ipm_hybrid_report.md` for full method, per-cam metrics, and visual evidence.
