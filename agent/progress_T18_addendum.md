# T18 progress addendum

- **2026-05-21 03:58 UTC** — Wrote `scripts/phase3/run_depth_backbone_swap.py` (3 backbones: DepthPro via HF transformers `apple/DepthPro-hf`, Metric3D v2 via torch.hub, Pi3 baseline). Submitted Colab job `phase3-t18-depthpro-anchor60-piponly` (pip-only, no git clone). Exit code 0 in 51 s.
- **Headline finding** — DepthPro on AV2 anchor 60: abs_rel = **0.580** (vs Pi3 0.204), δ<1.25 = **0.064** (vs Pi3 0.633). Depth Pro is 3× worse on outdoor driving than Pi3 — domain shift + letterbox padding artifacts. L3 forward-splat cycle-PSNR Δ = −0.01 dB but **15% L3 coverage** vs Pi3's 50% — the apparent gap-closing is an intersection-mask artifact, not a real algorithmic win.
- **Verdict** — angle C (algorithm wrong, not backbone) reinforced. Backbone-tuning (angle B) is NOT a free lunch; needs AV2 fine-tune first. Full report: `notes/t18_depthpro_report.md`.
