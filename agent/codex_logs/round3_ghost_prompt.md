# Adversarial round 3 — is the seam GHOST fundamental, or did I implement the de-doubling wrong?

You are gpt-5.5 at xhigh, acting as an adversarial reviewer. Attack my reasoning hard; do not be agreeable.

## Rig (this is the punishing part)
7 pinhole ring cameras on an Argoverse-2 AV. Each camera optical centre is ~2 m from the ego origin.
Adjacent-camera baseline 21–26 cm. Adjacent overlap is only a ~18.6° azimuth WEDGE. Target = a clean
360° equirectangular (ERP) panorama re-centred at the ego origin, for AV world-model training data — so
it must be SOURCE-FAITHFUL (real pixels / real LiDAR, absolutely no hallucinated content).

## What works and what ghosts
- L1 = rotation-only ERP projection (drop camera translation) + per-pixel cos²-weight argmax → exactly
  ONE camera per pixel. Far field is clean and sharp. But near-field objects that straddle a seam are
  DOUBLED (each camera is ~2 m off ego, ~20–36 px parallax at 18 m), and the cut is visible.
- view-interp (Meta Surround360 style): in the overlap wedge, DIS optical flow warps cam i and cam j
  toward a virtual centre at fractional shift = w_j/(w_i+w_j), then BLENDS novel = warp_i·(1−shift) +
  warp_j·shift, composited onto L1 only where forward-backward flow is consistent (else keep L1).
- gated-LiDAR-reproject: densify sparse LiDAR range across the seam band (kNN), reproject every camera
  to the ego centre at that depth, and where two reprojections AGREE (cross-view RGB resid < 16/255)
  keep avg = 0.5(reproj_i + reproj_j); else keep L1.

The user (vision, reliable) reports BOTH view-interp and gated-LiDAR still show clear GHOSTING (虚影):
seams partly improved but a lot of doubling-at-half-intensity remains. They insist the path is viable
and that Google/Meta get clean results, so this is an implementation failure, not a wall.

## My new hypothesis (attack it)
The ghost is caused by AVERAGING two copies that are not pixel-perfectly aligned. Two fixes, both
"never average two misaligned sources":
1. Production recipe: warp the losing slab to AGREE with the winner inside the band (flow, FB-gated,
   tapered), then HARD-SELECT one source (never blend geometry), then a THIN low-frequency colour
   blend across the cut only (hide the colour step without mixing geometry). Ghost is then impossible
   by construction; the de-doubling comes from the warp-to-agree before the single-source cut.
2. Depth path: after reprojecting BOTH cams to the true ego centre at the correct dense depth, the two
   copies ≈ coincide, so PICK ONE (single source, higher cos²-weight) instead of averaging. If depth is
   right → sharp + de-doubled. If depth is slightly off → still SHARP (just slightly mispositioned),
   never a ghost. Note: an earlier kill-test showed that selecting between two ROTATION-ONLY copies is
   geometrically wrong (both are 16–21 px off the true azimuth, same side). But selecting between two
   copies already REPROJECTED to the true centre is different — both are ≈ at the true position — so
   selection is valid there.

## Questions — be adversarial and concrete
1. Is the residual seam ghost FUNDAMENTAL (depth/flow accuracy bound for a 2 m-offset, 21–26 cm-baseline,
   18.6°-overlap rig) or fixable by "never average" (single-source after align)? Give the decisive
   reason, not a hedge.
2. Where does single-source-after-align STILL fail visibly? (e.g. the wide-baseline near object whose
   two views genuinely disagree by tens of px with no reliable flow in an 18.6° wedge — does a hard cut
   then show a step/tear instead of a ghost? Is a visible step actually better or worse than a faint
   ghost for AV training data?)
3. Is "reproject-to-true-centre then PICK ONE" really better than averaging, or does picking one just
   move the error from ghost to misregistration that the eye still flags? When does avg beat pick?
4. What is the MINIMAL recipe that beats plain L1 with NO ghost on THIS rig — and what is the honest
   ceiling of fraction-of-seam that can be cleanly singled vs must fall back to L1?
5. What concrete CPU-only (cv2/numpy/scipy) experiment would DECISIVELY separate "fundamental" from
   "implementation bug"? Design the kill-test.

Answer in 5 numbered sections matching the questions. End with a one-line verdict:
"GHOST = FUNDAMENTAL" or "GHOST = IMPLEMENTATION (fixable by single-source)" and the single most
important next experiment.
