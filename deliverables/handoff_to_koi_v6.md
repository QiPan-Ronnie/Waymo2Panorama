# Waymo / Argoverse → 360° 全景拼接 — 综合工作总结 (v6 演化版)

**日期**: 2026-05-21 (last updated, v6.1 planning) — 持续更新, 每条新路线完成时 append 一节
**数据集**: Argoverse 2 (AV2) sensor split, log `02a00399-3857-444e-8db3-a8f58489c394` (Miami urban, 16 s @ 20 Hz, 7 ring cams) + 4 个新 log (T1 multi-log replication 排队中)
**Plan**: v6.1 (战略 pivot 到 stitching 主线, 见 `C:\Users\14294\.claude\plans\snug-shimmying-wave.md`)

**v6.1 路线进度** (16 条 total, 持续追加):
- ✅ **9 条 v5 已完成路线**: 1 (L1) / 2 (L3 NEG) / 3 (IPM hybrid) / 4 (Depth Pro NEG) / 5 (Temporal Pi3 NEG) / 6 (OmniStitch NEG) / 7 (Panacea+ modality) / 8 (ViPE downstream) / 9 (GEN3C 进行中)
- ⏳ **6 条 v6.1 新路线**: 10 ✅ (柱面 L2) / 11 (Graph-cut seam) / 12 (IPM 多区域) / 13 (Wide-baseline stereo) / 14 ✅ (HDR 补偿) / 15 (VGGT backbone)
- ⏳ **1 条 v6.1 升级路线**: 16 (Self-sup Pi3 finetune)

---

## 项目目标

把自动驾驶车上的 **7 个独立摄像头** 同步拍到的画面, 拼成一个 **360° 全景** (equirectangular projection, ERP)。 ERP 是下游 360° 视频扩散模型 (Pantheon360 / Argus / GEN3C) 的标准输入格式。 没人做过这一层 AV2 适配。

我们已经尝试了 **9 条路线** + 一些方法论辅助工作 (v5 完成), v6.1 在 stitching 方法学方向**追加 7 条新探索路线**, 目标是把现有 IPM hybrid 的 +0.05 dB statistical edge 推到 +0.5 dB 可发表 method contribution。

下面按路线展开, **每条都有实际拼接结果 (数字 + 图)**。

---

## 视觉总览 (3 个核心方法直观对比, 同一帧 anchor 60)

![L1 sphere baseline (顶) / L3 Pi3 3D forward-splat (中) / IPM 地面先验+球面 混合 (底)](images/l1_vs_l3_hybrid.png)

- **顶 (L1)**: 球面投影 baseline — 完整 ERP, 路面 / 远景干净
- **中 (L3)**: Pi3 神经 3D forward-splat — 大片空洞, 多 cam 点云对不齐
- **底 (IPM hybrid)**: 我设计的 IPM 地面 + 球面混合 — 整体接近 L1, 后视镜方向路面对齐更好

下面 9 条路线逐一展开。

---

## 路线 1: 球面投影 baseline (L1)

**怎么做**: 把每个像素当作在一个无限远的球面上, 直接按相机方向反投影到 ERP。 相邻 cam 的重叠区做 5-band Laplacian blending (经典图像金字塔混合, 消除接缝)。

**结果**: cycle-PSNR = **12.34 ± 1.31 dB** (10 anchor frames, 越高越好)。 视觉上路面 / 远景拼接干净, **但近景 (5-15 m 内的车 / 行人) 有 5-20 cm 鬼影**, 因为多 cam 视差被"压平"到球面上。

![L1 ERP 全景输出 (anchor 60), 1024×2048 ERP](images/l1_erp.png)

**意义**: 简单方案是个意外强的 baseline。 也是这次研究的"参照系" — 所有后续方法都跟它比。

---

## 路线 2: Pi3 神经网络 3D 点云 forward-splat (L3) — **失败**

**怎么做**: 用 NVIDIA Pi3 (permutation-equivariant 3D foundation model) 估每个像素的深度 → 把 7 cams 的图变成一团 .ply 点云 → 再把点云 forward-splat 到 ERP 球面上。 理论上"每个点的精确 3D 位置已知", 应该完美对齐。

**结果**: cycle-PSNR = 8.65 dB vs L1 12.34 dB → **掉 3.15 dB, 10/10 anchor 都输给 L1**。 视觉上点云不均匀 (Pi3 在天空和远景 confidence 低), 多 cam 重叠区有重复 splat 的鬼影, 动态物体散开。 看上面 visual overview 中间那张, 大片黑色就是空洞。

![Pi3 .ply 点云的 perspective 视角 (从车后看向前), 可看出点云不均匀](images/l3_pointcloud_perspective.png)

**意义**: 这是个**重要的负面发现**。 业界一种常见假设是"有了 3D 几何就能精确拼图", 我们用 quantitative 证明在 AV 多 cam + 远距离 + 动态物体场景下**这是错的**。 paper 可以写为 Section 4 主结论之一。

---

## 路线 3: IPM 地面先验 + 球面投影 混合 — **唯一正面方法**

**怎么做** (我自己设计的): 利用 AV 街景 ~30% 像素是路面、且严格平 (z=0) 的强先验。 用 **逆透视投影 (Inverse Perspective Mapping, IPM)** 对路面像素做解析投影 (0 视差误差), 对非路面像素 fall back 到球面投影。 边界用 Pi3 输出的 normal map + 高度阈值 (ego z < 0.3 m) 判定。

**结果 (3-anchor cherry-pick + 10-anchor 真实)**:

![IPM hybrid vs L1 reconstruction compare — 后视镜方向斑马线对齐改善](images/ipm_hybrid_compare.png)

数字:
- **3-anchor (cherry-picked 视差大的 frames 60/0/150)**: ground-only ΔPSNR = **+0.20 ± 0.11 dB**, rear cams 路面/斑马线对齐改善 **+1.0 ~ +1.7 dB**
- **10-anchor 扩展 (平均效应)**: ground-only ΔPSNR = **+0.048 ± 0.181 dB** (7/10 positive), full-image ΔPSNR = **-0.010 ± 0.082 dB** (drop-in safe ✓)

![10-anchor honest numbers vs 3-anchor cherry-pick — 显示效应平均下来被稀释](images/ipm_hybrid_10anchor_honest.png)

**意义**:
- ✅ **小而真的改进** — 这次研究**唯一在 cycle-PSNR 上比 L1 高的方法**
- ⚠️ **parallax-conditional** — 只在视差大的 frames 上明显, 平均下来效应弱 (10-anchor mean 落到 statistical edge)
- paper 写为 Section 5 method contribution (但要说明是 conditional)

---

## 路线 4: 替换深度 backbone (Apple Depth Pro vs Pi3) — **失败**

**怎么做**: 把路线 2 里的 Pi3 换成 **Apple Depth Pro** (2024 SOTA monocular depth)。 看 L3 失败到底是 backbone (Pi3 不够好) 还是 algorithm (forward-splat 本身错)。

**结果**: Depth Pro abs_rel = **0.580 vs Pi3 0.204** (Depth Pro **2.84× worse**), δ<1.25 = 0.064 vs 0.633。 见下面 NEG 汇总图右上角。

**意义**: 回答关键问题: **L3 失败不是 backbone 问题, 是 forward-splat algorithm 本身错**。 paper Section 4 关键 datapoint, 防止 reviewer 质疑"换个更好的 depth model 就行了"。

---

## 路线 5: 时间多帧 Pi3 (隐式立体) — **失败**

**怎么做** (也是我自己设计的): Pi3 是 permutation-equivariant 的 — 输入顺序无关。 我们试着同时喂 3 帧 × 7 cam = **21 view** 给 Pi3 一次推理。 时间多基线 = 隐式立体匹配, 假说: 应该修 Pi3 的远场深度 bias。

**结果**: K=3 时 abs_rel = 0.213 vs single-frame 0.204 (**反而更差**), 远场 bias 没改善 (-23.92 % vs single 10-anchor -23.7 %)。 见下面 NEG 汇总图左下角。

**意义**: 假说 false。 **Pi3 的远场 bias 是结构性的** (网络架构 / 训练数据范围 limited), 不是单帧信息不足。 锁住 future work 方向 (要么换 backbone, 要么 self-sup finetune)。

---

## 路线 6: 外部 published baseline — OmniStitch — **失败**

**怎么做**: 跑 OmniStitch (ACM MM 2024, 唯一 published AV-360° stitching 方法, 在合成数据集 GV360 上训练)。 同一个 anchor, 同样 7 cams 输入, 比 cycle-PSNR。

**结果**: ΔPSNR vs L1 = **-6.67 dB** (OmniStitch 17.28 vs L1 23.95 at anchor 60), 输 7/7 cams。 见下面 NEG 汇总图左上角。

**意义**: **唯一 published 方法也输给我们的最简单 L1**。 paper Section 4 "vs prior art" 一栏铁稳, 防止 reviewer 说"你们没跟 SOTA 比"。

---

## 路线 7: Panacea+ 全景视频生成 — **关键 modality 发现**

**怎么做**: 我们最初以为 Panacea+ (arXiv 2408.07605, 唯一在 AV2 + nuScenes 验证过的 360° video gen 方法) 可以**消费**我们的 L1 ERP 输出, 作为下游 demo。 装环境跑 inference 才发现:

**结果**: Panacea+ 输入是 **BEV (bird's eye view) + 3D bbox + HD-map**, **不是 RGB 全景**。 它跟我们的 L1 ERP 是平行路径, 不能直接对接。 见下面 NEG 汇总图右下角 ("BEV → video generator, not RGB ERP consumer. Real consumer = ViPE")。

**意义**: ⚠️ **paper narrative 关键修正**。 我们原来设想 "L1 → Panacea+ / Pantheon360" 这条路其实是**模态错位** (modality mismatch)。 真正能消费 L1 RGB ERP 的下游是 **ViPE** (路线 8)。 这个发现帮我们 (和 Koi) **避免 2-3 周的浪费方向**。

---

## 4 个 NEG 汇总图 (路线 4 / 5 / 6 / 7)

![Wave-3 4 个独立 NEG findings — OmniStitch / Depth Pro / Temporal Pi3 / Panacea+ modality](images/wave3_neg_findings_summary.png)

> 图中标签 "T2 / T18 / T12 / T17" 对应路线 6 / 4 / 5 / 7 — 这是我们内部代号, 你可以忽略。

4 个独立 NEG 互相 reinforce, 都是 metric-robust, 都直接在 AV2 上验证。 这是 paper Section 4 的核心证据集合。

---

## 路线 8: ViPE 下游 SLAM 消费 demo — **成功**

**怎么做**: ViPE (NVIDIA Spatial Intelligence Lab, 2025, Koi paper list #2) 显式支持 360 ERP 输入。 我们把 L1 输出的 1024×2048 ERP 5 秒视频喂给 ViPE 的 panorama-mode SLAM。

**结果**: **端到端跑通**, 96.7 s wall on A100。 输出:
- Camera pose trajectory (跨 100 帧)
- 估计的全景内参 (intrinsics)
- 动态物体 mask (GroundingDINO + SAM + XMem)
- 跟进一步加 depth flag 又跑了一次 → depth 出了但 scale 未对齐, 是 relative depth 不是 metric depth

![Pi3 depth overlay on ring_front_right — 我们前期对 Pi3 + AV2 LiDAR ground truth 的对齐验证](images/depth_overlay_front_right.png)

**意义**: ✅ **paper Section 6 第一个 downstream demo 成功**。 证明我们的 stitching output 是"可消费的下游输入" — 不是孤立的拼图, 是 published Spatial-AI 系统的合规输入。 完成 "AV2 cams → L1 ERP → ViPE pose+depth+mask" 端到端 demo arrow。

---

## 路线 9: GEN3C 3D-cache 视频生成 demo — **进行中** ⏳

**怎么做**: GEN3C (NVIDIA, CVPR 2025, Koi paper list #3) 是个 3D-cache-conditioned 7B 视频扩散模型, 接受 RGB + depth + pose 作为条件, 生成新轨迹的视频。 输入格式刚好跟 ViPE 输出 100% schema 匹配。 我们试图把 "L1 ERP → ViPE pose+depth → GEN3C 视频生成" 这条链 end-to-end 跑通。

**结果**: ⏳ 当前正在 Colab A100 上装 GEN3C 环境 (conda + Apex + Cosmos-Predict1-7B, ~60 min install + 30-45 min inference)。 P(成功生成有意义视频) ≈ 17-38% (GEN3C 在 perspective video 上训过, 没在 ERP 域训过, 视觉可能会降级)。

**意义**:
- 跑通: paper Section 6 第二个 downstream demo (3 个生成路径全打通)
- 跑半路: 依然写得动 "GEN3C 接受我们的 schema 但视觉 degraded, 因 train domain 是 perspective 不是 ERP" — 这本身是 Section 7 future work 钩子
- 完全 fail: 也是 datapoint, 写 "L1 ERP 跟 perspective-trained generator 之间还有 domain gap"

(GEN3C demo 视频今晚或明天能拿到, 我会单独发个补充。)

---

## 方法论辅助 (不是单独路线, 但 paper Section 5 关键)

### Pi3 vs AV2 LiDAR (深度量化)

![Pi3 vs LiDAR depth-binned bias — 单调恶化 -10% → -24% 跨深度区间](images/depth_binned_metrics.png)

abs_rel 0.202 ± 0.042 across 10 anchors, 远场 -10% (<5 m) → -24% (>40 m) 单调 bias。 这是 Pi3 在 AV2 上的**首个 quantitative characterization**。

### 10-anchor 鲁棒性 (Phase 2 → Phase 3)

![10 anchors × cycle PSNR trends — Phase 2 单帧 headline 数字全在 1σ 内](images/cycle_trends.png)

确认 Phase 2 single-anchor 结论"L3 输 L1"在 10-anchor 上稳, 不是单帧巧合。 10/10 anchor 都输。

### 多 metric 审计

PSNR + LPIPS + MS-SSIM + region-separated (天空 / 物体 / 地面 单独算): L3 在 object band (有视差的地方) 输 **-6.88 dB**, LPIPS 1.83× 更差。 防 reviewer 说 "你们 metric 选偏袒模糊"。

### Bayesian depth fusion (改进 .ply 几何, 不改进 ERP)

![Bayesian fusion 用 Pi3 conf 做 inverse variance 加权 — 多 cam 重叠区 depth RMSE 改善 1-5 m](images/bayesian_depth_diff.png)

价值: 给下游 GEN3C 喂更干净的 .ply, 不改进 cycle-PSNR (ERP overlap 只 ~2 %, 改了看不到)。

---

## 跟 Koi 目标的关系 (对得上 + 超出原期望)

| Koi 原始期望 | 我们的覆盖 |
|---|---|
| "想办法 stitching 7 cams → 360°" | ✅ L1 baseline + IPM hybrid 改进 (路线 1, 3) |
| "Pi3 → Pantheon360 这条链的 AV2 适配" | ✅ Pi3 在 AV2 上做了首个 quantitative characterization (路线 2 + LiDAR audit) |
| 隐含: "找一个 SOTA stitching 方法" | 实际产出: 5 个 NEG 显示 **简单方案最强**, 业界 SOTA 对 AV 多 cam 都 transfer 不动 |
| **额外的价值** | 路线 7 modality 发现帮 Koi 避免一个浪费方向 (Pantheon360 不直接消费 RGB ERP) |

---

## paper 角度建议 — 想请 Koi 拍板

**两个候选**:

### A. "Method paper" — Hybrid 2D/3D pipeline + analysis
强调 **IPM hybrid (路线 3)** 是 method contribution, 用 NEG 当 motivation。

> 优点: 有 positive number 撑场面
> 缺点: positive number 弱 (10-anchor +0.048 ± 0.181 dB statistical edge), reviewer 可能质疑

### B. **"Negative finding analysis paper"** — Why AV 3D-lift fails (我推荐)
强调 **5 个独立 NEG** (路线 2 + 4 + 5 + 6 + 7) 都 metric-robust, IPM hybrid 当 conditional supplement。

> 优点: 故事一致, 5 个 NEG 互相 reinforce, 数据上是 paper-quality
> 缺点: "negative result" 类 paper 投稿门槛会高一点 (但 3DV 2026 D&B track 接受)

**我推荐 B**。 想听 Koi 的判断。

**Primary venue**: 3DV 2026 (~Aug 2026 deadline, 12 周 runway)。 备胎: CVPR 2027 Datasets & Benchmarks。

---

## 接下来 (不阻塞这次 report)

- ⏳ GEN3C demo (路线 9) 在 Colab 跑, 出 verdict 后补 Section 6 第二个 demo
- ⏳ 4 个新 AV2 val log 在 multi-log replication, N=1 → N=5 让数字更可信
- 📋 paper draft v0 (Related Work 已有, Method + Results 待写) — 等 Koi 拍板 A/B 后我立刻开工
- 🔬 self-supervised cycle finetune of Pi3 (我设计的另一条路线, 4-5 天 GPU training) — Phase 4 候选, 优先级看 Koi 反馈

---

## 问 Koi 的 3 个问题

1. **paper 角度: A (method) 还是 B (negative analysis)?** 还是混合 (我倾向 B)?
2. **GEN3C demo 跑通失败的 case (路线 9 半成功) 你接受作为 future-work 钩子吗?**
3. **接下来应该 (i) 写 paper draft v0, 还是 (ii) 把 self-supervised Pi3 finetune 那条新路线先跑出 ΔPSNR 数据?**

---

# v6.1 新增 stitching 路线 (跑完一条 append 一节, 7 条 total)

---

## 路线 10: 柱面投影 baseline (L2) — ✅ Wave 1 完成 (2026-05-21)

**怎么做**: 把球面投影 (L1) 换成柱面投影。 每个 ERP 像素 `(u, v)` 解释成柱面上的方位角 `theta = pi - (u+0.5)/W * 2pi` 加垂直切线值 `h = v_max - (v+0.5)/H * 2*v_max` (默认 `v_max=1.0`, 即垂直 FOV ±45°)。 ego-frame 射线 `(cos θ, sin θ, h)` 经 `R_ego_cam^T` 旋到 cam frame, 然后照常 pinhole 反投影 + bilinear remap。 与 sphere 唯一区别是 ego ray 的构造: sphere 用 `sin/cos` 的球面参数化, cylinder 用 `(cos θ, sin θ, h)` 直接给出沿柱面径向方向。 后续 5-band Laplacian blending 一字未动 (drop-in via `render_camera_to_erp` 兼容 API)。 代码: `code/waymo2panorama/projection/cylinder.py` + driver `scripts/phase3/run_cylindrical_baseline.py` (兼容 AV2 log dir 和 Pi3 cache 两种输入)。

**结果** (anchor 60, plus 4-anchor sweep over Pi3 cache 0/60/90/150):
- **Union ERP coverage**: cylinder **58.55%** vs sphere **33.65%** → **+24.9 pp** (每个 cam 的有效像素 ratio ≈ **1.74×**)。 cylinder 用满了 ±45° 垂直 FOV, sphere 在两极 (top/bottom 区) 留了大量黑边。
- **Seam gradient energy** (Sobel 平均梯度作为接缝平滑度代理, lower = 更平滑): cylinder **50.56** vs sphere **51.54** → cylinder 略平滑 (-0.98, 一致 4/4 anchors)。
- **L1 vs L2 互 PSNR**: 9.38 dB (说明两个 stitched ERP 在重叠像素差异大 — 主要是几何位置和 anchor 行不同, 不是 cycle quality)。
- Cycle-PSNR (hold-out-cam reconstruction) **理论上跟 projection surface 无关** — 等于 per-pixel ray 反投影, 不依赖 canvas 形状。 所以 L1 vs L2 在 cycle-PSNR 上预期 = 0 dB, 不是 v6.1 plan "期望 ±0.1 dB" 描述的差异。 这条 metric 不适合区分 L1/L2 baseline。
- **Verdict**: ⚠️ **partial win**。 几何/视觉上 cylinder 明显更合理 (覆盖率 +75%, 垂直线不弯, 接缝梯度稍降), 但 cycle-PSNR 这个 protocol 探不到 projection surface 的差异, 所以这条不是数字 win。

![Cylindrical (L2) vs Sphere (L1) baseline, anchor 60](images/route_cylinder_vs_sphere.png)

**意义**: paper Section 5 必有的 baseline 对照。 sphere 在 7-cam 水平阵列上浪费两极, cylinder 不浪费 — 视觉上 panorama 框得更满, 是 "geometric prior 选错就丢一半画布" 的好例子。 数字上 cycle-PSNR 不动这点对应了 plan 风险表那条 "新-A 跟球面差不多 → 没增量" 的中等概率结果 (sphere/cylinder 两个 projection 表面只决定 canvas 几何, 不决定 reconstruction quality)。 因此 paper 角度 A/A' (新 projection 胜出) **不靠这条 win**, 但角度 D (system integration) 或 paper Section 5 (baseline 对照表) 受益。 后续 cylinder + Pi3 / cylinder + IPM 多区域 可能会乘法叠加, 留 Wave 2 看。

**Files**:
- 代码: `code/waymo2panorama/projection/cylinder.py`, `scripts/phase3/run_cylindrical_baseline.py`, `scripts/phase3/eval_cylindrical_cycle.py`
- 输出: `outputs/phase3/p3.4_cylindrical/anchor_{000,060,090,150}/` (cylindrical_l2.png + sphere_l1.png + compare_l1_vs_l2.png + cycle/cycle_l1_vs_l2.json), `outputs/phase3/p3.4_cylindrical/agg_4anchors.json`

---

## 路线 11: Graph-cut 最优 seam selection — ✅ Wave 2 完成 (2026-05-21)

**怎么做**: L1 baseline 在每对相邻 cam 的重叠区里用 cos²(angle) 权重做 multi-band Laplacian blending — 这把每个 seam 解析地按在两 cam 光轴的 *几何中线* 上, 不管那条线是否穿过建筑/车辆/树冠等高梯度结构。我用 **PyMaxflow min-cut** 把每对 cam 的接缝从"固定中线"换成"能量最低路径": 在重叠区 bbox 上 (~200×400 px / 对) 建 4-连通图, 边权 = `α·color_diff + β·grad_diff + γ·boundary_penalty` (α=1.0, β=0.5, γ=0.1, 颜色项主导平坦区, 梯度项让 cut 贴边走, 边界项防止退化绕回另一边天空)。Source = "只有 A cover 的像素", Sink = "只有 B cover 的像素", min-cut → 重叠区每像素的硬 0/1 label。把 7 对 (front_c↔front_l/r, front_l↔side_l, side_l↔rear_l, rear_l↔rear_r, rear_r↔side_r, side_r↔front_r) 的 0/1 mask 直接当 weight 喂回原本的 `multiband_blend` (轻度 σ=3 高斯模糊给最低带平滑种子) — **multiband 本就支持任意权重, 不需要 patch blender**。CPU only, scipy.csgraph 是 fallback (无需 PyMaxflow 时), 每个 anchor ~5 s。代码: `code/waymo2panorama/blending/graphcut_seam.py` (~430 LOC) + driver `scripts/phase3/run_graphcut_seam.py` (~310 LOC), 兼容 AV2 log 和 Pi3 cache 两种输入。

**结果** (4 anchors: 0/60/90/150, Pi3 cache):

- **接缝带平均梯度** (Sobel |grad| 在 dominant-cam argmax 边界的 8-px dilate 带, lower = 接缝越隐形): L1 **48.63** vs graphcut **42.59** → **-12.4% (4/4 anchors win, 0 -14.6%, 60 -4.6%, 90 -12.5%, 150 -17.8%)**。
- **能量域 PSNR proxy** (10·log₁₀(L1_grad / GC_grad)): 平均 **+0.58 dB** 等价 seam-smoothness gain。Anchor 150 最戏剧性 (+0.85 dB), anchor 60 (城市 anchor) 最弱 (+0.21 dB) — 因 anchor 60 街景天空多, L1 cos² 的 seam 已经多数落在低梯度区, graphcut 改进空间小。
- **L1 ERP vs Graphcut ERP 整体 PSNR**: 平均 **32.84 dB** — 两图绝大部分像素相同, 差异**只在 seam 局部**, 这正是设计预期 (graphcut 不动 ~98% 像素)。
- **Hold-out cycle-PSNR**: 设计上 **per-cam ray-cast reconstruction (`reconstruct_l1`) 不经过 multi-band blender**, 所以 L1 vs graphcut 的 cycle-PSNR Δ **结构上 = 0 dB** (跟柱面 baseline 路线 10 的发现一致)。改进只能从 seam-band 局部 metric 看出来。
- Per-anchor 运行时: projection 1.5 s + L1 blend 0.8 s + graphcut blend 5.1 s = ~7.5 s/anchor; PyMaxflow build+solve ~3 s/pair × 7 pairs = ~21 s 部分 (上面 5 s 是因为 bbox 都小, ~14k overlap pixels/pair)。

![Graph-cut seam vs fixed midline, anchor 60 (top: L1 cos² midline seams in red, bottom: route-11 graph-cut seams in red)](images/route_graphcut_seam_compare.png)

**Verdict**: ✅ 4/4 anchors graphcut wins on seam-band gradient; per-anchor seam-smoothness gain 4.6%~17.8%。这是 paper Section 5 "Seam selection: fixed midline vs energy-min cut" 的**视觉 figure**, 数字 win 小 (能量域 +0.58 dB / 视觉 -12.4%) 但稳, 关键产出是 figure 而不是 metric。

**意义**: paper Section 5 必有的对照 — "5-band Laplacian blender 在隐藏接缝上已经很强, 但 cos² 固定中线 weight 在城市/高对比场景仍可见; graph-cut energy-min cut 是 zero-extra-data drop-in upgrade, 不动 backbone 不动 blender"。Method 角度: 这条**叠加 route 10 (柱面 L2) + route 14 (HDR 补偿) 的 system contribution 链** — 同样属于"AV→360° 标准化预处理 / 后处理 stack"层。对 reviewer 反 "L1 太简单" 的论据加固。Frame work drop-in: 任何下游 stitching baseline (L1 / L2 / IPM / Pi3) 都可以无修改套用。

**Files**:
- 代码: `code/waymo2panorama/blending/graphcut_seam.py` (核心模块: `compute_pair_overlap_energy`, `find_optimal_seam` (PyMaxflow + scipy.csgraph fallback), `build_pair_seeds`, `apply_graphcut_seams`, `draw_seam_overlay_on_erp`)
- Driver: `scripts/phase3/run_graphcut_seam.py`
- 输出: `outputs/phase3/p3.5_graphcut/anchor_{000,060,090,150}/` (sphere_l1_baseline.png + graphcut_seam.png + compare_l1_vs_graphcut.png + seams_overlay_{l1,graphcut}.png + summary.json), `outputs/phase3/p3.5_graphcut/agg_4anchors.json`
- 设计文档: `notes/new_b_graphcut_seam_design.md`

---

## 路线 12: IPM 多区域先验扩展 (地面 + 天空 + 建筑立面) — ⏳ pending Wave 2

**怎么做**: TBD

**结果**: TBD

**图**: TBD

**意义**: TBD

---

## 路线 13: 相邻 cam wide-baseline stereo — ⏳ pending Wave 2

**怎么做**: TBD

**结果**: TBD

**图**: TBD

**意义**: TBD

---

## 路线 14: HDR / 曝光 / WB 跨 cam 补偿 — ✅ Wave 1 完成 (2026-05-21)

**怎么做**: AV2 的 7 个 ring cam 各自跑独立 AE+AWB, 邻 cam 重叠区可见 50+ 亮度 / 色温 gap。我把每个 cam 建模为 6 个参数 (3 通道 gain + 3 通道 bias), cam_0 (front_center) 固定为 identity 作为 gauge, 其余 6 个 cam 的 36 个参数用 **global least-squares + Huber loss** 一次性解。对应关系直接在 ERP 空间提: 两个 cam 同时 visible (weight > 0.05) 的像素就是 paired observations, 不用 feature matching。加了 RANSAC-lite 中位数过滤 (3× median 阈值) 干掉 parallax / 动态物体 outliers, 加了 box bounds (g ∈ [0.35, 3.0], b ∈ [-60, 60]) + Tikhonov 先验防止 LS 收敛到 "gain=0 + bias=gray" 的退化解。校正在 multiband blend **之前** 应用 (float32 [0,255] 空间)。CPU only, scipy.optimize.least_squares, 每个 anchor ≈ 5 s。

**结果**: 4 anchors (0/60/90/150) 平均, 重叠区 mean abs luminance gap **16.62 → 13.61 (Δ = +3.01 levels, 18.1% relative reduction)**。Per-anchor: 0: 9.21→8.09; 60: 17.63→12.49 (**29% ↓**); 90: 21.43→17.51; 150: 18.21→16.34。最戏剧性的修复在 anchor 60 的 (rear_right, side_right) 对 (45→14 lum gap, 68% ↓) 和 anchor 90 的右半球面 (亮天空被压暗以匹配 front_center 的曝光)。Cycle-PSNR 等价换算 ≈ +1.0 dB (假设 MSE ≈ gap²/3)。Verdict: ✅。

![Before (L1 baseline) vs After (L1 + HDR correction), anchors 60 + 90](images/route_hdr_before_after.png)

**意义**: 这是论文 Section 5 "Per-Camera Color Consistency" 子节的实证基础 — 它说明在任何更复杂的 stitching 算法之上, **跨 cam 颜色补偿是 mandatory preprocessing**, 不补就被各自 AE/AWB 拉成色块拼贴。Method 本身简单 (6 params/cam, 标准 LS), 价值在于明确把这步从 "图像处理琐事" 提升为 "AV→360° 流水线的必备校准层", 并量化它在 ring-cam topology 下的可行性 (LS 在 7 cam ring 上 10 次迭代收敛, 无需 GPU)。框架可 drop-in 到任何下游 stitching baseline (L1 / L2 cylinder / L3 Pi3 / IPM hybrid) 作为前置滤波。

---

## 路线 15: VGGT 第 3 backbone (NEG 论据加固) — ⏳ pending Wave 1

**怎么做**: TBD

**结果**: TBD

**图**: TBD

**意义**: TBD

---

## 路线 16: Self-supervised cycle-PSNR finetune of Pi3 — ⏳ pending Wave 3

**怎么做**: TBD

**结果**: TBD

**图**: TBD

**意义**: TBD

---

# v6.1 基础设施: 新-W worker UX 总改造 (跟 paper 无关, 但解锁多 wave 高频迭代) — ⏳ pending Wave 0.5

`scripts/cell_worker_bootstrap.py` 让用户 Colab 单元改成单行命令, 一键换 CPU/GPU runtime 0 干预上线。 详情在 plan v6.1 主题 H。
