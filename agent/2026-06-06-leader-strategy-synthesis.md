# Waymo2Panorama — Leader Strategy Synthesis & DB76a Handoff (2026-06-06)

> **谁该读这份文档**：接手执行的新 agent（先读这份，再读 `handoff.md` / `progress.md` / `decision_briefs.md` / `README.md` / `plans/2026-06-04-egsr-seam-and-route-roadmap.md`）。这份是 leader 在 2026-06-06 一系列深度讨论（含两轮多 agent 文献勘探 + GPT Pro 多轮对话 + 对抗式审计）后的**方向锁定 + DB76a 执行交接**。它压缩了那几轮的核心推理，让你不必重跑就能接上。
>
> **一句话**：项目主线已从「把 BMW 缝修成无缝单中心全景」（物理上不可能）**reframe 成「source-faithful 多中心 mosaic + provenance/risk/abstain 数据契约 + 双格式交付」**。下一步是 **DB76a**：一个**只测量、不修 RGB** 的实验电池，先量清「现有 GREEN 到底有多可信」和「前向立体 / 环时序还能真修多少」，再决定我们是修复方法还是测量方法。**Brief-before-experiment、一次只一个 active brief、视觉规则、secret 安全规则全部继续生效。**

---

## 0. THE LOCK (what is decided vs what DB76a decides)

**LOCKED (settled across round-1 survey + round-2 survey + round-2 adversarial critic + GPT Pro):**
1. **Main deliverable = source-faithful data contract**: a multi-center mosaic + per-pixel `{source_id, operator_id, risk, loss_weight, unknown_or_abstain, generated_mask, source_mixed_mask, visibility_count, residual_px}`. Single-center seamless repair is allowed ONLY on evidence-GREEN segments. The seam is a **labeled provenance boundary**, not always a defect to repair. **Abstain is a valid, non-failure output.**
2. **Dual-format delivery**: `raw rig (images + calib + ego-pose + sidecars)` is **canonical**; `ERP panorama` is a **first-class derived view**. ERP is NOT obsolete — it has real downstream consumers (verified: PanoWorld, PathDreamer, World-in-World benchmark all condition on ERP). Do NOT assume Bosch wants only ERP, nor that it wants no ERP.
3. **Theory spine = two self-owned lemmas** (NOT a misread plenoptic citation — see §5): Lemma A (occlusion/target-ray non-identifiability, constructive counterexample) + Lemma B (textureless/1D-texture rank-deficiency / aperture). Plenoptic sampling (Chai/Lin-Shum) is **background only**, supports the *determinable-side* bound.
4. **IBR is framing language, not an escape hatch**: output **two images** — `erp_presentation_rgb` (may be source-mixed, pretty) and `erp_training_rgb + masks` (single-source/provenance/risk/abstain only). **Never mix.** DB75 stays permanently `presentation_only/source_mixed_not_repair`.
5. **Next experiment = DB76a** (measurement only, no RGB repair, no DB75 tuning). See §8.

**NOT LOCKED — DB76a decides these with numbers:**
- **How much can we actually repair?** Front-stereo + ring-temporal-side coverage deltas (the two repair levers dismissed too fast in round-2 — see §7).
- **How clean is current GREEN?** The leave-one-camera-out render-back false-GREEN rate (the blind spot of the whole project — see §8 battery 1).
- **How big is abstain?** Abstain area-fraction on the task-valid band, downstream-weighted (decides whether the contract is the main course or a footnote).

---

## 1. The arc — how we got here (so you don't re-derive it)

The project spent ~75 decision briefs (DB-01 … DB-75) trying to repair the multi-camera near-field seam, the hardest validation case being BMW log `02a00399`, anchor `0`. Almost all were NEG. Recent terminal chain:
- **DB64 (LTR-v0 layered target-raycaster)**: BMW seam bottleneck = `no_target_surface_support=0.5394`, longest LiDAR-supported continuous component `0.146`. Renderer gating refused.
- **DB67 (dense VGGT evidence)**: failed all gates (BMW no-surface `0.5726→0.7680`, visible-any `0.1753→0.0020`, LiDAR-agreed dense `0.0129`), degraded clean control.
- **DB72 (full-ERP source-label optimizer)**: proved same-frame source selection is structurally insufficient — BMW raw source coverage `0.2742`, two-source overlap `88,288 px`, **three+ source overlap = 0**, LiDAR visible fraction `0.0532`. Source selection can move ownership, cannot create target-surface alignment.
- **DB73/DB74**: ground-plane and temporal candidates generated but **selected fraction = 0.0** (DB74 `temporal_selected_fraction=0.0`, `temporal_any_valid_fraction=0.001147`).
- **DB75 (full-ERP source-mixed blend)**: current best *viewable* version (`soft_r64_a080_g1`, BMW ROI seam energy `95.94→56.20`, source_mix `0.1171`). User vision override: **only softens, does not connect** — left road still misaligned, right curb still stepped, BMW/SUV still ghosts. Classified `presentation_only/source_mixed_not_repair`. NOT source-faithful, NOT Bosch-training-ready.

Then (2026-06-06) we stopped patch-on-patch and re-examined the **root cause** and the **goal** from scratch.

---

## 2. Root cause — the physics (why every method failed the same way)

The 7 ring cameras have **different optical centers** (~0.21–0.26 m baseline); the panorama pretends a single virtual center. The required quantity for a correct pixel is "what surface does the target ray hit, at what depth, which cameras see it un-occluded" — which **was never captured** where the target surface is seen by ≤1 camera (textureless near-ground curb / wall-base / lower-FOV) or has no LiDAR return.

**Angular/baseline bound** (load-bearing, use this — it is OURS, measured): for a no-depth copy from camera to the virtual ray,
`d_px(Z) ≈ (W / 2π) · arctan(b / Z)`, with `W=2048`, `b≈0.25 m`.
- Our measured `16–21 px` copy bias ⟺ target surface `Z ≈ 3.25–5.29 m` (normal mid-range parallax, not a bug).
- To get `<1 px` no-depth bias you need `Z ≥ 68–85 m`. So everything at 3–8 m near-ground is fundamentally ambiguous without target-surface evidence.

**Why each method failed = the same root, different face:**
- **L1 blend** averages two cameras assuming they see the same 3D point → at near-field they see *different surfaces* → ghost/double-image. It treats a **visibility conflict as a photometric conflict**.
- **hard_select** refuses to average (no ghost) but cuts at a boundary where the two cameras saw the structure from different positions → **structure jumps**. It is a **multi-center mosaic**, and the seam is its honest signature.
- **A1/G post-ERP repair** operates on the *rendered* ERP, where source_id / ray / depth / visibility are already discarded → can only do cosmetic (blend/swap/warp/inpaint). It repairs the *photograph of the problem*.
- **VGGT/dense depth** gives depth *from a source camera*, with *confidence ≠ visibility*, not metric-aligned to the rig (DB45k reflection/rig-shape), and **cannot manufacture evidence the sensors never captured** → confident guess = hallucination (DB67).

**Key correction (the user asked):** AV2 *does* have LiDAR — we never "lacked LiDAR". The problem is **LiDAR is sparse and misses exactly the failing surfaces**: seam-band coverage ~`0.49–0.50` overall but DB25 ROI `9.4%`, DB41 right-line `8.4%`, **DB41 lower-right `0.000`**; and "LiDAR support 0.43" vs "LiDAR visible 0.053" is the gap between "a point is nearby" and "a usable, visible, continuous target surface at the seam pixel."

---

## 3. The reframe (the strategic pivot)

Stop chasing a physically impossible target ("seamless single-center near-field"). The deliverable becomes a **provenance-labeled, evidence-budgeted, multi-center panorama data product** (per the EGSR data-contract framing already in the roadmap §0.2). For a world model, an honest "unknown here" is safer than a fabricated curb — a **mislabeled real-looking pixel carrying a GREEN trust badge is more dangerous than an obviously-generated one.**

This reframe also fixes a *motivational/structural* problem: the old goal ("make it look good enough") was **unfinishable** (no definition of done → permanent churn). The new goal (a measured, provenance-complete data contract + a provable identifiability bound) **is finishable** and is exactly what a downstream consumer can verify and use.

**Honest scope note (do not lose this):** the meaning of the deliverable is **downstream-dependent and partially unverified** — confirm directly with Xinhan/Bosch (see §10) whether they want (a) trustworthy provenance data vs a pretty demo, and (b) ERP vs raw multi-camera streams. Two real consumers exist for ERP; do not infer Bosch's format from the literature.

---

## 4. Literature reviewed (two multi-agent surveys, web-verified, anti-hallucination)

Two background Workflow runs surveyed 2024–2026 top venues + classics. Full outputs persisted (see §12). **Mark verified vs unverified — one agent confidently MISUSED a paper; always re-check load-bearing citations.**

### 4.1 Borrowable into the SOURCE-FAITHFUL pipeline
| Method | Venue | Role / what we borrow | Caveat |
|---|---|---|---|
| **MASt3R / MASt3R-SfM** | ECCV'24 / 3DV'25 | dense correspondence + confidence → GREEN/abstain **gate** | matching-conf ≠ parallax-resolvability; gates co-visibility, not de-doubleability |
| **CVIU-2024 dense-matching seam** | CVIU'24 | seam cost = flow-smoothness + **duplication penalty** ("no object twice") | drop the warp step; use as placement |
| **Seamless Street View (Google)** | 2017 | confidence-filtered flow + **global regularized spline** as **seam-PLACEMENT prior** | NEVER use to average/warp training truth — it is "smarter warp-and-blend," not a faithfulness proof |
| **Seg-multi-homography** | Signal Proc.'25 | **winner-take-all by photometric error** (anti-ghost) + **disocclusion = explicit hole = abstain** | reject its non-overlap extrapolation |
| **Unstructured Lumigraph Rendering (ULR)** | SIGGRAPH'01 | the **operator framing**: k-nearest blending field, weight→0 at coverage/FOV border, **k<2 → single-source-or-abstain** | with our two-source overlap tiny / 3+ = 0, ULR has **no k≥2 regime** at the hard seam → it degenerates to existing hard_select. **Citeability, not new pixels.** |
| **MegaParallax / OmniPhotos** | TVCG'19 / TOG'20 | **prior art for our deliverable class** (deliberately multi-center pano, render each ray from nearest 1–2 real cams, never global-average) | — |
| **GradientShop / Screened-Poisson** | TOG'10 / ECCV'08 | feed `{confidence, loss_weight, abstain}` as data/edge weights → **abstain becomes a HARD in-solver constraint** (membrane provably cannot bleed across an abstain boundary) | the source-faithful compositor to replace hand-rolled blending |
| **Poisson / MVC cloning** | SIGGRAPH'03 / '09 | radiometric seam: flatten DC offset only, preserve gradients, invent nothing | MVC is O(boundary), cheap for the DB76b sweep |
| **Interactive Digital Photomontage** | SIGGRAPH'04 | published precedent for our exact output: **graph-cut per-pixel source-LABEL field + gradient cleanup** | add an explicit **abstain label** (their MRF assumes ≥1 source correct everywhere) |
| **circular padding** (from DiT360) | CVPR'26 | content-agnostic 0°/360° wrap topology | pure bookkeeping, zero hallucination |
| **PF3plat `S_geo`** | ICML'25 | render-free per-pixel geometry-aware confidence as a **cross-check** for the `risk` sidecar | it is a learned-3DGS confidence; must be validated to AGREE with analytic `d_px(Z)` before entering a source-faithful contract |
| **RoGS + SplatAD + static/dynamic split** | IROS'25 / CVPR'25 / CVPR'24 | multi-frame LiDAR static-surface densifier: RoGS LiDAR surfel (road, +29% elev), **SplatAD LiDAR line-of-sight gate + per-return rolling-shutter/ego correction**, static/dynamic split (boxes) | **widens GREEN (road interior), does NOT break the textureless/no-return wall**; AV2 generality unverified; do NOT borrow their render-everywhere field |

### 4.2 Theory (the determinable-side bound — background, not the abstain proof)
- **Chai et al. Plenoptic Sampling (SIGGRAPH'00)** + **Lin & Shum, Geometric Analysis of Light Field Rendering (IJCV'04)**: minimum-sampling / disparity-space twin. Makes our `16–21 px` a measured Nyquist instance; frames failure as **observability of a fixed 2-center rig**, not blanket impossibility.
- **Zhang & Chen (surface plenoptic, '01/'03)**: occlusion ≈ doubles required sampling rate (a defensible ×2 in the bound table).
- ⚠️ **DO NOT cite Do/Marchand-Maillet/Vetterli (TIP'12) as the "abstain license."** The round-2 synthesis agent claimed it "proves band-unlimited at occlusions"; the adversarial critic verified the paper **assumes no self-occlusion and derives FINITE bandwidth**. This is exactly the fake-rigor a reviewer punctures. Abstain rests on Lemma A+B (§5), not on this paper.

### 4.3 Presentation-only / generative (separate labeled branch, `generated_mask`, NEVER in training base)
CubeDiff, MVDiffusion, DreamCube, SphereDiff, DiT360/CubeComposer generative cores, PIS3R, LiftProj, PanSplat, DrivingForward (our own NEG learned 3DGS baseline). Their "seam-consistency" tricks (synced-GroupNorm, cube-padding geometric-neighbor fill) are built for **zero-parallax single-center** topology — **do NOT borrow them** for a multi-center mosaic (synced-GroupNorm can shift real content tone; cube-padding re-imports the L1 ghost). **The only generative WIN is sky-only outpaint**, gated, labeled generated.

### 4.4 Consumer-format reality (verified)
2026 driving/world models split: some ingest **per-camera frames + Plücker ray-maps + BEV** (OmniNWM, X-World, Panacea, Cosmos), others **condition on ERP panoramas** (PanoWorld, PathDreamer, World-in-World @576×1024). → **both formats live**; ship dual-format; confirm Bosch directly. World-in-World also shows **high visual quality ≠ downstream task success** (a strong argument that DB75's pretty image is not a deliverable).

---

## 5. Theory spine — two lemmas (use these, not misread plenoptic)

**Lemma A — occlusion / target-ray non-identifiability.** There exist two 3D scenes `S1, S2` identical in both adjacent input cameras `(I_i, I_j)` but different on the virtual-center target ray `r(x)` (different surface or occlusion order). Any source-faithful operator `A(I_i, I_j)` outputs the same for both → must err on one. ⇒ **without admissible target-surface/visibility evidence, abstain is the only honest state, not a failure.**

**Lemma B — textureless / 1D-texture rank deficiency (aperture).** For a constant patch the photometric residual's Jacobian w.r.t. disparity/warp ≈ 0 (depth unestimable); for a single straight edge the Jacobian rank ≈ 1 (only normal displacement constrained). ⇒ **textureless wall / dark road cannot self-certify GREEN via flow or stereo**; needs LiDAR / stereo consistency / temporal multi-view, else abstain.

**Theorem (reportable):** *Evidence-limited source-faithful panorama construction.* Given only two adjacent ring-camera observations and no admissible target-surface/visibility support, there exist indistinguishable scenes with different target-ray radiance; therefore any non-abstaining single RGB output cannot be guaranteed source-faithful. The system returns provenance-backed source-owned pixels where identifiable, calibrated risk where uncertain, and abstain otherwise.

This is stronger and safer than "a plenoptic bound says it's impossible."

---

## 6. Round-2 new-idea exploration — result: 0 wall-breakers (confirms the reframe)

8 ideas (6 seeded + 2 open-divergent) were developed then **adversarially self-killed**. Verdicts: 6 marginal, 1 reject, **0 promising**. None adds a correctly-repaired pixel where coverage is ≤1 camera or LiDAR-absent — they **relabel** the wall. Honest one-liners:
- **Layered output (MPI/MSI/LDI)**: marginal → only a DB78 contract tweak (optional LiDAR-determined ordered-layer field). Do NOT build a learned MPI predictor (= PanoGRF monocular-prior hole-filler = hallucination). 2026 consumers don't ingest MSI; raw frames + provenance are *more* faithful than depth-binned MSI.
- **Multi-frame-native panorama**: marginal — **the one source-faithful lever that adds real cross-time evidence**, but widens GREEN on the band, does not break the textureless/no-return floor. Folds into DB76a as a coverage test (≠ DB74, which kept single-frame base + offered temporal labels to an optimizer).
- **Calibrated uncertainty field**: marginal → promote to a required output channel (zero repaired pixels, but certifies provenance where verifiable). Kill the word "guaranteed" off wall pixels.
- **ULR/Soft3D IBR**: marginal → framing + one diagnostic figure (peaked weights = existing PICK; spread weights = ghost we already killed).
- **Map-prior ground mesh / Evidence-stack ERP / downstream-ablation**: marginal/reject — relabel or are eval-only.

**Net:** no magic; reframe holds; the only repairing lever worth real compute is multi-frame-native + the two under-tested levers in §7.

---

## 7. The TWO repair levers dismissed too fast (the round-2 critic's genuine catch)

The deliverable can be **more repairing than "mostly abstain"** — two real-evidence levers were under-tested:

1. **AV2 forward STEREO pair.** AV2 has **2 front stereo cameras** (`2048×1550`, `20 Hz`, undistorted, with intrinsics/extrinsics + 6-DOF pose) in addition to 7 ring cams. Calibrated stereo gives **metric near-field depth at 3–10 m with no `Z≥68 m` limit** — exactly the "no-LiDAR-return" forward near-ground the synthesis called hopeless. **Limit:** front only (does NOT help the side curb/wall-base — that's the user's right-ROI pain, which needs lever 2); passive stereo fails on textureless/wet/reflective; doesn't auto-solve rolling/exposure/ownership.
2. **Ring-temporal multi-azimuth on side near-ground.** DB74's `temporal_selected_fraction=0` killed the *implementation* (temporal labels into the existing optimizer), **not the direction**. As the ego passes, a right-curb patch is seen near head-on by the right cam at `t0` and from a different azimuth by the right-rear cam at `t+1s` → real multi-view parallax on the exact surface. Must be done **surface-centric** (build static surface candidate, ask "≥2 raw views see the same surface in the window?"), NOT by re-running the DB74 optimizer.

Corrected conclusion: **"abstain is the ceiling on the textureless / single-pass-occluded subset; the forward-stereo and ring-temporal-side subsets are likely YELLOW→GREEN and were under-tested."**

---

## 8. DB76a — the locked next experiment (measurement only, NO RGB repair)

**Name:** `DB76a: Calibrated GREEN reliability + stereo/temporal coverage audit`
**Scope:** No RGB repair. No DB75 tuning. No blend/warp output as a final pano. Only evidence maps, render-back residuals, and go/no-go tables. Fixed cases: BMW `02a00399:0` + clean control `0bae3b5e:30` (optional if data ready: dense-parking `2c65`, one Waymo segment).
**Sequencing (leader directive — respect bounded scope):** run the **two render-free / CPU batteries FIRST** (they decide the contract frame + whether current GREEN is even trustworthy); run the **two GPU batteries (stereo/temporal) only if/where warranted.**

**Battery 1 — GREEN reliability via leave-one-camera-out render-back (render-free, do FIRST, the keystone).**
For each held-out camera `c_h`: build the representation **forbidding its RGB**, reproject GREEN regions back into `c_h`'s image plane, compare to real `I_{c_h}` ONLY on pixels that are (i) visible/non-border in `c_h`, (ii) provenance-proven to not use `c_h`, (iii) operator ∈ GREEN (`raw_copy`/`reprojected_real`/`warped_real`/`tone_only`), (iv) z-buffer says `c_h` should see them; bucket dynamic/reflective/saturated separately. Error = `max(photometric_ΔE_or_ZNCC, gradient, edge/structure_chamfer, geometry_reproj_px)` — not a single mean. Outputs: `green_false_rate@τ`, `p50/p90/p95/p99 residual_px`, `operator_confusion_table`, `risk_calibration_curve`, `failure_by_{scene,operator,structure}`.
*Note:* LOO validates **co-observed GREEN** (most GREEN); the truly-marginal pixels are the ones we abstain on anyway. State this limit in the paper.

**Battery 2 — abstain mass report (render-free).** Three numbers, NOT on the full black ERP: `abstain_full_erp`, `abstain_task_valid_band` (ring pano occupies only the middle band of `1024×2048`; black top/bottom ≠ failure), `abstain_weighted = Σ abstain(x)·w_task(x)` with higher `w_task` on lane/curb/vehicle/pedestrian/near-ground/free-space boundary.

**Battery 3 — AV2 forward-stereo coverage (GPU, only if batteries 1–2 warrant).** Rectify → SGM/RAFT-Stereo/IGEV disparity + confidence + L-R consistency (NO pano render) → project stereo depth to ERP target rays → z-buffer raw-visibility check → **LOO residual validation**. Outputs: `stereo_{surface_valid,raw_visible,green_candidate,residual,failure_reason}_map`.

**Battery 4 — ring-temporal-side coverage (GPU, surface-centric, NOT the DB74 optimizer).** anchor `±1–2 s`, all ring cams, ego poses; build static surface candidate (LiDAR / stereo / sparse MVS / stable tracks); for each abstain ray ask "≥2 raw views see the same surface in the window?"; compute triangulation angle / baseline / photometric consistency / z-buffer; LOO render-back. Waymo: put rolling-shutter residual in a separate risk bucket (do NOT extrapolate AV2 success to Waymo).

**Pre-registered thresholds (set BEFORE the run; failure does NOT relax them):**
- *GREEN accepted*: overall high-error ≤ **3%**, critical-structure (lane/curb/object boundary) ≤ **1%**, p95 residual ≤ **3 px**.
- *Stereo/temporal build-worthy*: ≥ **25%** of the relevant abstain band becomes `surface_valid ∧ raw_visible ∧ LOO_residual<3px`, OR ≥ **8–10%** of task-valid ERP becomes validated GREEN; AND no protected-structure failure.
- *Contract-as-main-deliverable*: task-valid abstain > **10%**, OR driving-critical abstain > **5%**, OR any continuous seam abstain > **25%** of seam length, OR GREEN high-error > **3%** without risk calibration.

**Kill criteria:**
- GREEN false rate > **5%** → STOP calling current GREEN "training truth" (downgrade to YELLOW/risk-weighted).
- Stereo covers < **10%** of relevant band → keep as forward side-evidence only, don't change main line.
- Temporal again yields < **5%** validated coverage or only sparse islands → close the temporal route.
- Risk map not LOO/held-out-calibrated → call it **heuristic risk**, not conformal/calibrated.

---

## 9. The data contract (DB78 target — schema to build toward)

`operator_id ∈ {raw_copy, reprojected_real, warped_real, tone_only, source_mixed, generated_inpaint, generated_outpaint, abstain}`.
`source_id`: `0..6` = current ring cams; `1000 + 10*offset + cam` = temporal raw; `-1` abstain; `-2` source_mixed; `-3` generated.
`risk`: continuous, **conformal-calibrated where verifiable**, with a **mandatory per-pixel `calibrated_vs_uncalibrated` flag** (wall pixels are uncalibrated — never claim "guaranteed" there).
Downstream usage the contract must enable:
```
valid_sensor_mask = operator ∈ {raw_copy, reprojected_real, tone_only}
                    AND risk < τ AND unknown_or_abstain==0
                    AND generated_mask==0 AND source_mixed_mask==0
loss_weight = clamp(1 - risk, 0, 1)   # abstain/generated/mixed -> ~0 (stronger than image: must be ignorable, not silently learned)
```
Deliver `canonical/` (raw images, calib, ego_pose, all sidecars) + `derived_erp/` (rgb_erp + same sidecars in ERP coords + projection metadata) + a clean split `erp_presentation_rgb` (may be source-mixed) vs `erp_training_rgb` (single-source/provenance/abstain). DB75 = the worked counter-example: `generated_mask=0` yet source-mixed ⇒ `loss_weight≈0` for sensor truth. DB49/DB49b already flagged the missing pieces (`source_id_map`, `unknown_or_abstain_mask`, `risk_map`) — DB76a + DB78 close them honestly (never fabricate a `source_id_map`).

---

## 10. Open questions — ask Bosch/Xinhan directly (parallel to DB76a, de-risks the format assumption)
Ask SPECIFIC questions (not "what format?"):
1. Is the world-model input **single ERP, multi-camera tokens, or both**?
2. Can the training loss ingest `loss_weight_map / ignore_mask / risk_map`?
3. Must generated/source_mixed regions be **deleted**, or usable as **low-weight visual context**?
4. Is the training loss RGB reconstruction / occupancy / BEV / action-conditioned video / multi-task?
5. What do they fear most — **ghost, wrong geometry, exposure seam, or missing/abstain**?

Also (the user's motivation hinge): confirm whether the value is **trustworthy provenance data** vs a **pretty demo**, and whether they want a panorama at all. The honest one-liner to Bosch:
> "We provide a viewable panorama and a training-safe panorama bundle. The viewable one may use source-mixed presentation blending. The training-safe bundle is provenance-aware: each pixel is source-owned/reprojected with calibrated residual support, risk-weighted, or explicitly abstained. We are now quantifying the false-GREEN rate and the stereo/temporal recoverable fraction before claiming sensor-truth coverage."

---

## 11. Hard constraints for the incoming agent (do not violate)
- **Brief before experiment.** Every new idea/route → a `decision_briefs.md` brief with Question/Hypothesis/Why-now/Expected-evidence/**Kill criteria**/**Max scope**/Vision check/Output location. **One active brief at a time.** DB76a is the next (and only) one to open.
- **Don't re-walk NEG** (archived in `progress.md`): L1 blend, hard_select-as-solution, DP/superpixel/semantic seam routing, DiT360/generative seam-completion, VGGT-dense-as-repair (DB67), DrivingForward, single-frame LiDAR ground-plane, DB74-style temporal-into-optimizer, DB75 blend/alpha tuning, synced-GroupNorm in the faithful layer, cube-padding geometric-neighbor fill.
- **Vision rule:** every candidate → a review board; **personally look** at full ERP + same-ROI before/after + sidecar overlays + protected/object/lane/curb regions. If it looks better but sidecars don't support it → `presentation-only` or `rejected`. Eyes beat metrics on conflict.
- **Security:** runtime URL/token, Cloudflare tunnel, HF token, Bearer, endpoint JSON = SECRETS. Read only from process env or a non-repo secret file. NEVER write secret-like values into repo/manifest/board/log/prompt/shell output. Reject chat-pasted tokens. Each remote op = one bounded `/status` + `/exec` per brief; secret-scan must be 0. Tell the user before any A100.
- **Citation integrity (hard-won this round):** an agent confidently misused a paper (Do et al. TIP'12) to build the theory spine; the adversarial critic caught it. **Re-verify every load-bearing citation against the actual paper before it enters a brief, the contract, or the paper.** Treat 2026-dated preprints and unverified metrics (e.g. PTDCC CDCS) as zero-decision-weight until checked.
- **3-location rule:** GitHub (committed code + `progress.md` + evidence PNG/JPG) + Local (this Windows tree) + Drive (`MyDrive/koi_waymo2pano_colab/` large outputs). Operate under `1jingshuo1`.
- **Goal is GENERAL** (memory `waymo2pano-general-goal.md`): the method must hold across diverse AV2/Waymo scenes; BMW `02a00399` is only the hardest validation case, never the target. Input contract = AV-grade data WITH calibration+LiDAR+poses+multi-frame (Tier-1).

---

## 12. Pointers & artifacts
- **Living docs:** `agent/{handoff,progress,decision_briefs,README}.md`; plan `agent/plans/2026-06-04-egsr-seam-and-route-roadmap.md`.
- **Current best viewable (presentation-only):** `deliverables/layered_target_raycaster/db75_full_erp_source_mixed_fallback/` (`DB75_full_review_board.jpg`, `DB75_same_roi_comparison_sheet.jpg`, `DB75_vision_verdict.json`, `DB75_manifest.json`).
- **Deliverable seam operator (to upgrade in DB77):** `scripts/phase3/_seamroute.py` (align + object-moat min-cut seam + virtual-centre select).
- **Round-1 literature survey (18 confirmed methods):** Workflow `wf_ea30cec6-179`; full output `C:\Users\14294\AppData\Local\Temp\claude\D--BaiduSyncdisk-2024-to-future-koi-chen\6feb01ac-36d2-42fe-bafe-53407e098d10\tasks\wlfjqf1lc.output` (transient temp — re-run the workflow if gone; script saved under the session `workflows/scripts/`).
- **Round-2 ideas+gaps survey + adversarial critic:** Workflow `wf_195015bb-801`; full output `...\tasks\w478khzu6.output`.
- **Memory:** `waymo2pano-general-goal.md` (north star), `waymo2pano-seam-direction.md`, `agent-colab-direct-framework.md`.

---

## 13. Leader's immediate next action (on user greenlight)
1. Write **DB76a** into `decision_briefs.md` (4 batteries, sequenced render-free→GPU, pre-registered thresholds, kill criteria, fixed cases) and sync `handoff.md`/`README`/`progress.md` pointers.
2. Implement + show the **two render-free batteries** (GREEN LOO render-back + abstain mass) for review BEFORE any remote/GPU; pre-register thresholds in the brief.
3. In parallel, send the 5 specific questions (§10) to Bosch/Xinhan to fix the format/consumer assumption.
4. Only after batteries 1–2 pass review: run batteries 3–4 (stereo/temporal) under one bounded remote op.

*Status as of 2026-06-06: direction LOCKED (§0); DB76a specified but NOT yet opened as a brief; no remote/GPU run since DB75; no active brief.*
