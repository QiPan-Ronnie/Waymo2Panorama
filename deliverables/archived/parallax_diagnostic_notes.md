# WS4-D6 — Why A2 and B1 both NEG'd. Root-cause diagnostic.

**Date**: 2026-05-26 ~07:00 UTC
**Subject**: Phase 4 production results — neither sparse-stereo displacement (A2) nor disparity-aware graphcut seam (B1) visibly reduces the white halo. Root cause traced to stereo cache coverage, not the methods themselves.

---

## Visual outcome (4 anchors × 3 modes)

See `compare_{000,060,090,150}.png` (compact 3-row stack) and `zoom_{000,060,090,150}.png` (native-res halo region crop).

Across all 4 anchors:
- Plain L1: visible white wash/halo in overlap regions, vertical pinkish streaks in road area, ghost cam content
- A2 (sparse stereo displacement): visually indistinguishable from plain L1 in the halo region
- B1 (graphcut seam): visually similar; halo persists; seam transitions look slightly different but artifact intensity unchanged

**Honest conclusion**: neither method visibly reduces the artifact.

---

## Diagnostic — stereo cache coverage

Anchor 060 stereo `.npz` files:
```
stereo_front_center__front_left      N= 29  depth med=25.4m  max=28m  q90=27m
stereo_front_left__side_left         N=  0  (empty)
stereo_front_right__front_center     N= 57  depth med=16.6m  max=23m
stereo_rear_left__rear_right         N= 27  depth med=16.7m  max=27m
stereo_rear_right__side_right        N=122  depth med= 9.7m  max=29m
stereo_side_left__rear_left          N= 79  depth med=15.1m  max=21m
stereo_side_right__front_right       N=  0  (empty)
```

Total: 5/7 pairs with data, 314 points across all of anchor 060. Depth distribution: median 9-25m, **max 29m, no near-field (<5m) points**.

The white halo is caused by parallax in **near-field** objects (cars 2-4m, walls 3-6m, road surface 2-15m). The stereo cache has no anchors there.

## Diagnostic — A2 anchor spatial distribution

Where the A2 anchors land on the ERP (1024×2048, anchor 060):

```
cam                       N   v_med  v_min v_max   top<300%  horiz%  near>=600%
ring_front_center         86    486   454   506        0     100         0
ring_front_left           29    500   472   506        0     100         0
ring_side_left            79    494   430   520        0     100         0
ring_rear_left           106    495   430   520        0     100         0
ring_rear_right          149    468   362   514        0     100         0
ring_side_right          122    457   362   514        0     100         0
ring_front_right          57    477   454   499        0     100         0
```

**100% of anchors are in the horizon band (v=362-520)**. Zero in sky, zero in near-road (v=600-900) where the halo lives.

## Diagnostic — A2 TPS field outside anchor coverage explodes

For `ring_side_right` (the "busiest" cam), the dense TPS-RBF field after interpolation:

```
zone                                   median  max     q90
whole ERP                              841.25  2087    1770
near road (v=700-900)                  801.54  1970    1723
sky (v=100-300)                        883.46  2071    1822
side_right overlap zone (v=700-900,
        u=1100-1500)                   487.99   734     671
```

These are pixel displacements. **800-2000 px displacement is nonsense** on a 1024×2048 ERP. TPS extrapolates wildly outside the anchor support band.

## Diagnostic — confidence gating saves visual but kills the fix

A2's confidence map (Gaussian decay from nearest anchor, sigma=20px) suppresses the wild extrapolations. Zone-stratified pixel diff plainL1 vs A2 (anchor 060):

```
zone                          A2-MAE  A2-frac>5  A2-max
sky        (v=  0-300)         0.000      0.00%     3.0
horizon    (v=300-600)         6.880     17.46%   254.0   <- where anchors live
near road  (v=600-900)         0.026      0.03%   121.0   <- WHERE HALO LIVES
very low   (v=900-1024)        0.000      0.00%     0.0
```

A2 modifies 17% of pixels in horizon, **0.03% in near road**. The confidence gating wisely refuses to apply garbage extrapolated displacements — but that means A2 architecturally cannot touch the near-road halo.

## Diagnostic — B1 does modify near road but with no real signal

```
zone                          B1-MAE  B1-frac>5  B1-max
sky        (v=  0-300)         0.022      0.00%     1.0
horizon    (v=300-600)         6.516     28.84%   127.0
near road  (v=600-900)         6.389     25.91%   121.0   <- modified, but...
very low   (v=900-1024)        0.000      0.00%     0.0
```

B1 modifies 26% of near-road pixels. But B1's seam routing uses disparity map computed from the same sparse stereo. In near road there's no disparity signal → defaulted to 0 → "zero cost" everywhere → seam picks a random path → halo unchanged.

## Implication

Both methods fail for the **same root cause**: the stereo cache (5/7 pairs, all far-field) cannot inform a fix in the near-field region where parallax actually creates the artifact. The method choice (warp vs seam) is irrelevant — the input data lacks the signal needed.

## Options for WS4-D7 decision gate

**(a) C1 — RAFT dense optical flow between adjacent cams**
- RAFT gives **dense per-pixel** correspondence between cam_A and cam_B images directly (no stereo triangulation). Has data in near-field.
- Integration: project per-pixel flow into ERP via sphere unprojection; build dense displacement field with no extrapolation needed. Substantial new module (~300 LOC), GPU required, 3-5 days.
- Risk: RAFT trained on natural pairs; AV ring cams 60° apart violate small-baseline assumption. Quality TBD.

**(b) L3 — Pi3 forward splat redo**
- Pi3 monocular depth has per-pixel depth including near-field. Forward-splat into ERP using depth.
- Current Pi3 has black-hole and accuracy issues per handoff. Need to: (i) densify splat (multi-pass / Gaussian splat with footprint), (ii) verify Pi3 near-field depth accuracy on AV2, (iii) handle depth contradictions across cams.
- Substantial: 7-14 days. Highest potential payoff (true 3D-aware) but largest engineering risk.

**(c) Re-extract stereo with relaxed matching**
- Try DISK confidence threshold 0.01 instead of default; or LightGlue ratio test loosened; or different detector (SuperPoint) for near-field.
- Unclear if near-field stereo across 60°-apart cams is achievable at all (very large disparities, occlusion, surface deformation).
- 1-2 days investigation. Likely partial win.

**(d) Paper writeup as all-NEG ablation**
- 7 failed attempts (T4 v1/v2/v3, T5 v1/v2/v3, WS4 A2/B1) form a complete ablation. Each fails for principled reasons documented here.
- Story: "we tried these 7 attempts to fix parallax via post-hoc correction; all fail because near-field parallax requires depth-aware reconstruction, which we leave to future work / L3 direction".
- 2-3 days writeup. Most efficient path to publishable artifact.

**Recommendation given current evidence**: (c) is fastest disconfirmation — if relaxed stereo extraction doesn't get near-field points, that's strong evidence the AV2 60°-apart geometry just doesn't support stereo there, and (a) RAFT would suffer the same data problem. If (c) succeeds we get cheap improvement. If (c) fails, (a) RAFT is the next most leveraged investment.

The fundamental engineering tradeoff: do we believe AV ring-cam adjacent geometry can support near-field correspondence at all? If yes → (a) is right. If no → (b) (mono depth) or (d) (writeup) are right.
