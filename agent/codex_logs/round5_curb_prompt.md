# Adversarial round 5 — the LAST visible artifact: a near-ground curb/sidewalk seam the user circles as jagged. Fixable on CPU, or genuine limit?

gpt-5.5 xhigh, adversarial, vision. ONE diagnostic image attached: 5 horizontal panels of the SAME curb
crop — [1] L1 baseline, [2] moat-route (single-source seam routing), [3] moat-route + near-ground
reproject (my current best), [4] cam-id (which camera each pixel comes from), [5] gdepth (ground-plane
depth; blue=near<30m, red=far).

## Context
7-cam AV2 ring → ego-centred ERP, source-faithful (real pixels/LiDAR, no generation), CPU only. The
panorama is now ghost-free (single-source) and clean EVERYWHERE the user checked EXCEPT this one spot:
the near-ground curb/sidewalk where the road meets the dark storefront wall. The user circled it twice
and says the seam is "参差不齐" (ragged/jagged) there while everything else is fine.

What the diagnostic shows (verify against the image):
- Panel [4] cam-id: the curb sits on a vertical SEAM — the RED road-camera region meets the BLUE
  wall/sidewalk-camera region. The cut runs just left of the sidewalk; the road-vs-sidewalk boundary
  and the sidewalk's horizontal joint-lines step across that red/blue seam (parallax).
- Panel [5] gdepth: the sidewalk/wall side is RED = the LiDAR GROUND-PLANE model returns FAR there,
  because those rays travel toward the wall and graze the ground at a shallow angle — so plane-depth is
  useless on exactly this surface. The open road in front is blue (near, plane-depth OK).

## What I've tried (all single-source, no averaging → no ghost)
1. Moat min-cut seam routing (your round-4 lever): routes the seam around compact near objects, but this
   near-ground is a BARRIER spanning the wedge bottom → can't route around it.
2. Ground-PLANE near-road reproject: fired ~0% on the sidewalk because plane-depth is FAR there (panel 5).
3. LiDAR-depth near-ground reproject (densified sparse LiDAR range, true sidewalk depth ~5m, single-
   source PICK): now fires 2.87% and DOES cover the sidewalk — but the visible change vs L1 is still
   marginal (panel [3] ≈ panel [1] at the curb).

## Questions — adversarial, concrete, vision-grounded
1. Vision: in the attached image, is panel [3] actually better than panel [1] at the curb, or basically
   unchanged? Be specific about which lines/edges.
2. Why is the LiDAR-depth single-source reproject barely moving it? Candidates: (a) at a grazing angle
   the same depth error → huge ERP pixel shift, so my densified-LiDAR depth (kNN, ±18px) isn't accurate
   ENOUGH on this thin surface; (b) the two cameras genuinely see DIFFERENT surfaces here (one sees curb
   face, the other sees curb top — a real occlusion/foreshortening difference no single depth fixes);
   (c) the residual is actually the petal/coverage-edge + the magenta fringe, not content misalignment;
   (d) my reproject has a bug. Which is most likely, and what observation in the panels decides it?
3. Is a near-ground seam at a shallow grazing angle a place where source-faithful CPU stitching has a
   genuine FLOOR (because ERP shift = baseline·focal·Δ(1/Z) blows up as the surface grazes and Z is most
   uncertain), or is there a concrete fix (e.g. move the seam ABOVE the near-ground onto the wall/far
   region so the cut never crosses the near sidewalk; or per-row 1-D align of the two cameras' near-
   ground using the curb edge itself as the only feature)?
4. SINGLE highest-leverage next move for THIS spot. One concrete thing. If you think it's a genuine
   floor, say so plainly and say what the honest deliverable claim should be.

End: one line — "CURB = FIXABLE (do X)" or "CURB = FLOOR (honest claim: Y)".
