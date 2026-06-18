# Waymo2Panorama — First-Principles Thinking-Leader Handoff (2026-06-18)

> **To you (the incoming thinking AI):** You are being brought in deliberately, with fresh eyes, to act as a **first-principles thinking leader in the style of Elon Musk** — reason from the physics up, not from our conclusions. You must hold a **two-sided (正反方 / pro-vs-con) adversarial debate** with yourself before endorsing any direction. **Use every skill available to you** (brainstorming, autoresearch/deep-research, systematic-debugging, red-team self-audit, literature mining, etc.). Your job is to (1) understand the whole project as it stands today, (2) name the real unsolved problems, and (3) propose how we should proceed — delivered as a concrete `decision_briefs.md` brief. You may reply to the user in **Chinese** (keep code / file paths / metric names in English).
>
> **Two non-negotiable operating duties from the user:** (a) you must understand and use our **Colab + Google Drive execution framework** for any compute; (b) you must **keep `git`, `decision_briefs.md`, and `progress.md` continuously updated** — brief before experiment, log every result/failure/kill, commit.

---

## 0. YOUR MANDATE (hold this the whole time)

1. **Think from first principles.** Start from: *what is the operation "N perspective images → one 360° ERP panorama" at the lowest physical level?* Re-derive why it is hard before reading our answers.
2. **Two-sided debate is mandatory.** For every claim of ours and every idea of yours, argue the strongest case **for** and the strongest case **against** before concluding. Several of our "settled walls" turned out to be measurement artifacts (see §4) — assume more might be.
3. **Verify, don't trust. USE VISION.** This repo once had a session with *fabricated* tool output (phantom commits, invented numbers). Rule: route any remote/shell result to a file and Read-verify it; never trust echo/Glob/Edit-success text alone. For **every** image result, **open it and look with your vision** — our hardest-won lesson is "eyes beat metrics": a good score with a smeared curb in the picture is still a failure.
4. **The goal is GENERAL.** The method must work across diverse driving scenes (AV2 + Waymo), and **degrade gracefully** where evidence is missing. The hardest validation case (BMW log `02a00399`, the AV2 front-pod rig) is **only a stress test, never the target.** Every proposal is judged on generality + graceful degradation, not on one scene's polish.
5. **Deliverable = ONE concrete direction**, written as a decision brief, for the current state of the project (not a survey).

---

## 1. THE INVARIANT PROBLEM (the physics — start here)

**Input (Tier-1 data we have):** an AV capture. **Argoverse 2 (AV2):** 7 ring cameras (synchronized RGB ~20 Hz) + 2 forward stereo + LiDAR + 6-DoF ego-poses + multi-frame sequences + per-camera calibration & **per-camera capture timestamps** (asynchronous shutter). **Waymo:** 5 cameras + LiDAR + poses. So we have RGB **and** geometry **and** motion **and** time — not a single still.

**Output:** a 360° equirectangular (ERP) panorama (1024×2048), the scene from **one single virtual optical centre**, mapped to (azimuth, elevation).

**The fact everything orbits:** the cameras sit at **different physical locations**. A panorama pretends one optical centre. To place a pixel at the correct ERP angle you need that surface point's **depth/visibility**. No-depth copy bias `d_px(Z) ≈ (W/2π)·arctan(b/Z)` (W=2048): 16–21 px at Z≈3–5 m near-ground. ⇒ **near-ground is angularly ambiguous without depth/visibility evidence. This is "the seam."**

**Cubemap/generative-panorama methods do NOT transfer for free:** a cubemap's 6 faces share one centre (zero parallax). Our cameras have different centres → real near-field parallax. DiT360/CubeDiff assume the shared-centre premise we lack. (You may still borrow the cube-tiling *delivery* trick; it does not fix parallax.)

---

## 2. THE FULL ARC — WHAT WE DID (so you know the terrain, not so you continue it)

**(a) The original 8 stitching routes** (`README.md`): **L1** = sphere projection + 5-band multiband blend (long the strongest base); **L1 hard_select** (one source per region, no averaging) = the safe visual base; **L3** = monocular-depth forward-splat (failed, −3.15 dB, lost 10/10 anchors; 4 depth backbones Pi3/DepthPro/TemporalPi3/OmniStitch all failed alike → "algorithm-class problem, not backbone"); plus IPM-ground hybrid, cylindrical (新-A), graph-cut seam (新-B), IPM multi-region (新-C), wide-baseline stereo (新-D).

**(b) Seam exploration, briefs DB-01…DB-79:** post-ERP repair (A1/G blend/swap/warp/inpaint), source-label optimizers (DB72: same-frame 3-source overlap = 0), ground-plane & temporal candidates (selected fraction ≈ 0), source-mixed blend (DB75: softens, doesn't connect), dense learned geometry (DB67 VGGT: failed all gates), DiT360 generative (**sky-outpaint = WIN**; seam-completion **invents cars = NEG**), Difix learned refiner (safe but faint), Surround360 optical-flow view-interp on the overlap strip (DB-78: safe, no hallucination, visually MODEST). **Pattern: every safe in-band 2D edit comes out faint.**

**(c) Meeting with Xinhan → short-term goal + the downstream consumer** (see §3). A prompt was issued defining the near-term target ("make the stitched panorama look better"); other agents then explored the seam under that goal.

**(d) Infra — the Colab + Google Drive framework** (see §6): agent pushes a job → Colab GPU worker runs it → results to Drive → agent reads back. Live L4 GPU(s).

**(e) THE FABLE-5 BREAKTHROUGH (2026-06-09) — read the two attached docs.** A first-principles audit found the ERP virtual centre had been **pinned to the AV2 ego origin since L1** (1.8–2.2 m from the cameras, mostly height). Render-back error scales with the perpendicular baseline `b_perp`; moving the centre to the **ring-camera centroid (camera height ≈ 1.44 m)** shrinks `b_perp` ~7–100×. **DB-80 (5 scenes, CPU): depth-aware render-back p90 fell 18–96×** → the "seam wall" was ~an order of magnitude **self-inflicted**, never a free parameter in 79 briefs. **DB-81:** LiDAR-correspondence per-camera colour gains cut cross-camera colour steps 58–88%; the near-ground purple fringe = **source-ISP shadow chroma, not CA**. **DB-82:** no-LiDAR plane-only depth ≈ full LiDAR at panorama scale → the graceful-degradation claim has an on-disk A/B. **DB-84/85:** moving-object doubling = MOTION × **asynchronous shutter** (staggered exposure ±7.5/12.5/22.5 ms); per-camera exposure boxes + single-camera locking fixes most movers.

**(f) FABLE-5 THEN BUILT THE FULL PIPELINE (DB-86…DB-93, the "v8 complete-panorama stack"):** recentred depth-aware mosaic + photometric harmonization + **ground fill v8** (whole-log geometry-eligibility candidate search, displacement-bucketed time-nearest sources, **two-box ego self-occlusion** model, **resolution-matched nadir rendering** — low-passes the fill to the evidence's true optical resolution, invents nothing) + **FLUX.1-Fill sky outpaint** (DiT360 LoRA enforces ERP geometry; sky-only, object-gated). Deliverable: `deliverables/complete_pano_v8/`. **The endorsed core algorithm today = `scripts/phase3/db89_ghost_recovery.py` (full single-file stack) + sky_fill_flux** (marked in `agent/README.md`).

**(g) RECENT — the video task that exposed a new problem (DB-97 → DB-98, this is where we are now):**
- **DB-97:** render 4 scenes × **93 consecutive anchors** through the scene-band + STAGE-4 ground fill (NO sky) → assemble 4 mp4 videos. This is the first *temporal* stress test (everything before was single anchors).
- **DB-98:** the videos exposed ground-fill artifacts that single stills hid — **frosted speckle**, **jagged black streaky wedges** in the near-ground corners, and **softness/虚化** at the very bottom. A long first-principles + pro/con debugging campaign (full ledger in `decision_briefs.md` DB-98, *including every failed attempt*) concluded: the **near-pole-behind nadir** (ground directly under / just behind the car) is the rig's **PHYSICAL BLIND SPOT** — it is only ever seen at <4° grazing; steeper views are self-occluded by the ego body (proven: admitting steeper views backfired to 41% holes); even with geometrically-correct LiDAR ground height, grazing sources disagree at the ERP pole → streaks (proven: removing the agreement-gate brings the streaks back). Committed fix = **LiDAR ground-height reprojection + source-agreement (spread) gate**; the residual softness is the **honest evidence limit**, not makeup. **OPEN DECISION:** (b) accept the current spread-gate (clean but soft) vs (c) implement an **honest resolution-matched low-pass render** (real data shown at its true low resolution — soft-but-real, no blob, no streaks) — before re-rendering all 4 scenes as v2.

---

## 3. THE DOWNSTREAM CONSUMER (provisional — the CORE goal is invariant)

The *fundamental* consumer is **Bosch's world-model need**. *Currently* that looks like a **Cosmos-style 360° video diffusion pipeline** (Xinhan's `cosmos-transfer2.5-pcd`, repo `github.com/LouisonLu/cosmos-transfer2.5-pcd`). Cosmos inference takes **three separate inputs**: (1) a **point-cloud video** (geometry along a trajectory), (2) a **first frame** (appearance — *this is our stitched panorama*), (3) a **text prompt**. End goal: "one stitched panorama + a point-cloud path video → a realistic 360° video."

**Xinhan's LATEST status (attached: `meeting/6.9_meeting with xinhan/xinhan 做了什么.md` + `xinhan 视频.mp4`):** he is on the **training/generation** side. Right now he trains with **perfect 360s masked into the stitched shape** and **hard-locks the generated first frame to the GT first frame**, to test whether the model learns to **outpaint/repair** the stitched seams itself; next he'll relax the hard-lock / feed the broken (stitched) first frame. Original design: X = imperfect first frame (random-mask 20–30% info) then point cloud; Y = perfect 360 + perfect 360 video. **Our side's job: "how good can the stitched-360 first frame be"** — and per Xinhan, *"我们最后需要的是把 perspective image → panorama 做到最完美,从最底层的原理和算法的问题出发把这个问题解决清楚."*

**Implications you must weigh (argue both sides):** our output is **conditioning for a generative model that already has the geometry (point cloud) and will regenerate appearance.** So: it may *tolerate* an imperfect seam; it may be *misled* by **fabricated salient geometry** (a hallucinated car/curb); it may *prefer* honest-but-incomplete (explicit masked holes) over plausible-but-wrong — the masked-360 training regime literally teaches it to outpaint holes. A live, possibly higher-leverage direction: **stop perfecting the 2D seam; instead produce the first frame in exactly the form Cosmos wants** (honest partial-360 + masked holes + point cloud) and let the video model complete it. **Confirm the exact contract with the user/Xinhan before betting on it** (this is DB-94: is the point-cloud first-frame centre = our ring-centroid-at-camera-height virtual centre?).

---

## 4. OUR CURRENT BELIEFS — HYPOTHESES TO ATTACK (argue for AND against each)

1. **"Near-field multi-centre parallax is the root wall."** Partly overturned by Fable-5: the catastrophic render-back numbers were dominated by the wrong virtual centre, not adjacent-camera parallax (which is real but ~16–21 px). Re-test any "wall" at the centroid centre.
2. **"The seam can't be geometry-anchored (DB-79)."** Re-scoped: that 55–88 px was a property of (depth × ego-origin centre), not of depth alone; at the centroid it drops to a few px. Surfaces are cm-recoverable.
3. **"The near-pole-behind nadir is a physical blind spot (DB-98)."** Our newest claim — twice-verified (steeper-view backfire; no-gate streak return). *Attack it:* is there a representation (cube/nadir reparam) where grazing low-res data doesn't pole-warp into streaks? Is "soft-but-real low-res" (option c) honest enough, or should this region simply be a **masked hole** for Cosmos to outpaint (per §3)?
4. **"Generative is safe only for sky/tone, not salient geometry."** Confirmed for our layer (DiT ground fill hallucinates lane arcs). *But* the Cosmos consumer's own generator handles holes — so maybe we should never fill salient geometry ourselves at all.
5. **"Abstain / honest masked holes is a valid output."** Strengthened by the masked-360 training contract. The abstain *area* should shrink a lot now that depth tolerance relaxed ~20× at the centroid.
6. **"3DGS / reconstruct-then-render doesn't dissolve the seam."** Weakened — the off-trajectory-collapse literature assumed ~1.5–2 m extrapolation; at the centroid it's ≤0.3 m. Parked on cost, kill-evidence now stale. Its unique gift = canonical **dynamic-object** render (kills the moving-car ghost).

**Meta-warning:** at least three load-bearing claims were later shown wrong (a misused citation; the "12 m surface wall"; the "virtual centre is fixed physics"). Weight our beliefs accordingly.

---

## 5. WHAT TO READ / LOOK AT (do the vision pass before theorizing)

**Core 4 working docs (the user's "核心 4 个 md" — and you MUST keep these synced):**
1. `agent/README.md` — project front page + the 8-route table + the **endorsed core algorithm** block.
2. `agent/progress.md` — the permanent factual record (newest-first; top entries = DB-93/97/98).
3. `agent/decision_briefs.md` — the live brief queue (DB-94 Xinhan-centre, DB-95 Waymo-migration, DB-96 icebox, DB-97 video, **DB-98 with the full ground-fill ledger incl. all failures**).
4. `agent/handoff.md` — the chronological banner handoff (full arc through 2026-06-09; older than progress for DB-86+).

**The two Fable-5 first-principles docs (attached, read in full):**
- `agent/2026-06-09-fable5-firstprinciples-brief.md` — the prior thinking-AI handoff (the template for this one).
- `agent/2026-06-09-fable5-firstprinciples-analysis.md` — the recentre finding (the breakthrough).

**Xinhan's latest (attached):** `meeting/6.9_meeting with xinhan/xinhan 做了什么.md` + `xinhan 视频.mp4` (the downstream target quality).

**Artifacts to EYEBALL with your vision:** `deliverables/complete_pano_v8/` (current best full panoramas); the DB-97 videos `deliverables/ground_video_v1/bmw_h264.mp4` + `highway_h264.mp4` (the temporal stress test — watch the bottom-nadir softness grow at open intersections); `deliverables/gpt_pro_sources/04_L1_hard_select_bmw_*.png` (safe base); `deliverables/db79_fair_metric_wall/DB79_review_board.jpg`; the `deliverables/fable5_2026_06_09_summary/OLD_vs_NEW_5scenes.jpg` decision board. **Form your own visual opinion of where the real defect is before adopting our framing.**

(Memory index also lives at the user's auto-memory `MEMORY.md` — north-star, seam-direction, ground-fill physics, DiT360 findings, framework notes.)

---

## 6. THE COLAB + DRIVE EXECUTION FRAMEWORK (you must use this for compute)

- **agent-colab-direct v0.1.0.** A Colab notebook runs ONE setup cell that installs the framework, mounts Drive (`/content/drive/MyDrive/koi_waymo2pano_colab/`), clones the repo, and launches a Flask executor behind a **cloudflared tunnel**. It writes the live `{url, token}` to Drive `runtime/active_url.json` every 5 s (also mirrored locally to `~/.waymo2panorama/runtime/active_url.json`).
- **Client:** `scripts/phase3/db64_…_z_visibility_cause.py` → `ColabClient` (reads `active_url.json`, or env `COLAB_URL`/`COLAB_TOKEN` which override). Methods: `get`/`post`/`read_file` (binary-safe, base64, ≤80 MB); `poll_job`. Endpoints: `/status` `/exec` `/jobs/<id>` `/read`. **No cancel/kill endpoint.**
- **Remote-injection pattern:** `py = m.remote_py()` from `db89_ghost_recovery.py` (or `dataset_gen_av2.py`/`video_gen_av2.py`), string-replace the `CASES`/output-dir/anchors, base64-encode, POST to `/exec` as `bash -lc "python - <<'PY' …"`. **Resume-safe skip-if-exists** lets a re-submit skip frames already on Drive.
- **Resilience = warm-restart + checkpoint-resume, NOT keep-alive.** Colab reclaims an idle/over-limit runtime after ~90 min and it does **not** self-heal — the user must re-run the setup cell. Two L4 runtimes can cooperate (one forward, one `--reverse`; skip-if-exists dedups). Data on Drive is owned by `panq@usc.edu`, shared to `1jingshuo1@gmail.com`; **never touch `secrets/`.**
- **Heavy installs:** zstd-tar the conda env to Drive at the end (saves ~50 min on restart). **Drive↔Colab sync can lag minutes** — don't tight-loop on Drive search after a Colab write.

---

## 7. HARD RULES (operating constraints — keep these)

- **Brief before experiment.** Every idea → a `decision_briefs.md` brief with: question, hypothesis, why-now, expected evidence, **kill criteria**, **max scope**, **vision check**, output location. **No brief, no experiment. One active brief at a time.** Completed briefs are archived into `progress.md` (newest-first) then deleted from the queue.
- **Keep git + decision_briefs + progress in sync — continuously.** Log every progress / failure / kill / accept / blocked to `progress.md` promptly; commit. **Record failures too** (the user insists: the DB-98 ledger keeps every dead end). Direct push to `main` is authorized for this repo.
- **Vision is mandatory** on every image result. Eyes beat metrics on conflict.
- **Two-sided (正反方) + anti-local-optimum reasoning is mandatory** before committing to a direction. We spent ~98 briefs; escaping local optima matters more than another patch.
- **Secrets:** keep raw runtime URL/token + HF token out of any git-committed file/log/board (the repo pushes to a public GitHub remote). Read the endpoint from a local file/env. HF token in-process only, never persisted. Secret-scan must be 0.
- **Subagents (token control):** do the hard *thinking* yourself; you MAY dispatch a *simple* subagent only for clerical/doc work. No large multi-agent workflows unless the user explicitly asks.
- **Communication:** you may reply to the user in **Chinese** (code/paths/metrics stay English).

---

## 8. YOUR DELIVERABLE

After reading the above + the attached docs + doing the vision pass, produce:
1. A short **first-principles re-derivation** of the core problem and an honest **two-sided assessment of where the project actually stands** (what is genuinely solved — recentre, photometric, async-shutter, ground v8, sky outpaint — vs what is genuinely open).
2. The **real unsolved problems**, ranked, with a pro/con argument for why each matters (candidates: the DB-98 nadir blind-spot — fix vs mask-for-Cosmos; whether the single-virtual-centre ERP is even the right target given the Cosmos consumer; generality to Waymo / no-LiDAR; the DB-94 centre contract; dynamic objects).
3. **ONE concrete direction to pursue next**, written as a `decision_briefs.md` brief (with kill criteria + vision check), and the reasoning (including the strongest counter-argument you had to defeat).

You are the fresh mind we are betting on. Don't refine our local optimum — re-derive from the physics, look at the pictures, attack our hypotheses, mine the literature, and come back with one concrete, general direction. Keep git/briefs/progress updated as you go.
