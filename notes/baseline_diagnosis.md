# L1 Baseline Diagnosis

Date: 2026-05-19
Pipeline: L1 baseline (sphere projection + multi-band blending), 7 ring cams + 1 empty.
Test sequence: `02a00399-3857-444e-8db3-a8f58489c394` (AV2 val), first 5 s @ 20 Hz.
Code commits referenced: v1 = `e509c9c`, mirror-fix = `885b5da`.

This document captures **what L1 does well, what fails by design, and what fails due
to actual bugs** — so Phase 2 (Pi3/DVGT-aware) has a measurable target.

---

## L1 v1 (commit `e509c9c`) — first render

Output: 1024×2048 ERP, 100 frames, ~5 s mp4. First frame eyeballed via Cell 7.

### What works (no fix needed)

| # | Aspect | Evidence |
|---|---|---|
| 1 | Full ERP shape | 2:1 aspect; center column = forward; left/right edges meet behind vehicle |
| 2 | All 7 cones + empty slot rendered | 7 distinct camera contributions visible in expected angular positions |
| 3 | Horizon level | No roll/pitch error; sky on top, ground on bottom |
| 4 | Sky on top, ground on bottom | No upside-down flip |
| 5 | Multi-band wrap padding at θ=±π | No visible vertical seam at the wrap-around |
| 6 | Pipeline end-to-end | `pip install -e . → av2_loader → render → blend → mp4` all chained OK on Colab CPU |
| 7 | Performance | ~100 frames @ 1024×2048 in 2-5 min on CPU |

### Real bug (fixed in `885b5da`)

**Horizontal mirroring across the entire ERP.** Storefront text appeared reversed
(e.g. "Hostprojects" rendered as backwards letters reading right-to-left). All seven
camera cones were placed on the wrong horizontal half of the ERP.

- Root cause: `sphere_projection.py` mapped `u → θ` with `θ = u/W·2π − π` (CCW).
  AV2 ego frame is right-handed (`+y = LEFT`), so this puts the LEFT side of the
  vehicle at `u > W/2`, but a human reader expects LEFT-of-vehicle at `u < W/2`.
- Fix: `θ = π − u/W·2π` (CW). With this, `u > W/2 → θ < 0 → y < 0 = RIGHT of vehicle`.
- Verification: v2 re-render expected to show text reading normally.

### L1 expected failure modes (NOT bugs — by design)

These are what L1 leaves on the table for Phase 2 to fix.

| # | Failure | Where visible | Why it's expected |
|---|---|---|---|
| F1 | Black bands top/bottom of ERP | Above and below each cone | Ring cams don't look at zenith/nadir; only sphere region inside their frustums has any data. Fix needs a fisheye lens, an upper cam, or a 3D model. |
| F2 | Black gaps between adjacent cones | Between each pair of cameras | AV2's 7 ring cams have ~70° HFoV; there's ~10° gap between adjacent cones at the horizon. Fix would need wider lenses or filled by L3/L4. |
| F3 | Per-camera exposure mismatch | Visible brightness/color jump between cones | Each camera auto-exposes independently. Phase 1+ should add per-cone histogram matching or learned color harmonization. |
| F4 | Ego vehicle hood/body in lower cone region | Partly cropped, sometimes visible | No ego mask painted yet. Plan §4 Phase 1 task list has `Hand-paint ego masks for 7 cameras` pending. |
| F5 | Geometric ghosting on near objects | (Would appear if cones overlapped) | L1 ignores camera translation → close objects map to different θ across cams. F2 hides most ghosts because cones don't overlap. As we widen cones or move to L3, ghosting will become visible — that's the Phase 2 target. |
| F6 | Narrow cones (less data than seems possible) | Each cone narrower than full HFoV would suggest | Bilinear margin + cone falloff feather. Tune `weight = cos²(angle)` exponent if too aggressive. |

### Conclusions for v1

- **L1 pipeline is end-to-end correct except for the azimuth mirror bug.**
- The bug was high-signal (visible immediately) and trivially fixable (one line, no
  semantic risk).
- Failure modes F1–F6 are exactly what plan v2 §4 Phase 1 predicted and §6 risk
  register listed. No surprises.
- Phase 2 (foundation-model 3D lift) has a clear job to do: close F2/F4/F5 specifically.

---

## L1 v2 (after `885b5da`) — TBD

After git pull on Colab + re-run Cell 9, capture:

- [ ] Text orientation: storefronts read normally left-to-right
- [ ] Forward view at ERP center stays forward (sanity check the fix didn't break placement)
- [ ] Same F1–F6 failure modes (these don't change with the mirror fix)
- [ ] Replace the v1 mosaic in this doc with the v2 mosaic once captured
- [ ] If text still mirrored: investigate further (camera-frame convention may differ from
      OpenCV; AV2 docs suggest sensor-frame is `x=forward, z=up` but image projection still
      uses standard OpenCV pinhole — check empirically)

---

## Next steps (priority order)

1. **Verify v2 mirror fix** — quick (1 cell re-run).
2. **Paint ego masks** for the 7 ring cams once a clean v2 frame is in hand. Save as
   `data/mini/ego_masks/<cam_name>.png` (8-bit grayscale, `>127` = keep). Re-run L1
   with `--ego-masks-dir data/mini/ego_masks` to remove hood/body.
3. **Optional**: simple histogram matching across cones to reduce F3. Implement in
   `blending/color_harmonize.py` if visual improvement is worth the complexity.
4. **Move to Phase 2** — D1 decision: Pi3 vs DVGT head-to-head. See plan v2 §4 Phase 2.

L1 is good enough as a published baseline once v2 mirror fix is verified + ego masks
painted. Phase 2 takes ownership of F2/F4/F5.
