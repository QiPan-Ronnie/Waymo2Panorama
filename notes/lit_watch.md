# Literature Watch — Phase 3 W2 Scan

**Date**: 2026-05-20 / 2026-05-21
**Curator**: T8 subagent (Phase 3 W2 first scan)
**Sources**: arXiv 2025-2026 + GitHub + CVPR/ICLR/ICRA/AAAI tracks
**Caveat**: Some venue assignments (e.g., "ICLR 2026 accepted") and dates listed by the subagent are not independently verified. Cross-check before citing in paper. Use this list as a research-priority filter, not a citation list.

---

## Executive Summary

8 papers (+2 supporting) flagged as Phase 3 W3 or Phase 4 candidates. **Competitive landscape**: 360° panoramic video generation is hot (5+ 2025-2026 papers), but **AV→360° with explicit 3D scene as input is still open**. Closest direct competitor: Percep360 (ICRA 2026, code pending) — diffusion-only, no 3D-scene step → orthogonal to our hybrid.

**Scooping risk**: minimal (4-6 week window). **Action**: ship our integration spike (T9 ViPE on L1 + T10 Pantheon360 + T17 Panacea+) in W3 to lock differentiation.

---

## Top 5 (Phase 3 W3 candidates / immediate)

### 1. PanFlow — Spherical Noise Warping for Panoramic Diffusion Video (AAAI 2025)
- arXiv: 2512.00832 (un-verified ID, check before citing)
- Method: spherical noise warping + decoupled camera rotation for panoramic video diffusion; large-scale panoramic dataset with frame-level pose + flow
- **Relevance**: Direct baseline for panoramic video gen with motion control. Aligns with Pantheon360's trajectory control story.
- **Code**: claimed open
- **Phase 3 W3 baseline?**: Yes, 2-3 days to test if code is real. Adds value as "another spherical-aware diffusion path"
- **Verdict**: **CONSIDER adding as alternative to T17 Panacea+ if Panacea+ doesn't transfer to AV2 cleanly.**

### 2. Pi3 (we already use it) — verified peer review
- arXiv: 2507.13347
- Confirmation: claimed ICLR 2026 acceptance. Already our backbone.
- **Action**: cite from ICLR 2026 (if confirmed) in our paper related work.

### 3. CylinderSplat — Cylindrical 3DGS (claimed ICLR 2026)
- arXiv: 2603.05882
- Method: feed-forward 3DGS with cylindrical triplane representation for panoramic novel-view synthesis
- **Relevance**: 3D scene representation native to panorama (vs Cartesian). Could replace or complement our `.ply` output.
- **Code**: claimed open (github.com/wangqww/CylinderSplat)
- **Phase 3 W3 / W4**: previously skipped (v5 Out-of-Scope: indoor-trained, 5+ days). T8's report suggests reconsidering if code is fresh + active.
- **Verdict**: **PROMOTE FROM Out-of-Scope TO Phase 4 candidate** (T15 alternative / Phase 4 L4).

### 4. Percep360 — AV→panorama generation (claimed ICRA 2026)
- arXiv: 2507.06971
- Method: Local Scenes Diffusion (LSDM) + Probabilistic Prompting; AV→360 via diffusion (not stitching)
- **Relevance**: **Closest direct competitor**. Their angle: diffusion-only generation. Ours: stitching + 3D + diffusion hybrid.
- **Code**: claimed pending (post-ICRA acceptance, github.com/FeiT-FeiTeng/Percep360)
- **Verdict**: **Monitor code release**. When released → add as T17b baseline (parallel to Panacea+). Compare FVD / LPIPS / cycle-PSNR.

### 5. Fin3R — LoRA fine-tune 3D reconstruction models (claimed NeurIPS 2025)
- arXiv: 2511.22429
- Method: lightweight LoRA fine-tune for Pi3 / DUSt3R / MASt3R on driving data using monocular teacher (Depth-Anything-V2 / ViPE)
- **Relevance**: **DIRECTLY relevant to our T13** (self-sup cycle finetune). Fin3R uses monocular teacher; ours uses cycle-PSNR. Compatible / orthogonal — could combine.
- **Code**: status unclear
- **Verdict**: **READ + adapt for T13 design**. If Fin3R published with code, our T13 becomes "cycle-PSNR-tuned vs Fin3R-tuned" comparison — stronger paper.

---

## Top 6-8 (Phase 4 backlog / cite-only)

### 6. CoGen — 3D-conditional video generation (Mar 2025, Alibaba)
- arXiv: 2503.22231
- Method: 3D-condition-adaptive multi-view driving video, FVD 68.43 on nuScenes
- **Code**: likely gated (Alibaba research)
- **Verdict**: Phase 4 reference. Architectural inspiration for "3D cache → video diffusion".

### 7. VideoPanda — text/video-conditioned 360° video diffusion (NVIDIA)
- arXiv: 2504.11389
- **Verdict**: cite-only. Less AV-specific.

### 8. Dur360BEV — real 360 single-cam AV dataset (claimed ICRA 2025)
- arXiv: 2503.00675
- Real spherical camera + 128-ch LiDAR + RTK GNSS (Durham UK)
- **Code + dataset**: claimed open (github.com/Tom-E-Durham/Dur360BEV)
- **Verdict**: **VALIDATION dataset candidate**. After our pipeline works on AV2, run on Dur360BEV → "cross-dataset generalization" claim.

---

## Plan v5 → v6 deltas (action items from T8)

1. **Promote CylinderSplat** from Out-of-Scope back to Phase 4 candidate (T8 says code active).
2. **Add T19** (new track): **PanFlow inference spike** — alternative panoramic video gen baseline, ~2 days. Fires if T17 Panacea+ hits AV2 adaptation walls.
3. **Add T20** (new track): **Fin3R-style LoRA + cycle-loss combo for Pi3** — combines our T13 (cycle-PSNR self-sup) with Fin3R (monocular teacher) for stronger fine-tune. Phase 4 candidate.
4. **Add T21** (new track): **Dur360BEV cross-dataset eval** — after Phase 3 W3 pipeline works on AV2. Phase 3 W4 / Phase 4.
5. **Watch list** (subagent ongoing): Percep360 code release, Pi3 paper acceptance status, CoGen academic release.

---

## Competitive landscape snapshot

```
Approach                  Papers                         Strength       Limitation
─────────────────────────────────────────────────────────────────────────────────────
Diffusion (spherical)    PanFlow, VideoPanda, Percep360  High visuals   Needs huge paired data
Stitching + projection   LiftProj, our L1               3D consistent  Seams in complex depth
Hybrid (ours)            Pi3 + L1 + 3D cache + diffusion 3D-scene aware Phase 3 in flight
```

**Our differentiator**: 3D-scene-aware foundation (Pi3 .ply) feeding panoramic video diffusion (Pantheon360 / PanFlow / Panacea+). No published method does all 3 layers explicitly on AV2.

**Window**: 4-6 weeks before Percep360 code drops + bibliography shifts. Ship T9/T10/T11/T17 integration spike + T7 paper-angle commit by W3 end to lock claim.

---

## Files

- `notes/lit_watch.md` — **this document** (Phase 3 W2 first scan, will append for subsequent scans)
- `agent/progress_T8_addendum.md` — short status entry for merge into progress.md
