# Adversarial round 7 — definitive ceiling check (gates a real multi-day GPU spend)

gpt-5.5 xhigh, decisive. ONE image: full-pano L1 | view_none | deliverable (current best).

I have now implemented EVERY lever you gave across rounds 3-6 for this 7-cam AV2 ring -> ego ERP,
source-faithful, CPU-only stitch:
- r3: single-source (pick/align) -> the 虚影/ghost is gone (averaging was the bug). 4x validated.
- r4: object-moat min-cut seam routing -> near objects (cars) come whole from one camera.
- r5: near-ground curb = grazing FLOOR, confirmed 3 ways (aggressive smears 2c65 SUV / agreement-gated
  reproject fires 0.12% / geometry pixel_shift=baseline*focal*Delta(1/Z)).
- r6: virtual-centre hard-select (warp both to virtual centre, select one, no blend) -> implemented;
  verified safe + a MARGINAL facade improvement (facades were already ghost-free).

Deliverable = align + object-moat seam + virtual-centre select. Clean + ghost-free on BMW/0bae/2c65,
beats view_none. The only visible residual is the grazing near-ground curb (the accepted floor).

The user must now decide whether to spend MULTI-DAY GPU work (learned LiDAR-supervised metric depth,
leave-one-camera-out) to land that grazing curb. Before they spend it, I need your decisive call.

Questions:
1. Vision: is there ANY remaining source-faithful CPU gain visible in the attached deliverable that I
   have NOT addressed, other than the grazing near-ground? Be specific or say "none".
2. Is the CPU path now genuinely at the source-faithful ceiling, with the grazing near-ground curb the
   ONLY remaining residual (which truly needs learned metric depth = GPU)? Or is there ONE more concrete
   cv2/numpy lever I haven't done?
3. If GPU: is the expected gain HONESTLY just that localized grazing near-ground (small, not a dramatic
   whole-image change), or would learned depth meaningfully improve more of the panorama?

End ONE line: "CPU CEILING REACHED — GPU buys only: <X>" or "ONE MORE CPU LEVER — do <Y>".
