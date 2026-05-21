# T9b — ViPE + DAP depth on L1 ERP (partial success)

**Date**: 2026-05-21 ~05:22 UTC
**Job**: `phase3-t9b-vipe-depth-dap-v1` (commit `4ef3755`)
**Wall time**: 138 s end-to-end (76 s SLAM + ~60 s DAP depth + IO)

## What ran

Same panorama-mode SLAM as T9 v2, with two config overrides:
- `pipeline.post.depth_align_model=dap` (enable DAP depth alignment)
- `pipeline.output.save_viz=false` (skip the visualization step that hit the font OSError in T9)

`/content/vipe` install was cached from T9 — no re-clone, no re-pip install. Protobuf 5.28.3 already in place.

## Artifacts produced (Drive `outputs/phase3/t9b_vipe_depth/`)

| File | Size | Purpose |
|---|---|---|
| `depth/l1_erp.zip` | 48 MB | Per-frame depth maps (100 frames) |
| `pose/l1_erp.npz` | — | SLAM camera trajectory |
| `intrinsics/l1_erp.npz` + `intrinsics/l1_erp_camera.txt` | — | Estimated panorama intrinsics |
| `mask/l1_erp.zip` + `mask/l1_erp.txt` | — | Dynamic-object masks (GroundingDINO + SAM + XMem) |
| `rgb/l1_erp.mp4` | — | ViPE-preprocessed RGB |
| `vipe/l1_erp_info.pkl` | — | SLAM state + keyframe indices |
| `run_log_v1.txt` | 29 KB | Full run log |

## Critical caveat — scale estimation skipped on all 100 frames

The DAP depth alignment ran, but ViPE's panorama-mode post-processor emitted this warning for every frame from 0 to 99:

```
vipe.pipeline.panorama - WARNING - Too few valid pixels in pano frame N,
skipping scale estimation.
```

This means: **depth values are *relative* (model output), not *metric* (scale-aligned to SLAM keyframe geometry)**.

Root cause is one of (haven't isolated which):
- ERP frames have too much sky / black region after AV-domain masking, dropping valid-pixel count below threshold.
- DAP did not produce sufficiently dense depth on these specific 1024×2048 ERP frames.
- The panorama-mode scale alignment uses a stricter pixel count threshold than perspective mode (it operates on 4 virtual horizontal pinhole views, each of which loses more pixels after sky/dynamic masking).

## What this means for paper Section 6

**Still net-positive for "downstream consumer demo" narrative**:
- ViPE accepts our L1 ERP and produces pose + intrinsics + relative depth + dynamic masks end-to-end without manual intervention.
- Most downstream consumers of ViPE outputs (GEN3C, Pantheon360 inference) operate on relative depth + their own scale recovery — they do not require ViPE's metric-aligned depth output.
- The narrative "L1 ERP → ViPE → 3D-aware downstream model" holds. The metric-depth claim becomes a separate sub-claim that needs T9c follow-up.

**Still a gap for any quantitative depth-vs-LiDAR claim**:
- If we want to compare ViPE-on-L1-ERP depth against AV2 LiDAR (the way Pi3 was compared in P2.11/P3.3), we need metric depth.
- Workaround: estimate scale post-hoc by least-squares fitting ViPE depth against AV2 ego trajectory (we have ground-truth `city_SE3_egovehicle.feather`) and propagate to depth via SLAM keyframe constraints.

## Follow-up options

| Option | Cost | Value |
|---|---|---|
| **T9c — investigate "too few valid pixels"** | 1-2 d; need to read ViPE panorama post-processor code, identify threshold, possibly adjust mask or virtual-view geometry. | Get metric depth → enables ViPE-vs-LiDAR comparison. |
| **T9d — post-hoc scale fit from ego trajectory** | 0.5 d; fit a single global scale (SLAM is metric-affine up to scale + drift, AV2 ego is ground truth). | Rough metric — good enough for paper figure but not a research contribution. |
| **Accept relative depth, pivot to T11 (GEN3C)** | 0 d; T9b is "done enough" for Section 6. | Move on to next track. |

## Verdict

T9b **succeeded in producing artifacts**, **partially succeeded in metric quality** (relative depth only). For the paper's Section 6 "L1 ERP is consumable by a published downstream system" claim, this is sufficient. For any quantitative depth claim against AV2 LiDAR, T9c is needed.

Recommended path: **accept relative depth for now, move to T11 GEN3C spike** (which is the bigger downstream-consumer hook for paper Section 6).
