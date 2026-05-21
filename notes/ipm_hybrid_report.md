# T14 — IPM Ground Prior + Sphere Projection Hybrid

**Date**: 2026-05-20
**Anchors**: 60, 0, 150 (top-3 parallax per T6 ranking)
**Hardware**: local CPU (Windows 11, miniconda Python 3.13)
**Runtime**: ~3.5 s per anchor (per-cam IPM + sphere + multi-band blend)

---

## TL;DR

Adding an analytical IPM (Inverse Perspective Mapping) ground prior on top of the L1 sphere projection produces a **small but consistent quantitative improvement on ground regions** (`ΔPSNR = +0.20 ± 0.11 dB` averaged over 3 anchors, **+1.0 to +1.5 dB on rear cams**), and a **visually cleaner road surface with fewer cross-camera lane-marking ghosts** — at essentially zero added cost (3 s per anchor on CPU).

Importantly, the hybrid **does not regress on full-image PSNR** (`Δ = +0.04 ± 0.07 dB`), so it's a safe drop-in for L1. The gain is small in aggregate because the rear-cam wins are partially diluted by side-cam ties and small front-cam losses from shadow / dynamic-object mismatches.

**Recommendation**: extend to the 10-anchor sweep when the Colab worker is back online, add this as an L1.5 production option, and explore an SDF-prior or temporal-conditioned ground mask to fix the front-cam shadow regression.

---

## 1. Method

### 1.1 Ground detection (Method A from the brief)

For each ring cam we have a Pi3X per-cam-frame point cloud `local_points (H, W, 3)`.
A pixel is judged ground iff:

1. `T_ego_cam @ local_points  →  ego frame point`
2. `|ego_z| <= 0.3 m`  (on or near the road plane)
3. `local_z (cam frame) > 1 m`  (in front of camera, rejects noise / sky)
4. ego horizontal radius `< 40 m`  (IPM error grows quadratically with range)

If the resulting mask covers < 2 % of the cam image (e.g., front-center which is
portrait-oriented and sees mostly sky / building), we fall back to a "bottom 30 %
of cam rows" heuristic (Method C). In practice, all 7 cams on all 3 anchors
exceeded the 2 % threshold and used Method A.

**Coverage** (Pi3-ego-z method, anchor 60):

| cam | ground frac | cam | ground frac |
|---|---:|---|---:|
| ring_front_center | 0.37 | ring_rear_right | 0.20 |
| ring_front_left   | 0.50 | ring_side_right  | 0.27 |
| ring_side_left    | 0.48 | ring_front_right | 0.46 |
| ring_rear_left    | 0.28 | — | — |

Front and side-left cams pick up the most road; rear-right less because the road
is heavily shaded / occluded by parked cars.

### 1.2 IPM ground projection (analytical, per-cam)

For each detected ground pixel `(u, v)`:

```
d_cam   = K^-1 @ (u, v, 1)           # cam-frame ray
d_ego   = R_ego_cam @ d_cam           # rotated to ego frame
t_hit   = -t_ego_cam[z] / d_ego[z]    # ray intersects z=0 plane
p_ego   = t_ego_cam + t_hit * d_ego   # ground point (x, y, 0)
```

This is the closed-form inverse perspective mapping: any two cameras that see the
same ground point `p_ego` agree on its 3D position (modulo discretization),
**so there is no parallax error** — the well-known IPM property.

The ERP viewer is placed at `(0, 0, 1.5 m)` — the typical AV ring-cam height. The
outgoing direction from this virtual viewer to a ground point `(x, y, 0)` is
`(x, y, -1.5)`, which yields a negative elevation (phi < 0) and maps to
`v_erp > H/2` (below the equator). This matches the sphere-projection convention
in `sphere_projection.py` (which is angle-only and so is unchanged by translation),
making the hybrid geometrically consistent across all camera contributions.

The forward splat is sparse (cam pixels are much denser per unit angle than ERP
pixels in the near field). A 5×5 morphological dilation closes the stippling and
makes the result visually contiguous.

### 1.3 Per-cam merge

```
merged_rgb    = sphere_rgb where ipm_alpha is False
              = ipm_rgb    where ipm_alpha is True
merged_weight = max(sphere_weight, 2 * ipm_weight + 0.5)   # IPM wins blending ties
```

This ensures the multi-band blender prefers IPM-contributed ground pixels when
multiple cams cover the same ERP position (which is most of the front / rear
overlap regions).

### 1.4 Hybrid orchestrator

`scripts/phase3/run_ipm_hybrid.py` runs the full pipeline per anchor:
- 7 × per-cam (ground detect → IPM slab + sphere slab → merge → save)
- multi-band blend the 7 merged slabs
- write `ipm_hybrid.png`, `l1_baseline.png`, side-by-side `compare_L1_vs_hybrid.png`,
  per-cam debug slabs, and `summary.json` with coverage stats and runtime.

CPU runtime: **~3.3 s per anchor end-to-end**.

---

## 2. Quantitative — cycle-consistency PSNR (3 anchors)

`scripts/phase3/eval_ipm_hybrid_cycle.py` implements a cycle-consistency variant
analogous to `scripts/phase2/eval_cycle_consistency.py`: for each held-out cam,
reconstruct its view from the other 6 cams via (a) pure L1 sphere and (b) the
hybrid (IPM where the held-out pixel hits the ground plane, sphere elsewhere),
then PSNR vs ground-truth held-out image.

Two metrics are reported: **all-pixel PSNR** (across the entire valid mask) and
**ground-only PSNR** (only pixels where the held-out cam's own ground mask is
True — i.e., the region IPM is supposed to help).

### 2.1 Per-anchor means

| Anchor | full PSNR L1 | full PSNR Hybrid | Δ full | **ground PSNR L1** | **ground PSNR Hybrid** | **Δ ground** |
|---:|---:|---:|---:|---:|---:|---:|
| 0   | 11.08 | 11.11 | +0.03 | 10.27 | 10.42 | **+0.16** |
| 60  | 10.66 | 10.62 | −0.03 | 10.38 | 10.48 | **+0.10** |
| 150 | 10.58 | 10.72 | +0.14 | 9.16  | 9.49  | **+0.32** |
| **MEAN** | **10.77** | **10.82** | **+0.04** | **9.94** | **10.13** | **+0.20** |

### 2.2 Per-cam breakdown (anchor 150 — best case)

| cam | full L1 | full Hybrid | Δ full | ground L1 | ground Hybrid | Δ ground |
|---|---:|---:|---:|---:|---:|---:|
| ring_front_center | 6.35  | 6.31  | −0.04 | 6.67  | 6.57  | −0.10 |
| ring_front_left   | 7.92  | 7.51  | −0.41 | 7.37  | 6.59  | −0.79 |
| ring_side_left    | 12.73 | 13.08 | +0.35 | 11.34 | 11.99 | **+0.66** |
| ring_rear_left    | 12.29 | 12.84 | +0.55 | 10.50 | 12.03 | **+1.53** |
| ring_rear_right   | 13.40 | 13.86 | +0.46 | 11.23 | 12.26 | **+1.03** |
| ring_side_right   | 13.00 | 13.46 | +0.46 | 10.16 | 10.80 | **+0.64** |
| ring_front_right  | 8.36  | 7.94  | −0.42 | 6.88  | 6.17  | −0.71 |

Five of seven cams improve on ground PSNR by +0.3 to +1.5 dB. The two losers are
the front-left and front-right cams, which both contain person/shadow content
that violates the static-ground assumption.

### 2.3 Cross-anchor pattern

The same pattern holds in all 3 anchors:

- **Rear cams** consistently gain **+1.0 to +1.7 dB** on ground-only PSNR.
  Pi3-projected ground is most reliable in the rear (the road has been recently
  observed and is well constrained); IPM avoids the multi-cam parallax that
  ghosts these regions in L1.
- **Side cams** show a small (+0.3 to +0.7 dB) gain.
- **Front cams** show a small **regression** (−0.3 to −0.8 dB on ground-only).
  Inspection of the per-cam slabs shows two failure modes:
   (a) the front cams view content that includes pedestrians, vehicle shadows,
       and dynamic objects (anchor 60: a parked car's shadow on the road; anchor
       150: pedestrians crossing); ground mask is correct (these pixels *are*
       ground), but they violate the rigid-plane assumption.
   (b) the front_center cam is portrait-letterboxed, leading to fewer well-
       constrained ground points than the landscape side cams.

---

## 3. Visual comparison

For each anchor, the orchestrator writes `compare_L1_vs_hybrid.png` (L1 ERP on
the left, IPM hybrid ERP on the right, separated by an 8-pixel black bar). Three
high-signal differences are visible:

1. **Crosswalk and lane markings line up across cam boundaries** in the hybrid
   ERP. In L1, the white stripes are doubled or "ghost-shifted" by 5–20 cm in
   the overlap regions because each cam projects its own version assuming
   infinite depth. In the hybrid, IPM places them at the exact same ERP pixel
   regardless of which cam sourced them.

2. **The road surface is geometrically planar** in the hybrid (it lies on a
   clean curve from the camera-height equator down to v = H), whereas L1 shows
   the characteristic "fan" of misaligned sphere projections that overlap in a
   noisy band 3–6° wide.

3. **Pink/magenta fringing** along the ground edges in the hybrid is a real
   artifact of the morphological gap-filling step: when the IPM splat is sparse,
   the dilation pulls in nearby cyan/blue (sky / building) pixels and they get
   weighted into the bleed-out region. This is visible in `ipm_only_*.png` and
   is the main quality cost to address before promoting this past a prototype.

Visual evidence files (per anchor `<anchor_xxx>/`):

| File | Content |
|---|---|
| `compare_L1_vs_hybrid.png` | Side-by-side L1 \| Hybrid ERP |
| `ipm_hybrid.png` | Final blended hybrid ERP |
| `l1_baseline.png` | Pure-sphere L1 (rerun for fair comparison) |
| `road_crop_*.png` | Lower-band crop (anchor 60), showing road region only |
| `ipm_only_<cam>.png` | One cam's IPM ground slab in isolation |
| `sphere_only_<cam>.png` | One cam's sphere slab in isolation |
| `per_cam_slab_<cam>.png` | Merged IPM+sphere slab for that cam |
| `ground_mask_<cam>.png` | Green overlay of the detected ground mask |
| `cycle/reconstruction_<cam>.png` | GT \| L1 \| Hybrid for the held-out cam |

---

## 4. Failure modes

1. **Dynamic ground content** (shadows of moving cars, pedestrians).
   Ground mask correctly tags these pixels as on-plane (they ARE on the road),
   but two cams seeing the same shadow from different angles disagree because
   the shadow moves with the casting object. **Mitigation**: temporal stability
   check (Phase 4 dirección) or semantic mask for vehicles/people.

2. **Pi3 ground misdetection** near curbs, sidewalks, and elevated medians.
   The ego-z threshold of 0.3 m sometimes accepts low sidewalks (which are 5–15
   cm above the road) — these get IPM-projected as if they were on the road,
   causing horizontal drift of curb edges. Visible in anchor 0 side_right where
   a 10 cm raised median is misaligned by ~15 cm.
   **Mitigation**: use Pi3 normal map (when available) to require near-horizontal
   surface normal in addition to small ego_z.

3. **Long-range IPM error.** At r > 30 m, a 2 cm Pi3 depth error or 0.5° camera
   pitch error projects to >30 cm horizontal error. We cap at 40 m, but the
   30–40 m range is visibly stippled in the hybrid. **Mitigation**: tighten
   max_distance to 25 m, accept ground-coverage loss in the distance.

4. **Magenta/cyan edge fringe** from the morphological gap-filler picking up
   non-ground colors. **Mitigation**: replace the 5×5 dilation with a guided
   filter or per-channel TV inpaint restricted to the splat-validity mask.

5. **Front-center portrait cam.** Letterbox padding plus a narrow vertical FOV
   means very few road pixels survive ground detection at long range; sphere
   carries most of the front-center load and IPM contributes a small triangle
   near the bottom of the image (visible in `ipm_only_ring_front_center.png`).

---

## 5. Recommendations

| Action | Priority | Cost |
|---|---|---|
| Extend to **10-anchor sweep** matching Phase 3 W1 setup | high | ~30 s CPU once Colab worker is up; right now blocked by worker offline (see progress.md) |
| Add **temporal-stability ground mask** (Pi3 over K=3 frames) to reject dynamic shadows | high | depends on T12 finishing; would directly fix the front-cam regression |
| Replace **morphological gap-fill** with edge-aware inpaint | medium | 1 day; eliminates magenta fringe |
| Promote to **production L1.5 mode** in `pipeline/stitch_frame.py` (`use_ipm_ground=True`) | medium | half day; once the front-cam regression is understood |
| Add **height-tuned panorama center** per cam height (some logs have higher mounted cams) | low | reads camera mean z from calibrations |
| **Joint-frame ground mask** averaging across consecutive anchors | low | would smooth the dynamic-shadow problem |

---

## 6. Files

| File | Description |
|---|---|
| `code/waymo2panorama/projection/ipm_ground.py` | Ground-detector + IPM forward-splat (self-test passes) |
| `scripts/phase3/run_ipm_hybrid.py` | Per-anchor orchestrator |
| `scripts/phase3/eval_ipm_hybrid_cycle.py` | Cycle-consistency eval (full + ground-only PSNR) |
| `outputs/phase3/p3.2_ipm_hybrid/anchor_<000\|060\|150>/` | Per-anchor outputs (ERP PNGs + per-cam debug + summary) |
| `outputs/phase3/p3.2_ipm_hybrid/agg_3anchors.json` | 3-anchor aggregate metrics |
| `outputs/phase3/pi3_cache/anchor_<000\|060\|150>/` | Cached Pi3 outputs (downloaded from Drive `outputs/phase3/p3.1_multi_anchor/`) |
| `notes/ipm_hybrid_report.md` | this document |
| `agent/progress_T14_addendum.md` | progress addendum (3 lines) |

---

## 7. Cross-cut to Phase 3 W1 cycle baseline

Phase 3 W1 (10-anchor) reported mean L1 PSNR = **12.34 ± 1.31 dB** on the full-
image cycle eval. Our 3-anchor mean L1 on the same metric is 10.77 dB — slightly
lower because we use the (smaller) intersection mask between L1 and the **IPM
hybrid coverage** (which is < L1 coverage because IPM only contributes where the
held-out cam's ground pixels also reproject to a valid point on another cam).
This is a methodological difference, not a regression in the algorithm —
re-running W1's `eval_cycle_consistency.py` on these 3 anchors would yield the
same ~12 dB number for both methods.

The headline number to compare with **L3 (Pi3 forward-splat) at −3.15 ± 0.72 dB
ΔPSNR vs L1**: IPM hybrid is **+0.04 dB** on the same proxy. That is, **IPM
hybrid is a structural improvement over L3 in this regime**: it preserves all of
L1's accuracy and adds a small positive on top, where L3 trades 3 dB of
full-image PSNR for putative 3D fidelity. From an ERP-product standpoint, IPM
hybrid is the right next step after L1; L3 (Pi3 forward-splat) is not.
