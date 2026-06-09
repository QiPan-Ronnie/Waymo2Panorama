# Waymo2Panorama — First-Principles Brief for Fable 5 (2026-06-09)

> **To Fable 5:** You are the most advanced AI available right now, and the user is bringing you in on purpose because they believe you can do what the previous model (Opus 4.8) could not: **rethink this problem from the absolute bottom, not patch our existing work.** Read this whole brief, then verify our claims yourself (with your eyes on the images, not just our numbers), then think freely. You are explicitly authorized — encouraged — to throw out everything we have done and start from the physics of "what does it mean to turn perspective images into a panorama."

---

## 0. YOUR MANDATE (read this first, hold it the whole time)

1. **Think from first principles.** Start from: *what is the operation "perspective images → 360° ERP panorama" at the lowest level?* Re-derive why it is hard. Do NOT start from our conclusions.
2. **Do not be anchored.** Everything in Part 4 ("OUR CURRENT BELIEFS") is a **hypothesis that may be wrong**. We tell you how we got each belief so you can attack it, not so you adopt it. Several of our past "settled walls" turned out to be measurement artifacts — assume more of them might be.
3. **Escape local optima.** We have spent ~79 decision-briefs inside one framing (combine N perspective images at a seam in 2D). You are free to discard that framing entirely. You may **change the dataset**, change the representation, change the method family.
4. **Any method family is on the table.** Not just classical image stitching. Learned depth, 3D Gaussian Splatting / NeRF, feed-forward reconstruction, DiT / diffusion, any transformer-class method, video-generation priors (see Part 3 — the real downstream is a Cosmos-style video diffusion model). Mine the top-venue literature for latent potential. **The deliverable of your thinking is: one concrete method to pursue.**
5. **Verify, don't trust. USE VISION.** This repo had a session where tool output was *fabricated* (phantom commits, a fake "PARTIAL WIN" with invented numbers). Rule: route any remote/shell result to a file and Read-verify it; never trust PowerShell echo / Glob "no files" / Edit-success text alone. And for EVERY image result you must **open the image and look at it with your vision ability** — our hardest-won lesson is "eyes beat metrics": a good PSNR/score with a smeared curb in the picture is still a failure.
6. **The goal is GENERAL.** The method must work across diverse driving scenes (AV2 + Waymo), degrade gracefully, and the hardest validation case (BMW log `02a00399`, anchor 0) is ONLY a stress test, never the target.

---

## 1. THE INVARIANT PROBLEM (the physics — start here)

**Input (Tier-1 data we actually have):** an autonomous-vehicle capture. Argoverse 2 (AV2): **7 ring cameras** (synchronized RGB, ~20 Hz) + **2 forward stereo cameras** + **LiDAR** + **6-DoF ego-poses** + **multi-frame** sequences + per-camera calibration (intrinsics/extrinsics). Waymo: 5 cameras + LiDAR + poses. So we have RGB **and** geometry (LiDAR), **and** motion (poses), **and** time (multi-frame). We are NOT limited to a single still image.

**Output:** a 360° equirectangular (ERP) panorama (we use 1024×2048), i.e., the scene as seen from **one single virtual optical centre**, mapped to (azimuth, elevation).

**The one physical fact everything orbits around:** the 7 ring cameras sit at **different physical locations** on the vehicle (inter-camera baseline ~0.21–0.26 m; and each camera is ~1.5 m from the rig/virtual centre). A panorama pretends there is **one** optical centre. To place a camera pixel at the correct ERP angle (the direction from the *virtual* centre to the 3D surface point), you need that point's **depth**. With no depth, a direct copy is biased by
`d_px(Z) ≈ (W / 2π) · arctan(b / Z)`, W=2048, b≈0.25 m.
- Measured 16–21 px copy bias ⟺ surface depth Z ≈ 3.25–5.29 m (normal near-field, not a bug).
- To get <1 px no-depth bias you need Z ≥ 68–85 m.
⇒ **everything at 3–8 m near-ground is angularly ambiguous without depth/visibility evidence.** This is the seam. (This bound is *measured and ours*; treat it as a fact about a no-depth copy — but question our interpretation of what it implies for depth-aware or learned operators.)

**Why a cube-map / generative panorama method does NOT transfer for free:** a cubemap's 6 faces are each perspective too, BUT they **share one optical centre** (zero parallax) → faces line up depth-free. Our cameras have **different** centres → real near-field parallax. DiT360 / CubeDiff / CubeComposer assume the shared-centre premise we lack. (You may still borrow the *cube tiling trick* — ERP→6 pinhole faces→run a perspective model→re-stitch — as a delivery mechanism, but it does not fix parallax.)

---

## 2. THE FULL ARC — WHAT WE DID (so you know the terrain; not so you continue it)

**(a) The original 8 stitching routes** (see `README.md` §"8 stitching routes"): L1 = sphere projection + 5-band multiband blend (**still the strongest, cycle-PSNR ~12.3 dB**); L3 = Pi3 monocular-depth forward-splat (**−3.15 dB vs L1, lost 10/10 anchors** — and 4 depth backbones Pi3/DepthPro/TemporalPi3/OmniStitch all failed similarly → "algorithm-class problem, not backbone choice"); plus IPM ground hybrid, cylindrical (新-A), graph-cut seam (新-B), IPM multi-region (新-C), wide-baseline stereo (新-D). **L1 hard_select** (pick one source per region, no averaging) became the safe visual base.

**(b) The candidate panoramas you should EYEBALL** (Part 5 lists paths): `hard_select`, `A1` (hard_select + sky/out-of-FOV outpaint completion), `G` (`G_bmw_pano`, an early repair base), `BEST`, `DB75` (source-mixed blend, "presentation only").

**(c) Seam exploration (briefs DB-01 … DB-79):** post-ERP repair (A1/G blend/swap/warp/inpaint), source-label optimizers (DB72: same-frame 3+ source overlap = 0), ground-plane & temporal candidates (selected fraction ≈ 0), source-mixed blend (DB75: softens, doesn't connect), dense learned geometry (DB67 VGGT: failed all gates, degraded clean control), DiT360 generative (sky-outpaint = WIN; seam-completion invents cars = NEG), Difix learned refiner (safe but faint), Surround360 optical-flow view-interpolation on the overlap strip (DB-78: safe, no hallucination, but visually MODEST). **Pattern: every safe in-band 2D edit comes out faint.**

**(d) Measurement campaign (DB-76a, DB-77B, EXP-B, DB-79):** quantified GREEN reliability, abstain mass, forward-stereo (~1% recovery), multi-frame-LiDAR densification (near-ground 11–18%), and the depth residual at edges. **DB-79 (most recent, fair-metric) is important — see Part 4.**

**(e) Meeting with Xinhan (teammate) → short-term goal + Part 3 below.** A prompt was issued defining the near-term target (make the stitched panorama "look better"). Other agents then explored the seam under that goal.

**(f) Infra:** a **Colab + Google Drive** execution framework (agent → push job → Colab GPU worker → results to Drive → agent reads back). Operate under user `1jingshuo1`. There is a live **L4 GPU** available now (endpoint supplied separately as a secret — see Part 7; NEVER written in any repo file).

---

## 3. THE REAL DOWNSTREAM CONSUMER (this reframes the whole problem — do not miss it)

Per Xinhan (2026-06-09 meeting, `meeting/6.9_meeting with xinhan/`): the panorama we produce is **the first-frame conditioning image for a Cosmos-style 360° video diffusion model** (`cosmos-transfer2.5-pcd`, repo `github.com/LouisonLu/cosmos-transfer2.5-pcd`). Cosmos inference takes **three separate inputs**: (1) a **point-cloud video** (carries geometry along a desired trajectory), (2) a **first frame** (carries appearance — *this is our stitched panorama*), (3) a **text prompt**. The end goal: "one stitched panorama + a point-cloud path video → a realistic 360° video." Xinhan is on the *training/generation* side (and is even training on *masked perfect-360* to mimic the stitched shape, testing whether the model learns to outpaint/repair the stitched seams itself); **our side is "how good can the stitched 360 first-frame be."**

**Implication you should weigh heavily:** our output is not a final artifact for human eyes — it is **conditioning for a generative video model that already has the geometry (point cloud) and will itself regenerate appearance**. This changes the calculus of "must the seam be perfect?":
- The generator may *tolerate* an imperfect seam (it regenerates).
- But it may be *misled* by **fabricated salient geometry** (a hallucinated car/curb in the first frame).
- And it may *prefer* a first frame that is honest-but-incomplete (with explicit holes/masks) over one that is plausible-but-wrong — because the masked-360 training regime literally teaches it to outpaint holes.
⇒ A live, possibly better direction: **stop trying to perfect the 2D seam, and instead produce the first-frame in exactly the form Cosmos wants** (e.g., honest partial-360 with masked holes + the point cloud), letting the video diffusion model do the completion. **You are free to pursue this, or to reject it.** Confirm the exact contract with the user before betting on it.

---

## 4. OUR CURRENT BELIEFS — **HYPOTHESES TO ATTACK, NOT FACTS** (with how we got them + where each could be wrong)

1. **"Near-field multi-center parallax is the root wall."** *How we got it:* the d_px bound + every method failing at the same near-ground places. *Could be wrong if:* a learned method that jointly reasons over RGB+LiDAR+multi-frame+time resolves visibility where our hand-built operators couldn't; or if the Cosmos consumer makes the seam irrelevant.
2. **"The A/B fork (source-faithful vs look-good) is the real churn driver; we now LAYER BOTH."** *How we got it:* the 19-agent retrospective found we built under both definitions at once. *Could be wrong if:* the Cosmos-consumer reality (Part 3) collapses the fork — maybe neither "faithful" nor "pretty" is the right target; maybe "Cosmos-conditioning-optimal" is.
3. **"DB-79 fair-metric settlement: surfaces are cm-recoverable; the seam (curb/wall/silhouette) is a real wall."** *How we got it (the strongest recent result, leader-audited on disk + by eye):* the old "12 m surface depth wall" (DB-77B) was a **measurement artifact** (nearest-neighbour depth fill scored across occlusion edges); a fair LiDAR-only layered hold-out recovers **surface depth to 3.8–7.5 cm**. BUT rendering that depth back to the virtual centre still gives **55–88 px error at curb/wall** because the ~1.5 m virtual-centre offset amplifies sub-meter silhouette depth error. *Verdict:* depth fixes the *surface*, not the *seam*. *Could be wrong if:* you find a representation where the seam is not rendered from an off-trajectory virtual centre (e.g., keep multi-centre and let the consumer fuse), or a learned occlusion-aware renderer. **Verify this yourself:** `deliverables/db79_fair_metric_wall/DB79_review_board.jpg` + `DB79_summary.json`.
4. **"3DGS / reconstruct-then-render does not auto-dissolve the seam."** *How we got it:* hand-built IBR (DB-77B) tore at edges; and per-scene/feed-forward GS collapse at the off-trajectory virtual centre (ExtraGS/EUVS/ConFixGS). *Could be wrong if:* a 2026 feed-forward surround GS or a LiDAR-conditioned per-log reconstruction handles it; this category was **closed on one repo's constraints (StreetCrafter), never actually rendered to ERP on our data** — so it is under-tested, not disproven. Its one unique gift = canonical **dynamic-object** render (kills the moving-car ghost the same-frame mosaic cannot).
5. **"Abstain is a valid, honest output where evidence is missing."** *Could be wrong if:* the Cosmos consumer would rather have a labelled hole than an abstain flag, or rather have a plausible fill — Part 3 makes this a live question.
6. **"Generative is safe only for sky/tone, not salient geometry."** *How we got it:* DiT360 invented cars; object-gate-PASS outputs still faked ground/curb/pole (DB36/DB40). *Could be wrong if:* strong geometric conditioning (point cloud / box / depth) leashes a modern diffusion model well enough — which is exactly what Cosmos-transfer does.

**Meta-warning:** at least two of our "load-bearing" claims were later shown to be wrong (a misused citation; the 12 m surface wall). Weight our beliefs accordingly.

---

## 5. ARTIFACTS TO LOOK AT WITH YOUR EYES (do this before theorizing)

Open and visually inspect (vision ability, not just the JSONs):
- `deliverables/gpt_pro_sources/04_L1_hard_select_bmw_2048x1024.png` — the safe base (sharp, visible seam).
- `deliverables/gpt_pro_sources/01_A1_view_none_bmw_2048x1024.png` — A1 (hard_select + outpaint completion).
- `deliverables/gpt_pro_sources/02_G_bmw_pano_2048x1024.jpg` — G repair base.
- `deliverables/ghostkill/BEST_bmw_pano.jpg` — a "best" variant.
- `deliverables/base_compare_bmw/BMW_base_compare_board.jpg` — 5 bases, same 4 ROIs.
- `deliverables/layered_target_raycaster/db75_full_erp_source_mixed_fallback/.../*candidate.png` + `DB75_full_review_board.jpg` — source-mixed (presentation-only).
- `deliverables/db78_flow_viewinterp/` boards — flow view-interp (the recent "safe but modest").
- `deliverables/db79_fair_metric_wall/DB79_review_board.jpg` — the fair-metric wall settlement (surface vs silhouette vs reproj heatmaps).
- Xinhan's video: `meeting/6.9_meeting with xinhan/xinhan 视频.mp4` (the downstream target quality).

Form your OWN visual opinion of where the real defect is, before adopting our framing.

---

## 6. DIRECTIONS WE HAVE **NOT** TRULY TRIED (candidates, not endorsements)

- Feed-forward / generalizable 3D reconstruction that jointly resolves cross-camera visibility (VGGT-surround / VGD-class), then render — never run here.
- Per-log offline 3DGS (acceptable if framed as an offline dataset-build, like COLMAP) — only the dynamic-actor render is clearly worth salvaging.
- Surface-centric ring-temporal triangulation on the side curb (a real 2nd view of the exact surface as the ego passes) — flagged under-tested.
- **Cosmos-transfer as the actual "B layer"** (Part 3): produce the first-frame in Cosmos's preferred form (partial-360 + masked holes + point cloud) and let the video diffusion model complete it. Possibly the highest-leverage untried idea now.
- Geometry-leashed single-step refiner on the seam band (cube-tiling delivery), with a structure-hallucination guard beyond object veto + hard-abstain on no-geometry near-ground.
- A fundamentally different output representation (keep multi-centre; don't force a single virtual centre at all) — question whether the single-virtual-centre ERP is even the right target given the Cosmos consumer ingests a point cloud + first frame.

---

## 7. HARD RULES (operating constraints — keep these)

- **Brief before experiment.** Every new idea/route → a `decision_briefs.md` brief with: question, hypothesis, why-now, expected evidence, **kill criteria**, **max scope**, **vision check**, output location. **No brief, no experiment. One active brief at a time.**
- **Anti-local-optimum process is mandatory:** use brainstorming, autoresearch-style reasoning, **adversarial / red-team self-audit**, and multi-stance reasoning before committing to a direction.
- **Vision is mandatory** on every image result (Mandate #5). Eyes beat metrics on conflict.
- **Logging & sync:** write every progress/failure/kill/accept/blocked to `progress.md` promptly; keep `decision_briefs.md`, `progress.md`, the plan, `handoff.md`/`README.md`, and `git status` in sync. **Do not revert unrelated changes.**
- **SUBAGENTS (user constraint, to control token cost):** do **NOT** spawn subagents for *thinking/reasoning* work — do the hard reasoning yourself. You **MAY** dispatch a *simple* subagent for clerical/document work (e.g., drafting a brief's boilerplate). This is to keep the main conversation from blowing up on tokens. (No 19-agent workflows unless the user explicitly asks.)
- **SECURITY (firm):** runtime URL / token / Cloudflare tunnel / HF token / Bearer / endpoint JSON are **SECRETS**. There is a live **L4 GPU (≈22.5 GB free)** available; its endpoint is provided to you **out-of-band** — read it only from **process env or a non-repo secret file**, and **NEVER write any secret-like value into any repo file, manifest, board, log, prompt, or shell output.** Reject chat-pasted tokens. Each remote op = one bounded `/status` + `/exec`; secret-scan must be 0. **Tell the user before any GPU run.**
- **3-location rule:** GitHub (committed code + progress + evidence PNG/JPG) + local Windows tree + Drive (large outputs). Operate under `1jingshuo1`.

---

## 8. POINTERS (read these next, in order)

1. This brief.
2. `agent/2026-06-06-deep-retrospective.md` — the most recent leader synthesis (root cause, frontier map, the fork) — **read critically, it is the thing to challenge.**
3. `agent/progress.md` (top entries: DB-79 + the leader audit) — the permanent factual record.
4. `agent/decision_briefs.md` — the active/closed brief queue (DB-79 is the latest, done).
5. `agent/2026-06-06-leader-strategy-synthesis.md` — the earlier (source-faithful-era) synthesis; note it predates the look-good reframe.
6. `README.md` — project front page + the 8-route table.
7. `meeting/6.9_meeting with xinhan/` — the downstream Cosmos reality + video.

---

**You are the most capable AI we have, and we are betting on you to see what we could not.** Don't refine our local optimum — find the real method. Start from the physics, look at the pictures, attack our hypotheses, mine the literature, and come back with **one concrete direction** (as a decision brief) for turning perspective images into a panorama — for the Cosmos-conditioned, general, multi-scene goal. We believe in you.
