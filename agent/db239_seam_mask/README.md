# DB-239 B-route — module index

Full record: `experiments/Waymo2Panorama/deliverables/db239_broute_scene_band/RESULTS.md`.
Everything here runs on CPU (no GPU, no LiDAR in the pixel path) and works both
locally and on Colab; geometry mirrors production `db89_ghost_recovery`
(EMC poses, max-facing territory, support contract).

| Module | Role |
|---|---|
| `db239_seam_mask.py` | Core: EMC pose loader, support/territory, rotation-only sampling helpers, photometric seam disagreement + mask morphology. |
| `db239_broute_demo.py` | Single-frame B-route demo (fr_0037) — the first render that fixed all three defect classes. |
| `db239_broute_temporal.py` | B-93: all 93 frames + pre-registered temporal gates (R2/R3 riders inside). |
| `db239_b93_harden.py` | Pre-registered fallback ladder: hysteresis 16/11.2 + 2-of-5 persistence + keep-islands; writes unmasked renders + hardened masks + clips. |
| `db239_union_probe.py` | One-log union-budget probe (S3 fetch + harden pipeline) — used for the slowest/fastest cross-scene validation. |
| `db239_probe_job.py` | Early single-frame seam-disagreement probe with tau sweep. |
| `db239_source_fidelity.py` | "Is every delivered pixel a sensor pixel somewhere?" sweep (depth-swept min-residual). |
| `db239_rule5_ab_job.py` | The falsified Rule-5 hypothesis A/B (kept as the record of a clean kill). |

Shipping contract (as of 2026-08-11): rotation-only band + hardened
window-union mask (16.19% on the acceptance scene), ceiling 17.5% with
auto-flag; per-frame masks (255 = KEEP/supervise, 0 = RECONSTRUCT/no-loss)
ship alongside every window.
