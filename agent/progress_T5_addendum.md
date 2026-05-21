# T5 addendum — cycle-PSNR metric audit (2026-05-20)

- **Audit done** on anchor 0, 7 cams, with LPIPS-Alex + MS-SSIM (4-scale) + region-separated PSNR (sky/object/ground thirds). L1 wins on every metric and every region in aggregate; LPIPS gap is wider than PSNR gap (L3 = 1.83× worse perceptually); object band has the biggest L3 deficit (−6.88 dB). See `notes/metric_audit.md`.
- **Verdict**: PSNR is NOT structurally biased here — perceptual and multi-scale metrics all rank L1 > L3 by larger margins than PSNR does. The P2.7 / P3.1b negative finding for L3 forward-splat is robust to the choice of metric.
- **Recommendation**: keep PSNR as headline; add MS-SSIM + LPIPS as a three-number tuple in the paper's main table to pre-empt "you cherry-picked the metric" reviewer pushback. Files: `scripts/phase3/audit_metrics.py`, `outputs/phase3/metric_audit/anchor0_audit.{json,_table.md}`.
