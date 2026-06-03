You are an ADVERSARIAL reviewer. Be skeptical, find the flaw in my reasoning, and tell me what I'm doing wrong — do not be agreeable. I (another AI, Claude) may be stuck in a local optimum. The human supervisor explicitly wants your opposing view.

## The problem
Stitch 7 non-co-located AV2 (Argoverse2) ring cameras into ONE clean 360° equirectangular (ERP) panorama. Rig: pinhole cameras, adjacent baseline 21–26 cm, adjacent FoV overlap ~18.6°. Target use: faithful AV world-model training data (so NO hallucinated objects; structure must be real).

## What works / doesn't (my current state)
- **L1 = hard_select**: project each camera to ERP by rotation-only (single optical center assumption, drops camera translation), then per-pixel pick the camera with max cos² weight (argmax). Result: SHARP, clean far field, but (a) visible seams and (b) NEAR-FIELD DOUBLING — a near object straddling a seam appears twice because the two cameras (21–26cm apart) see it at different ERP azimuths.
- I tried many enhancements; EVERY one that looks "more different from L1" adds an artifact the human catches by eye:
  1. **view-interp** (Surround360-style: warp camera_i by shift·flow_ij + camera_j by (1-shift)·flow_ji, then ALPHA-BLEND): translucent double-image GHOST wherever the warp is imperfect.
  2. **align = warp-to-align + hard_select + E1.5 multiband low-freq color**: the wide multiband low-freq band WASHES the near-black wall (bright sky/storefront low-freq bleeds in) → WHITE-SPOT.
  3. **align --color none/gain --seam argmax/graphcut** (pure single-source: warp losing camera toward neighbor in the seam band with FB-gated DIS flow, then hard_select, optional cv2.detail GraphCutSeamFinder seam routing, optional global gain comp): CLEAN (no ghost, no white-spot) BUT ≈ L1 — color=none changes only 0.94% of the pano, graphcut 1.86%. It avoids doubling but does NOT synthesize the center-correct view, so near objects stay at one-camera position.
- **DrivingForward (feed-forward 3DGS, AV2-finetuned, single optical center reproject via learned depth)**: genuinely fuses the 7 cams to ONE center (BMW appears once, no doubling) BUT the render is SOFT, has comb/streak SHREDDING at camera seams, near-ground warping, and is FoV-band-limited (black top/bottom). See attached image #2.

## My current conclusion (CHALLENGE THIS)
"Every CLEAN single-source 2D method ≈ L1 (hard-select only picks one camera's view; can't synthesize the center view). Anything that looks more different than L1 does so by MIXING two sources → artifact (ghost / white-spot). Therefore the 2D-stitch ceiling on this wide-baseline rig is ~L1, and the only genuinely-better-AND-clean route is single-center reprojection via depth (DrivingForward), which currently looks worse due to 3DGS artifacts."

## The human's challenge to me
"Google Street View, Meta Surround360, Google Jump all produce CLEAN panoramas from non-co-located rigs with PUBLISHED algorithms. They can do it; the algorithm is given. So we must have a bug or be missing a key piece. Why can't we?"

## Images attached
1. (A1_align_graphcut_none_L1_vs_result.jpg) TOP = L1 hard_select; BOTTOM = my best clean 2D (warp + graphcut seam + single-source). They look nearly identical = my "2D ≈ L1" claim.
2. (DFWD_bmw_single_center_ERP.jpg) the DrivingForward single-center 3DGS ERP — fused but shredded/soft/band-limited.
3. (VIS_align_zoom_fixed2.jpg) gray-car zoom: L1 | view+none(alpha-blend, faint ghost on car rear) | align(single-source, clean).

## Repo context you can read (you have read access to this directory)
- My pipeline: `scripts/phase3/run_a1_streetview_pipeline.py` (functions: render_camera_to_erp via `code/waymo2panorama/projection/sphere_projection.py`; `flow_align_chain`/`_align_cur_to_prev`; `graphcut_label`; `view_interp_panorama`).
- Research notes (source-grounded summaries of Surround360/Jump/Google/Zhang&Liu/SEAGULL) are in `agent/progress.md` (search "RESEARCH" and "part 4").
- The agent downloaded reference copies: `NovelView.cpp`, `NovelView.h`, `jump.pdf` in the repo root.

## What I want from you (be concrete and adversarial)
1. Is my "2D single-source ≈ L1, mixing → artifact, so 2D ceiling = L1" conclusion WRONG or incomplete? Where exactly?
2. Google/Meta on similar rigs: what is the SPECIFIC technique I am missing or implementing wrong? Candidates I suspect: GLOBAL spline/mesh warp (Ceres) that aligns the WHOLE overlap (mine is local + FB-gated → timid, ≈L1); per-column/ODS synthesis (Jump) instead of fixed-seam ERP; disparity-ordered OVER-compositing with depth intervals (Jump §5.5) using my real LiDAR depth instead of flow; flow-magnitude/depth foreground-select (Surround360 deghost softmax). Which of these is the actual missing piece for a CLEAN result on a 21–26cm-baseline rig, and why?
3. Critically: is the near-field DOUBLING even fixable by ANY 2D method without either (a) mixing→ghost or (b) reprojecting to a single center via depth? Argue both sides.
4. Given the DrivingForward 3DGS is fused-but-shredded, is "3DGS single-center + diffusion refine (Difix3D+ / 3DGS-Enhancer) anchored to L1 far field" a better bet than continuing 2D? Or is there a cleaner non-learned single-center DIBR using my dense LiDAR that I should try first?
5. Give me the SINGLE most decisive next experiment to run (with the A100 available) that would either break my local optimum or confirm the ceiling.

Be specific, cite the methods, and tell me bluntly if I'm wasting time on the 2D path. Keep your answer focused and technical. You do NOT need to run code — reason from the images + the repo + your knowledge.
