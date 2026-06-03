# Adversarial round 6 — am I STILL under-implementing the CPU de-doubling on TEXTURED surfaces, or is this the ceiling?

gpt-5.5 xhigh, maximally adversarial. ONE image: full-pano L1 | view_none | deliverable. Vision-judge it.

## Where we are (your prior rounds drove all of this)
- r3: the 虚影 = AVERAGING two misaligned copies → fixed by SINGLE-SOURCE (pick/align). Done, 4× validated.
- r4: the missing lever was seam TOPOLOGY → I built an OBJECT-MOAT min-cut seam (custom DP cost: cross-
  view resid + LiDAR-near + dilated near-object as ∞ moats) that routes the single-source cut AROUND
  near objects so cars come whole from one camera. Done.
- r5: the near-ground curb = a grazing-angle FLOOR. Confirmed 3 independent ways: (a) aggressive near-
  ground reproject SMEARS dense scenes (2c65 SUV), (b) a cross-view-AGREEMENT-gated reproject fires only
  0.12% (grazing → reprojections never agree), (c) your geometry: pixel_shift=baseline·focal·Δ(1/Z).

Current deliverable = `align` (DIS-flow warp-to-agree in the ~18.6° overlap band, FB-consistency gated,
then HARD-SELECT + global gain) + object-moat seam. CPU only (cv2 DIS flow + numpy + LiDAR). Clean +
ghost-free on BMW/0bae/2c65, beats view_none.

The user (reliable, persistent, usually right) still says "you haven't implemented it well, raise your
intelligence" — i.e. they believe more clean de-doubling is extractable. They are NOT talking about the
curb (they accept other regions are "ok"). I want you to find what I'm still missing on the TEXTURED
mid-range, or concede the ceiling with a decisive reason.

## Questions — adversarial, vision-grounded, concrete
1. Vision: in the attached deliverable (bottom row) vs L1 (top), is there RESIDUAL doubling/stepping on
   TEXTURED mid-range surfaces — building facades, windows, poles, signage — that single-source align
   should have de-doubled but didn't? Point to specific locations. Or is the textured mid-range actually
   clean and the only residual is the (accepted) near-ground?
2. If there IS residual textured doubling: what's the CPU lever I'm under-using? Candidates — (a) my DIS
   flow band is too narrow / max_disp too small so facade parallax (~10-15px at 20-30m) isn't fully
   warped; (b) FB-consistency gate too strict → abstains on alignable facade → keeps doubled L1; (c) I
   warp toward the neighbour but should do true Surround360 virtual-centre view-interp THEN single-source
   select (not just warp-to-agree); (d) chain-warp drift; (e) the seam should follow the facade's
   low-disparity columns (vertical DP) rather than my horizontal moat routing. Which, and the concrete
   fix.
3. Is "warp-to-agree + hard-select" strictly WEAKER at de-doubling textured surfaces than Surround360's
   "warp BOTH to a virtual centre + blend", given the blend ghosts? Is there a SINGLE-SOURCE way to get
   view-interp's de-doubling power without the blend-ghost (e.g. warp both to virtual centre, then pick
   the higher-confidence one per pixel instead of blending)?
4. Brutal: is the user right that more is extractable on CPU, or have I hit the genuine source-faithful
   CPU ceiling for the textured mid-range (and only the near-ground + learned-depth GPU remain)?
5. SINGLE highest-leverage next CPU experiment to extract more clean textured de-doubling. One concrete
   thing I can code in cv2/numpy today.

End one line: "MORE EXTRACTABLE (do X)" or "CPU CEILING (textured mid-range is clean; only GPU remains)".
