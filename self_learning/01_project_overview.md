# Chapter 01 — 项目概览: 我们到底在做什么

---

## 1.1 一句话讲清楚

**自动驾驶车顶有 7 个朝不同方向的相机, 我们把它们同一时刻的画面拼成一张 360° 全景图, 给下游 3D world model 用**.

具体来说:
- **输入**: Argoverse 2 数据集里, 某个时刻 t 的 7 张 RGB 图 (`ring_front_center`, `ring_front_left`, `ring_front_right`, `ring_side_left`, `ring_side_right`, `ring_rear_left`, `ring_rear_right`)
- **输出**: 一张 1024×2048 的 ERP (equirectangular projection) 全景图, 看起来像 Google Street View 那种 360° 球幕展开

---

## 1.2 为啥这件事难 (3 个挑战)

### 挑战 1: 几何 (Geometry)
每个相机角度不一样, 看到的同一物体在 360° 全景图上**应该在哪个像素位置**不显然.
- 相机 A 拍到一个路灯 → 这个路灯在全景图的哪里?
- 需要知道: 相机 A 自己的内参 (K 矩阵, 在 §02.4 讲) + 相机 A 在车上的位置朝向 (外参 T_ego_cam, 在 §02.6 讲)

### 挑战 2: 视差 (Parallax)
**视差 = 同一物体从不同视角看, 位置不同**.
- 7 个相机不在同一点 (相距 ~30-50 cm), 看一个 5m 远的车, 在不同相机里位置稍微不同
- 简单 overlay 7 张图 → 重叠区出现**鬼影 (ghost)** 因为同一物体被 2 个相机各画了一次, 位置错开
- 远场 (>50m) 视差小可以忽略, 近场 (<10m) 视差大必须处理

### 挑战 3: 没有 ground truth
**关键困难**: AV 数据集没人给你一张"正确的 360° 全景"作为参考答案.
- COCO 数据集有人工标注, Cityscapes 有语义分割 GT, 但 360° panorama 没人标
- 所以我们**不能用 supervised learning 训练** (没标签), 也**不能直接算 PSNR**(没参考图)

---

## 1.3 我们怎么评测 (cycle-PSNR)

既然没有 ground truth, 我们用 **cycle consistency** (循环一致性) 做评测:

**方法**:
1. 把第 i 个相机的真实图 (RGB_i) **挡住**
2. 用剩下 6 个相机的图 + 几何信息, **推断**第 i 个相机视野里应该看到啥
3. 推断出的图跟真实的 RGB_i 算 **PSNR** (Peak Signal-to-Noise Ratio, 越高越接近)
4. 对每个 holdout cam 都做一次, 取平均

**PSNR 是啥** (CV 入门概念):
- 衡量两张图有多像的标准指标, 单位 dB
- 完全一样 → PSNR = ∞, 差很多 → PSNR < 10 dB
- 经验值: PSNR > 30 dB = 几乎一样, PSNR ~ 15 dB = 大致看得出是同一场景但细节有差异
- 公式: `PSNR = 10 * log10(MAX² / MSE)`, MAX 是像素最大值 (255 for uint8), MSE 是均方误差

**为啥 cycle-PSNR 够用**:
- 不需要外部 GT
- 数据自己就够: 7 个相机各自的真实图都有, 用 hold-one-out 互相验证
- 关键假设: 如果你拼接方法正确, 用 6 cam 推 1 cam 应该接近真实

---

## 1.4 项目数字背景

| 数字 | 含义 |
|---|---|
| **7 个相机** | AV2 ring cameras, 朝外环绕覆盖 360° |
| **1550×2048** | AV2 原图分辨率 (每个 cam) |
| **504×504** | 我们 letterbox 后输入到神经网络的分辨率 (§02.5) |
| **1024×2048** | ERP 全景图输出分辨率 |
| **10 anchors** | 我们主要测的 10 个关键帧 (indices 0, 30, 60, ..., 270) |
| **02a00399** | 主用 log UUID (Argoverse 2 val 集), 319 frames @ 20Hz |
| **12.34 dB** | 我们 L1 baseline 的 cycle-PSNR (10 anchors mean) |

---

## 1.5 上下游 (project chain)

```
AV2 / Waymo 7 cam (THIS PROJECT)
        ↓ stitch 7-cam → 360° ERP
ERP panoramic video (1024×2048 RGB)
        ↓ 喂下游 (paused 在 v6.1 pivot 之后)
Pantheon360 (3D-aware 360° diffusion, CVPR 2026)
        ↓
360 world simulation / Cosmos-Predict / Argus
```

**Koi 老师的研究链**: Pi3 (我们用的 backbone, CVPR 2025) → 这个项目 (stitching) → Pantheon360 → Cosmos.

我们只做**第一步**: 把 7 cam 拼成 ERP. 后面的下游模型暂时不动 (v6.1 strategic pivot 之后, 见 §05).

---

## 1.6 时间线总览 (2 周做了啥)

```
Week 1 (2026-05-15 to 05-20)
├── Day 1 (05-15): repo + plan + 调研
├── Day 2 (05-16): AV2 API spike, 2×4 mosaic, GO
├── Day 3 (05-17): L1 球面 baseline 跑通 (sphere + multiband)
├── Day 4-5 (05-18 to 05-19): DVGT vs Pi3 backbone 选型, 试 8 次后选 Pi3
│                              衍生 agent-colab-queue 框架 (调试副产品)
└── Day 6 (05-20): Pi3 forward + Sim(3) + L3 .ply + L3 NEG 量化 + LiDAR eval

Week 2 (2026-05-20 to 05-21)
├── 05-20 late: Phase 3 W1 — 10-anchor robustness
├── 05-21 early (Wave 1): T-Koi-1 PDF + T5 metric audit + T6 parallax + T8 lit + T14 IPM + T16 Bayesian + T7-prelim
├── 05-21 mid (Wave 2): T18 Depth Pro NEG + T2 OmniStitch NEG + T12 temporal NEG + T9 ViPE downstream
├── 05-21 07:30: 战略 pivot v6.1 — 砍下游, 主线切到 stitching 方法学
├── 05-21 12:00 之后 (CPU 大爆发): 新-A 柱面 + 新-B graphcut + 新-C IPM 多区域 + 新-D wide stereo + 新-E HDR
└── 05-21 late: T-Koi-4 PDF 5 版迭代 + 新-F VGGT 尝试失败 + 清理 + 学习方案
```

---

## 1.7 8 条拼接路线一览 (用一句话概括)

| ID | 名字 | 一句话 | Verdict |
|---|---|---|---|
| **L1** | Sphere baseline | 每像素当作远处球面上的方向, 球面投影 + Laplacian 多带混合 | ✅ 强 baseline (12.34 dB) |
| **L3** | Pi3 forward-splat | 神经网络估每像素 3D 深度 → 点云 → splat 到球面 | ❌ -3.15 dB, 10/10 输 |
| **IPM (T14)** | Ground plane hybrid | 路面像素 (z=0) 用 IPM 数学精确投, 其他 fallback 球面 | ⚠️ +0.05 dB marginal |
| **新-A** | Cylindrical L2 | 球面换柱面, AV cam 水平排列更贴合 | ⚠️ Coverage +25%, cycle ~平 |
| **新-B** | Graph-cut seam | 用图论 min-cut 让接缝沿低梯度路径走 | ✅ 视觉接缝消失 |
| **新-C** | IPM 多区域 | IPM 扩到 ground+sky+building 三区域 | ✅ Ground +0.20 dB (4× T14) |
| **新-D** | Wide-baseline stereo | 邻 cam 当人眼经典三角化恢复 sparse 3D | ⚠️ 5/7 对成功 |
| **新-E** | HDR 跨 cam 补偿 | 7 cam 独立曝光 → LS 解 gain+bias 让颜色一致 | ✅ Lum gap -18% |

详细每条在 [03_methods_walkthrough.md](03_methods_walkthrough.md).

---

## 1.8 我们的发现 (3 个反直觉)

1. **经典球面 baseline 意外地强**: 它比 SOTA 神经网络 3D-lift (Pi3 forward-splat) 高 ~3 dB. 没人之前想到经典几何能赢 SOTA ML.

2. **换 backbone 也救不了 L3**: Pi3 (CVPR 2025) / Apple Depth Pro (SOTA 2024) / 多帧 Temporal Pi3 / OmniStitch — **4 种 backbone 都不如 L1**. 说明问题在 forward-splat 算法本身, 不在深度估计准不准.

3. **物理先验 > ML**: IPM (路面 z=0) + HDR (跨 cam 颜色一致性) 这种几十行数学公式, 在 AV 场景里比神经网络稳得多. 暗示 AV 域 ML 训练数据分布有问题.

详细解释 + paper 意义在 [05_findings_and_paper.md](05_findings_and_paper.md).

---

## 1.9 总结 — 你应该带走的

读完这章, 你应该能:

- [ ] 30 秒讲清楚项目 (任务 / 评测 / 数据集)
- [ ] 解释为啥这件事难 (geometry / parallax / 没 GT)
- [ ] 说清 cycle-PSNR 是啥 + 为啥不用 supervised
- [ ] 念出 12.34 / -3.15 / +0.20 / -18% 这几个关键数字
- [ ] 知道 8 条路线名字 + 一句话各自做啥
- [ ] 知道 3 个核心发现

**做不到? 回头看相应小节. 做得到? 进 §02 学 CV 概念**.

---

**下一章**: [02_cv_foundations.md](02_cv_foundations.md) — 项目里用到的所有 CV 概念, 各 1-2 句解释
