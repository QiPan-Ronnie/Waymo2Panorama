# Adversarial round 8 — am I in a LOCAL OPTIMUM on the wavy near-ground seam? (gpt-5.5 xhigh — be my opposition)

You are my adversarial counterpart. Attack my framing and conclusions. The user EXPLICITLY warned me not to get stuck in a local optimum, and told me to have you fight me. Be ruthless, concrete, and specific. Do not flatter.

## The system
- 7 non-co-located AV2 (Argoverse-2) ring cameras (front-center, front-L/R, side-L/R, rear-L/R) mounted around a car roof, the rig spans ~2m, ADJACENT overlap is only a ~18.6° wedge, each pinhole. We also have per-frame LiDAR.
- GOAL: stitch into a clean 360° ERP panorama as FAITHFUL AV world-model training data (NO hallucinated objects). The output is a horizontal band (no top/bottom cameras → black sky/ground above/below the band).
- Source-faithful deliverable = `_seamroute.py`: render each cam ROTATION-ONLY to ERP (= "L1"), DIS-flow-align the losing slab inside the ~18.6° overlap band, HDR-gain, object-moat min-cut DP seam (route the single-source cut AROUND near objects via custom cost = cross-view RGB residual + LiDAR-near moat + off-plane object moat), then virtual-center select (warp BOTH cams to the virtual center, HARD-select one per pixel — never average, so no ghost), composite. Result: ghost-free, sharp, beats naive blend view-interp. The user calls this the closest-to-goal so far.

## The remaining defect (the ONLY thing the user cares about now)
The NEAR-GROUND seam is WAVY. Where the single-source cut crosses the road, the lane lines / curb KINK — a lateral parallax offset shows up as a wavy / zigzag discontinuity in the road markings and the curb. Far field and the upper scene are clean. The user circled this on the road and the curb.

## What I tried and concluded — ATTACK THIS HARD
1. **Ground-plane IPM reproject** (reproject the road via the LiDAR ground plane so road points land at the true ego position regardless of camera): NEG. Whole-road → grazing-angle STRETCH/smear in front of near objects (the BMW). Band-confined + depth-window 3–35m → no regression but improvement INVISIBLE (~0.3% of pixels touched). I concluded: grazing-angle ill-conditioning + road-not-perfectly-planar + off-plane curb = physical floor.
2. **Seam-reroute via a ground high-gradient cost** (penalize the DP cut from running ALONG lane lines/curb → route through uniform asphalt, cross perpendicular). At line-weight 10 AND 50, the seam barely moves and the result is visually UNCHANGED. I think the cross-view RGB-residual term already does something similar.
3. **Learned single-center** (DrivingForward-AV2 feed-forward 3DGS): WORSE — soft/shredded/band-limited.
4. **GPU occlusion test**: the curb is a CO-OBSERVATION floor — the two relevant cams barely both-see it (38% grazing-occlusion cross-view disagreement). Can't learn depth for a surface a camera doesn't see.

My current (possibly local-optimum) conclusion: the wavy near-ground seam is a PHYSICAL FLOOR for source-faithful geometry; the only levers left are (a) DiT360 thin-seam GENERATION (trimap-clamp: regenerate only the ~1.6% core seam line, far field byte-exact — prepared, needs GPU), or (b) different capture hardware.

## The user's acceptance bar (this WIDENS the solution space — exploit it)
"HIDE the seam" is ACCEPTABLE — make it INVISIBLE / plausible source-faithfully, NOT geometric truth. (Google/Meta also HIDE near-parallax seams, they don't perfectly solve them.) Generation (DiT) is a SEPARATE queued route. So I want NON-generative, source-faithful ways to make the wavy near-ground seam not noticeable.

## Your job — be my opposition, help me ESCAPE the local optimum
1. **Name the frame I'm stuck in.** I've been tweaking the cut LOCATION and the blend, one parameter at a time. What is the larger assumption I haven't questioned?
2. **Attack "near-ground seam = physical floor."** Give the STRONGEST case that it is NOT a floor and there's a source-faithful lever I'm missing.
3. **Concretely propose fundamentally different near-ground representations that give a CONTINUOUS road**, e.g.:
   - A proper **BEV / IPM ground composite**: project ALL cameras onto the single LiDAR ground plane in a TOP-DOWN ortho view → the road is inherently continuous (one plane, all cams agree, sampled from ABOVE so no grazing stretch) → then re-warp that single continuous ground image back into the ERP lower band. Does this dodge the grazing problem my ERP-space IPM hit? Where does it break?
   - Choosing the ERP yaw / seam AZIMUTHS so the worst near-ground seams fall where there are no markings.
   - Compositing the ground as ONE textured plane (LiDAR-masked) rather than per-camera slabs.
   - A wider feather / seam ONLY on the ground (and why that ghosts or doesn't).
   - Anything else I'm not seeing.
4. For each candidate: give the MECHANISM, why it might beat what I tried, and the CHEAPEST CPU kill-test to falsify it fast.
5. **Be honest about worth**: is the wavy near-ground seam even worth more effort, or should I tell the user the bigger gap to Google-Map quality is the black sky/ground (vertical FoV, generation-only) and redirect? Don't hedge — take a position.

Prefer cheap CPU kill-tests. Find what I'm missing.
