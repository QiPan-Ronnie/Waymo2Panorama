# 新-F VGGT 3rd Backbone — Research Report

**Explore agent**: a45ef8f (2026-05-21)
**Target**: integrate VGGT as 3rd depth backbone (after Pi3, Depth Pro) for L3 forward-splat
**Goal**: 加固 paper Section 4 NEG #3 "algorithm not backbone" 论据 — 从 2 个 backbone fail → 3 个

---

## §1 VGGT 是什么 (Verified Available ✓)

- **Paper**: VGGT: Visual Geometry Grounded Transformer ([arXiv 2503.11651](https://arxiv.org/abs/2503.11651))
- **Authors**: Meta + Oxford (Wang, Chen, Karaev, Vedaldi, Rupprecht, Novotny)
- **Award**: **CVPR 2025 Best Paper**
- **GitHub**: [facebookresearch/vggt](https://github.com/facebookresearch/vggt) — public code + weights
- **Status**: Production-ready, no scoop / availability risk

**Architecture**: 24-layer feed-forward transformer w/ alternating attention (even = frame-self, odd = global-cross-frame). Permutation-equivariant except for reference frame #0. DINO tokenization. Trained on undisclosed public 3D-annotated datasets.

**Outputs per frame**: camera params (9D), depth map (H, W) float32, point map (3, H, W), dense tracking features.

**Multi-view native**: "one, a few, or hundreds of views" in single forward — matches our 7-cam ring use case.

---

## §2 API & Install

**Install on Colab (Python 3.12 / CUDA 13 / A100, ~5-8 min)**:
```bash
git clone https://github.com/facebookresearch/vggt
cd vggt
pip install -r requirements.txt
pip install -e .
# First inference auto-downloads ~4 GB model.safetensors from HF
```

**Checkpoints**:
- `facebook/VGGT-1B` (non-commercial)
- `facebook/VGGT-1B-Commercial` (excludes military) — use this for paper safety

**Inference example**:
```python
import torch
from vggt.models.vggt import VGGT
from vggt.utils.load_fn import load_and_preprocess_images

device = "cuda"
dtype = torch.bfloat16  # A100 supports
model = VGGT.from_pretrained("facebook/VGGT-1B-Commercial").to(device).eval()

image_paths = ["cam_FRONT.png", "cam_FRONT_LEFT.png", ...]  # 7 cams
images = load_and_preprocess_images(image_paths).to(device)

with torch.no_grad(), torch.cuda.amp.autocast(dtype=dtype):
    predictions = model(images)
# predictions.depth_maps → list of (H, W) tensors, one per cam
# predictions.camera_params → estimated intrinsics/extrinsics (we IGNORE, use AV2's)
```

**Input**: PIL paths or (B, 3, H, W) tensor. Auto-resizes to 518×518 max (aspect preserving). Our 504×504 letterbox passes through unchanged. **No K-rescaling needed beyond existing `rescale_K_for_letterbox()`**.

**Output**: depth at input resolution, float32 metric meters. No built-in confidence — use 1.0 like Depth Pro.

---

## §3 GPU Memory / Latency

- Memory: **7-8 GB per frame** on A100 (newer than spec sheet's 1.88 GB; user reports + GitHub #81). May 2026 optimization landed → ~2-3× more frames in same budget.
- Latency: **0.2 sec for 10 frames**, 8.75 sec for 200 frames on A100
- Our 7-cam joint forward: **~0.2-0.4 sec estimated**
- bfloat16 native on A100

---

## §4 Adapter Pattern (Add to `run_depth_backbone_swap.py`)

The existing script has a modular backbone plugin. Just add ~80-line `run_vggt()` function:

```python
def run_vggt(images_lb: dict[str, np.ndarray], device: str = "cuda"
             ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict]:
    """VGGT joint 7-cam forward, returns metric depth + uniform conf."""
    import torch, time
    from PIL import Image
    from pathlib import Path
    from vggt.models.vggt import VGGT
    from vggt.utils.load_fn import load_and_preprocess_images

    cams_ordered = ["FRONT_LEFT", "FRONT", "FRONT_RIGHT",
                    "SIDE_RIGHT", "REAR_RIGHT", "REAR_LEFT", "SIDE_LEFT"]
    # save to disk for VGGT loader
    tmp = Path("/tmp/vggt_in"); tmp.mkdir(exist_ok=True)
    paths = []
    for cam in cams_ordered:
        p = tmp / f"{cam}.png"
        Image.fromarray(images_lb[cam]).save(p)
        paths.append(str(p))

    device_t = torch.device(device if device == "cpu" or torch.cuda.is_available() else "cpu")
    use_bf16 = (device_t.type == "cuda"
                and torch.cuda.get_device_capability()[0] >= 8)

    t_load = time.time()
    model = VGGT.from_pretrained("facebook/VGGT-1B-Commercial").to(device_t).eval()
    load_s = time.time() - t_load

    images = load_and_preprocess_images(paths).to(device_t)
    t0 = time.time()
    with torch.no_grad():
        with torch.cuda.amp.autocast(dtype=torch.bfloat16 if use_bf16 else torch.float16):
            predictions = model(images)
    if device_t.type == "cuda":
        torch.cuda.synchronize()
    fwd_s = time.time() - t0

    depth_by_cam, conf_by_cam = {}, {}
    for i, cam in enumerate(cams_ordered):
        d = predictions.depth_maps[i].detach().float().cpu().numpy().astype(np.float32)
        depth_by_cam[cam] = d
        conf_by_cam[cam] = np.ones_like(d)

    meta = {
        "backbone": "VGGT",
        "checkpoint": "facebook/VGGT-1B-Commercial",
        "device": str(device_t),
        "autocast_bf16": use_bf16,
        "model_load_s": round(load_s, 3),
        "forward_s_7cam_joint": round(fwd_s, 3),
    }
    return depth_by_cam, conf_by_cam, meta
```

Add `--backbone vggt` arg, wire into existing per-cam metric eval + L3 cycle eval (no change to those — they're backbone-agnostic).

---

## §5 Expected Outcome (Honest)

| Metric | Pi3 | Depth Pro | **VGGT (expected)** |
|---|---|---|---|
| abs_rel vs LiDAR | 0.204 | 0.580 | **0.18-0.25** |
| L3 cycle-PSNR | 8.65 | (worse) | **~9-10 dB** |
| Far-field bias | -24% | (worse) | **-15 to -25%** |
| **vs L1 (12.34 dB)** | -3.69 dB | (worse) | **-2 to -3.5 dB** |

**Hypothesis-confirming outcome (most likely, 70%)**: VGGT decent abs_rel but L3 still loses ~2-3 dB to L1 → confirms "algorithm is bottleneck, not backbone" hypothesis. Paper Section 4 NEG #3 strengthens from 2 → 3 backbones.

**Hypothesis-breaking outcome (~10%)**: VGGT abs_rel < 0.15 AND L3 > 11 dB → backbone really matters → paper narrative pivots heavily. Would be a major surprise.

**Middling outcome (~20%)**: VGGT roughly = Pi3 → no clear story, just adds noise. Paper writes "3 backbones tested, all hover around -3 dB vs L1".

---

## §6 Time Budget (revised < plan v6.1 estimate)

Plan v6.1 says ~3 days; revised based on findings:

| Task | Time |
|---|---|
| Install VGGT on Colab | 8 min |
| Inference 10 anchor × 7 cams (joint forward) | ~10 min |
| LiDAR eval | 5 min |
| L3 cycle eval | 15 min |
| Per-anchor batch | ~40 min |
| **All 10 anchors** | **6-7 hours** |
| Debug/viz/re-runs buffer | 8-16 hours |
| **Total** | **1.5-2 days** (faster than 3d planned) |

---

## §7 Recommendation: GO AHEAD ✅

VGGT is publicly available, multi-view native, drop-in fit for our existing backbone-swap infrastructure. No scoop risk, no install pain expected. Implementation gp subagent (next Wave) can clone-and-extend `run_depth_backbone_swap.py` with ~80-line `run_vggt()` function above.

**Critical files for implementation**:
- `scripts/phase3/run_depth_backbone_swap.py` (extend with `--backbone vggt`)
- `scripts/phase3/run_vggt_backbone.py` (new sibling, can also just live in swap script)
- `outputs/phase3/p3.5_vggt/anchor_<id>/` (output dir, mirror Depth Pro pattern)
- `deliverables/images/route_vggt_vs_pi3_vs_depthpro.png` (3-backbone abs_rel bar chart, paper Section 4 figure)

**Citation**:
- [VGGT arXiv 2503.11651](https://arxiv.org/abs/2503.11651)
- [GitHub](https://github.com/facebookresearch/vggt)
- [HF model card](https://huggingface.co/facebook/VGGT-1B)
- [VGGT-360 panoramic depth derivative (arXiv 2603.18943)](https://arxiv.org/pdf/2603.18943) — might be useful future-work hook
