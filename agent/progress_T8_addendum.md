# T8 Progress Addendum — Phase 3 W2 Literature Watch

**2026-05-21** — Scanned 50+ papers (arXiv + GitHub + 2025-2026 venues). 8 high-relevance finds:
- **PanFlow** (AAAI 2025, arXiv:2512.00832) — spherical noise warping panoramic diffusion, alternative panoramic video gen baseline
- **Pi3** (claimed ICLR 2026) — our backbone, confirms peer-reviewed venue (cite update)
- **CylinderSplat** (claimed ICLR 2026, arXiv:2603.05882) — cylindrical 3DGS, promote from Out-of-Scope to Phase 4
- **Percep360** (claimed ICRA 2026, arXiv:2507.06971) — closest competitor (AV→pano diffusion only, code pending June 2026)
- **Fin3R** (claimed NeurIPS 2025, arXiv:2511.22429) — LoRA fine-tune Pi3/DUSt3R with monocular teacher, **directly relevant to our T13** (combine with cycle-PSNR self-sup)
- **CoGen** (Alibaba, gated) — Phase 4 reference for 3D-conditional video
- **Dur360BEV** (claimed ICRA 2025) — real 360 AV dataset, cross-dataset validation candidate
- **VideoPanda / PanoWorld-X** — cite-only architectural references

**Scooping risk**: minimal. AV→pano with explicit 3D-scene input is still open; Percep360 is closest but diffusion-only. **4-6 week window** before Percep360 code release.

**Plan v6 candidates added**: T19 (PanFlow spike), T20 (Fin3R + cycle-loss combo), T21 (Dur360BEV cross-dataset).

**Caveat**: subagent's arXiv IDs and venue claims are not independently verified — confirm before citing.

Files: `notes/lit_watch.md`.
