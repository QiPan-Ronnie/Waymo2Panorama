DB-198 generalisation check of the DB-184 fixes, on scenes they were never tuned on.

Scene selection: annotations.feather only (a few MB per log) was downloaded for 90
sampled AV2 logs and ranked by peak pedestrians-per-frame. The three used here:

  s1  1842383a-1577-3b7a-90db-41a9a6668ee2  train  peak 108 ped/frame  Miami Beach
  s2  e453f164-dd36-3f1a-9471-05c2627cbaa5  train  peak 106 ped/frame  Pittsburgh downtown
  s3  280269f9-6111-311d-b351-ce9f63f88c81  val    peak  76 ped/frame

The three PNGs named *_peak*.png are the SHIPPED configuration:
v11 + DB-171 + DB-181 + DEPTH_SEAMRAMP=60 + GAIN_STRENGTH=1.0, raw band with the
ego hood retained, GROUND_MODE=off.

VERDICTS
1. DEPTH_SEAMRAMP=60 generalises. Holding gains at 1.0, ramp0 vs ramp60 moves the
   seam step by 5.50->5.42 / 9.08->9.08 / 6.33->6.33, i.e. nothing, exactly as the
   design predicts (w=1 at seams). It touches 0.22-0.58% of pixels and those are
   the torn ones: see q_ramp_only_effect.png - the building window grid at
   s2 (1991,397) is cut and offset without the ramp, continuous with it.
2. GAIN_STRENGTH=0.5 does NOT generalise and was reverted. Seam step on these three
   logs: 5.50->7.33, 9.08->14.17, 6.33->7.67 (worse on all three). It only helped on
   00a6ffc1, whose gain SOLVE is broken there (fr_0037 front_right|side_right seam
   is 70.5 at full gain but 12.8 with no gain at all). Evidence of the regression:
   q_gain_halving_regression.png - the under-corrected dark trapezoid on the s2 road.
   The real fix is robustifying solve_gains_for, not a global scale.

MEASUREMENT NOTE: seam steps here are measured by walking the territory boundary
ROW BY ROW (200+ samples per seam). The earlier method pinned a seam to one column
and only sampled rows where that column was a boundary - a boundary is a curve, so
most rows went unmeasured and the numbers were unreliable. Territory maps are
computed per-log from that log's own calibration (C = centroid of the 7 camera
centres in the ego frame); reusing another log's map puts seams in impossible
places.
