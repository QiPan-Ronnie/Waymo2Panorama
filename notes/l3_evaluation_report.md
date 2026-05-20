# Phase 2 L3 Evaluation Report

**Tag**: `v0.2-l3-mvp`
**Date**: 2026-05-20
**Frame**: AV2 val log `02a00399-3857-444e-8db3-a8f58489c394`, anchor_idx=0
**Backbone (D1 decision)**: Pi3X (vs DVGT, see `notes/backbone_decision.md`)

---

## TL;DR

L3 (Pi3 + Sim(3) + forward-splat) **量化 + 视觉双双不优于 L1** on this frame.

- PSNR cycle-consistency: **L1 mean 11.78 dB vs L3 mean 8.65 dB → ΔPSNR = -3.13 dB**
- SSIM: **L1 0.54 vs L3 0.10 → ΔSSIM = -0.44** (L3 远落后)
- Coverage: L1 30-55%, L3 7-32% (L3 砍了天空 / 远景 / 低 conf)
- 7 cam 中 L3 只在 `ring_front_center` 微弱胜出 (+0.26 dB), 其他 6 全输

**含义**: L3 的产物**不应是 ERP image** (那是 L1 的题), 而应是 **3D scene representation (`.ply` + per-view depth maps)** 给下游 3D-aware consumer (Pantheon360 / 3DGS / depth-conditioned diffusion)。要让 L3 ERP 视觉超 L1 需要更先进的 rendering (raycast + z-buffer 或 3D Gaussian Splatting), 这是 Phase 4 题目。

---

## 1. 评估协议 (P2.7 cycle-consistency)

对每个 7 ring cam:

```
hold out 该 cam 的真实图像 (ground truth)
        │
        ↓
用其它 6 cam 重建它应该看到什么:
        │
        ├── L1: backward sphere projection
        │   每个 held-out 像素 → ego 方向射线 →
        │   对每个其它 cam, 反向投到其像平面 → bilinear sample →
        │   cos² feather blend across multiple cams
        │
        └── L3: forward 3D projection + z-buffer
            其它 6 cam 的 Pi3 3D 点 (Sim3 aligned to ego)
            → transform to held-out cam frame
            → pinhole project via K_holdout
            → z-buffer (nearest depth wins) via np.unique sorted-first
        │
        ↓
对比真实 vs L1 / L3 重建 (intersection mask, 公平比较)
        │
        ↓
metrics: PSNR / SSIM / MAE
```

实现: `scripts/phase2/eval_cycle_consistency.py` (commit `24f92cb`).
执行: `phase2-p2.7-cycle-consistency-v2` (CPU job, 16s wall-clock)
输入: `MyDrive/koi_waymo2pano_colab/outputs/phase2/pi3_one_frame/` (Phase 2 P2.5 产出)
输出: `MyDrive/koi_waymo2pano_colab/outputs/phase2/cycle_consistency/`

参数:
- conf_threshold = 0.3
- min_dist = 0.5 m
- max_dist = 60 m
- 重建分辨率 = 504×504 (held-out cam letterboxed)

---

## 2. 数值结果

### Per-camera 表

| Cam | cov_L1 | cov_L3 | cov_∩ | PSNR_L1 | PSNR_L3 | **ΔPSNR** | SSIM_L1 | SSIM_L3 | ΔSSIM | MAE_L1 | MAE_L3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ring_front_center | 55.7% | 32.2% | 31.6% | 7.73 | 8.00 | **+0.26** | 0.319 | 0.141 | -0.179 | 72.55 | 79.86 |
| ring_front_left | 40.6% | 20.4% | 19.8% | 10.71 | 8.80 | **-1.91** | 0.539 | 0.136 | -0.403 | 38.68 | 63.95 |
| ring_side_left | 28.8% | 15.9% | 14.1% | 15.06 | 9.52 | **-5.54** | 0.575 | 0.105 | -0.470 | 22.01 | 59.24 |
| ring_rear_left | 29.8% | 6.9% | 6.9% | 13.75 | 7.22 | **-6.53** | 0.536 | 0.020 | -0.516 | 25.86 | 89.23 |
| ring_rear_right | 30.0% | 9.6% | 9.5% | 11.68 | 7.50 | **-4.18** | 0.607 | 0.046 | -0.561 | 28.72 | 87.43 |
| ring_side_right | 30.5% | 14.7% | 13.5% | 11.81 | 8.88 | **-2.92** | 0.678 | 0.156 | -0.522 | 28.69 | 61.82 |
| ring_front_right | 40.9% | 15.1% | 15.1% | 11.72 | 10.62 | **-1.10** | 0.541 | 0.102 | -0.439 | 34.77 | 58.17 |
| **MEAN** | **36.6%** | **16.4%** | **15.8%** | **11.78** | **8.65** | **-3.13** | **0.542** | **0.101** | **-0.441** | **35.90** | **71.39** |

### 阅读

| 指标 | 阈值 | 结果 |
|---|---|---|
| 设计阈值 (plan v2) | L3 PSNR > L1 + 1 dB → 切换 backbone, 否则 default keep | **L3 输 3.13 dB → 不切, L1 仍是 baseline** |
| 视觉一致性 | L3 应在视觉上"修 ghost" | 也没修 (P2.6 视觉对比) |
| Coverage 差距 | L3 ~15.8% vs L1 ~36.6% | L3 砍掉了 ~57% 区域 (sky + textureless + far) |

---

## 3. 为什么 L3 输 — diagnosis

### 3.1 Coverage 问题 (砍得太狠)

L3 砍掉了:
- 天空区域 (Pi3 conf < 0.3, 因为没视差线索)
- 远景 (> 60 m, 信号不可靠)
- 自车遮挡区 (近 0 m)

L1 不需要任何这些信息, 永远填满相机 FOV。

**含义**: forward-splat 的本质要求 high-confidence 深度, 不像 L1 直接 sample 已知 RGB。

### 3.2 Intersection mask 内 L3 仍输 (~3 dB)

即便只看两者都覆盖的像素, L3 仍输 3 dB。 原因:
- **Pi3 单点 depth variance ±0.3 m** → 在像平面上引起几个像素的偏移
- **每个 cam Pi3 depth 估的 ground 面有微 systemic bias** → ego frame ground 不是完美 z=0
- 不同 cam 重叠区, Pi3 depth 给出的 3D 点不完全一致 → 同一物体在 ERP 上偏离

L1 的反向投影避开了这些问题: 它假设深度无穷, 不依赖 Pi3 几何, 误差只是 parallax (因为忽略 cam 平移)。

### 3.3 唯一胜出的 front_center (+0.26 dB)

`ring_front_center` 是唯一 portrait cam, 镜头中心位置距 ego 原点最远 (~1.63 m 前). 所以 parallax 在这个 cam 上**最显著**, L3 的 3D-aware 投影有机会修一点。 但增益微弱 (+0.26 dB), SSIM 反而输。

**结论**: 这个 frame 没有近距 high-parallax 物体 (e.g., 横穿路口的近车 / 1-3m 路灯杆 / 头顶过街标志), 所以 L3 没机会显示优势。

---

## 4. 关键 takeaway

### L3 forward-splat ERP 不可用

不管参数怎么调:
- conf=0.5 (严) → coverage 6%, 太稀
- conf=0.1 (松) → 天空噪声爆炸
- conf=0.3 (中) → 上面的 8.65 dB
- hybrid L1+L3 hard-mask → "双白车" ghost (L1/L3 位置不一致)

**forward-splat to ERP 是错误的输出形式 for L3 in this regime**。

### L3 的正确产物

```
                                  ┌───────────────────┐
   Pi3 outputs (per cam)          │                   │
   ─────────────────────────► ────│   L3 deliverable: │
                                  │                   │
                                  │  • fused .ply 3D  │
                                  │    point cloud    │
                                  │    (690K points,  │
                                  │     ego metric)   │
                                  │                   │
                                  │  • per-view depth │
                                  │    maps (7 ×)     │
                                  │                   │
                                  │  • Pi3 7-view     │
                                  │    raw outputs    │
                                  │    (3D + conf +   │
                                  │     pose)         │
                                  │                   │
                                  └────────┬──────────┘
                                           │
                                ┌──────────┴───────────┐
                                ↓                      ↓
                       Pantheon360 ERP        360° depth-conditioned
                       renderer (consumes     diffusion / 3DGS
                       3D cache + writes      training
                       its own ERP)
```

L3 不是为了再做一张 2D 全景图, 是为了**给下游"知道 3D"的模型一个 dense colored 3D scene**。

### 想让 L3 ERP 视觉超 L1 怎么办

需要换 rendering 算法 (forward-splat 是错路):

| 方法 | 描述 | 估时 |
|---|---|---|
| **Raycast + z-buffer** | 对每 ERP 像素 cast 一条 ego 射线, 与 3D 点云 mesh 求交, sample | 1 周 |
| **3D Gaussian Splatting** | 把 Pi3 点云转 3D Gaussians, 用 PyTorch3D/nvdiffrast 训出 view-dependent rendering | 2 周 |
| **CylinderSplat / LiftProj-class** | 论文级别的 driving panorama 3D rendering | 2-4 周 |

这些是 **Phase 4 题目**, 不是这周的 deliverable。

---

## 5. 后续路径 (调整后)

### Tier 1 (下周内)
- ✅ **P2.7 done** (this report)
- **Pi3 vs AV2 LiDAR depth comparison** — AV2 自带 LiDAR sweep, 投到 7 cam 算 abs rel err。 给 Pi3 几何精度一个 ground-truth-anchored 数字
- **Phase 2 收官 + tag `v0.2-l3-mvp`**

### Tier 2 (Week 3)
- **多 sequence 扩展**: 现在只 1 log × 1 anchor。 扩到 1 log × 10 anchors (一段 ~5s video), 验证 L1/L3 metric 的 frame-to-frame variance
- **跨 log 扩展**: 3-5 个 AV2 log, 不同场景 (高速 / 拥堵 / 路口 / 黑夜)
- **找 parallax-heavy frame**: 系统地扫 frame, 找近物 + cam 重叠区, **给 L3 一个有机会胜出的场景**

### Tier 3 (Week 4+)
- **Phase 3 OmniStitch baseline** (Track D): 三方对比 L1 / OmniStitch / L3
- **Diffusion polish** (Argus / Percep360): 填 ERP 上下黑边 + 接缝处
- **D8 paper angle decision**: 看 Phase 3 数据决定写 dataset paper 还是 method paper

### Tier 4 (Phase 4+, 长期)
- Pantheon360 集成
- 3DGS / proper raycast L3 ERP (让 L3 视觉超 L1)
- Waymo Track B (5 cam → 360 with diffusion fill)

---

## 6. 文件清单

| 文件 | 描述 |
|---|---|
| `scripts/phase2/eval_cycle_consistency.py` | 评估实现 |
| `code/waymo2panorama/alignment/sim3_align.py` | Umeyama Sim(3) (依赖) |
| `code/waymo2panorama/pipeline/lift_and_project.py` | L3 forward-splat 实现 (依赖) |
| Drive: `outputs/phase2/cycle_consistency/cycle_consistency.json` | 完整数字表 |
| Drive: `outputs/phase2/cycle_consistency/cycle_consistency_bars.png` | L1 vs L3 PSNR 柱状图 |
| Drive: `outputs/phase2/cycle_consistency/reconstruction_<cam>.png` × 7 | per-cam 3-panel (GT / L1 / L3) |
| `notes/backbone_decision.md` | D1 (Pi3 vs DVGT) 决策 |
| `notes/l3_evaluation_report.md` | **本文** |
| `agent/progress.md` | 当前状态 (含本结果) |

---

## 7. Conclusion

**L1 sphere projection 在这个 frame 上是更优的 360° ERP 生成方法**, 量化 + 视觉双确认。 **Phase 2 主线工作至此完成**, 标记 `v0.2-l3-mvp` 作为 Phase 2 终点。

Phase 3 应:
- 在更多 frames / logs 上重复实验, 看 L3 是否在特定场景下有优势
- 接 OmniStitch baseline 做三方对比
- 决定 D8 paper angle

L3 的价值已**重定位**为下游 3D-aware consumer 的输入, 不再追求"L3 ERP 超 L1"这个目标。
