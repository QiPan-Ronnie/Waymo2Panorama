# Waymo2Panorama — 第一周交付 (精简版)

**致**: Koi  ·  **作者**: Ronnie  ·  **时间**: 2026-05-15 → 2026-05-20
**仓库**: https://github.com/QiPan-Ronnie/Waymo2Panorama @ tag `v0.2-d1-resolved`
**完整版**: `deliverables/handoff_to_koi_2026-05-20.md` (含全部细节 + 反思 + 数字)

---

## TL;DR

- ✅ **核心交付**: AV2 7-camera → 1024×2048 ERP **360° 全景**跑通 (L1: sphere projection + multi-band blending)。
- ✅ **3D backbone 选型**: Pi3X 胜 (vs DVGT, 后者因 dinov3 gated weight + HF schema 不兼容, 8 次尝试均失败)。 Pi3X 7-view forward on A100 = 8.35s, K-recovery 误差 ≤ 2.1%。
- ⚙️ **L3 3D-lift 探索**: Sim(3) 对齐误差 0.157m mean → 输出 690K colored 3D 点 (`.ply`)。 但 forward-splat 到 ERP 视觉**不优于 L1**, L3 真正价值在下游 3D 消费 (Pantheon360, 3DGS)。

---

## 1. 核心交付 — 360° 全景

![L1 ERP — AV2 7-ring 拼成的 1024×2048 360° 全景图 (anchor frame 0, Miami 街景)。 横向覆盖 azimuth 360°, 上下黑边是仰角覆盖盲区, 由 Phase 3 diffusion 填。](images/l1_erp.png)

- **输入**: AV2 val log `02a00399-...`, 7 ring camera (front_center portrait + 6 landscape)
- **算法**: backward sphere projection (每 ERP 像素 → ego 方向 → cam 像素) + cos² feather + 5-band Laplacian blending
- **输出**: 1024×2048 PNG @ 20Hz · 5-10s 序列直出 MP4
- **已知局限** (设计上接受): 近物 (<5m) parallax ghost, 由 L3 修

---

## 2. 输入 sanity check (Phase 0.5)

![AV2 一帧 9 cam 2×4 mosaic. 时间同步 22ms (< 50ms 阈值), 内外参 feather 可读, 几何信息完整。](images/spike_mosaic.png)

---

## 3. Pi3 vs DVGT 选型

| Metric | Pi3X | DVGT-1 |
|---|---|---|
| 一次跑通 | ✅ 64s | ❌ 8 次均失败 |
| Forward (A100 bf16, 7 view) | **8.35s** | — |
| Peak GPU mem | 7.5 GB | — |
| K-recovery vs AV2 真值 | **±1% 典型** | — |
| 安装 | `Pi3X.from_pretrained()` 一行 | clone DVGT + clone dinov3 + 装 deps + gated weight + 转格式 + key schema 不兼容 |

**DVGT 卡在**: HF 上 `facebook/dinov3-vitl16-pretrain-lvd1689m` 用 transformers-style keys (`embeddings.cls_token`...), DVGT 期待 Meta 原生 keys (`cls_token`, `blocks.X.attn.qkv`...), `load_state_dict` 满屏 unexpected keys。 修复需写 ViT-L/16 key remapper, 超出 D1 scope。

**决议**: Phase 2-4 主线用 Pi3X。 tag `v0.2-d1-resolved`。

---

## 4. L3 3D-lift 输出

Sim(3) 拟合 (Pi3 cam 位置 vs AV2 ego 真值): **scale = 1.0346, mean residual = 0.157 m, max = 0.218 m**。 → Pi3 几乎本来就是 metric。

应用 Sim(3) 后, **690,360 个 colored 3D 点**在 AV2 ego 米制坐标系。

![L3 融合 .ply (perspective). 红球 = ego, 红/绿/蓝 axes = +x前/+y左/+z上。 左右建筑外立面 (locustprojects 招牌可读), 右前 ~5m 白车, 路面带"网格波纹" — Pi3 504×504 grid 投影到地面的固有采样模式, 不是 noise。](images/l3_pointcloud_perspective.png)

![L3 .ply (top-down). 道路沿 +x 方向延伸, 黄色中线居中。 路面厚度 ~0.5-1m, 反映 Pi3 单目深度 ±0.3m 的单点 variance (LiDAR 是 <5cm)。](images/l3_pointcloud_topdown.png)

![Per-view depth: ring_front_right (含白车). 紫=近 (~1m), 黄=远 (~30m+), 黑=被滤 (天/低 conf/远景)。 白车清晰显示 Pi3 对近物估计良好。](images/depth_overlay_front_right.png)

---

## 5. 关键 negative finding — L3 forward-splat ERP **不优于** L1

![L1 vs L3 raw vs L1+L3 hybrid 三面板。 顶: L1 干净; 中: L3 地面鼓包+稀疏; 底: hybrid 引入"双白车" (L1 错位 + L3 修正位置共存)。](images/l1_vs_l3_hybrid.png)

试过 3 种参数组合 (raw / filter 严格 / hard-mask hybrid), 视觉都不及 L1。 原因:
- Pi3 单目深度 ±0.3m variance → 路面在 ERP 鼓包
- L1/L3 同物 ERP 位置不同 → blend 出双影
- 天空 conf 低被砍 → 大片黑色

**结论**: forward-splat to ERP 不是 L3 价值的正确通道。 **L3 的产物应是 3D scene (`.ply` + depth maps), 喂给下游 3D-aware 消费 (Pantheon360, 3DGS, depth-conditioned diffusion)**, 不是再做一张 2D 全景图。 想要 L3 ERP 在视觉上超 L1, 需用 raycast + z-buffer 或 3D Gaussian Splatting (Phase 4 题目)。

---

## 6. 衍生产物 — `agent-colab-queue` v0.1.2

调试过程中发现 `colab-mcp` 对长任务不稳, 投入 ~5h 实现了一套 **Drive-as-queue agent ↔ Colab 框架** (FastMCP + Drive + GitHub)。 后续 Pantheon360 / 360° diffusion 训练都可复用。 仓库: https://github.com/QiPan-Ronnie/agent-colab-queue

---

## 7. 下周建议路径

| 优先级 | 任务 | 估时 |
|---|---|---|
| Tier 1 | **Cycle-consistency** — hold-out 1 cam, L1/L3 重建 PSNR/SSIM/LPIPS | 1 天 |
| Tier 1 | **Pi3 vs LiDAR GT depth** — AV2 自带 LiDAR, 算 absolute relative error | 1 天 |
| Tier 2 | 多 sequence 扩展 (现在只 1 log × 1 anchor) | 2-3 天 |
| Tier 2 | OmniStitch baseline (Track D) 接入 | 2 天 |
| Tier 3 | 3DGS / proper raycast L3 ERP (Phase 4) | 1-2 周 |

---

## 数字快查

| | |
|---|---|
| AV2 数据 | log `02a00399-...`, 319 frames @ 20Hz, 7 ring + 2 stereo |
| L1 ERP | 1024 × 2048, ~100 frames/5s sequence |
| Pi3X forward (A100 bf16, 7 view) | **8.35 s**, peak 7.5 GB |
| Pi3 K-recovery 误差 | +0.06% ~ +2.08% (mean ~1%) |
| Sim(3) 对齐残差 | mean **0.157 m**, max 0.218 m |
| L3 .ply | **690,360** 点, 9.9 MB |
| DVGT 尝试 | 8 次, 全失败 |

完整 forensics + 反思 + 时间线 + commit 索引: `deliverables/handoff_to_koi_2026-05-20.md`
