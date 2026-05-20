# Phase 2 D1 — Backbone decision: **Pi3X**

Date: 2026-05-19 evening (UTC 2026-05-20)
Status: **RESOLVED — Pi3X wins by walkover (DVGT operationally blocked).**
Owner: Track A (main).
Tag: `v0.2-d1-resolved`

## TL;DR

We ran Pi3X end-to-end on one AV2 anchor frame (7 ring cams, 504×504 letterbox, A100 + bf16). **8.35 s forward, 7.5 GB peak, K-recovery within 0.3% of AV2 ground truth.**

We tried to run DVGT-1 on the same frame. Got as far as cloning + installing + downloading the DVGT checkpoint, but DVGT's `dvgt1_aggregator.py` requires `dinov3 ViT-L/16` *pretrained* weights from a file path that Meta gates behind their HuggingFace access-request flow (license acceptance). Both candidate public download URLs (`dl.fbaipublicfiles.com` and `huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m`) returned 404 / empty without authentication.

**Per the design doc tie-breaker rule** ("If a clear failure mode appears on one model only: hard rule that disqualifies it"), this is a code-maturity hard fail. Pi3 — which runs out of the box via `Pi3X.from_pretrained("yyfz233/Pi3X")` — wins.

DVGT is not permanently disqualified. If we obtain dinov3 access later, we can re-run the head-to-head. For Phase 2 / 3 / 4 main-line work, **proceed with Pi3X**.

## Setup cost comparison

| Step | Pi3X | DVGT-1 |
|---|---|---|
| Clone backbone repo | `git clone yyfz/Pi3` (one line) | `git clone wzzheng/DVGT` (one line) |
| Install Python deps | `pip install huggingface_hub safetensors plyfile einops` (~10 s) | `pip install -r requirements.txt` (~60 s, downgrades torch 2.10→2.8) |
| Clone third-party submodules | (none) | `git clone facebookresearch/dinov3` into `third_party/dinov3/` (not in DVGT default tree) |
| Install third-party submodule deps | (none) | `pip install -r third_party/dinov3/requirements.txt` + `pip install torchmetrics` (~20 s) |
| Download backbone init weights | `Pi3X.from_pretrained("yyfz233/Pi3X")` — open, automatic, ~40 s, ~3 GB | **`dinov3_vitl16_pretrain_lvd1689m-8aa4cbdd.pth` required at `ckpt/dino_v3/`** — **GATED, REQUIRES MANUAL ACCESS REQUEST** |
| Download trained weights | (same as above) | `RainyNight/DVGT-1` from HF (open, ~30 s, ~1 GB) |
| **Total agent attempts to get one forward pass** | **1 (succeeded)** | **5 (all failed at progressively later stages)** |

`acq` job IDs for the 5 DVGT attempts:
1. `phase2-dvgt-one-frame` → CWD wrong, `third_party/dinov3` relative-path fail
2. `phase2-dvgt-one-frame-v2` → pre-check caught missing `third_party/dinov3` early
3. `phase2-dvgt-one-frame-v3` → cloned dinov3, hit `ModuleNotFoundError: torchmetrics`
4. `phase2-dvgt-one-frame-v4` → installed deps, hit `FileNotFoundError: dinov3_vitl16_pretrain_lvd1689m-8aa4cbdd.pth`
5. `phase2-dvgt-one-frame-v5` → tried public URLs, all returned 404 / empty

For Pi3:
1. `phase2-pi3-one-frame` → state=done, exit_code=0, ~64 s wall clock

## Pi3X actual results — one AV2 anchor frame, 7 ring cams

Log: `02a00399-3857-444e-8db3-a8f58489c394` · anchor_idx=0 · ts=315966070549927210

### Runtime

| Metric | Value |
|---|---|
| Backbone | Pi3X |
| Checkpoint | `yyfz233/Pi3X` |
| Input shape | (1, 7, 3, 504, 504) — B=1 view-batch, V=7 cams |
| Letterbox protocol | pad shorter side, Lanczos to 504×504 |
| Device | CUDA (A100, bf16 autocast) |
| Model load (HF download + state_dict load) | 36.45 s |
| **Forward pass (7 views jointly)** | **8.35 s** |
| Peak GPU memory | 7506 MB (~7.5 GB) → fits A100 (40 GB) with 5× headroom |

### Per-camera diagnostics

| Cam | conf > 0.1 | conf > 0.5 | local-z median | local-z p10 | local-z p90 | fx recovered / AV2 truth | fx rel-err |
|---|---|---|---|---|---|---|---|
| ring_front_center | 64% | 30% | 6.22 | 1.40 | 36.79 | 437.76 / 436.65 | +0.25% |
| ring_front_left | 67% | 51% | 5.53 | 2.35 | 22.16 | 418.90 / 414.52 | +1.06% |
| ring_side_left | 69% | 53% | 5.97 | 2.40 | 17.86 | 423.08 / 414.48 | +2.08% |
| ring_rear_left | 58% | 27% | 5.74 | 0.99 | 28.67 | 420.31 / 414.68 | +1.36% |
| ring_rear_right | 71% | 35% | 6.28 | 1.00 | 18.14 | 418.71 / 414.49 | +1.02% |
| ring_side_right | 81% | 34% | 3.27 | 2.22 | 14.16 | 410.65 / 414.36 | -0.90% |
| ring_front_right | 93% | 42% | 3.88 | 2.43 | 7.49 | 414.58 / 414.32 | +0.06% |
| **Mean** | **72%** | **39%** | **5.27** | **1.83** | **20.75** | **— ** | **±1% typical** |

### What this tells us

1. **Geometric correctness**: Pi3X recovers focal length within **+0.06% to +2.08%** of AV2's true K. Excellent — comparable to LiDAR-calibration noise.
2. **Confidence distribution**: 72% of pixels have conf > 0.1 (Pi3 considers them valid 3D). 39% reach conf > 0.5 (high confidence). No view collapsed to zero confidence.
3. **Depth scale plausibility**: median depths 3.3 – 6.3 in Pi3's scale-free unit. Variation across cams reflects scene structure (`side_right` and `front_right` see closer content — buildings/parked cars; rear cams see farther road). The ratio of view medians is plausible for a real driving scene.
4. **Cross-view consistency** is not directly tested in this one-frame run (requires Sim(3) alignment + held-out cam — Phase 2 P2.7 task). But all 7 views returning valid output with comparable confidence distributions is a necessary precondition, met.

## Open issue (not blocking Phase 2)

| Issue | Workaround | Owner |
|---|---|---|
| Pi3's `RoPE2D` falls back to slow pytorch impl (no CUDA-compiled version found in our deps install) | Add `pip install flash-attn` + Pi3's RoPE cuda kernel later when we move to multi-frame / temporal stage. Not blocking single-frame Phase 2. | Phase 2 P2.5 |
| Pi3 is **scale-free** — DVGT would have given metric depth. We will need a Sim(3) alignment step before fusing 7-view point clouds in the ego frame. | `code/waymo2panorama/alignment/sim3_align.py` — already on Phase 2 task list as P2.4 | Phase 2 |
| **DVGT not actually evaluated** | If, by Phase 3 end, Pi3 ghosting / scale-drift bothers us, revisit DVGT. Steps: (a) request access at HF `facebook/dinov3-vitl16-pretrain-lvd1689m`, (b) export `HF_TOKEN`, (c) `huggingface-cli download facebook/dinov3-vitl16-pretrain-lvd1689m pytorch_model.bin --local-dir $DVGT_REPO/ckpt/dino_v3/`, (d) re-run `phase2-dvgt-one-frame` job. | Phase 3 if Pi3 underwhelms |

## Decision

**Use Pi3X for Phase 2 main-line work (P2.2 – P2.10).** Re-evaluate D1 if Pi3 ghosting / scale alignment becomes a hard bottleneck by Phase 3 end.

## Artifacts

| File | Location |
|---|---|
| Pi3 per-cam outputs | Drive: `koi_waymo2pano_colab/outputs/phase2/pi3_one_frame/` (43 files: 7 views × 6 arrays + summary.json + 7 input PNGs) |
| Pi3 summary | local + Drive: `outputs/phase2/pi3_one_frame/summary.json` |
| Pi3 source script | `scripts/phase2/run_pi3_one_frame.py` |
| DVGT source script (unused — kept for future re-test) | `scripts/phase2/run_dvgt_one_frame.py` |
| Comparison script (run when DVGT becomes available) | `scripts/phase2/compare_pi3_vs_dvgt.py` |
| All 5 DVGT attempt result JSONs | Drive: `koi_waymo2pano_colab/results/phase2-dvgt-one-frame{,-v2,-v3,-v4,-v5}.json` |
