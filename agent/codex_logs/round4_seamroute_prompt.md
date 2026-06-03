# Adversarial round 4 — the single-source deliverable still looks ≈L1; the user wants near-object doubling DRAMATICALLY singled. What CPU lever am I missing?

gpt-5.5 xhigh, adversarial. Two images attached: (A) full-pano 3-row L1 | view_none | deliverable;
(B) BMW seam zoom L1 | view_none | deliverable. Vision-judge them yourself, then attack my plan.

## State
Rig: 7 pinhole ring cams, optical centres ~2 m off ego origin, adjacent baseline 21-26 cm, adjacent
overlap a ~18.6° azimuth WEDGE. Target: clean ego-centred ERP, source-faithful (real pixels/LiDAR, NO
generation). CPU only (cv2/numpy/scipy).

Settled last round (you concurred): the seam GHOST (虚影) = AVERAGING two misaligned copies; fixed by
SINGLE-SOURCE (never average) — `pick` (reproject both to ego at dense-LiDAR depth, pick higher-weight
cam) and `align` (flow warp-to-agree → hard_select). Perturbation proof: averaging loses 30% sharpness
at 0.5 px, pick stays sharp.

The clean deliverable = align + pick (single-source, gain colour, obj-route around compact near objects,
graph-cut option). It is GHOST-FREE and beats view_none (which still blends→ghosts). BUT: the clean
DE-DOUBLING fires on only ~2% (depth + cross-view-agree gate); loosening or smoothing the depth gate did
NOT enlarge it cleanly (4 independent confirmations: E2 dense-depth smears, plane-sweep <1%, loose
LiDAR gate, edge-aware-smoothed gate). So near objects that L1 doubles and obj-route didn't catch stay
doubled. The user (reliable vision) says it still has "lots of doubling" and isn't clearly better than
view_none, and insists the path is viable and I'm under-implementing.

## The reframe I want you to pressure-test
For a source-faithful MULTI-PERSPECTIVE panorama (not a true single centre), the doubling at a seam is
just "the same object appears in BOTH cameras' regions because the cut runs through it." If I instead
ROUTE THE SEAM ENTIRELY AROUND every near object (take each whole object from ONE camera, run the cut
only through far / low-disparity / cross-view-AGREEING regions — sky, distant facade, road vanishing
point), then NO near object is split → no doubling and no ghost, by construction. The object sits at one
camera's (off-ego) viewpoint, which is fine for a multi-perspective pano. This is the Google-Street-View
move (min-disparity seam), and it does NOT need accurate depth — only a disparity/agreement COST map
(LiDAR range discontinuity, or cross-view RGB residual after the band warp) to push the cut away from
near/disagreeing pixels.

## Questions — adversarial, concrete
1. Vision verdict on the attached deliverable: is there still visible near-object DOUBLING, or is it
   actually ≈L1-clean and the user is reacting to the OLD averaged result? Be specific about WHERE.
2. Is "route the seam around ALL near objects via a disparity/agreement-cost min-cut" the right
   highest-leverage CPU lever to dramatically reduce visible doubling — or does it just MOVE the problem
   (e.g. the object's BACKGROUND now has a discontinuity at the cut, or two adjacent objects make the
   wedge un-routable)? When does seam-routing fail and you're forced back to doubling/ghost?
3. My current graph-cut uses cv2.detail COST_COLOR_GRAD (route through low colour+gradient diff). Is
   that already a disparity-routing proxy, or do I need an explicit cost = (LiDAR range or 1/agreement)
   so the cut provably avoids near/high-parallax pixels? Give the concrete cost map to build.
4. Brutal check: is the user's "lots of doubling, not better than view_none" most likely (a) true
   residual splitting my routing misses, (b) them judging the old averaged image, or (c) a real ceiling
   where on an 18.6° wedge with multiple near objects the seam CANNOT avoid them all? Which, and why?
5. Give the SINGLE highest-leverage next experiment (CPU, source-faithful) to make near-object doubling
   dramatically cleaner. One concrete thing.

End with one line: the single highest-leverage CPU lever + whether you expect it to clearly beat
view_none and L1, or whether this genuinely needs GPU learned depth.
