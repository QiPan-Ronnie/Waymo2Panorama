# Waymo / AV2 → 360° 拼接 — W2 v6.1 mid-CPU 完整快照

**致**: Koi · **作者**: Ronnie · **日期**: 2026-05-21 (Phase 3 W2, v6.1 mid-wave)
**仓库**: `github.com/QiPan-Ronnie/Waymo2Panorama @ main`
**数据**: Argoverse 2 sensor val, log `02a00399-3857-444e-8db3-a8f58489c394` (Miami urban, 7 ring cams, 16 s @ 20 Hz) + 4 个新 val log (T1 multi-log)

---

## TL;DR (5 行)

1. **拼接路线 8 条** (3 老 L1/L3/IPM T14 + 5 新 v6.1 CPU 全完) — **3 条正面贡献** (新-C / 新-E / IPM T14), **1 条视觉胜场** (新-B), **2 条结构 NEG** (L3 主 NEG, 新-D sparse 不够)。
2. **下游消费任务 3 条** (ViPE SLAM ✅ / GEN3C 3D-cache ⏸️ install 失败 paused / Panacea+ modality NEG)。
3. **外部 published baseline 测试 3 条** (Depth Pro / Temporal Pi3 / OmniStitch) 全 NEG, 强化 paper Section 6 "AV ring 3D-aware 全 brittle" 论据。
4. **还剩 2 条 GPU 路线** 等执行: 新-F VGGT 第 3 backbone (~6-16h on A100) + T13 self-sup Pi3 finetune (~5d on A100, 高风险高收益)。
5. **paper 角度 ask**: 从 T-Koi-3 时的 B-with-C 推到 **A' Method paper** (3 个 stack-able 正贡献 + 4-5 NEG 当 motivation) — 见 §4。

---

## §1 拼接路线 (8 条统一总表 — 老 + 新合并)

| # | 路线 | 怎么做 (一句) | 关键数字 vs L1 | 差距 / 不足 | 潜力 | 状态 |
|---|---|---|---:|---|---|---|
| 1 | **L1 球面 baseline** | 球面投影 + 5-band Laplacian blending | cycle **12.34 ± 1.31 dB** (10-anchor) | 近物 5-20 cm ghost, sky 两极浪费 33% canvas | ⭐⭐⭐ 强 baseline | ✅ |
| 2 | **L3 Pi3 forward-splat** | Pi3 估深度 → .ply → forward-splat 到 ERP | **-3.15 ± 0.72 dB** (10/10 输) | naive 3D-lift 结构性失败, multi-cam splat 噪声 | ❌ paper Section 6 主 NEG | ✅ |
| 3 | **IPM 地面 hybrid (T14)** | 地面像素 IPM 解析投影 (z=0), 非地面回 sphere | **+0.05 ± 0.18 dB full**, **+0.20 dB ground (cherry)** | parallax-conditional, 平均下来 statistical edge | ⭐⭐ partial win | ✅ |
| 4 | **新-A 柱面投影 L2** | 球面换柱面, 5-band blending 不变 | **+24.9 pp coverage**, cycle = 0 (结构) | hold-out reconstruction 对 canvas 不敏感, 不动数字 | ⭐⭐ baseline 对照必备 | ✅ |
| 5 | **新-B Graph-cut seam** | min-cut 替代 cos² 固定中线 seam | **seam |grad| -12.4% (4/4)**, cycle = 0 | reconstruction 不经 blender, cycle metric blind | ⭐⭐⭐ **paper Section 5 主图** | ✅ |
| 6 | **新-C IPM 多区域** | ground + sky + building 三区域 IPM 决策树 | **ground mask +0.20 dB (4× T14)**, full +0.05 | building 分支 cycle eval 不稳 → default OFF | ⭐⭐⭐ **最高 paper upside** | ✅ |
| 7 | **新-D Wide-baseline stereo** | 相邻 cam 已知外参 + LightGlue + DLT 三角化 | 5/7 pair OK, ~44 inlier pts/pair | 太稀疏不能修 dense ERP, 2/7 pair NEG | ⭐⭐ NEG 论据 + 视觉 | ✅ |
| 8 | **新-E HDR cross-cam** | global LS + Huber, 6 params/cam (gain+bias) | **overlap gap -18.1%**, anchor 60 -29%, cycle proxy **+1.0 dB** | proxy not direct cycle (Wave 3 补测) | ⭐⭐⭐ mandatory preprocess | ✅ |

**已完成 8/8 拼接路线**。 **潜在最好的 (按 paper 价值排)**:
- 🥇 **新-C IPM 多区域**: 已有 +0.20 dB on ground mask (4× T14), 加 building cross-cam consensus 可再挖
- 🥈 **新-E HDR**: 简单可靠, +1.0 dB proxy, 是任何 stack 的 mandatory preprocess
- 🥉 **新-B graph-cut**: cycle 不动但视觉 figure 主图, 跟所有 method 正交可叠加

---

## §1.1 详细 — 拼接路线 1: L1 球面 baseline

**怎么做**: 每个 ERP 像素 `(u, v)` → 方位角 `(θ, φ)` → ego-frame 射线 → 通过 `T_ego_cam^T` 旋到 cam frame → pinhole 反投影 + bilinear remap。 7 cams 用 cos²(angle from optical axis) 权重 + 5-band Laplacian blending 融合。

**结果**: cycle-PSNR **12.34 ± 1.31 dB** (10 anchor), 路面/远景拼接干净。

**差距**: 近物 (5-15 m) 有 5-20 cm 鬼影 — 多 cam 视差被压平到球面。 Sky 区域两极浪费 ~33% canvas。

![L1 ERP 全景输出 (anchor 60)](images/l1_erp.png)

---

## §1.2 详细 — 拼接路线 2: L3 Pi3 forward-splat (核心 NEG)

**怎么做**: 用 Pi3 (CVPR 2025 permutation-equivariant 3D foundation model) 估每个像素的 3D 位置 → 把 7 cam 的图变成一团 .ply 点云 → forward-splat 到 ERP 球面上 (z-buffer 取最近点)。

**结果**: cycle-PSNR **8.65 vs L1 12.34 → 掉 3.15 dB, 10/10 anchor 全输**。 视觉上点云不均匀 (Pi3 在天空 / 远景 confidence 低), multi-cam 重叠区有重复 splat 鬼影, 动态物体散开。

**差距**: naive 3D-lift forward-splat 在 AV 多 cam + 远距离 + 动态物体场景结构性失败。 不论换 backbone (Depth Pro 2.84× 差, 见 §3) 还是堆时间 (Temporal Pi3 也输, 见 §3) 都救不回来。

![L1 (顶) / L3 forward-splat (中) / IPM hybrid (底) 同 anchor 60 对比](images/l1_vs_l3_hybrid.png)

**意义**: paper Section 6 主 NEG — 业界假设 "有 3D 几何就能精确拼图" 在 AV 场景下不成立。

---

## §1.3 详细 — 拼接路线 3: IPM 地面 hybrid (T14)

**怎么做**: 利用 AV 街景 ~30% 像素是路面 + 严格平 (z=0 in ego frame) 的强先验。 用逆透视投影 (IPM) 对地面像素做解析投影 (0 视差误差), 非地面像素 fall back 球面。 边界用 Pi3 normal map + 高度阈值 (ego z < 0.3 m) 判定。

**结果**: 
- 3-anchor cherry-pick (60/0/150): ground-only **+0.20 ± 0.11 dB**, rear cams 斑马线对齐 **+1.0~1.7 dB**
- 10-anchor 真实平均: full **-0.010 ± 0.082 dB** (drop-in safe), ground **+0.048 ± 0.181 dB** (parallax-conditional)

**差距**: 视差大的 frame 上明显, 平均下来弱到 statistical edge。 这是 T-Koi-3 时的 partial win。

![IPM hybrid vs L1 — 后视镜方向斑马线对齐](images/ipm_hybrid_compare.png)

**潜力**: 已被 **新-C 升级到 +0.20 dB on ground mask** (4× 提升), 见下。

---

## §1.4 详细 — 拼接路线 4: 新-A 柱面投影 L2

**怎么做**: 球面 (sphere) → 柱面 (cylinder)。 ERP 像素 `(u, v)` 解释为柱面方位角 + 垂直高度比 (而不是球面的方位角 + 仰角 asin)。 AV 多 cam 水平排列, 柱面几何先验更贴合。 5-band Laplacian blending 不动。

**结果**: 
- Union ERP coverage: cylinder **58.55%** vs sphere **33.65%** = **+24.9 pp** (每个 cam 有效像素 1.74×)
- Seam gradient: -0.98 (一致 4/4 anchor 略平滑)
- Cycle-PSNR: ≈ 0 dB Δ (hold-out reconstruction 不依赖 canvas 形状)

**差距**: cycle-PSNR 不动 — 这条 metric 在 reconstruction protocol 上 blind to projection surface。 数字上不能 claim, 视觉 + coverage 上明显胜。

![Cylinder (L2) 顶 vs Sphere (L1) 底, anchor 60](images/route_cylinder_vs_sphere.png)

**意义**: paper Section 5 必有的 baseline 对照 — "geometric prior 选错丢一半画布"。

---

## §1.5 详细 — 拼接路线 5: 新-B Graph-cut 最优 seam selection

**怎么做**: L1 用 cos²(angle) 权重 → seam 解析地按在两 cam 光轴的几何中线上, 不管那条线是否穿建筑/车辆。 新-B 用 **PyMaxflow min-cut** 在每对 cam 的重叠区找能量最低路径 (边权 = color diff + grad diff + boundary penalty)。 7 对的 0/1 mask 直接喂回 multiband_blend (**不 patch blender, multiband 已支持任意权重**)。

**结果**: 
- 接缝带 |grad| (Sobel 8-px dilate 带): L1 48.63 → graphcut **42.59 (-12.4%, 4/4 anchor win)**
- 能量域 PSNR proxy: **+0.58 dB** seam-smoothness gain
- Cycle-PSNR Δ: 结构上 = 0 (reconstruct_l1 不经 multi-band blender)
- 每 anchor ~5 s CPU

**差距**: cycle metric 看不出来, 主胜场是视觉 figure (接缝从建筑边缘移到 uniform 区域)。

![L1 cos² midline seams (顶, 红线) vs 新-B graph-cut seams (底, 红线), anchor 60](images/route_graphcut_seam_compare.png)

**潜力**: ⭐⭐⭐ paper Section 5 主视觉图。 跟所有其他 method 正交, 可叠加。

---

## §1.6 详细 — 拼接路线 6: 新-C IPM 多区域 (最高 paper upside)

**怎么做**: 把 T14 单一地面 IPM 扩展为**三区域决策树**:
- **Ground** (复用 T14): ego z < 0.3 m AND |n_z| ≥ 0.85 (法线垂直) AND 距离 ≤ 60 m
- **Sky** (新): Pi3 log-conf < -2.0 OR (远 + 高 + 图像上半) — 走 sphere 不变, 只是标签
- **Building** (全新): ego z > 0.5 m AND 法线接近水平 — 32×32 tile RANSAC 拟合垂直平面 `n_x*x + n_y*y = d`, 按 inlier 像素逐 ray 求交

法线从 `local_points_<cam>.npy` 用 finite-diff + box-filter 估出 (Pi3 cache 没有 normal map)。

**结果** (4 anchor mean cycle-PSNR):

| Config | Full ERP | Ground mask | Building mask |
|---|---:|---:|---:|
| L1 sphere | 10.85 dB | — | — |
| T14 ground-only | +0.05 | +0.05 | — |
| **新-C ground+sky (ship default)** | **+0.05** | **+0.20** (4× T14!) | — |
| 新-C with building | +0.01 | +0.20 | **-0.33** |

**差距**: 
- ✅ ground 分支显著优于 T14 (normal-aware 分割剔除了 T14 纯 z-threshold 误判的 false-positive 地面)
- ❌ building 分支 cycle eval 不稳 — 每 cam RANSAC 拟合的平面参数 (n_x, n_y, d) 离散较大, 对 held-out cam 不通用
- ⚠️ **按设计 hard floor 触发**, default `--enable-building False`, ship ground+sky 二区域

**诚实 framing**: ground mask +0.20 dB 是真的, **full image 仍只 +0.05 dB** (T14 一致)。

![L1 / T14 / 新-C 三方对比 (anchor 60)](images/route_ipm_multi_region_compare.png)

**潜力**: 🥇 **最高 paper upside**。 Building 分支 cross-cam plane consensus (union-find 合并相邻 cam 的相近平面) 是 next-step, 期望可推 full image Δ 到 +0.2-0.5 dB。

---

## §1.7 详细 — 拼接路线 7: 新-D 相邻 cam wide-baseline stereo

**怎么做**: AV2 ring cams 邻 cam 对外参 `T_ego_cam` 是出厂标定 (精度 ±5-10 mm / ±0.1°), 所以**基线/相对位姿 KNOWN**, 不用做 SfM, 只需 sparse stereo。 流水线: (1) kornia DISK 提 keypoints, (2) kornia LightGlue 做匹配, (3) 已知外参直接算 F = K_b^-T [t]× R K_a^-1, (4) Sampson ≤ 3 px 过滤, (5) cv2.triangulatePoints DLT, (6) cheirality + depth band [0.5, 120 m] + parallax ≥ 0.5° 三重几何过滤 (干掉远距近平行射线退化解)。

**结果** (4 anchor × 7 邻对 = 28 stereo pair):
- 平均 N_final = **44 inlier 3D pts/pair** (range 0-127)
- depth 中位数 9-22 m, 跨度 [2.5, 26.5] m (典型城市建筑)
- Anchor 60: **5/7 对成功**, **2/7 honest NEG**:
  - front_left ↔ side_left: 152 epi inlier 全部 cheirality 失败 (近平行射线)
  - side_right ↔ front_right: 仅 11 LightGlue match (side_right 看到近距离黑墙)

**差距**: 
- ✅ 几何上 metric-sane (depth 中位数符合物理距离)
- ❌ 太稀疏 (~50 pts/pair) 不能覆盖 ERP 重叠区 (50-150k 像素), Option B 反加权 L1 暂未跑

![Anchor 60 七邻对 sparse stereo depth viz (turbo cmap; "no inliers" 是退化对)](images/route_wide_baseline_depth.png)

**意义**: paper Section 6 NEG #5 — "AV ring 3D-aware 不论 monocular (Pi3/Depth Pro/VGGT 等) 还是 classical sparse stereo (新-D) 都 brittle", 论据链汇流。

---

## §1.8 详细 — 拼接路线 8: 新-E HDR cross-cam 补偿

**怎么做**: 7 cam 各自跑独立 AE + AWB → 邻 cam 重叠区可见 50+ luminance/色温 gap。 每 cam 建模为 6 参数 (3 通道 gain + 3 通道 bias), cam_0 固定为 identity 作 gauge。 用 **global least-squares + Huber loss** 一次性解 36 参数。 ERP 空间提对应 (两 cam 都 visible 的像素就是 paired observation, 不用 feature matching)。 加 RANSAC-lite 中位数过滤 + box bounds + Tikhonov 防退化解。 校正在 multi-band blending **之前**应用。

**结果** (4 anchor mean):
- 重叠区 mean abs luminance gap: **16.62 → 13.61 = -3.01 levels, -18.1% 相对**
- Anchor 60 (rear_right, side_right) pair: 45 → 14 = **-68% 修复**
- Cycle-PSNR 等价代理 (假设 MSE ~ gap²/3): **~+1.0 dB**

**差距**: ⚠️ +1.0 dB 是亮度差代理, **不是直接测的 cycle-PSNR** — Wave 3 用校正后图重跑 hold-out reconstruction 补测。

![Before (L1 baseline) vs After (L1 + HDR correction), anchors 60 + 90](images/route_hdr_before_after.png)

**意义**: paper Section 5 "Color Consistency" 子节实证 — 跨 cam 颜色补偿是**任何 stitching 之上的 mandatory preprocess**。

---

## §2 下游消费任务 (3 条 — 独立章节)

> 这一章是 "拼好的 360° 全景能给哪些下游模型用"。 跟拼接方法本身正交。

### §2.1 ViPE 全景 SLAM (NVIDIA, 2025) — ✅ 成功

**怎么做**: ViPE (Koi paper list #2) 显式支持 360° ERP 输入, 把 L1 输出的 1024×2048 ERP 5s 视频喂给它 panorama-mode SLAM。

**结果**: 端到端跑通 **96.7 s on A100**。 输出: camera pose trajectory (100 帧), 估计的全景内参, 动态物体 mask (GroundingDINO + SAM + XMem 三层)。

**差距**: ViPE depth flag 加了再跑一次 (T9b), depth 出但 scale 没对齐 — 是 relative depth 不是 metric。

**意义**: ✅ paper Section 7 **第一个 downstream consumer demo 成立** — "我们的 L1 不是孤立拼图, 是 published Spatial-AI 系统的合规输入"。

### §2.2 GEN3C 3D-cache 视频生成 (NVIDIA CVPR 2025) — ⏸️ paused

**怎么做**: GEN3C (Koi paper list #3) 是 3D-cache-conditioned 7B 扩散模型, 接受 RGB + depth + pose → 生成新轨迹视频。 用我们 ViPE pose+depth 喂给它的 `gen3c_dynamic --vipe_path` API。

**结果**: ⏸️ 装环境时我 bash 脚本两次低级错误 (`set -u` + `set -eo pipefail | head` SIGPIPE) 导致 install 5 秒就 crash, 浪费 3 小时 A100。 GEN3C 这条线 v6.1 已 pause, 后续 paper 写为 future work。

**差距**: install 没跑成, 推理没机会测。

**意义**: 暂时 paper 未用; 若 Wave 3 重试可作 Section 7 future-work 钩子。

### §2.3 Panacea+ 全景视频生成 (arXiv 2408, 唯一 AV2 验证) — ⚠️ modality NEG

**怎么做**: 最初设想 Panacea+ 可消费我们 L1 ERP 作下游 demo。 装环境跑 inference 才发现:

**结果**: Panacea+ 输入是 **BEV + 3D bbox + HD-map, 不是 RGB 全景**。 它跟 L1 是平行路径, 不能直接对接。

**意义**: ⚠️ **paper narrative 关键修正** — 我们原以为的 "L1 → Panacea+ / Pantheon360" 是 modality mismatch。 真正能消费 L1 RGB ERP 的下游 = ViPE。 这个发现帮我们 (和 Koi) **避免 2-3 周的浪费方向**。

---

## §3 外部 baseline 对比 + 方法论 audit (压缩 recap)

### §3.1 外部 published baseline 测试 (3 条 NEG, 强化 paper Section 6)

| Baseline | 怎么做 | 数字 | NEG 论据 |
|---|---|---|---|
| **OmniStitch** (ACM MM 2024, 唯一 published AV-360 stitching) | github tngh5004/Omnistitch 跑 inference | ΔPSNR **-6.67 dB** vs L1, 7/7 cam 输 | sim2real transfer 失败 |
| **Depth Pro** (Apple SOTA monocular, 2024) | L3 backbone Pi3 → Depth Pro | abs_rel 0.580 vs Pi3 0.204 (**2.84× 差**) | **algorithm not backbone** |
| **Temporal Pi3 K=3** (我自创 21-view joint inference) | 3 帧 × 7 cam 同时喂 Pi3 | abs_rel 0.213 vs 0.204 (反而差) | 时间多基线假说 false, **Pi3 远场 bias 结构性** |

![4 NEG 汇总 (OmniStitch / Depth Pro / Temporal Pi3 / Panacea+ modality)](images/wave3_neg_findings_summary.png)

### §3.2 方法论 audit (paper Section 4 "Methodology Rigor")

- **10-anchor robustness**: single-anchor 单帧巧合排除, 跨 L3/T18/T2/T12/T14b 都 lock
- **Multi-metric audit**: PSNR + LPIPS + MS-SSIM + region-separated (天空/物体/地面) — L3 在 object band -6.88 dB, LPIPS 1.83× 差, 防 reviewer "PSNR 偏袒模糊"
- **Pi3 vs LiDAR depth-binned**: abs_rel 0.202 ± 0.042, 远场 -10% (<5 m) → -24% (>40 m) **单调 bias**

![Pi3 vs LiDAR 单调 bias](images/depth_binned_metrics.png)

---

## §4 Paper 角度 ask (核心问题)

### §4.1 三个候选 (v6.1 mid-CPU 之后的新评估)

| 候选 | Story | Pros | Cons |
|---|---|---|---|
| **B-with-C-as-motivation** (T-Koi-3 推荐) | Hybrid IPM + 5 NEG analysis | reviewer-friendly, 有 1 个 positive | positive 弱 (statistical edge) |
| **C-headline** (T-Koi-3 备选) | Negative-finding analysis paper | 5 NEG 互相 reinforce, paper-quality | "negative paper" 投 method conf 门槛高 |
| **A' Method paper** (**v6.1 新提议**) | **AV→360° preprocessing/composition stack** (HDR + graph-cut + IPM 多区域 + cylinder) + 4-5 NEG 当 Section 6 | **3 个 stack-able positive 贡献** + 视觉 figure 强 | 没单点大 win; 需 framing 成 system stack |

### §4.2 我推荐 A' (从 T-Koi-3 的 B/C 推一档)

**关键变化**: v6.1 给了 3 个新的 positive contribution (新-B 视觉 / 新-C +0.20 dB ground / 新-E +1.0 dB HDR proxy), 不再像 T-Koi-3 时只有 T14 一个 (+0.05 dB)。 3 个独立 method contribution + 4-5 NEG 在 A' Method paper 框架下更合适。

**新 paper narrative 草稿**:
> **Title**: "Building a 360° Panorama Stack for Autonomous Vehicles: What Helps, What Fails, and Why"
> **Story**: 我们提出 AV→360° ERP stitching 的 modular preprocessing + composition stack (HDR / graph-cut seam / IPM 多区域 / cylinder canvas), 每层 zero-extra-data drop-in, 可独立 ablate, 可叠加。 同时通过 5 独立 NEG 揭示 SOTA 3D-aware 方法的系统失败模式。

### §4.3 3 条诚实 caveats

1. 新-E +1.0 dB 是亮度差代理 (Wave 3 补 direct cycle 测)
2. 新-B cycle Δ = 0, 视觉胜场 — paper 必须写为 "perceptual figure"
3. 新-C building 分支 ablated, 出货是 ground+sky 二区域 — 不能 over-sell

---

## §5 剩 2 条 GPU 路线 (设计完成, 等执行)

### 新-F VGGT 第 3 backbone (Meta CVPR 2025 Best Paper, ~6-16h on A100)

**怎么做**: VGGT (facebookresearch/vggt, HF `facebook/VGGT-1B-Commercial`) 替换 Pi3 在 L3 forward-splat 里, 10-anchor LiDAR + cycle eval。 已确认公开可用, multi-view native (joint 7-cam forward), 7-8 GB VRAM, ~0.4 s/forward。 直接扩 `run_depth_backbone_swap.py` 加 `--backbone vggt` 一个 `run_vggt()` 函数 (~80 LOC)。

**预期**: 70% 概率 abs_rel 体面但 L3 仍输 L1 ~2-3 dB → 加固 NEG #2 ("algorithm not backbone" 从 2 backbone 失败 → 3); 10% 概率 L3+VGGT 真超 L1 → 反推 paper hook。

### T13 Self-supervised Pi3 cycle-PSNR finetune (~5d on A100)

**怎么做**: 把 cycle-PSNR (hold-one-cam reconstruction) 改成**可微版本** (用 grid_sample + 占用 mask 替代 forward-splat 的 scatter), 当 self-sup loss; **Tier-A LoRA** 微调 Pi3 的 conv_head depth output (~3M 可训参数); 5 epoch 训 4 个 log。

**预期 P(success) ~30-50%**: 成功的话压 Pi3 远场 -24% bias 到 -15%, 期望 ground IPM 全图 Δ +0.1-0.2 dB; 失败的话另一个 NEG ("self-sup 也不能修结构 bias")。

---

## §6 给 Koi 的 4 个问题

1. **Paper 角度**: A' Method paper (新提议) / B-with-C (T-Koi-3 保守) / C-headline (T-Koi-3 备选)?
2. **新-D Option B reweight**: 留接口给 Wave 3 把 sparse 3D pts 用来 reweight L1 (期望 +0.1 dB)。 跑 (~1 周 CPU) 还是只 ship 视觉 NEG figure?
3. **T13 self-sup Pi3 finetune**: 实跑 (5-6 d A100, P~30-50%, 可能 NEG) 还是 ship "designed not run"?
4. **Target venue**: 3DV 2026 main (~Aug ddl) vs 3DV 2026 D&B (NEG-friendly)?

---

## §7 附录 — 文件路径 + commit

**PDF / MD 历史**:
- T-Koi-1 (W2 D0): `deliverables/handoff_to_koi_w2_2026-05-20.{md,pdf}`
- T-Koi-2 (W2 D1 mid): `deliverables/handoff_to_koi_w2_2026-05-21_mid.{md,pdf}`
- T-Koi-3 (Wave-3 收官): `deliverables/handoff_to_koi_w2_2026-05-21_late_mid.{md,pdf}`
- **本封 T-Koi-4 (v6.1 mid-CPU)**: `deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.{md,pdf}`

**关键代码 (新 v6.1)**:
- `code/waymo2panorama/projection/cylinder.py` (新-A)
- `code/waymo2panorama/blending/graphcut_seam.py` (新-B)
- `code/waymo2panorama/projection/ipm_multi_region.py` (新-C)
- `code/waymo2panorama/stereo/wide_baseline_stereo.py` (新-D)
- `code/waymo2panorama/color/hdr_gain_estimate.py` (新-E)

**关键 commits** (Wave 1-2 v6.1 CPU 完):
- `a089932` 新-A 柱面 / `b50b7c6` 新-E HDR / `508e084` 新-B graph-cut / `9984e95` 新-C IPM 多区域 / `24af375` 新-D wide-baseline stereo
