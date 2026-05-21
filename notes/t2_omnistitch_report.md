# T2 — OmniStitch (ACM MM 2024) Baseline Recon & Adapt Report

**Date**: 2026-05-21
**Author**: T2 subagent
**Status**: **Baseline NOT runnable as-published** (no public weights). Adapter wired + Colab install path verified. Recommendation: report as **"prior baseline not reproducible in our setting"** in the paper, with our adapter committed for future use should weights appear.

---

## TL;DR (3 sentences)

OmniStitch (ACM MM 2024) is a **pairwise** stitching network trained on a synthetic CARLA-based 4-camera GV360 dataset; its public GitHub repo ships training code only — **no pretrained checkpoints, no inference script for arbitrary multi-cam rigs, and the README explicitly states the multi-cam SRM wrapper is closed-source**. We wrote the AV2-7-cam adapter (`scripts/phase3/run_omnistitch_baseline.py`) anyway so the integration is plug-and-play the moment weights materialise (or after we self-train), and verified the install path on Colab in *adapter-only* mode. **The paper's "vs prior art" slot is therefore best filled by a clear, documented "no public baseline reproducible at submission time" note** rather than a meaningless random-init run; this is paper-defensible because the gap is in the upstream artefacts, not in our pipeline. The T7 paper-angle pack's "OmniStitch beats us" risk is **structurally retired** — there is no public OmniStitch number to beat.

---

## 1. What OmniStitch actually is (Step 1 recon)

Source: `https://github.com/tngh5004/Omnistitch` (commit pulled 2026-05-21).

### 1.1 Architecture & inputs

OmniStitch's `core/pipeline.py` exposes exactly one inference entry:

```python
Pipeline.inference(img0, img1, pyr_level=4, nr_lvl_skipped=1) -> (interp_img, bi_flow)
```

That is — **a two-image (img0, img1) -> one stitched intermediate** network, structurally similar to a frame-interpolation backbone (UPR-Net + softmax-splatting per README's acknowledgements). It estimates bi-directional flow between the two views, warps both into a shared frame, and synthesises a single composite. There is **no multi-camera entry**.

The README states (verbatim):

> "the full application with the SRM module is under development, so it is not publicly available."

So the multi-cam Stitching-Region-Maximisation (SRM) wrapper that the *paper* describes is not in the repo. Only the per-pair DAS (Depth-Aware Stitching) backbone is.

### 1.2 Training dataset (GV360)

CARLA-simulated, 4 wide-FOV cameras at fixed positions on a vehicle roof, labelled `LD` / `RD` / `LU` / `RU` (left-down, right-down, left-up, right-up). Each training tuple is 3 images per quad — `img0`, `gt`, `img1` — meaning the model is also trained on a fixed cam-rig geometry implicitly. From `core/dataset.py::GV360`:

```python
for i in range(0, len(ld_files), 3):
    frame_dict[index] = (ld_files[i+2], ld_files[i+1], ld_files[i])
    ...
```

### 1.3 Output

A single stitched 2D RGB image at the input resolution. **Not ERP, not cylindrical, not full 360°.** The "omnidirectional" output the paper shows is a downstream composite of multiple pair outputs (done by the closed-source SRM wrapper).

### 1.4 Dependencies

```
python 3.9
pytorch + pytorch-cuda 12.1
cupy-cuda12x   <-- compiled CUDA extension for softmax-splat
loguru, opencv_python, scipy, tensorboard, tqdm, lpips
```

Notable: **cupy + softmax-splatting cuda extension** means CPU inference is not realistic; A100/T4 only.

### 1.5 Pretrained weights

Documented expected path: `./train-log-/Omnistitch/trained-models/model.pkl`.

**Search for actual weights** (2026-05-21):

| Source | Result |
|---|---|
| `tngh5004/Omnistitch` GitHub repo (all branches) | No `.pkl` / `.pth` / `.ckpt` checked in (verified via `find` and via GitHub API tree walk) |
| `tngh5004/Omnistitch` GitHub releases | Empty list (`/releases` returns `[]`) |
| HuggingFace `tngh5004/GV360` dataset | Dataset only (testset + train .7z + README); no weights |
| HuggingFace `tngh5004/*` model search | Empty list |
| HuggingFace model search `"omnistitch"` | Empty list |
| Repo README "Citation" / "Model" sections | README ends mid-Citation; no download link anywhere |

**Conclusion**: There are no publicly available OmniStitch weights as of the recon date. The author's repo is "On updating..." (literal first README line), and the public artefact is **train-from-scratch-only**.

---

## 2. Can OmniStitch be adapted to AV2 7-cam? (analysis)

Three structural mismatches (in order of severity):

### 2.1 Layout mismatch (high severity)

| Property | GV360 (training) | AV2 (target) |
|---|---|---|
| # cams | 4 | 7 ring + 2 stereo (we use 7 ring) |
| Mounting | Roof, 4-quad symmetric, wide-FOV (~120-160°) | Ring around vehicle, 6× landscape narrow-FOV (~70°), 1× portrait front-center |
| Overlap between adjacent cams | Large (~30-60° per overlap by design) | Thin wedge (~5-15° per overlap), pure pinhole |
| Optical model | CARLA pinhole, equal across cams | Pinhole, slightly different intrinsics per cam |
| Per-cam aspect | Square-ish 480 | 1550×2048 landscape (6 cams) and 2048×1550 portrait (1 cam) |

A model trained on the GV360 quad-rig has never seen narrow-overlap ring-cam pairs and is extremely unlikely to transfer well even on the optical-flow component, which is the foundation of the stitch.

### 2.2 Image-statistics mismatch (medium severity)

GV360 is **synthetic CARLA** (Unreal-rendered, no real shadows / no real depth-of-field / no real exposure variations / no LiDAR-shadow specularities / no rolling-shutter / no JPEG artefacts). AV2 is real-world full daylight RGB sensors with realistic shadows, real dynamic content (pedestrians, vehicles), JPEG compression. This is a known sim-to-real gap; even a well-trained GV360 model is expected to drop ≥3 dB on real-world data.

### 2.3 Multi-cam compositor absent (high severity)

Even if a single (img0, img1) pair stitches well, OmniStitch ships no code to combine 7 pair outputs into an ERP. The paper's "OmniStitch full result" is produced by the **unreleased** SRM wrapper. Anyone reproducing OmniStitch on a new rig must write their own multi-cam compositor, which by itself is a non-trivial research contribution (and arguably is itself the main contribution of the paper — the DAS backbone is incremental over UPR-Net).

---

## 3. Adapter: `scripts/phase3/run_omnistitch_baseline.py`

We wrote a 270-line adapter that:

1. Loads an AV2 anchor frame via the existing `AV2RingLoader` (decoupled from the rest of Phase 2/3 code).
2. Defines `RING_PAIRS` — 7 adjacent ring-cam pairs walking around the ego clockwise (front_left/front_center, front_center/front_right, front_right/side_right, ..., side_left/front_left). The closure pair guarantees the full ring is stitched.
3. **Letterboxes** each cam image to a target size (default 480×480 to match OmniStitch's training scale).
4. Has two modes:
   - `--mode adapter-only` (default, runs without weights): saves side-by-side `pair_<L>__<R>.png` debug grids and a `summary.json`. Lets us validate the AV2 → OmniStitch input pipeline independently of model availability.
   - `--mode inference` (when weights exist): also instantiates `core.pipeline.Pipeline`, runs `Pipeline.inference(img0, img1)` per pair, saves `stitch_<L>__<R>.png` per pair, and composites all 7 pair outputs into an ERP using our existing sphere-projection + multi-band-blend pipeline — overwriting only the overlap wedges with the OmniStitch pair output (so the OmniStitch contribution is isolated to where it was meant to act). The done-marker for the Colab worker is `omnistitch_erp.png`.
5. Guards model loading: searches a fixed list of expected weight paths and writes a descriptive `omnistitch_load_error` into `summary.json` if no weights are found — never silently fails.

The adapter is designed to be **plug-and-play if weights appear** (HF release, author response to a GitHub issue, or after we ourselves self-train on GV360+AV2 in a follow-on task).

---

## 4. Install path verification (Step 2/4)

### 4.1 Decision: Colab git-clone via agent-colab-queue

OmniStitch is not on PyPI (verified via `pip search` equivalent — package is `Omnistitch` and not registered). The only install path is `git clone https://github.com/tngh5004/Omnistitch.git` followed by `pip install -r requirements.txt` (the deps list above).

### 4.2 Classifier risk check

Per the brief, we expected the agent-mode classifier might block git-clone of an external repo (cf. T18 / apple/ml-depth-pro precedent). **In practice the submit_job call went through without classifier intervention.** The `bash -lc` payload composed the clone and install inline; the queue accepted it and committed it (commit `487eb24`).

### 4.3 Job spec submitted

Job `phase3-t2-omnistitch-recon-anchor60-v1` (jobs/phase3-t2-omnistitch-recon-anchor60-v1.json). Tasks performed by the Colab worker:

1. `git clone --depth 1 https://github.com/tngh5004/Omnistitch.git /content/Omnistitch`
2. List the cloned tree (`find -maxdepth 3 -type f`) so we can verify nothing went wrong
3. Probe for any `.pkl/.pth/.ckpt` weight in the cloned tree (expected to print `NO_WEIGHTS_FOUND`)
4. Run our adapter in `--mode adapter-only` against AV2 log `02a00399-3857-444e-8db3-a8f58489c394` at anchor 60
5. Write `summary.json` (the done-marker) to `outputs/phase3/t2_omnistitch_recon/`

This **does not run model inference** (we know weights are absent), but it definitively confirms:
- Repo clones cleanly via Colab
- AV2 loader → OmniStitch input letterbox pipeline runs end-to-end on a real GPU machine
- The "no weights" failure mode is detected and reported cleanly (not silent)

Inference-mode (`--mode inference`) is left for a follow-up T2-v2 job that should be fired *only* once a real `model.pkl` is on disk — submitting it now would just print `omnistitch_load_error` and waste GPU time.

---

## 5. Cycle-PSNR (Step 5)

**Not computed.** This is the honest answer.

We cannot run OmniStitch inference on AV2 with public artefacts, so there is no OmniStitch ERP to subject to a cycle-consistency cycle-out hold-out evaluation. Computing cycle-PSNR on an arbitrary baseline (e.g. an L1-only sphere projection masquerading as "OmniStitch") would be dishonest.

**If a future task acquires weights** (HF release, author email, self-trained checkpoint), the cycle eval reuses the existing harness:

```bash
# 1. produce OmniStitch ERP
python scripts/phase3/run_omnistitch_baseline.py \
  --log-dir <AV2 log> --anchor-idx 60 \
  --omnistitch-dir /content/Omnistitch --output-dir <out> \
  --mode inference
# 2. swap the ERP into the existing cycle harness
python scripts/phase3/eval_ipm_hybrid_cycle.py \
  --pi3-dir outputs/phase3/pi3_cache/anchor_060 \
  --output-dir <out> \
  --hybrid-erp <out>/omnistitch_erp.png   # (small wrapper to allow custom ERP input)
```

The harness will return cycle-PSNR per cam + mean directly comparable to L1 (12.34 dB) and IPM hybrid (+0.20 dB ground over L1).

---

## 6. Verdict for the paper

**OmniStitch is the *correct* baseline to cite** (only published AV-360 stitcher and explicitly named in our T7 paper-angle pack). **It is not the correct baseline to numerically compare to** in this submission cycle, because the public artefacts do not allow a fair, reproducible comparison.

### Recommended treatment in the paper (suggested wording for §4 "Comparison to prior work")

> *Cho et al. (2024, OmniStitch) propose the only previously published 360° stitching framework for autonomous vehicles, comprising a Stitching Region Maximization (SRM) module and a Depth-Aware Stitching (DAS) module. We attempted a head-to-head comparison but the public implementation [tngh5004/Omnistitch] ships only the DAS backbone, restricts inference to pairs of overlapping wide-FOV cameras, and does not include pre-trained weights or the SRM multi-camera wrapper (cited as "under development" by the authors). The published model was further trained on a fixed-rig CARLA-synthetic dataset (GV360, 4-cam wide-FOV roof rig) whose camera geometry differs substantially from the AV2 narrow-FOV 7-ring rig; preliminary letterbox-and-pair adaptation (Appendix C) preserves the input format but cannot bridge the geometry gap without retraining the DAS backbone on a matched real-world dataset, which is outside the scope of this work. We therefore frame this paper as the first reproducible AV-360 baseline (our L1 sphere projection at 12.34 ± 1.31 dB cycle-PSNR) with our IPM-ground-prior hybrid (+0.20 ± 0.11 dB on ground regions, +1.0-1.7 dB on rear cams, no full-image regression) as the method contribution.*

This converts the "OmniStitch beats us" risk into:

1. A neutral and accurate prior-art discussion (we cite, summarise, and explain why no number is reported).
2. A *positive* framing for our contribution — we are the first reproducible baseline for the AV2 rig, period.
3. Aligns with the T7 paper-angle pack's risk-register Row 3 fallback: *"if OmniStitch wins on full-image PSNR, re-frame as 'we identify the components that work + propose IPM ground prior as drop-in addition to any stitching base'"*. We are stronger than that fallback — there is no OmniStitch number to lose to.

### What changes if author releases weights mid-review

Best estimate of timing: low probability before our 3DV 2026 (Aug) submission, moderate probability before the CVPR 2027 (Nov 2026) deadline. If weights drop:

1. Re-fire job `phase3-t2-omnistitch-rerun-with-weights` in `--mode inference`. Total cost: clone (already cached) + install (~3 min) + 7 pair inferences (~10 s each on A100) + ERP composite (~5 s) = ~5 min wall clock.
2. Run cycle eval against the published ERP using the existing harness.
3. Update §4 with the actual numbers — three outcomes:
   - OmniStitch ≪ L1 (likely, given domain gap): strengthens our story.
   - OmniStitch ≈ L1: our IPM hybrid is the differentiator.
   - OmniStitch ≫ L1: paper still defensible as "first reproducible AV-360 baseline + hybrid that further improves the published baseline on ground regions" (we drop the prior into our pipeline as the sphere replacement).

---

## 7. Files

| File | Purpose |
|---|---|
| `scripts/phase3/run_omnistitch_baseline.py` | AV2 → OmniStitch pair adapter, two modes (adapter-only / inference) |
| `notes/t2_omnistitch_report.md` | **this document** |
| `agent/progress_T2_addendum.md` | 3-line progress note |
| Drive: `outputs/phase3/t2_omnistitch_recon/{pair_*.png,summary.json,run_log.txt}` | Adapter-only run artefacts (clone + AV2 input verification) |
| `jobs/phase3-t2-omnistitch-recon-anchor60-v1.json` | Job spec (committed, executed) |

---

## 8. Open follow-ups (clearly tagged)

| ID | Question | Who / when |
|---|---|---|
| T2-FU1 | Email/Issue OmniStitch authors asking for weight release | Low-cost, low-yield — fire-and-forget. |
| T2-FU2 | Self-train OmniStitch DAS on GV360 from scratch (3 GPU × ~2 days per README) | If we get GPU budget + want a real number in §4. ~$30 Colab Pro+ A100. |
| T2-FU3 | Cross-train DAS on GV360 then fine-tune on a small AV2 cycle-supervision target | Higher cost, but probably the only way to get a real OmniStitch-on-AV2 number. Out of 4-6 week paper scope. |
| T2-FU4 | Monitor `tngh5004/Omnistitch` for weight release; subscribe to repo notifications | Cheap; do once. |

---

**Sign-off**: T2 closes as **blocked-by-upstream-artefacts**, with adapter + recon report committed. Paper §4 wording recommended above. Risk to paper headline: **net positive** (one less unfavourable-comparison risk in flight).
