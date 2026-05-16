# Waymo2Panorama — Implementation Plan (Draft)

Date: 2026-05-15
Status: Draft v0 — intentionally written *before* `/ultraplan` deep-think completes, so the cloud session can challenge / sharpen / extend this.
Author: Claude Code, local session
Inputs: `2026-05-15-brainstorm-survey.md` (full method landscape) + `handoff.md`

---

## §1 — Goal Statement

Build a **reproducible pipeline** that takes synchronized multi-camera frames from autonomous-driving datasets (primary: Argoverse 2 Sensor) and produces **equirectangular 360° panoramic video** that:

1. Is geometrically faithful at multiple depth layers (no ghosting on near objects)
2. Is temporally consistent across frames
3. Plugs directly into Pantheon360 / Argus / other 360 downstream consumers
4. Comes with a clean evaluation protocol (so methods are comparable)

Two parallel research outputs are targeted:
- **Data**: an "AV2-360" derivative — first public 360° driving dataset stitched with parallax-aware methods
- **Method**: a Pi3/DVGT-based stitching technique (open-source counterpart to LiftProj 2025-12)

---

## §2 — Success Criteria

| Tier | Criterion | Verification |
|---|---|---|
| **MVP (Week 1)** | L1 baseline produces 1 ERP video clip (≥3 sec) from 1 AV2 sequence, visually recognizable | Output file `outputs/mvp_baseline.mp4` plays without crashes; horizon mostly continuous |
| **Phase 2 (Week 3)** | L3 pipeline produces ERP clip with measurably better parallax than L1 | Cycle-consistency PSNR(L3) > PSNR(L1) on held-out reference views |
| **Phase 3 (Week 5)** | OmniStitch baseline + diffusion polish integrated | 3-way comparison table (L1 / L2 / L3) on ≥5 sequences |
| **Stretch (Week 8+)** | nuScenes-360 cross-validation run; Waymo blind-spot completion via Argus | Cross-dataset numbers + Waymo demo clip |

---

## §3 — Scope

### In scope (Phase 1–3)
- AV2 sensor data ingestion (7 ring cameras)
- Per-frame ERP rasterization (L1, L3)
- Pi3 / DVGT inference integration
- Confidence-weighted 3D fusion
- Multi-band blending
- Temporal smoothing (cross-frame ego pose)
- Cycle-consistency evaluation
- Mini dataset (~5–10 sequences) curation

### Out of scope (deferred)
- Full L4 (per-scene NeRF/3DGS training)
- Custom diffusion model training (use frozen Argus / Percep360 if needed)
- Real-time inference optimization
- Stereo depth fusion (AV2 has 2 stereo cameras, treat as future signal)
- Mass-scale dataset release (>100 sequences)
- Waymo full pipeline (only blind-spot completion demo)

---

## §4 — Phases

### Phase 0 — Repo bootstrap ✅ DONE
- [x] Scaffold directory tree
- [x] Init git + push to `github.com/QiPan-Ronnie/Waymo2Panorama`
- [x] Brainstorm survey written and committed

### Phase 1 — L1 baseline + AV2 ingestion (Week 1)

**Goal**: Reproducible classical baseline that proves the pipeline plumbing works end-to-end.

Tasks:
- [ ] **P1.1** Install `av2` library; verify `import av2` in local Python env
- [ ] **P1.2** Identify one AV2 sensor log to download (~3–5 GB). Document log_id and city
- [ ] **P1.3** Write `scripts/download_av2_log.py` (wraps `s5cmd` or `boto3`) — downloads to `data/argoverse2/<log_id>/`
- [ ] **P1.4** Write `code/av2_loader.py` — given a log dir + timestamp, returns dict of `{cam_name: (image, intrinsic K, extrinsic SE3)}`
- [ ] **P1.5** Write `code/sphere_project.py` — for each cam, for each pixel, compute ray in ego frame, intersect with unit sphere, return (θ, φ)
- [ ] **P1.6** Write `code/erp_rasterize.py` — given multiple `(image, mask, (θ, φ))` lists, accumulate into one ERP canvas with per-pixel weight buffer
- [ ] **P1.7** Write `code/multi_band_blend.py` — Laplacian-pyramid blending across the 7 view contributions
- [ ] **P1.8** Hand-paint ego-vehicle mask for each of the 7 cameras (PNG masks, one-time)
- [ ] **P1.9** Pipeline driver `scripts/stitch_l1.py` — single-timestamp ERP output
- [ ] **P1.10** Loop driver — generate ERP video from a temporal slice (3 sec, ~60 frames at 20 Hz)
- [ ] **P1.11** Visual diagnosis: `notes/baseline_diagnosis.md` listing observed failure modes (near-object ghost, hood, exposure, seams, sky)
- [ ] **P1.12** Pull one nuScenes-360 reference frame for visual comparison
- [ ] **P1.13** Commit + push everything; tag `v0.1-l1-mvp`

**Deliverable**: `outputs/mvp_baseline.mp4` + `notes/baseline_diagnosis.md` + commit hash

### Phase 2 — L3 main line (Weeks 2–3)

**Goal**: Foundation-model-based 3D-aware stitching that visibly fixes parallax issues from Phase 1.

Decision gate at start of phase: **Pi3 vs DVGT** (see Decision Register §5).

Tasks:
- [ ] **P2.1** Decide backbone (Pi3 vs DVGT) — write `notes/backbone_decision.md`
- [ ] **P2.2** If Pi3: reuse `01-pi3/scripts/run_pi3x_export.py`, adapt for AV2 input dimensions (resize to 504×504, batch 7 cams)
- [ ] **P2.3** If DVGT: clone `github.com/wzzheng/DVGT`, run on AV2 frames, verify metric-scaled outputs
- [ ] **P2.4** Write `code/sim3_align.py` (Pi3 path) — solve Sim(3) per camera using known ego extrinsics + LiDAR median depth
- [ ] **P2.5** Write `code/lift_and_project.py` — `(points, conf, color) → ERP` via confidence-weighted splatting
- [ ] **P2.6** Compare L1 vs L3 visually on same frame; commit comparison gallery
- [ ] **P2.7** Implement cycle-consistency metric: hold out 1 camera, render its viewpoint from the other 6, compute PSNR/LPIPS vs ground truth
- [ ] **P2.8** Add temporal smoothing: ego-pose-aligned blend across 3 adjacent frames
- [ ] **P2.9** Write `outputs/l3_evaluation_report.md`
- [ ] **P2.10** Commit + push; tag `v0.2-l3-mvp`

**Deliverable**: `outputs/l3_stitch_demo.mp4` + cycle-consistency table + tag

### Phase 3 — Baselines + diffusion polish + multi-sequence (Weeks 4–5)

**Goal**: Apples-to-apples comparison against deep baselines; produce a small curated dataset.

Tasks:
- [ ] **P3.1** Clone & set up OmniStitch (`github.com/tngh5004/Omnistitch`); adapt for AV2 input
- [ ] **P3.2** Run OmniStitch on same Phase 2 sequences; record outputs
- [ ] **P3.3** Try to implement LiftProj-style hole-completion (MAE-based) — if code unavailable, write minimal reproduction
- [ ] **P3.4** Integrate Argus (`05-argus-video-to-360/`) as optional polishing pass — apply to ERP boundary regions
- [ ] **P3.5** Run on 5 AV2 sequences total; produce gallery `outputs/sequence_gallery/`
- [ ] **P3.6** Comparison table: L1 / L2 (OmniStitch) / L3 / L3 + diffusion polish
- [ ] **P3.7** Cross-dataset: run L3 on 1 nuScenes-360 reference scenario, compare to their stitching
- [ ] **P3.8** Write `outputs/phase3_report.md`
- [ ] **P3.9** Commit + push; tag `v0.3-phase3`

**Deliverable**: comparison table + 5-sequence gallery + cross-dataset proof point

### Phase 4 — Pantheon360 integration + Waymo blind-spot extension (Weeks 6–8)

**Goal**: Close the loop with the upstream consumer; tackle Waymo's harder case.

Tasks:
- [ ] **P4.1** Adapt `04-pantheon360/scripts/pi3_to_cache.py` to accept our L3 outputs
- [ ] **P4.2** Run Pantheon360 ERP renderer on our 3D cache; verify roundtrip
- [ ] **P4.3** Download 1 Waymo Perception segment; produce L1 + L3 stitch (will leave 130° rear gap)
- [ ] **P4.4** Run Argus on the gap as inpainting → demo Waymo→360 with generation
- [ ] **P4.5** Write `outputs/phase4_pantheon_integration.md`
- [ ] **P4.6** Decide on paper angle (data / method / combined) — discuss with Koi
- [ ] **P4.7** Commit + push; tag `v0.4-integration`

**Deliverable**: end-to-end pipeline from AV2 → ERP → Pantheon360 + Waymo gap-fill demo

---

## §5 — Decision Register (to lock during/after `/ultraplan`)

| ID | Decision | Default | Reason to reconsider |
|---|---|---|---|
| D1 | L3 backbone: Pi3 vs DVGT | **DVGT primary, Pi3 fallback** | If DVGT code immature or outputs poor on AV2 |
| D2 | Sphere vs cylinder for L1 surface | **Sphere** (→ ERP) | Cylinder simpler; cleaner for driving (no poles) |
| D3 | Output ERP resolution | **512×1024** for dev, **1024×2048** for final | Pantheon360 uses 512×1024 in their renderer prototype |
| D4 | Number of AV2 sequences in dataset stub | **5 for Phase 3, scale to 10** later | Time/storage |
| D5 | Compute platform | **Colab A100** (matches Pi3 workflow) | AutoDL as fallback for longer runs |
| D6 | Self-vehicle handling | **Hard mask out** Phase 1; inpaint later if needed | Inpainting adds dependency |
| D7 | Temporal consistency strategy | **Per-frame Phase 1; ego-pose-smoothed Phase 2** | Video diffusion later if needed |
| D8 | Paper angle: data, method, benchmark? | **Combine S1 + S2 narrative** | Decompose if too broad after Phase 3 |
| D9 | License | **Code MIT, data respect upstream NC** | None |
| D10 | Cycle-consistency reference cam | **Rotate (each cam held-out once)** | Statistical robustness |

---

## §6 — Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| DVGT code immature / undocumented | Medium | Phase 2 slip | Fall back to Pi3 (we own this) |
| AV2 download too slow on local | Low | Phase 1 day-2 slip | Use Colab disk + s5cmd parallel; pre-stage |
| Pi3/DVGT outputs at 504×504 too low-res vs 2048×1550 AV2 | Medium | Quality cap | Upsample with classical method; tile inference if time allows |
| Per-camera exposure mismatch creates visible seams | High | Quality cap | Implement histogram matching pre-blend; OR multi-band blend already mitigates |
| LiftProj has no code → reproduction risk | High | Phase 3 partial | Their core idea is simple; minimal MAE substitute OK |
| Ego pose drift in temporal smoothing | Medium | Video flicker | Use AV2-provided ego pose (their SLAM is solid) |
| Compute quota exhausted | Medium | Phase 2/3 slip | Cache aggressively; use Drive workspace per Pi3 pattern |
| nuScenes-360 has different ERP convention than ours | Medium | Cross-dataset eval invalid | Verify yaw=0 convention early |
| 12-hour Colab limit | Low (we know this) | Long runs interrupted | Use checkpointing; same as Pi3 workflow |
| Stereo cams confuse the loader | Low | Hour-of-debugging | Explicitly filter to ring-only initially |

---

## §7 — Asset Reuse

| Existing asset | Where | How reused here |
|---|---|---|
| Pi3X inference on Colab A100 | `01-pi3/scripts/run_pi3x_export.py` | Direct import for backbone path |
| Pi3 → 3D cache adapter | `04-pantheon360/scripts/pi3_to_cache.py` | Adapt for AV2 → Pantheon360 path |
| ViPE pose/intrinsic | `02-vipe/` | Sanity-check AV2-provided pose |
| Argus repo + paper | `05-argus-video-to-360/` | Optional final polishing/completion |
| Pantheon360 ERP renderer | `04-pantheon360/scripts/render_geometry_placeholder.py` | Consumer pattern |
| Drive MCP + Colab MCP workflow | Already operational | Same workflow for outputs/logs |
| Pi3 mini ERP→crops setup | `04-pantheon360/scripts/make_perspective_crops.py` | Inverse pattern we now implement |

---

## §8 — Evaluation Plan

### Quantitative
1. **Cycle-consistency PSNR/LPIPS** — held-out camera, render from other 6, compare. Repeat for each of the 7 cams.
2. **Line consistency** (Argus paper's metric) — straight lines (lane markings, building edges) should remain straight in ERP.
3. **Temporal stability** — frame-to-frame pixel variance in static regions (sky, distant buildings).
4. **Edge sharpness in overlap regions** — should not blur (sign of bad blending).
5. **Coverage ratio** — fraction of ERP pixels with ≥1 source observation.

### Qualitative
1. Side-by-side gallery: input 7 cams → L1 → L2 → L3 → L3+polish
2. Manual checklist: near-object ghosts, hood visibility, exposure seams, horizon discontinuity, polar artifacts
3. Cross-dataset: visual comparison to nuScenes-360 same-style scene

### Downstream
- Feed L3 outputs into Pantheon360 renderer; verify scene viewable from any user-defined camera
- Train tiny Argus on our outputs as sanity check (optional, only if time)

---

## §9 — Compute & Data Budget

| Resource | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| Colab A100 hours | 2 | 10 | 15 | 8 |
| Drive storage | 5 GB | 20 GB | 50 GB | 60 GB |
| Local disk (D:) | 1 GB | 3 GB | 5 GB | 6 GB |
| AV2 sequences | 1 | 1 | 5 | 5 |
| nuScenes refs | 0 | 0 | 1 | 1 |
| Waymo segments | 0 | 0 | 0 | 1 |

---

## §10 — Open questions for `/ultraplan` to sharpen

These are the questions where I have a default answer but want a second opinion:

1. **Should we treat the project as primarily a data paper or a method paper?** (My default: combined, but venues differ — NeurIPS D&B for data, CVPR/ICCV for method)
2. **Is DVGT actually a better backbone than Pi3 for our use case?** Need to read DVGT paper carefully; it's only 4 days old.
3. **Should we run Pi3 individually per camera, or jointly on all 7 cams at once?** Joint exploits cross-view geometry; individual is closer to Pi3's training regime.
4. **Should we evaluate against existing nuScenes-360 stitchings, or only against held-out cameras?** Both is best but takes time.
5. **Does it make sense to release a notebook tutorial alongside, like Pi3 has?** Probably yes for pedagogy.
6. **Should we coordinate with Koi's ViPE work? His PR #16 on multi-view SLAM init is relevant.** Likely yes — integrate ViPE pose as an alternative source.
7. **Is there a published or near-published competitor on the EXACT same task (AV multi-cam → 360)?** Need to do one more focused search.
8. **How do we handle motion blur / rolling shutter between cameras at high vehicle speed?** AV2 cameras are global-shutter — not an issue. Waymo unclear.

---

## §11 — Timeline summary

```
W1  Phase 1 (L1 baseline + AV2 ingestion)        [now]
W2  Phase 2 part 1 (Pi3/DVGT inference)
W3  Phase 2 part 2 (3D fusion + cycle eval)
W4  Phase 3 part 1 (OmniStitch baseline)
W5  Phase 3 part 2 (multi-seq + cross-dataset)
W6  Phase 4 part 1 (Pantheon360 integration)
W7  Phase 4 part 2 (Waymo extension)
W8  Buffer / paper draft start / Koi review
```

---

## §12 — Workflow convention

- Each phase ends with a git tag (`v0.X-<short-name>`)
- Each phase produces `outputs/<phase>_report.md`
- `progress.md` (to be created) tracks daily state
- Notes go into `notes/`
- Code mirrors existing `koi chen/` style — Python 3.10/3.12 compat, conda env name suggestion: `waymo2pano-py310`
- All big outputs (>50 MB) live on Drive, not in repo
