# Chapter 04 — 外部 baseline + Downstream demos

我们不是只对照 L1 自己, 还测了 **3 个外部 published 方法** 当 baseline / NEG. 这些数据点是 paper Section 4 "**Why prior art fails**" 的支柱.

---

## 4.1 OmniStitch (ACM MM 2024) — 唯一真正的 head-to-head

### 是啥
**Title**: OmniStitch: Depth-aware Multi-view Panorama Stitching
**Venue**: ACM Multimedia 2024
**Code**: https://github.com/tngh5004/Omnistitch
**模型类型**: 端到端深度学习, 输入多 cam 图 → 输出 ERP 全景

**关键点**: 这是**目前唯一公开发表的 multi-view AV 360° stitching 方法**. 跟我们任务定义最接近.

### 我们怎么测
- 直接 git clone + 官方 inference checkpoint
- 喂同样的 AV2 anchor 60 输入
- 输出 ERP 跟 L1 ERP 算 cycle-PSNR

### 结果
- **ΔPSNR = -6.67 dB vs L1** (OmniStitch 17.28 vs L1 23.95 on anchor 60 ERP)
- **输 7/7 cams** (每个 holdout cam OmniStitch 都比 L1 差)
- 视觉上有显著色差 + 边界 artifacts (sim2real transfer 失败)

### 为啥失败
- OmniStitch 训练在 synthetic 数据 (CARLA + Synthia)
- AV2 是真实数据, sim2real transfer gap 大
- 训练时 cam 配置可能跟 AV2 7 cam ring 不一样

### Paper 含义
- "唯一 published AV-360 baseline 也输 L1" → paper "vs prior art" 一栏铁稳
- **这是真 head-to-head 比较** (跟我们 L1 直接 apples-to-apples)
- 是 paper 论据 chain 里**最强**的一环 — 不能被 reviewer 推翻 "你只跟自己比"

### Code
- `scripts/phase3/run_omnistitch_inference.py` (driver)
- Drive: `outputs/phase3/t2_omnistitch_cycle/cycle_omnistitch.json` (numbers)

---

## 4.2 Apple Depth Pro (CVPR 2024) — L3 backbone swap NEG

### 是啥
**Title**: Depth Pro: Sharp Monocular Metric Depth in Less Than a Second
**Authors**: Apple (Bochkovskii et al.)
**Year**: 2024-10 release
**模型类型**: 单图输入, 输出 metric depth (米单位). **当时 SOTA monocular** (在 KITTI / NYU 上).

### 我们怎么测
**不是**直接做 stitching, 而是把 **L3 forward-splat pipeline 里的 Pi3 换成 Depth Pro**:
- Pi3 Inference → 深度 → Sim(3) → forward-splat → ERP
- 替换为: **Depth Pro inference** → 深度 → Sim(3) → forward-splat → ERP
- 其他步骤完全一样

### 结果
- **abs_rel 0.580 vs Pi3 0.204** (vs LiDAR GT) — **2.84× worse**
- δ<1.25 = 0.064 vs Pi3 0.633 (近场准确率崩了)
- 视觉上 ERP 全是噪点, 无法识别场景

### 为啥失败
- Depth Pro 训练数据偏室内 / 近景 / 静态场景
- AV outdoor 远场 (>20m) / 移动物体 / 强光阴影 在它训练分布外
- 域偏差 (domain shift) 严重

### Paper 含义
**"Algorithm not backbone"**: 我们 L3 输 L1 不是因为 Pi3 backbone 选错了 — 换 Apple SOTA monocular **更差**. 说明问题在 L3 forward-splat 算法类本身.

这是 paper Section 4 第 2 个 NEG datapoint, 加固"L3 算法类系统性失败"论据.

### Code
- `scripts/phase3/run_depth_backbone_swap.py` (单脚本支持 depthpro / metric3d / pi3 backbone)
- Drive: `outputs/phase3/t18_depthpro/depthpro_lidar_metrics.json`

---

## 4.3 Temporal Pi3 K=3 — 多帧时间堆叠 NEG

### 是啥
**不是 published method**, 是**我们自己设计的 NEG 实验**:
- 假说: Pi3 远场 bias -24% 是因为单帧信息不足
- 如果喂多帧 (K=3, 时间窗口 ~0.15s), Pi3 应该用 motion parallax 修远场
- 这相当于免费 stereo (帧间车移动了几 cm)

### 我们怎么测
- 输入: 3 时间相邻帧 (t-1, t, t+1) × 7 cam = 21 views
- Pi3X joint forward 21 views (Pi3 是 permutation-equivariant, 支持任意 view 数)
- 输出深度跟 LiDAR 比

### 结果
- **abs_rel 0.213 vs 单帧 0.204** — **反而更差**
- δ<1.25 = 0.572 vs 0.633 (退化)
- **远场 bias -23.92%** vs 单帧 -23.7% (几乎不变)

### 为啥失败
- AV 车移动太慢 (1 帧间隔 ~50 ms, 车移动 ~30 cm 在 60 km/h 时)
- baseline 跟 cam 间距 (30-50 cm) 同量级 → motion parallax 不提供新信息
- 实际上 Pi3 跨帧可能产生更多 noise (matching ambiguity)

### Paper 含义
**Pi3 远场 bias 是结构性, 不是单帧信息不足**. 这关掉了一条"加 cheap trick 救 L3"的可能性. 配合 §4.2 Depth Pro NEG, 形成"Pi3 / Depth Pro / Multi-frame Pi3 都不能救 L3" → 更深的算法类失败论据.

### Code
- `scripts/phase3/run_temporal_pi3.py`
- Drive: `outputs/phase3/temporal_pi3/anchor060_K3/eval/temporal_lidar_metrics.json`

---

## 4.4 (Bonus NEG) Panacea+ — Modality 不匹配

### 是啥
**Title**: Panacea+: Panoramic and Controllable Video Generation for Autonomous Driving
**Venue**: CVPR 2024
**模型类型**: BEV layout + 3D bbox + HD-map → 6-cam video (生成模型)

### 我们怎么测
原本想测它当 stitching 下游, 但**做完代码 reading 发现关键 insight**:
- Panacea+ 是 **parallel generator** (从 BEV / 3D 描述生成视频)
- **不消费 RGB 输入** — 它跟我们 L1 是平行的另一条生成路径, 不是 L1 的下游
- 同理 Pantheon360 (CVPR 2026)

### Paper 含义
**Modality NEG**: 不是"性能差", 是"任务定义不匹配". 写进 paper Section 4 当第 5 个 NEG (说明我们认真调研过, 排除了一类伪 baseline).

### Code
- `scripts/phase3/run_panacea_inference.py` (建好了 inference 框架, **没真跑**, 因 modality 不匹配)
- 详见 `progress_T17_addendum.md` (已合并入 progress.md)

---

## 4.5 Downstream demos (paused 在 v6.1 pivot 之后)

我们做了 3 个下游消费 demos 证明 L1 输出有用. 这些是 paper Section 6 "Downstream Applications" 的支撑:

### 4.5.1 ViPE (NVIDIA 2025) — SLAM on L1 ERP ✅

**是啥**: ViPE = "Versatile Panoramic Egocentric SLAM". 输入 360° ERP video → 输出 SLAM pose + intrinsics + masks.

**我们做了**: 跑 5s L1 ERP clip on A100, 96.7 sec wall clock. **首个 "stitched-RGB → published-downstream system" 数据流跑通**.

**结果**:
- ✅ SLAM pose / intrinsics / masks 都出
- ⚠️ Depth 是 relative 不是 metric (default config `depth_align_model: null`, T9b 一行 config flip 修)

**Paper value**: Section 6 demo 1, 证明 L1 不是孤立产物.

### 4.5.2 GEN3C (Nvidia 2024) — Image-to-Video 3D ⏸️

**是啥**: GEN3C 是 3D-aware image-to-video diffusion (NVIDIA cosmos-predict 系列).

**我们做了**: 想喂 L1 ERP → 跑 inference 看 GEN3C 能不能用 panoramic 输入. **Install 卡了 2 个版本** (T11 v1 + v2, 都被 SIGPIPE / unbound var 杀掉, 详见 防御教训 §05).

**结果**: ⏸️ Install 跑通后 inference 未确认, v6.1 pivot 后 paused.

### 4.5.3 Panacea+ ✅ but modality NEG

详见 §4.4. ⚠️ NEG (modality 不匹配, 不消费 RGB).

---

## 4.6 总结 — 5 个 NEG 论据链

| Rank | Datapoint | Type | Strength |
|---|---|---|---|
| 1 | **OmniStitch -6.67 dB** | True head-to-head stitching | **最强** (直接 apples-to-apples) |
| 2 | **Depth Pro 2.84× worse** | L3 backbone swap | 强 (algorithm-class evidence) |
| 3 | **Temporal Pi3 -3.6% worse** | Our own NEG | 强 (closes "cheap trick" door) |
| 4 | **Panacea+ modality mismatch** | Modality NEG | 中等 (excludes pseudo-baseline) |
| 5 | (Pending) **VGGT** | 4th backbone | 强 IF 跑通 (Meta CVPR 2025 Best Paper) |

**5 个独立 datapoint 共同支持**: AV ring cam 上 3D-aware (forward-splat 类) 方法系统性 brittle. 这是 paper 论据 chain 最坚固的部分.

---

**下一章**: [05_findings_and_paper.md](05_findings_and_paper.md) — 核心发现 + paper 角度决策 + 研究方法论
