# Waymo2Panorama

Multi-camera **360° panorama stitching** for autonomous-driving datasets (primarily Argoverse 2). Sub-project of the Koi Chen paper-reproduction chain. **Downstream consumer (provisional, 2026-06-09):** fundamentally **Bosch's world-model needs**; *currently* this looks like a **Cosmos-style 360° video diffusion pipeline** (via Xinhan, `cosmos-transfer2.5-pcd`; ingests first-frame + point-cloud path video + text). Treat the specific consumer as not-yet-fixed (earlier docs say Pantheon360). The **invariant core is the perspective→panorama algorithm itself.**

**Target venue**: 3DV 2026 (main or D&B track).
**Maintainer**: Qi Pan (panq@usc.edu), advisor Koi Chen.
**Status (2026-06-09)**: direction reset after a 19-agent retrospective; **read `agent/2026-06-09-fable5-firstprinciples-brief.md` and `agent/2026-06-06-deep-retrospective.md` first.** Root cause of churn = an unresolved source-faithful(A) vs look-good(B) fork → decision: **layer both**. DB-79 (fair-metric) settled: near-field surfaces are cm-recoverable, but the curb/wall **seam** stays a real wall (off-trajectory virtual-centre amplifies sub-meter silhouette depth error) → abstain or labelled-generate. Safest visual base remains AV2 raw L1 `hard_select` + DB-78 flow view-interp (safe but modest) + sky-only outpaint. *(The 2026-05-27 sections below are historical.)*

---

## 30-second pitch

Take Argoverse 2's **7-camera ring** (synchronized RGB at the same timestamp) and stitch into a **1024×2048 equirectangular (ERP) panorama** that downstream 3D-aware models can consume. Evaluated with **cycle-PSNR** (hold-one-cam reconstruction) since no panorama ground-truth exists for AV data.

Key finding so far: **classical sphere projection + multiband blending (L1) beats SOTA neural 3D-lift (Pi3 forward-splat, L3) by ~3 dB** — and 4 different depth backbones (Pi3, Apple Depth Pro, Temporal Pi3, OmniStitch) all fail similarly. Algorithm-class problem, not backbone-selection problem.

---

## Current seam status (2026-05-27)

The latest seam work moved beyond the original 8-route snapshot:

- `hard_select` fixes the multiband ghost/halo on AV2 raw and avoids near-field optical-flow fragmentation. It is the conservative Bosch-facing baseline.
- No-DL seam-local ECC alignment and DP seam-routing were tested on BMW / pedestrian / clean anchors. Both are safer ablations than full-image OF, but neither clearly beats `hard_select`.
- DiT360 masked seam completion now runs on A100. Raw DiT edits can fill seam strips, but they alter driving evidence. A hard post-compose variant restores all non-mask pixels exactly; however tiny masks do not repair geometry and wider masks introduce vertical/block artifacts.
- The current research framing is therefore: different optical centers make perfect single-surface panorama impossible; without depth or object-level coherence, the practical target is artifact minimization plus honest failure/confidence reporting.

---

## 8 stitching routes — current state

| ID | Method | Verdict | Headline number |
|---|---|---|---|
| **L1** | Sphere baseline + 5-band Laplacian | ✅ Strong baseline | cycle-PSNR **12.34 ± 1.31 dB** (10 anchors) |
| **L3** | Pi3 forward-splat (CVPR 2025 backbone) | ❌ Structural NEG (paper §4) | **-3.15 dB** vs L1, 10/10 anchors lose |
| **IPM** (T14) | Ground plane hybrid | ⚠️ Marginal +0.05 dB | +0.20 dB on ground-only mask |
| **新-A** | Cylindrical L2 | ⚠️ Coverage gain only | +24.9 pp coverage, cycle ~flat |
| **新-B** | Graph-cut seam selection | ✅ Visual win | -12.4% seam-band gradient (4/4 anchors) |
| **新-C** | IPM multi-region (ground+sky+building) | ✅ Method win | **+0.20 dB on ground** (4× T14) |
| **新-D** | Wide-baseline stereo (邻 cam) | ⚠️ Partial (5/7 pairs) | 44 inlier 3D pts/pair median |
| **新-E** | HDR cross-cam compensation | ✅ Drop-in preprocess | **-18% lum gap** (4 anchors) |

Plus **3 external NEG**: OmniStitch (-6.67 dB), Apple Depth Pro (2.84× worse abs_rel), Temporal Pi3 (no improvement) — all reinforce the L3-class-fails argument.

Plus **3 downstream demos** (paused per v6.1 pivot): ViPE SLAM, GEN3C image-to-video, Panacea+ (BEV→video modality NEG).

---

## Navigate the repo

**For advisors / reviewers / Koi**:
- `deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.md` (+ `.pdf`) — **THE Koi-facing deliverable**. 13 pages, 11 figures, 8 routes + 3 external NEG + 3 downstream demos + ranking table.
- `deliverables/meeting_cram.md` — 5-minute talking-points version for lab meetings.

**For new agents picking up this work**:
- `agent/handoff.md` — agent handoff doc with current state + defensive lessons + infrastructure pointers.
- `agent/progress.md` — single-source-of-truth log; latest entries at top.
- `C:\Users\14294\.claude\plans\snug-shimmying-wave.md` — full v6.1 plan with decision gates + risk register.

**For me (Qi) learning CV from scratch**:
- `deliverables/learning_plan.md` — 7-phase CV roadmap tied to actual files in this repo. Quick path 3 days / deep path 3-4 weeks.

**Code layout**:
```
code/waymo2panorama/
  data_io/         AV2 ring loader (RING_CAMS_7, calib parsing)
  projection/      sphere / cylinder / ipm_ground / ipm_multi_region
  blending/        multiband (Laplacian pyramid) + graphcut_seam
  stereo/          wide_baseline_stereo (LightGlue + DLT)
  color/           hdr_gain_estimate (6-param gain+bias per cam)
  alignment/       sim3_align (Umeyama)
  pipeline/        lift_and_project (L3) + depth_bayesian_fusion
  utils/           colab_jobs / drive_queue helpers
scripts/
  phase2/          single-anchor evals (eval_pi3_vs_lidar, eval_cycle_consistency)
  phase3/          multi-anchor batch evals + per-route drivers + run_*_multi_anchor
  phase4/          (placeholder) T13 finetune scripts when needed
deliverables/
  handoff_to_koi_w2_2026-05-21_v6cpu_done.{md,pdf}  THE Koi handoff
  meeting_cram.md                                     5-min talking points
  learning_plan.md                                    7-phase CV roadmap
  _render_pdf_v6cpu.py + _make_*.py                   figure / PDF tooling
  images/route_*.png                                  paper figures
agent/
  handoff.md      agent onboarding doc
  progress.md     single-source-of-truth log (append-only)
notes/
  new_{a,b,c,d,e,f}_*.md     per-route research / design docs
  t13_*.md                   T13 self-sup finetune design
jobs/*.json                  Historical Colab queue specs (do not use for new runs)
outputs/phase3/*             run results (gitignored; aggregate JSONs tracked manually)
```

---

## Primary dataset

**Argoverse 2 Sensor** — 7 ring cameras + 2 stereo, full 360° with overlap, MIT-licensed API, undistorted imagery, calibration provided. Main log: `02a00399-3857-444e-8db3-a8f58489c394` (10 canonical anchors at indices `[0, 30, 60, 90, 120, 150, 180, 210, 240, 270]`). Additional 4 logs downloaded for multi-log validation (T1).

---

## Open decisions (G3 v6 gate)

| Decision | Default if no input | Trigger to flip |
|---|---|---|
| **Paper angle**: A' Method / B-with-C / C Negative-only | A' Method (3 stack-able positives) | Koi says "not enough wins" → fall back to B |
| **Run 新-F VGGT** (4th backbone NEG, ~6-16h A100) | Skip (gated repo blocker, low marginal value) | User clicks HF access + says go |
| **Run T13 Pi3 self-sup finetune** (5-6 day A100) | Skip (paper angle A' doesn't need it) | Koi says "need ≥1 backbone-level win" |
| **Target venue**: 3DV 2026 main / D&B / CVPR workshop | 3DV 2026 main | Reviewer feedback on draft v0 |

---

## Tooling

- Python 3.10/3.12 (Colab compat)
- AV2 API: `pip install av2`
- Pi3: `../../../01-pi3/code/official/Pi3` (local clone) + HF `yyfz233/Pi3X` (open)
- VGGT: `git clone https://github.com/facebookresearch/vggt` + HF `facebook/VGGT-1B-Commercial` (GATED — needs user click)
- Compute: Colab Pro A100 (panq@usc.edu)
- Colab execution: `agent-colab-direct` raw HTTP `/exec` via the active Cloudflare URL/token written to Drive `runtime/active_url.json`. The older `agent-colab-queue` path is frozen and should not be used for new experiments.

---

## License & data

- Code: MIT (TBD).
- Data: respect upstream dataset licenses (AV2: CC-BY-NC, Waymo: research license, nuScenes: CC-BY-NC-SA).
- HF tokens / GitHub SSH keys: stay LOCAL only, never push to Colab or send to agents.

---

## Quick links

- Project chain: `01-pi3` (depth backbone) → **this repo** (stitching) → `04-pantheon360` (3D-aware diffusion) → Cosmos / Argus (world model)
- 1-liner project statement: "AV ring-cam 7-view → 360° ERP panorama, geometric-first method with neural depth as ablation."
