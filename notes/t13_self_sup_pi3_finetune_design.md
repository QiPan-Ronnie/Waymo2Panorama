# T13 Self-supervised Cycle-PSNR Finetune of Pi3 — Implementation Design

**Plan agent**: a6d2d36 (2026-05-21)
**Target**: train LoRA adapter on Pi3 to reduce far-field bias (-24% at 40m → goal ≤ -15%)
**Cycle-PSNR target**: > base Pi3 + 0.5 dB on hold-out anchor

---

## §1 Critical Pi3 Architecture Surface

Pi3X's `forward()` returns dict with:
- `local_points`: (B, 7, H, W, 3) — 3D points in EACH cam's own frame, **metric-scaled** (after `model.metric`)
- `points`: (B, 7, H, W, 3) — same in cam-0 frame
- `conf`: (B, 7, H, W, 1) — raw logits

**We use AV2 GT extrinsics, NOT Pi3's predicted `camera_poses`**.
**Depth = local_points[..., 2]** (z in cam's optical frame).
`run_pi3_multi_anchor.py` calls `model.disable_multimodal()` — deletes depth_encoder, must use this mode for training too.

---

## §2 Differentiable Cycle-PSNR Loss (the key innovation)

Existing `eval_cycle_consistency.py` uses forward-projection (scatter / z-buffer) → **not differentiable**.

**Switch to inverse-warp variant**: hold out cam H, use H's predicted depth to lift H's pixels into ego, then project each into each neighbor K, sample K's RGB via `grid_sample` (differentiable). Aggregate via conf-weighted average. PSNR vs H's GT RGB.

```python
def diff_cycle_psnr_loss(local_points, conf_logits, imgs,
                          K_per_cam, T_ego_cam, holdout_idx):
    B, N, H, W, _ = local_points.shape
    other = [k for k in range(N) if k != holdout_idx]

    # 1. Lift H pixels via predicted depth to ego frame
    pts_H_cam = local_points[:, holdout_idx]                   # (B, H, W, 3)
    pts_H_hom = torch.cat([pts_H_cam, torch.ones_like(pts_H_cam[..., :1])], -1)
    pts_ego = torch.einsum('bij,bhwj->bhwi',
                            T_ego_cam[:, holdout_idx], pts_H_hom)[..., :3]

    rgb_acc, w_acc = torch.zeros(B, 3, H, W), torch.zeros(B, 1, H, W)

    for K_idx in other:
        # 2. ego → K cam frame
        T_Ke = torch.linalg.inv(T_ego_cam[:, K_idx])
        pts_K = torch.einsum('bij,bhwj->bhwi', T_Ke,
                              torch.cat([pts_ego, ones], -1))[..., :3]
        z = pts_K[..., 2].clamp(min=1e-3)

        # 3. Project to K's image plane via K matrix
        K_mat = K_per_cam[:, K_idx]
        u = K_mat[:, 0, 0] * pts_K[..., 0] / z + K_mat[:, 0, 2]
        v = K_mat[:, 1, 1] * pts_K[..., 1] / z + K_mat[:, 1, 2]
        u_n = 2 * u / (W - 1) - 1; v_n = 2 * v / (H - 1) - 1
        grid = torch.stack([u_n, v_n], -1)

        # 4. Differentiable sample via grid_sample
        sampled = F.grid_sample(imgs[:, K_idx], grid,
                                 mode='bilinear', padding_mode='zeros',
                                 align_corners=True)

        # 5. Occlusion-consistency soft mask (no hard z-buffer)
        depth_K = local_points[:, K_idx, ..., 2:3].permute(0, 3, 1, 2)
        depth_K_sampled = F.grid_sample(depth_K, grid, mode='bilinear',
                                         padding_mode='border', align_corners=True)
        depth_consistent = torch.exp(-((depth_K_sampled - z).abs() / (z + 1e-3)) * 5.0)

        # 6. Cos-axis feather (mirrors existing L1 reconstruction)
        cos_w = (pts_K[..., 2] / (pts_K.norm(dim=-1) + 1e-6)).clamp(min=0).pow(2)

        w = in_bounds * depth_consistent * cos_w
        rgb_acc += sampled * w; w_acc += w

    recon = rgb_acc / (w_acc + 1e-6)
    mse = ((recon - imgs[:, holdout_idx]) ** 2).mean(1)
    psnr = 10 * torch.log10(1.0 / (mse * mask).sum / mask.sum + 1e-8)
    return -psnr.mean()  # negate → SGD maximizes PSNR
```

**Why this works**:
- Gradient flows into `local_points[:, holdout]` (via pts_ego) AND `local_points[:, K]` (via depth_K_sampled occlusion mask)
- Both cams get supervised per step
- No hard scatter → fully differentiable
- Pi3 input is in [0,1] RGB → max_val=1 in PSNR

---

## §3 LoRA Targets — Actual Pi3 Layer Paths

From reading `pi3/models/pi3x.py` + `pi3/models/layers/attention.py`:

**3 tiers** (start with A):

### Tier-A (cheapest, ~50K trainable params, recommended)
```python
target_modules=[
    "point_head.output_block.1.0",   # Conv2d producing z features
    "point_head.output_block.1.3",   # Conv2d 1x1 → z output (depth)
    "point_decoder.blocks.*.attn.qkv",
    "point_decoder.blocks.*.attn.proj",
]
r=8, lora_alpha=16, lora_dropout=0.1, bias="none"
```

`output_block` is a `ModuleList` with `dim_out=[2, 1]` — **index 1 is the depth head** (z=1ch), index 0 is xy rays (2ch).

### Tier-B (last 6 decoder blocks + point branches, ~500K params)
```python
target_modules=[
    "decoder.30.attn.qkv", "decoder.30.attn.proj",
    ...,
    "decoder.35.attn.qkv", "decoder.35.attn.proj",
    "point_decoder.*.attn.qkv", "point_decoder.*.attn.proj",
    "point_head.output_block.1.*",
]
r=16, lora_alpha=32
```

### Tier-C (every decoder block, ~5M params) — risk of catastrophic forgetting

**Verification step**: gp must first run `for name, _ in model.named_modules(): print(name)` and confirm exact dotted paths before training.

---

## §4 Training Infrastructure

**File layout (gp creates)**:
- `code/waymo2panorama/training/cycle_psnr_loss.py` (the diff_cycle_psnr_loss above)
- `code/waymo2panorama/training/pi3_lora.py` (LoRA wrapping helper)
- `scripts/phase4/train_pi3_cycle_psnr.py` (main loop)
- `scripts/phase4/eval_pi3_finetuned.py` (eval)

**Data**:
- Train: 200 anchors of `02a00399` + 2 of 4 new logs (~480 total anchors)
- Val: 50 anchors from 3rd new log
- Test: 70 anchors from 4th new log + 10 cached anchors
- Reuse `run_pi3_multi_anchor.py` data pipeline
- Need to extend cache: write `scripts/phase4/make_train_cache.py`

**Optimizer**:
- Adam (or AdamW) lr 1e-5 main, 1e-6 warmup
- Gradient clip 1.0
- bf16 autocast (Pi3 native)
- LoRA params fp32, base frozen
- Holdout cam rotates per step (or random) for equal supervision

**Schedule (5 epochs, ~10-12 h on A100)**:
- 100 steps warmup @ lr 1e-6
- 4 epochs main @ lr 1e-5 cosine decay
- 1 epoch fine @ lr 1e-6
- Val PSNR every 200 steps on 50-anchor val
- Per step: ~2.3 s (1.5s Pi3 forward + 0.8s loss+backward)
- 480 × 7 holdouts = 3360 steps/epoch × 2.3s = 2.1h/epoch

**Convergence**: success if val PSNR > base + 0.5 dB sustained over 2 evals; abort if 2 epochs no improvement.

---

## §5 Evaluation Pipeline

1. `Pi3X.from_pretrained("yyfz233/Pi3X")` + `PeftModel.from_pretrained(adapter_dir)` + `disable_multimodal()`
2. Test set: 80 anchors (70 new-log + 10 cached)
3. Compute: cycle-PSNR (existing eval), LiDAR (abs_rel, δ<1.25, depth-binned bias)
4. Compare to base Pi3

**Deliverables**:
- `outputs/phase4/cycle_finetuned_pi3/eval_summary.json`
- `deliverables/images/t13_finetune_curves.png` (loss + val PSNR)
- `deliverables/images/t13_pi3_vs_finetuned_depth.png` (depth viz compare)
- `deliverables/images/t13_depth_binned_compare.png` (binned bias, with T12-v2 K=3 line)
- `deliverables/images/t13_cycle_psnr_per_anchor.png` (scatter base vs finetuned)
- `deliverables/handoff_to_koi_v6.md` route 16 section append

---

## §6 Risks + Fallbacks

1. **Loss doesn't converge** (most likely failure)
   - Photometric losses notoriously hard (monodepth literature)
   - Mitigations: add SSIM term (weight 0.15), or drop to L1-photometric, or try smaller LoRA tier
   - Fallback: ship NEG with curves (still paper value)

2. **OOM on A100 40GB** — drop to 392² resolution (Pi3 supports) + gradient checkpointing

3. **Catastrophic forgetting** (cycle improves, LiDAR abs_rel degrades)
   - Add 5% L1-distillation: `|local_points_finetuned - local_points_base.detach()| * 0.05`
   - Or 5% LiDAR-supervised term on `02a00399` frames

4. **Overfits to training log** — mitigated by 3-of-4-new-logs train + 1 hold-out

5. **`grid_sample` edge cases** — `padding_mode='zeros'` + require `pts_K[2] > 0.5m` (in-front)

---

## §7 Implementation Order

- **Step 1 (~1 d)**: write `diff_cycle_psnr_loss` + smoke 1 forward+backward on anchor_060. Confirm gradient flows. PSNR value should be within ~1 dB of existing numpy eval.
- **Step 2 (~0.5 d)**: peft Tier-A LoRA wrapping, verify forward + 10 training steps, loss decreases.
- **Step 3 (~0.5 d)**: extend Pi3 cache to ~480 anchors from 4 new logs.
- **Step 4 (~3-4 d wall on A100)**: full 5-epoch training. Background. Snapshot val every 200 steps. Save best.
- **Step 5 (~0.5 d)**: eval + 4 deliverable PNGs + MD section append.

**Total**: 5-6 d on A100. **GPU-only, requires `labels.requires=gpu`**.

---

## §8 Critical Files for Implementation gp

- `01-pi3/code/official/Pi3/pi3/models/pi3x.py` (Pi3X model; lines 19-272 forward, lines 401-455 forward_head for local_points)
- `01-pi3/code/official/Pi3/pi3/models/layers/conv_head.py` (depth head — Tier-A target: `output_block[1]`)
- `01-pi3/code/official/Pi3/pi3/models/layers/attention.py` (Tier-B/C targets: `qkv`/`proj` linear, lines 51-53, 257-259)
- `scripts/phase2/eval_cycle_consistency.py` (numpy cycle-PSNR reference; port to differentiable for §2)
- `scripts/phase3/run_pi3_multi_anchor.py` (data pipeline + `disable_multimodal()` pattern, lines 184-205)
- `outputs/phase3/pi3_cache/anchor_060/` (cache schema for training data)
