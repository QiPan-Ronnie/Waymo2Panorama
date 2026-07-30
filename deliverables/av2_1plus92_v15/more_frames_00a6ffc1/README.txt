More frames from the ORIGINAL log 00a6ffc1 (the fr_0037 scene), rendered with the
CURRENT committed kernel: v11 + DB-171 + DB-181 + DEPTH_SEAMRAMP=60 +
GAIN_STRENGTH=1.0 (after the DB-198 revert). Raw band, ego hood retained,
GROUND_MODE=off. 2048x1024 ERP.

Anchors picked by pedestrian count, spread across the log, >=15 frames away from
the already-delivered a095/a099/a100: a019 a045 a063 a079 a273 a303.
(This log peaks at 51 pedestrians/frame - far less crowded than the DB-198 set.)

WHAT TO LOOK AT
- a273 / a303 pass right by the BILL'S BAR & BURGER storefront: the sign reads
  cleanly, no tearing. That is the DEPTH_SEAMRAMP fix holding on frames it was
  not tuned on, in the same scene where the tear was first reported.
- a019 / a063 / a079 show a narrow vertical honest-black strip at theta ~ -73deg
  (ERP x 1429-1447, width 5-19 px), i.e. right around the front_right|side_right
  territory boundary. This is the long-standing no-source black (the depth sample
  lands outside every camera's field of view) documented in DB-163 - present since
  the first commit of db89, not introduced by any recent change. a045/a273/a303
  have none, so it is content-dependent, not structural.
- Dark pixels inside the band envelope: 2.1-2.4% on every frame, essentially all
  of it the band's own top/bottom edge, not holes.

GAIN NOTE
A GAIN_STRENGTH=0.5 counterpart of each frame is not shipped here because the
comparison does not settle: on this very log the seam step goes 12.92 -> 13.17
(a273, flat), 13.42 -> 17.96 (a303, worse), 12.21 -> 10.71 (a063, better).
Even within one log the answer flips frame to frame, which is further evidence
that a global scale is the wrong instrument; the fix belongs inside
solve_gains_for. _gain1.0_vs_0.5_a303.png shows one pair for reference.
