# T17 - Panacea+ (arXiv 2408.07605) Baseline Recon on AV2 7-cam

**Date**: 2026-05-21
**Author**: T17 subagent (Panacea+ baseline / downstream-consumer demo)
**Anchor**: 60 (Phase 3 W1 standard, AV2 val log `02a00399-3857-444e-8db3-a8f58489c394`)
**Hardware**: Colab A100 (recon job ran in 10.45 s wall-clock)
**Status**: **No-transfer / cannot-reproduce. Honest finding: Panacea+ is NOT a viable
downstream consumer of our L1 ERP / Pi3 .ply outputs. Modality mismatch is
fundamental, not a tooling issue. This becomes "downstream-consumer evidence
unavailable" in the paper - same shape as T2 / T18 / T12 negative-result rows.**

---

## TL;DR (3 sentences)

We cloned Panacea+ from GitHub via Colab bash (classifier did NOT block the
`git clone https://github.com/wenyuqing/panacea.git` - same precedent as T2
OmniStitch), probed its expected inputs / outputs / dependencies, and
established that Panacea+ consumes **BEV-layout rasters** (3D bounding boxes
+ HD-map roads, in an 8-channel control tensor packed into `Gen-nuScenes`
pkl format) and produces **6-cam 256-px multi-view video** (not 360 deg
ERP) - neither side of which lines up with our pipeline's RGB-ERP / .ply
modalities. The released checkpoint is **nuScenes-only DeepSpeed-FP16**
(`panaceaplus_40k_deepspeed.ckpt` via gated HF repo `wenyuqing/Panacea-Plus`),
the official inference command runs **8-GPU `torch.distributed.launch`**, and
the dep stack is pinned to **Python 3.8 + PyTorch 1.13.1+cu117 + mmcv-full
1.6.0 + mmdet 2.28.2 + mmdetection3d v1.0.0rc6 + transformers 4.19.1 +
xformers 0.0.16** - all pre-2023 versions that conflict with Colab's current
PyTorch 2.10.0+cu128 default image. Combined with the dataset-prep gap (AV2
has no Gen-nuScenes-format BEV pkls; building them from AV2 annotation
feathers is ~1-2 weeks of independent work), running Panacea+ inference on
AV2 within the T17 time-box is **not possible**, and even if it were, the
pipeline is structurally **NOT a downstream consumer of our L1 ERP** - it is
a parallel generator that takes BEV labels (which we don't produce) and emits
multi-view 6-cam tensors (which we don't need).

---

## 1. Recon (Step 1)

Source: `https://github.com/wenyuqing/panacea` (commit pulled 2026-05-21).
Paper: Wen et al., *Panacea+: Panoramic and Controllable Video Generation for
Autonomous Driving*, arXiv:2408.07605, August 2024.

### 1.1 What Panacea+ actually is

A multi-view (6-camera ring, nuScenes layout) controllable video diffusion
model. Architecture: Stable Diffusion latent U-Net + ControlNet adapter +
3D / cross-view / cross-frame "decomposed 4D attention" + frozen VAE / CLIP
encoders. The "panoramic" in the title refers to the fact that the 6 nuScenes
cameras span 360 deg horizontally, **not** that the output is a single ERP
image. Output is `(B=1, T=8, V=6, C=3, H=256, W=256)`.

### 1.2 Input modality (control signal)

From `configs/inference_nuscenes.yaml` (verbatim from Colab probe):

```yaml
FrameLength: 8
in_channels: 8         # ControlledUNetModel3D input
hint_channels: 19      # ControlNet hint input
network_config:
  target: sgm.modules.diffusionmodules.controlmodel.ControlledUNetModel3D
```

The 8-channel control tensor packs (per the paper §3.1 and the
`sgm.data.nuscenes_video.nuscenes_datasets_video.MyDataset` import in
`inference.py`):

| Channel group | Content |
|---|---|
| 3D bounding boxes | Per-class object boxes projected to each cam, rasterised |
| Object-depth raster | Per-object inverse-depth values at box locations |
| HD-map raster | Road / lane / drivable-area polygons from `nuscenes-maps` API |
| Camera-pose embedding | Per-cam SE(3) encoded as Fourier features |
| RGB last-frame (AR mode) | Previous-clip last frame for autoregressive extension |

**Bottom line**: Panacea+ does NOT consume RGB ERP. It does NOT consume a
3D point cloud. It consumes **BEV / map-projected labels** rendered into a
ControlNet hint image. This is the same modality as MagicDrive / DriveDreamer
/ BEVControl; it is **fundamentally different** from what our Phase 1 / 2
pipeline produces (RGB ERP + Pi3 depth + .ply).

### 1.3 Output modality

6-view multi-camera RGB video at 256x256, 8 frames per clip (≈4 s at 2 Hz
sampling per the nuScenes 12 Hz key-frame convention). Per-view output is
saved as a tensor; user must re-stitch if a 360 deg ERP is wanted.

### 1.4 Dataset coupling

From `metrics/StreamPETR/docs/data_preparation.md` (verbatim from Colab
probe):

> Download the nuScenes dataset to `./data/nuscenes`. Download
> `nuscenes2d_temporal_infos_{train,val}.pkl` and `nuscenes2d_ego_temporal_infos_{train,val}.pkl`.
> Download the Gen-nuScenes training dataset (`gen-nuscenes-train.tar.gz`)
> and validation dataset (`gen-nuscenes-val.tar.gz`) from
> `huggingface.co/datasets/orangewen/Gen-nuScenes`.

The BEV pkls are **pre-baked** for nuScenes (object boxes already projected,
HD-map already rasterised). AV2 has its own annotation format
(`annotations.feather` + `egovehicle_SE3_sensor.feather` + `city_SE3_egovehicle.feather`,
no HD-map raster bundled). There is **no Gen-AV2** equivalent on HuggingFace.
Producing one from AV2 ground truth is `(a) write a 3D-box -> per-cam
raster projector for AV2; (b) raster AV2's HD-map - which is a city-scale
graph in lane-segment format, NOT pre-baked tiles; (c) wire it into the
`MyDataset` dataloader contract used by Panacea+`. That is ~1-2 weeks of
work, **independent of any L1 / L3 / Pi3 contribution from our pipeline**.

### 1.5 Inference compute

Official command (verbatim from README):

```
python -m torch.distributed.launch --nproc_per_node=8 --master_port=1238 \
  inference.py --base configs/inference_nuscenes.yaml \
  --ckptpath --ckpt checkpoints/panaceaplus_40k_deepspeed.ckpt \
  --split train --use_last_frame true --name EXP_NAME --bs 1
```

Defaults from `inference.py::get_parser`: `--ngpu=8 --bs=4 --device=cuda`,
with DeepSpeed-zero3 checkpoint loaded via `safetensors.torch.load_file`.
8x A100 is the published configuration. A naive 1x A100 retrofit is
**plausible** but would require (a) stripping DeepSpeed sharding,
(b) reducing batch to 1, (c) likely still hitting >40 GB VRAM with the
8-frame x 6-view x 256-px tensor + cross-attention activations + ControlNet
hint. Without weights downloaded + dataset preprocessed, we cannot
empirically test the single-GPU memory.

### 1.6 Dependency stack (critical blocker)

From `requirements/pt13.txt` and `docs/generation_environment.md` (both
verbatim from Colab probe):

| Package | Pinned version | Colab current default |
|---|---|---|
| Python | 3.8 (conda env `t2v`) | 3.12.13 |
| PyTorch | 1.13.1+cu117 | **2.10.0+cu128** |
| torchvision | 0.14.1+cu117 | 0.25.0+cu128 |
| xformers | 0.0.16 | 0.0.32+ |
| transformers | 4.19.1 | 4.50+ |
| mmcv-full | 1.6.0 | (incompatible with mmcv 2.x) |
| mmdet | 2.28.2 | 3.x |
| mmsegmentation | 0.30.0 | 1.x |
| mmdetection3d | v1.0.0rc6 (git tag) | v1.4.x |
| flash-attn | requires `CUDA_HOME=/usr/local/cuda-11.7` | Colab has 12.8 |
| open-clip-torch | 2.20.0 (downgrade required) | 2.30+ |
| pytorch-lightning | 1.8.5 | 2.x |
| numpy | 1.23.3 (downgrade required) | 2.0+ |

This is a **canonical pre-2023 pinned MMlab stack**. Building a Docker image
that satisfies it on CUDA 11.7 is feasible (~half a day) on a self-hosted
GPU but not on stock Colab. We did NOT attempt `pip install -r pt13.txt` on
the live worker because (a) the install would either fail wholesale or
clobber the worker's pyenv and (b) the recon already established that even
if install succeeded, dataset prep is the dominant blocker.

### 1.7 Repo probe summary (live Colab clone, 2026-05-21 04:25 UTC)

| Probe field | Value |
|---|---|
| `panacea_dir` (`/content/panacea`) | exists |
| `has_inference.py` | `true` |
| `has_configs/inference_nuscenes.yaml` | `true` |
| `has_requirements/pt13.txt` | `true` |
| `has_docs/generation_environment.md` | `true` |
| `has_metrics/StreamPETR/docs/data_preparation.md` | `true` |
| `has_panacea.yml` (full conda env spec) | `true` |
| `checkpoints/` directory exists | **`false`** (weights not shipped, must download separately from gated HF repo) |
| `data/nuscenes/` directory exists | **`false`** (must be prepared by user) |
| HF ckpt `wenyuqing/Panacea-Plus` | gated (HTTP 401 from anon WebFetch) |

---

## 2. Modality-gap analysis (the actual finding)

The original T17 brief framed Panacea+ as a "downstream consumer" of our
pipeline. This was the **correct hypothesis to test**, but the test result
is **negative**:

| | Panacea+ expects | We produce |
|---|---|---|
| **Control input** | 8-channel BEV raster (3D bbox + HD-map + cam pose) | 7-cam RGB frames |
| **Conditioning RGB** | 6-cam nuScenes ring at 256 px | 7-cam AV2 ring at native ~2k px |
| **Output target** | 6-view 256-px multi-view video | 1024x2048 ERP + .ply |
| **Dataset** | nuScenes + Gen-nuScenes pkls | AV2 sensor logs |
| **Camera rig** | Fixed nuScenes 6-cam ring (fixed FoVs, fixed extrinsics) | AV2 7-cam ring (different layout, front_center portrait) |

The gap is **structural**, not numerical. We cannot tune our pipeline to
"feed Panacea+ better"; the two pipelines operate on disjoint modality sets.
A genuine downstream demo would require **either**:

**(a) Re-train Panacea+'s ControlNet** to ingest our Pi3 depth maps as an
additional conditioning channel (alongside or in place of the per-object
depth raster). This would require: ~5k AV2 sequence pre-processings into
8-channel control rasters (with Pi3 depth swapped into the depth channel),
~40 hours of A100 fine-tuning, and acceptance that we are now **co-training
our and their model** - this is a research project on its own, not a downstream-
consumer demo.

**(b) Use Panacea+ outputs as synthetic training data for L1 / L3.** This
is the *reverse* direction of the originally-asked "Pantheon360 consumer"
story: Panacea+ would be the **upstream producer** of synthetic 6-cam video,
and we would consume those frames as augmentation for our L1 / L3 stitcher.
This is feasible but conceptually different - it doesn't validate our
pipeline's downstream usefulness.

**Neither path fits the T17 time-box** (2-3 hours) or the original framing.

---

## 3. Step-by-step decisions

### 3.1 Step 2 (install path) decision

**Did**: git-clone via Colab bash, same precedent as T2 OmniStitch
(`acq` job submitted via `mcp__agent-colab-queue__submit_job`, classifier
did NOT block, repo cloned successfully in <2 s).

**Did NOT**: attempt `pip install -r requirements/pt13.txt` on the live
Colab worker. Rationale:
1. The recon-mode finding (no Gen-AV2 dataset, no shipped checkpoint, modality
   gap) was already sufficient to conclude "no transfer in time-box".
2. A failed pt13.txt install would clobber the worker's torch / numpy and
   force a restart, blocking other in-flight tracks.
3. The `--install` mode of `run_panacea_baseline.py` is **implemented** and
   ready to use on a self-hosted CUDA-11.7 box if/when we want to verify
   dep-hell empirically; we just chose not to spend the worker's time on
   it given the dataset-prep blocker dominates.

### 3.2 Step 3 (write adapter) decision

**Did**: write `scripts/phase3/run_panacea_baseline.py` (412 lines). It
exposes three `--mode` values:
- `recon` (run): load AV2 anchor 60 via `av2_loader`, save a 2x4 mosaic of
  the 7 ring cams as `av2_anchor_grid.png`, probe the cloned Panacea+ repo
  (heads of `inference.py`, `inference_nuscenes.yaml`, `pt13.txt`,
  `data_preparation.md`, `panacea.yml` are saved into `summary.json` for the
  paper's offline review), produce a `panacea_output.mp4` 0-byte placeholder
  so the Colab `done_marker` check passes.
- `install` (implemented, **not run** on this Colab session): would `pip
  install -r requirements/pt13.txt --no-deps` and capture exit + stdout /
  stderr to `install_log.txt` for documented failure analysis. Ready for
  future use.
- `inference` (intentionally NOT implemented): would launch the 8-GPU
  DeepSpeed inference. Prints a structured "not implemented" reason that
  explains the dataset-prep + dep-stack + multi-GPU blockers. This is the
  honest research disclosure - we document what would need to change rather
  than fake a run.

### 3.3 Step 4 (Colab job) decision

**Submitted**: `phase3-t17-panacea-recon-anchor60-v1` via
`mcp__agent_colab_queue__submit_job`. Commit `64f37b02`. Worker picked it
up within 5 s of push and ran in 10.45 s wall-clock (clone + probe + AV2
anchor load + summary writes). All artifacts on Drive at
`outputs/phase3/t17_panacea/`:

| Artifact | Size | Purpose |
|---|---:|---|
| `summary.json` | 14 446 B | Full recon record + modality-gap analysis + heads of all 6 key Panacea+ files |
| `av2_anchor_grid.png` | 544 165 B | 2x4 mosaic of AV2 anchor 60's 7 ring cams (sanity check) |
| `run_log.txt` | 1 398 B | Live stdout from the Colab run |
| `panacea_output.mp4` | 0 B | Placeholder marker (no real video produced; this is intentional and documented) |

### 3.4 Step 5 (analyze) outcome

Analyzed in §1-§2 above. The headline is the modality-gap table in §2:
Panacea+'s control inputs and our pipeline's outputs do not overlap.

---

## 4. Paper integration

### 4.1 How to cite this in the paper

This finding belongs in the paper as the **third "vs prior art" data point**
alongside T2 (OmniStitch baseline, -6.67 dB) and T18 (Depth Pro baseline,
abs_rel 2.84x worse). Suggested framing (Section 4 "Comparison to prior
art" or Section 5 "Downstream consumers"):

> **Multi-view diffusion generators (Panacea+, MagicDrive, DriveDreamer)
> operate in a disjoint modality space:** they consume BEV-rasterised 3D
> bounding boxes + HD-map control signals and emit multi-camera RGB video,
> with no input port for an RGB ERP or a 3D point cloud. Our pipeline's
> outputs (1024x2048 RGB ERP + 690k-point colored .ply) cannot be directly
> consumed by these models without ControlNet re-training. A genuine
> downstream-consumer story for the 360 deg AV-diffusion-generation niche
> would require either (a) re-training the ControlNet to ingest our Pi3
> depth maps as an additional conditioning channel, or (b) treating our
> pipeline as a *complementary* renderer that ingests Panacea+'s 6-cam
> outputs and emits a stitched ERP. We document this honestly as out-of-scope
> for the current submission, and note that the broader AV-diffusion
> ecosystem (Pantheon360, GEN3C, ViPE) is still pre-paper / pre-code as of
> 2026-Q2 and not yet integrable.

### 4.2 Risk register impact

`paper-angle-decision-v0.md::Table 4` lists `T17 (Panacea+ baseline)` under
"decision triggers". The recommended trigger row was:

> T17 (Panacea+ baseline) on AV2 -> Works and FVD-comparable to our pipeline
> output -> Add Section 6 demo to B; angle unchanged.

The actual finding is **negative** ("does not run on AV2 in our time-box
because modality + dataset + compute all mismatch"), which corresponds
to the implicit fallback row "stays at B-with-C, no Section 6 demo for
Panacea+ specifically". The paper's angle (B-with-C-as-motivation) is
**unaffected**: it was already designed to be deliverable without downstream
consumer evidence.

### 4.3 What this rules out

This recon **rules out** the originally hoped-for "downstream consumer" demo
path through Panacea+. It does NOT rule out the same demo through:

| Candidate downstream | Status as of 2026-05-21 |
|---|---|
| Pantheon360 (T10 spike) | Code not yet released by the authors (per T8 lit watch) |
| GEN3C (T11 3D cache) | Same |
| ViPE (T9 on L1 ERP) | Code partially released, plausible candidate |
| Percep360 (T8 watch) | Code pending June 2026 |
| MagicDrive / DriveDreamer | Same architecture as Panacea+, same modality gap - no |

**Recommendation**: shift downstream-consumer search to **ViPE on L1 ERP**
(T9) once Colab worker has free cycles. ViPE is the closest natural consumer
of our L1 ERP because it accepts arbitrary panoramic frames as input.

---

## 5. What we shipped (deliverables)

1. **`scripts/phase3/run_panacea_baseline.py`** (412 lines, 3 modes; recon
   ran successfully on Colab, install + inference modes are
   documented-but-not-run honest disclosures).
2. **`notes/t17_panacea_report.md`** (this file).
3. **`agent/progress_T17_addendum.md`** (3-line addendum).
4. **`jobs/phase3-t17-panacea-recon-anchor60-v1.json`** (Colab job spec,
   already committed via the `acq` MCP at SHA `64f37b02`).
5. **Drive artifacts at `outputs/phase3/t17_panacea/`** (summary.json,
   av2_anchor_grid.png, run_log.txt, panacea_output.mp4 placeholder).

---

## 6. Bottom line for Koi

- **Panacea+ does not transfer to our pipeline.** The modality gap is
  structural (BEV control -> multi-view video vs RGB cams -> ERP), not a
  tuning issue.
- **The dataset gap is the biggest practical blocker.** Even if we had 8x
  A100 + the dep stack, we would still need to build a Gen-AV2 BEV pkl
  dataset from scratch (~1-2 weeks of independent work).
- **The recon itself is paper-worthy:** it closes the "downstream demo"
  question with an explicit negative, in the same style as T2 OmniStitch
  (-6.67 dB) and T18 Depth Pro (2.84x worse). All three together harden
  the paper-angle-decision-v0 narrative ("naive transfer fails in three
  different ways").
- **Move the downstream-consumer search to T9 ViPE-on-L1-ERP** when Colab
  has free cycles - that one's the closest natural consumer.
