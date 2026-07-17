# AV2 1+92 Panorama Dataset (v15) — README

> Repo copy of the dataset README shipped at Drive `koi_waymo2pano_colab/datasets/av2_1plus92_v15/README.md`
> (file id `1KFGL4b7VdQ_hZ2idnvPJvUR34cH0Atab`, uploaded 2026-07-17).

**555 dual-version (A/B) panoramic video samples** built from the Argoverse 2 Sensor dataset,
for fine-tuning panoramic video generation models (NVIDIA Cosmos).
Production complete 2026-07-17: all 850 usable AV2 logs judged (val 150 + train 700), zero unrecovered failures.

- **val**: 101 samples (of 150 logs, 67% pass)
- **train**: 454 samples (of 700 logs, 65% pass)
- Source: AV2 Sensor has 1000 logs; the 150-log *test* split has no 3D annotations, so 850 are usable.

---

## 1. What is a sample ("1+92")

Each sample is a **93-frame equirectangular (ERP) panoramic video** from one AV2 log:

| Frame | Content |
|---|---|
| `fr_0000` | **Perfect 360° anchor frame** — scene band + FLUX-outpainted sky + FLUX-filled ground blind spot. Fully complete panorama. Identical in A and B. |
| `fr_0001 … fr_0092` | **92 scene-band frames** — the real multi-camera stitch (7 ring cameras). Sky top and nadir are black (cameras never see them). 20 Hz anchors → 92 frames ≈ 4.6 s of driving. |

Resolution **2048×1024** PNG. Preview mp4 at 10 fps.

## 2. Versions A and B (pixel-aligned pair, both shipped for every sample)

The AV2 ego vehicle's white hood/body permanently occludes part of every frame. We ship two treatments:

- **A — hood removed + real-pixel fill**: the hood region is refilled **only with real pixels** observed at other times (temporal reprojection + a world-BEV ground map accumulated over the whole log). Regions that *no camera ever saw* (e.g. the lower half of a close-parked truck the hood used to cover) stay **honest black** — we never hallucinate.
- **B — hood region blacked out**: zero fill. Same window, same frames, masks pixel-aligned with A.

Why both: whether "real fill" or "leave black" is the better conditioning for generative fine-tuning is an
experimental question the data cannot answer — train on both and compare.

## 3. Mask contract (the most important part)

Every frame has a mask. **White (255) = strictly real camera pixels. Black (0) = no trustworthy real pixel → the generative model's responsibility.**

Black regions come from exactly these sources:
1. Outside the scene band (sky top / nadir) on band frames;
2. Never-observed occlusions (honest black, see §2);
3. Telea-inpainted residual pixels in version A (interpolated → flipped to black so white stays 100% real);
4. **γ seam strips**: if a frame has a camera-seam misalignment (max residual > 8 px, ≤3 such frames per window), only a ±90 px vertical strip around that seam is blacked — the other 99% of real pixels are kept;
5. The whole hood region in version B.

`mk_0000` (anchor frame) is all-white by convention: the anchor is fully completed imagery intended as a clean conditioning frame (its sky/ground are FLUX-generated, not sensor-real — treat it as the model's visual target, not as ground-truth supervision).

Recommended use: treat the mask as a **loss / conditioning-validity mask** — supervise only where white; let the model generate freely where black.

## 4. Directory layout

```
av2_1plus92_v15/
├── README.md                      (this file)
├── db144_v15_ledger_*.json        (production ledgers, all judged logs incl. rejects)
├── val/
│   └── <log8>_w1/                 (log8 = first 8 chars of the AV2 log UUID)
│       ├── A/
│       │   ├── frames/fr_0000.png … fr_0092.png
│       │   ├── masks/ mk_0000.png … mk_0092.png
│       │   └── clip_<log8>_A.mp4
│       ├── B/
│       │   ├── frames/  masks/  clip_<log8>_B.mp4
│       ├── ledger.json            (this sample's production record)
│       ├── sample_sheet.jpg       (quick visual: anchor + 3 band frames)
│       └── worldmap_m2.png        (the world-BEV ground map used for fill, provenance/debug)
└── train/
    └── <log8>_w1/                 (same structure)
```

## 5. Per-sample `ledger.json` (provenance)

Key fields: `window` = [start, end] anchor indices of the 93-frame window inside the log (20 Hz);
`dmax_m` = max ego displacement inside the window (motion gate ≥ 8 m);
`dirty_frames` = [[anchor, seam_px], …] frames that received γ strips;
`cascade` = {t1pct, t2pct, residpct} fill provenance: % of hood-region pixels filled by temporal reprojection (Tier1), world-BEV map (Tier2), and Telea residue (masked black);
`specmap`, `*_s` timings, and the fleet ledgers embed the git commit of the production code.

Dataset-wide quality: residual median **6.3%**, p90 **12.2%** (gate: ≤15%).

## 6. Quality gates (what was rejected and why)

Logs failing any gate were **not shipped** (they are recorded in the fleet ledgers):
- `SKIP_static` — ego barely moves (dmax < 8 m): the hood region physically has no alternate-time observation → unfillable;
- `SKIP_fine_dirty_N` — more than 3 frames with seam residual > 8 px;
- `SKIP_resid_gt15` — real fill covers < 85% of the hood region;
- `SKIP_no_clean_window` — no valid 93-frame window.

## 7. Known characteristics (by design, not bugs)

- **Grazing-angle softness** in the filled hood region (A): those pixels were really observed at 20–28 m
  distance at a 4–6° grazing angle — the softness is what the real data looks like. No sharpening/generation applied.
- **Honest black** patches: information that never existed. Do not treat as errors.
- **γ strips**: ≤3 frames per sample, ~1% of frame area each.
- Band frames are raw stitches: sky/nadir black is expected and masked accordingly.

## 8. Contact / provenance

Produced by the Waymo2Panorama pipeline (v15 data contract, driver `db144_v15.py`, kernel v11).
Every sample is reproducible from its ledger (git commit + window + gates).
