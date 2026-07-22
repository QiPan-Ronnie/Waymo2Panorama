# AV2 1+92 Panorama Dataset (v15) — README

**555 dual-version (A/B) panoramic video samples** built from the Argoverse 2 (AV2) Sensor dataset, purpose-built for
fine-tuning / post-training panoramic **video generation models** (e.g. NVIDIA Cosmos).
Production completed 2026-07-17: all 850 usable AV2 logs were judged (val 150 + train 700); every shipped sample passed all quality gates; zero unrecovered failures.

| Split | Samples | Of logs | Pass rate |
|---|---|---|---|
| `val/` | **101** | 150 | 67% |
| `train/` | **454** | 700 | 65% |
| total | **555** | 850 | 65% |

(AV2 Sensor has 1000 logs; its official *test* split of 150 has no 3D annotations, so 850 are usable. Our val/train split follows the official AV2 split — keep it if you want AV2-comparable evaluation.)

---

## 1. What is a sample ("1 + 92")

Each sample is a **93-frame equirectangular (ERP) panoramic video** cut from one AV2 log:

| Frame | Content | Mask |
|---|---|---|
| `fr_0000` | **Perfect 360° anchor frame**: real scene band + FLUX-outpainted sky + FLUX-filled ground blind spot. The only *fully complete* panorama in the sample. Identical in A and B. | `mk_0000` all-white (see §3 caveat) |
| `fr_0001 … fr_0092` | **92 scene-band frames**: the real 7-ring-camera stitch. Sky-top and nadir are black — the cameras physically never see them. | per-frame strict masks |

- Resolution: **2048×1024** (2:1 ERP), 8-bit RGB PNG.
- Temporal: anchors at **20 Hz** → 92 frames ≈ **4.6 s** of driving. The `clip_*.mp4` files are 10 fps *previews only* — train from the PNGs.
- ERP convention: yaw 0° (vehicle forward) at x = W/2; the image **wraps horizontally** (column 0 is adjacent to column 2047).

## 2. Versions A and B — a pixel-aligned controlled pair

The AV2 ego vehicle's white hood/body permanently occludes the bottom-center of every band frame. We ship **two treatments of the same frames** (same log, same window, byte-identical everywhere except the hood region):

- **A — hood removed + real-pixel fill.** The hood region is refilled **only with real pixels** observed at other times: temporal reprojection (Tier-1) + a world-BEV ground map accumulated over the whole log (Tier-2). Anything *no camera ever observed* — e.g. the lower half of a truck parked right next to the ego — stays **honest black**. We never hallucinate content.
- **B — hood region simply blacked out.** Zero fill. Masks black over the hood.

**Why both:** whether "real-filled but slightly soft" or "clean black hole" is the better conditioning signal for a generative model is an empirical question. A and B are pixel-aligned so you can run the comparison with everything else held constant (see §7.8).

## 3. Mask contract ⭐ (read this before training)

Every frame has a mask PNG. Semantics:

> **White (255) = strictly real camera pixels — trustworthy supervision / conditioning.**
> **Black (0) = no trustworthy real pixel here — the generative model's territory.**

Black arises from exactly five sources:

1. Outside the scene band (sky-top / nadir) on band frames;
2. **Honest black** — occluded regions never observed by any camera at any time;
3. **Telea residue (A only)** — tiny leftover holes filled by classical inpainting are *flipped to black* in the mask, so white is 100% real by construction;
4. **γ seam strips** — if a frame has a camera-seam misalignment (seam residual > 8 px; at most 3 such frames per sample), only a **±90 px vertical strip** around that seam is blacked (~1% of the frame). The rest of the frame's real pixels are kept;
5. The whole hood region in **B**.

**Caveat on `fr_0000` / `mk_0000`:** the anchor mask is all-white *by packaging convention*, but its sky and ground blind spot are FLUX-**generated**, not sensor-real. Use `fr_0000` as a *conditioning/appearance reference* (that is what it is designed for). If you need strictly-real supervision on frame 0, intersect it with the band coverage of `fr_0001` as an approximation, or simply exclude frame 0 from the loss.

## 4. Directory layout

```
av2_1plus92_v15/
├── README.md                      ← this file
├── db144_v15_ledger_*.json        ← fleet production ledgers (ALL 850 judged logs, incl. rejects)
├── val/                           ← 101 samples
│   └── <log8>_w1/                 ← log8 = first 8 hex chars of the AV2 log UUID
│       ├── A/
│       │   ├── frames/fr_0000.png … fr_0092.png     (93 frames)
│       │   ├── masks/ mk_0000.png … mk_0092.png     (93 masks, single-channel)
│       │   └── clip_<log8>_A.mp4                    (10 fps preview)
│       ├── B/
│       │   ├── frames/   masks/   clip_<log8>_B.mp4
│       ├── ledger.json            ← this sample's production record (provenance)
│       ├── sample_sheet.jpg       ← anchor + 3 band frames, quick eyeball
│       └── worldmap_m2.png        ← the world-BEV ground map used for the A-fill (debug/provenance)
└── train/                         ← 454 samples, same structure
```

Full AV2 log UUID recovery: `log8` is unique within each split; the full UUID is in the fleet ledgers and in each `ledger.json`-adjacent manifest if you need to join back to AV2 raw data (LiDAR, 3D boxes, HD map).

## 5. Per-sample `ledger.json` (provenance)

| Field | Meaning |
|---|---|
| `window` | `[start, end]` anchor indices of the 93-frame window inside the source log (20 Hz index space) |
| `dmax_m` | max ego displacement within the window (motion gate: ≥ 8 m required) |
| `dirty_frames` | `[[anchor_idx, seam_px], …]` — frames that received γ strips |
| `cascade` | `{t1pct, t2pct, residpct}` — % of the hood region filled by temporal reprojection / world-BEV map / left to Telea (→ masked black) |
| `specmap`, `*_s` | speculative-map stats and per-stage timings |

Dataset-wide fill quality: residual median **6.3%**, p90 **12.2%** (shipping gate: ≤ 15%). The fleet ledgers embed the git commit of the production code — every sample is reproducible.

## 6. Quality gates (what was rejected, so you know what's NOT in here)

Rejected logs are recorded in the fleet ledgers but not shipped:
`SKIP_static` (ego moves < 8 m → hood region physically unfillable), `SKIP_fine_dirty_N` (> 3 seam-dirty frames), `SKIP_resid_gt15` (real fill < 85% of hood region), `SKIP_no_clean_window` (no valid 93-frame window). Consequence: **every shipped sample has ego motion ≥ 8 m and ≥ 85% real fill** — the dataset is biased toward moving, well-observed scenes by design.

---

## 7. How to use this dataset (Cosmos / video-diffusion recipes)

> We don't know your exact training stack, so this section maps the dataset onto the **standard post-training patterns** of Cosmos-style video world models and general video diffusion. Pick what matches your pipeline; the dataset supports all of them. Items marked 🔧 depend on your code.

### 7.1 The task this dataset encodes

The natural formulation: **given a complete 360° first frame + partially-observed panoramic video, generate the complete panoramic video.**

- Condition: `fr_0000` (complete panorama) — appearance/layout anchor;
- Condition (per-frame): band frames `fr_0001..0092` + their masks — the *real* partial observations;
- Target: the full ERP video (the model must keep white regions faithful and invent black regions — sky, nadir, honest-black holes — coherently).

### 7.2 Recipe — Video2World / first-frame conditioning (Cosmos-Predict style)

If your Cosmos variant does Video2World post-training (condition on 1–N input frames, predict the rest):

- Use `fr_0000` as the conditioning frame(s); the 92 band frames are the video continuation target.
- Because the targets are *incomplete* panoramas, combine with the **masked loss** of §7.4 — otherwise the model learns to paint the band's black sky/nadir as literal black.
- 🔧 If your variant only supports fixed frame counts (e.g. 57/121), see §7.7 for temporal resampling.

### 7.3 Recipe — masked-video conditioning (inpainting/outpainting style, recommended)

If you can modify the conditioning channels (ControlNet-style branch, or concat-to-latent like classic inpainting UNets):

- Input per frame: `masked_frame = frame * (mask/255)` **concatenated with the mask** as an extra channel (resized to latent resolution — see §7.5);
- Target per frame: for band frames the only ground truth is the white region → masked loss; frame 0 provides a fully-dense target if you choose to supervise on it (see §3 caveat).
- This teaches the model exactly the deployment condition: "here is what the cameras really saw; complete the panorama."
- **Important:** do *not* rely on black pixels alone to signal "missing" — black is a valid image color. The mask channel is what disambiguates "missing" from "dark object". This is why we ship masks instead of asking you to threshold the frames.

### 7.4 Masked diffusion loss

Standard practice, works with ε-prediction, v-prediction, or flow matching:

```
loss = (pred - target).pow(2)               # per-pixel / per-latent-element
loss = loss * mask_latent                    # only supervise where real
loss = loss.sum() / mask_latent.sum().clamp(min=1)
```

- Black regions receive **no gradient** — the model is free to generate there, constrained only by its prior and the white surroundings. (This is the intended semantics: "the dataset provides only real pixels; generation is the model's job.")
- 🔧 If your trainer cannot do masked loss, fallback: train on **A** (which minimizes black area — median residual 6.3%) and accept a small bias toward black in never-seen regions; do NOT use B in this fallback (B would teach a permanent black hood hole).

### 7.5 Masks in latent space (VAE)

- Downsample masks to latent resolution with **min-pooling or area-then-threshold(<1.0)** — a latent cell touching *any* unreliable pixel should count as unreliable (VAE receptive fields smear black into neighboring pixels).
- Slightly **eroding the white region** (e.g. 8–16 px at image resolution) before downsampling is a cheap way to keep VAE boundary contamination out of the supervised set.
- Encode `masked_frame` (band content with black outside) rather than raw frames when building conditioning latents, so the condition never leaks unreal pixels.

### 7.6 ERP-specific handling

- **Horizontal wrap-around:** column 0 ↔ column 2047 are physically adjacent. If your conv stack doesn't use circular padding, at minimum apply the augmentation below so seams don't imprint at fixed positions.
- **Free augmentation — horizontal roll:** `np.roll(frame, k, axis=1)` (and the mask, same k) is an exact yaw rotation of the panorama. It is the single best augmentation for ERP data; apply per-sample (same k for all 93 frames to stay temporally consistent).
- Do **not** apply horizontal flips casually (it mirrors traffic direction / driving side), and never vertical shifts (breaks the ERP horizon geometry).

### 7.7 Resolution / frame-count adaptation

- Native 2048×1024. Any 2:1 downscale is safe (1024×512, 960×480, …); use area interpolation for frames and **min-based** downsampling for masks (see §7.5 logic).
- Native 93 frames @ 20 Hz. To fit a model expecting T frames: temporal stride (e.g. every 2nd frame → 47 @ 10 Hz), or crop a sub-window; keep frame 0 if you use first-frame conditioning. Frame indices are exact 20 Hz steps — `ledger.json:window` maps them back to log time.

### 7.8 The A/B experiment (why you got two datasets in one)

Train the same recipe twice — once on `A/`, once on `B/` — and compare panorama completion quality (especially in the hood region and around honest-black holes):

- **A** gives denser real supervision but its fill has grazing-angle softness (real, but soft — see §8);
- **B** gives a cleaner "generate everything below" signal but zero supervision in the hood region.
- Everything else (frames, windows, masks outside the hood, anchor frame) is identical, so the comparison is controlled. Whichever conditioning wins becomes the production contract — that decision is intentionally left to your experiments.

### 7.9 Minimal PyTorch loading example

```python
import os, glob, numpy as np, torch
from PIL import Image

class AV2Pano(torch.utils.data.Dataset):
    def __init__(self, root, split="train", version="A", size=(1024, 512)):
        self.dirs = sorted(glob.glob(os.path.join(root, split, "*_w1")))
        self.version, self.size = version, size

    def __len__(self):
        return len(self.dirs)

    def _load(self, path, is_mask):
        im = Image.open(path)
        im = im.resize(self.size, Image.NEAREST if is_mask else Image.BILINEAR)
        a = np.asarray(im, dtype=np.float32)
        return a[None] / 255.0 if is_mask else a.transpose(2, 0, 1) / 127.5 - 1.0

    def __getitem__(self, i):
        d = os.path.join(self.dirs[i], self.version)
        frames = [self._load(os.path.join(d, "frames", f"fr_{k:04d}.png"), False) for k in range(93)]
        masks = [self._load(os.path.join(d, "masks", f"mk_{k:04d}.png"), True) for k in range(93)]
        # NOTE: production code should threshold masks AFTER min-pool downsampling (§7.5);
        # NEAREST here is only for brevity.
        return {
            "video": torch.from_numpy(np.stack(frames)),   # (93, 3, H, W) in [-1, 1]
            "mask": torch.from_numpy(np.stack(masks)),     # (93, 1, H, W) in {0, 1}
            "anchor_is_generated_sky": True,                # §3 caveat for frame 0
        }
```

### 7.10 Pitfalls checklist

- ❌ Don't supervise on black regions (you'd teach the model that skies are black).
- ❌ Don't treat `fr_0000`'s sky/ground as sensor truth (it is FLUX-generated; §3).
- ❌ Don't "fix" the soft hood-fill in A with sharpening — the softness is the real appearance of 4–6° grazing-angle observations.
- ❌ Don't horizontal-flip without thinking (traffic direction), don't break the 2:1 aspect.
- ✅ Do use the mask as an explicit channel, not just as a loss weight, if your architecture allows both.
- ✅ Do keep the val split untouched for eval; it follows the official AV2 split.

---

## 8. Known characteristics (by design, not bugs)

- **Grazing-angle softness** in A's filled hood region: those pixels were genuinely observed at 20–28 m distance and 4–6° grazing angle; softness is what the real signal looks like. No sharpening or generative enhancement was applied — by policy, this dataset never trades realness for looks.
- **Honest black** patches: information that never existed in the sensor data.
- **γ strips**: ≤ 3 frames per sample, ~1% of frame area each, always a vertical strip at a camera-seam yaw.
- Band frames are raw stitches: black sky-top/nadir is expected and correctly masked.

## 9. Provenance

Produced by the Waymo2Panorama pipeline, v15 data contract (driver `db144_v15.py`, render kernel v11, md5 `cca4f0c5`).
Fleet ledgers embed the exact git commit; each `ledger.json` records window, gates, fill provenance and timings — any sample can be regenerated bit-comparably from the AV2 source log.

Questions about the data (mask semantics, provenance, a specific sample's ledger): ask the dataset producers.

---

## 10. Band-only variant (added 2026-07-22)

Each version directory additionally contains a **band-only** twin of the sample — all 93 frames are pure
scene band, no FLUX-completed anchor:

```
<V>/frames_band/fr_0000.png ... fr_0092.png    93 frames
<V>/masks_band/ mk_0000.png ... mk_0092.png
<V>/clip_<log8>_<V>_band.mp4                   10 fps preview, 93 frames
```

- `fr_0001..0092` are byte-identical copies of `<V>/frames/fr_0001..0092` — duplicated so the variant is
  self-contained; switching your loader from `frames/` to `frames_band/` is the only change needed.
- `fr_0000` is a **newly rendered band frame at the window anchor P** (`ledger.json:window[0]`), produced with
  the exact production pipeline:
  - **A**: hood removed + real-pixel fill — Tier-1 temporal reprojection plus the sample's own
    `worldmap_m2.png` world-BEV map; Telea residue flipped black in `mk_0000`. Note: as the *first* frame of
    the window, its Tier-1 share is naturally low and its honest-black area is typically larger than the
    per-sample average — window-start physics, not a defect.
  - **B**: hood region blacked out (EGO_BLACK), zero fill.
  - Unlike the completed anchor of §1 (shared by A and B), the band-only anchors of A and B **differ**
    (filled vs black), consistent with each version's semantics. If the anchor frame itself carries a seam
    flaw, the γ strip rule of §3 is applied to `mk_0000` identically.
- The original `frames/`, `masks/` and mp4 files are untouched; both variants coexist in every sample
  (all 555 samples carry the band-only twin; production ledger: `db151_bandanchor_ledger.json`).
