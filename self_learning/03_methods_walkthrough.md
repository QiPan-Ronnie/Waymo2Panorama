# Chapter 03 — 8 条拼接路线深度讲解

每条路线按统一格式: **任务 → 直觉 → 算法步骤 → 用到的 CV 概念 → 代码位置 → 结果 + 数字 → 失败模式 → Takeaway**.

---

## 3.1 L1 — Sphere baseline (球面 + 多带混合)

### 任务
把 7 张 504×504 letterboxed 图拼成 1024×2048 ERP, 不用神经网络, 只用经典几何.

### 直觉
**远场视差小可忽略 → 假设场景在远处球面上**. 每个像素 (u, v) 反推到球面上一个方向 (3D unit ray), 这个 ray 在 ERP 上有唯一位置. 接缝处用 multiband Laplacian 让颜色平滑过渡, 接缝消失.

### 算法步骤
1. 对每个 cam: 像素 (u, v) → cam frame unit ray (用 K^-1)
2. 用 T_ego_cam 把 ray 旋转到 ego frame
3. ray 转 (lat, lon) → 转 ERP 像素 (u_erp, v_erp)
4. 边缘用 cosine² feathering 给软权重
5. 7 cam 的 ERP slab + 权重 → **multi-band Laplacian blend (5 bands)** → final ERP

### 用到的 CV 概念
- §2.4 K 矩阵 (像素 → ray 用 K^-1)
- §2.6 T_ego_cam (cam frame → ego frame)
- §2.12 球面投影 (ray → 球面方向)
- §2.14 ERP (球面 → 矩形 unwrap)
- §2.15 Multi-band Laplacian Blending (接缝消失)
- §2.16 Cosine² Feathering (边缘软权重)

### 代码
- `code/waymo2panorama/projection/sphere_projection.py` (核心球面 → ERP)
- `code/waymo2panorama/blending/multiband.py` (Laplacian pyramid blending)
- `code/waymo2panorama/pipeline/stitch_frame.py` (整合 driver)

### 结果
- **cycle-PSNR = 12.34 ± 1.31 dB** (10 anchors mean) ← paper main baseline number
- L3 forward-splat 输 L1 -3.15 ± 0.72 dB (10/10 anchor 输)

### 失败模式
- 近场 (<10m) 视差大 → 球面假设破坏 → 鬼影 (车 / 行人重影)
- ERP 两极扭曲 (cam 朝下/朝上的 cam 看不见, 上下黑边)

### Takeaway
**简单 → 强**. 这是反直觉的发现 — 经典 50 年前的方法 (球面 + Laplacian) 比 SOTA 神经网络 3D 方法稳. 因为 L1 不假设深度, 错误集中在近场, 但近场只占像素少数. **这是 paper 的强 baseline, 所有其他方法跟它比**.

---

## 3.2 L3 — Pi3 forward-splat (paper 主 NEG)

### 任务
用 Pi3 神经网络估每像素 3D 深度, 把图像变点云, splat 到 ERP 球面. **理论上**比 L1 强 (有深度信息), 实际上**完败**.

### 直觉
- L1 用 ray 没用 depth, 远场对近场容错
- L3 用 depth, 近场应该更准 (能正确处理视差) → 应该赢 L1
- **实际**: depth 错 → splat 错位 → 鬼影; 远场 depth 错 -24%; 近场 occlusion 处理差 → **全输**

### 算法步骤
1. Pi3X 7 cam 一次 joint forward → 输出 `local_points (7, H, W, 3)` (cam frame metric depth) + `points (7, H, W, 3)` (cam-0 frame)
2. Sim(3) 对齐: Pi3 自己的 frame ↔ AV2 ego frame (Umeyama 解 R/t/scale)
3. 应用 Sim(3): pts_ego = scale * R @ points + t
4. **Forward-splat**: 对每个像素 (u, v, z), 把 RGB 投到 ERP 上对应位置 (depth 决定具体位置)
5. 7 cam splat 结果 + confidence 权重 → blend → final ERP

### 用到的 CV 概念
- §2.18 Pi3 模型 (神经网络估 depth)
- §2.9 Back-projection (depth + K → 3D 点)
- §2.11 Sim(3) Umeyama (Pi3 frame ↔ AV2 ego frame)
- §2.19 Forward-splat (splat 算法本身)
- §2.14 ERP (final 输出格式)

### 代码
- `scripts/phase3/run_pi3_multi_anchor.py` (Pi3 inference)
- `code/waymo2panorama/alignment/sim3_align.py` (Umeyama)
- `code/waymo2panorama/pipeline/lift_and_project.py` (forward-splat 核心)

### 结果
- **cycle-PSNR -3.15 ± 0.72 dB vs L1**, 10/10 anchor 输
- 跨多个 metric 验证 (T5 metric audit): LPIPS 1.83× 差, MS-SSIM 0/7 cams, object-band PSNR -6.88 dB → **NEG metric-robust**
- Pi3 vs LiDAR depth: abs_rel 0.202 ± 0.042 (over 10 anchors, 893k matched LiDAR points)
- Pi3 远场 (>40m) depth bias **-23.7% ± 6.8%** (单调远场更差, 10/10 anchor)

### 失败模式
1. **远场深度低估 24%** → splat 位置错 → 鬼影
2. **Forward-splat 没 z-buffer** → 远的覆盖近的 (物理错的方向)
3. **Sparse coverage** → depth 不连续处 splat 出洞
4. **跨 cam 不一致** → 同一物体在 cam A / cam B 的深度估计不一样, splat 到 ERP 位置不一致 → 重影

### Takeaway
**这是 paper 的核心 NEG**. 不只是"我们这个算法不行", 更深的发现:
- 4 个 backbone (Pi3 / Depth Pro / Temporal Pi3 / OmniStitch — 见 §04) 都不能让 L3 反超 L1
- 说明问题在 **forward-splat 算法本身**, 不在 backbone 选择
- 暗示 AV ring cam 这种几何配置 (大多共点旋转, 视差小) 上 3D-aware 方法系统性 brittle

---

## 3.3 IPM (T14) — Ground plane hybrid

### 任务
利用 "AV 路面是平的" 这个**强物理先验** (z=0), 路面像素用 IPM 数学公式精确投影, **非路面像素 fallback 球面**.

### 直觉
- Pi3 估的路面深度有几 m 误差 (远场 -24%)
- 但 IPM 公式上**精确**到 0 误差 (因为路面真的就是 z=0)
- 把两个混合: 路面用 IPM, 其他用球面

### 算法步骤
1. 从 Pi3 输出的 `local_points` 算每像素的 ego_z (该像素在 ego frame 的 z 坐标)
2. **Ground mask**: `|ego_z| < 0.30 m AND |normal_z| >= 0.85` (z ≈ 0 且法线竖直)
3. **IPM 投影 ground 像素**: 解 line-plane intersection (相机 ray ∩ z=0 平面) → 精确 3D 位置 → 投到 ERP
4. 非 ground 像素 → fallback 球面投影 (跟 L1 一样)
5. 3px Gaussian feather 在 ground / non-ground 边界软化

### 用到的 CV 概念
- §2.25 IPM (Inverse Perspective Mapping)
- §2.18 Pi3 (用其 local_points 算 ground mask)
- §2.12 球面投影 (non-ground fallback)

### 代码
- `code/waymo2panorama/projection/ipm_ground.py`
- `scripts/phase3/run_ipm_hybrid.py`

### 结果
- **Full image ΔPSNR = -0.010 ± 0.082 dB** (10 anchors, drop-in safe ✓)
- **Ground-only ΔPSNR = +0.048 ± 0.181 dB** (7/10 positive, range -0.24 ~ +0.32)
- 3-anchor cherry-pick (T14 60/0/150): +0.20 ± 0.11 dB
- **Rear cams ground-only +1.0 ~ +1.7 dB** (crosswalk / lane markings 跨 cam 边界对齐)

### 失败模式
- Front cams 在动态阴影 (车自己投的阴影) -0.5 ~ -0.8 dB → 因为阴影破坏 ground 检测
- 上下坡 / 路面不平时 z=0 假设破坏

### Takeaway
**首个正面 method contribution** (但 marginal). 验证物理先验思路. 是 paper 的"现有正向工作"标志.

---

## 3.4 新-A — Cylindrical L2 (柱面投影)

### 任务
把球面投影换成柱面投影 (圆筒展开), 5-band Laplacian blending 不动. 测 cycle-PSNR + coverage.

### 直觉
- AV2 7 cam 几乎水平排列 (除了 front_center 略上倾)
- 球面假设场景在 sphere 上, 柱面假设场景在 cylinder 上
- 柱面跟 cam 水平排列**几何更贴合** → 应该 coverage 更高 (利用率高)

### 算法步骤
1. 像素 (u, v) → cam frame ray
2. ray → cylinder coord: `θ = atan2(x, z), h = y / sqrt(x² + z²)`
3. cylinder → ERP-like 矩形输出 (θ 为横轴, h 为纵轴)
4. 7 cam 用同样 multi-band Laplacian blend

### 用到的 CV 概念
- §2.13 柱面投影
- §2.15 Multi-band blending (复用 L1)

### 代码
- `code/waymo2panorama/projection/cylinder.py`
- `scripts/phase3/run_cylindrical_baseline.py`

### 结果
- **Cylinder coverage = 58.55%** vs **Sphere coverage = 33.65%** (+24.9 pp coverage, per-cam 1.74× alpha)
- **cycle-PSNR ≈ 0** Δ vs L1 (cycle 不敏感 projection surface, see §2.31)
- Seam gradient -0.98 (4/4 anchor visual improvement)

### 失败模式
- 视觉上没明显 win (cylinder 跟 sphere 在 cam 重叠区表现相近)
- cycle metric 不动 (这是 metric 局限, 不是方法问题)

### Takeaway
**Baseline 对照价值**. 写 paper 必有 "tried different projection surfaces" 对照, 我们试了 sphere + cylinder + ERP (隐含). 跟 risk register 预期一致 ("新-A 跟球面差不多").

---

## 3.5 新-B — Graph-cut seam selection

### 任务
不用固定 cam 边界做接缝, 让算法自动找"**最不显眼**的接缝路径" (沿低梯度走, 像沿马路画一条线而不是穿建筑).

### 直觉
- 原始 L1 接缝在 cam 边界 (cos²(angle) 切换处), 这条线可能正好穿建筑边缘 → 视觉上看得见
- Graph-cut: 把图像建模成图, 像素 = 节点, 边权 = 颜色差 + 梯度. min-cut 找权重最小的切割路径
- 接缝沿低梯度 (uniform 颜色区, e.g., 天空 / 路面) 走 → 接缝消失

### 算法步骤
对每对 ERP-adjacent cam (e.g., front_c ↔ front_l):
1. 在重叠 bbox (~200×400 px) 上构造网格图
2. 边权 = `1.0 × color_diff + 0.5 × gradient + 0.1 × boundary_distance`
3. **Source = only-A region** (cam A 独占像素), **Sink = only-B region** (cam B 独占)
4. **Boykov-Kolmogorov min-cut** (PyMaxflow 实现) → 硬 0/1 mask
5. σ=3 Gaussian feather softening
6. 直接喂回 `multiband_blend` (multiband 接受任意 weight)

### 用到的 CV 概念
- §2.26 Graph cut + Min-cut (Boykov-Kolmogorov)
- §2.15 Multi-band blending (重用)

### 代码
- `code/waymo2panorama/blending/graphcut_seam.py`
- `scripts/phase3/run_graphcut_seam.py`

### 结果
- **Seam-band 平均 |grad| 48.63 → 42.59** (-12.4%, 4/4 anchor win)
- 等价 seam-smoothness gain ~ +0.58 dB
- L1 ERP vs graphcut ERP 整体 PSNR = 32.84 dB (基本一致, 只差在 seam 局部)
- **Cycle-PSNR 不动** (reconstruct_l1 不经过 blender, metric limitation)

### 失败模式
- Cycle-PSNR 显示不出 win (我们的 paper 论据是 visual figure, 不是数字)
- 重叠区太窄时 (e.g., side ↔ rear) min-cut 选择空间小

### Takeaway
**视觉 figure 是 paper Section 5 主产出**. 不动 cycle metric 不代表没 value — 视觉接缝消失对 Pantheon360 等下游消费**很重要** (rgb seamless 输出). Drop-in 可以叠加任何 stitching baseline (L1 / L2 / IPM / Pi3).

---

## 3.6 新-C — IPM 多区域 (ground + sky + building)

### 任务
把 T14 单 ground IPM **推广**到 3 区域: ground + sky + building 各用不同投影策略.

### 直觉
- T14 只对 ground 用 IPM, 其他 fallback sphere
- 天空 (z >> 0, 远场) 用 sphere 已经够好
- **建筑立面** (vertical plane) 可以 RANSAC 拟合 plane equation, 然后用 IPM 风格但 plane 法线水平 (不是竖直)
- 三区域决策树: ground → building → sky → fallback

### 算法步骤
1. **Normal estimation**: 从 Pi3 `local_points` 用 finite-diff + box filter 估每像素 surface normal (with valid-mask conv 避免 NaN 传播)
2. **Region segmentation** (first-match-wins):
   - Ground: `|ego_z| ≤ 0.30 m AND |normal_z| ≥ 0.85`
   - Sky: `conf < -2.0 OR (z_cam > 30m AND z_ego > 5m AND v < 0.4H)`
   - Building: `z_ego > 0.5m AND |normal_z| ≤ 0.30 AND normal_xy ≥ 0.85 AND radius ≤ 80m`
   - Fallback: L1 球面
3. **Ground IPM**: 同 T14
4. **Sky sphere**: 远场 sphere projection
5. **Building IPM-style**:
   - 每 32×32 tile RANSAC 拟合 vertical plane `n_x*x + n_y*y = d` (n_z=0)
   - 50 iter, threshold 0.20 m, inlier ≥ 0.40, PCA-refit
6. **Forward composite**: sphere base + building override + ground override (优先级), 3px Gaussian feather

### 用到的 CV 概念
- §2.25 IPM
- §2.28 RANSAC (building plane fitting)
- §2.18 Pi3 normal estimation
- §2.12 球面投影 (sky + fallback)

### 代码
- `code/waymo2panorama/projection/ipm_multi_region.py` (~590 LOC)
- `scripts/phase3/run_ipm_multi_region.py`
- `scripts/phase3/eval_ipm_multi_region_cycle.py`

### 结果 (4 anchors mean)
- L1 cycle-PSNR: 10.85
- T14 (ground only): 10.90 (+0.05)
- **新-C ground+sky: 10.90 (+0.05, +0.20 dB on ground-only mask, sky neutral)**
- 新-C with building: 10.86 (+0.01, **-0.33 dB on building-only mask**)

### Building 失败原因
- 单 cam RANSAC over-segments 同一 facade (跨 2-3 cams 找出不同 (n_x, n_y) 参数 → cycle eval 不一致)
- **设计 hard floor**: `--enable-building False` 默认出货 (即只 ground + sky), building 接口保留供 future cross-cam plane consensus 工作

### Takeaway
**Paper method win**. **Ground 区域 +0.20 dB 是 T14 (老 IPM) 的 4 倍**, sky 路由 neutral 不掉. Building 的失败是 future work (union-find on (n_x, n_y, d) within Δθ<10°, Δd<0.5m). 这条路线证明**"物理先验 + 区域分割"是正确方向**.

---

## 3.7 新-D — Wide-baseline stereo (邻 cam 经典立体)

### 任务
利用 AV2 邻 cam (front_c ↔ front_l) 距离 ~30-150 cm, 当**经典 stereo pair** 用, 通过特征匹配 + 三角化恢复 sparse 3D 点云.

### 直觉
- 邻 cam 视角差 ~10-30%, 看到部分相同景物
- AV2 出厂校准外参 (T_ego_cam) 精度 ±5 mm → **不需要估相机相对姿态, 直接用**
- 经典视觉 (LightGlue 匹配 + DLT 三角化) 是 deterministic 几何, 不需要训练

### 算法步骤 (每对邻 cam):
1. **DISK** 抽 ≤2048 keypoints + descriptors (每张图)
2. **LightGlue matcher** 匹配两图 keypoints (~50-500 inlier/pair typical)
3. **Compute F from known T_a_b**: `F = K_b^{-T} [t]_× R K_a^{-1}` (不用 cv2.findFundamentalMat 估)
4. **Sampson distance** ≤ 3 px 过滤 outliers
5. **DLT 三角化** (cv2.triangulatePoints) recovery 3D 点
6. **Cheirality 过滤** (Z > 0 in both cams)
7. **Depth band** [0.5, 120] m + **parallax angle ≥ 0.5°** (剔除远距离近平行射线退化)

### 用到的 CV 概念
- §2.20 经典 stereo
- §2.21 DISK + LightGlue (features + matching)
- §2.22 DLT 三角化
- §2.23 Cheirality 检查
- §2.28 RANSAC (epipolar filter)

### 代码
- `code/waymo2panorama/stereo/wide_baseline_stereo.py` (~430 LOC)
- `scripts/phase3/run_wide_baseline_stereo.py` (~390 LOC, CLI)

### 结果 (anchor 0/60/90/150 × 7 邻对 = 28 stereo pair)
- **平均 N_final = 44 inlier 3D pts/pair** (range 0-127)
- Depth median 9-22 m, depth 跨度 [2.5, 26.5] m
- **Anchor 60**: 307 个 3D 点跨 7 对, **5/7 对成功** (29-115 pts each), **2/7 对 NEG**
- Median parallax 0.55-1.39° (triangulation 数值稳定区)

### 2 对失败原因 (诚实 NEG)
- **front_left ↔ side_left**: 152 个 epi inlier 全部 fail cheirality → 远距离 sky/building 内容近平行射线退化
- **side_right ↔ front_right**: 仅 11 LightGlue match → side_right 视野被近距离黑墙占据

### Takeaway
- **Partial success** — 5/7 对成功证明经典 stereo 在 AV ring 上**部分**可行 (Option B "reweight L1" 留给 Wave 3 集成)
- Module 输出 ego-frame 3D pts, 可以做 "drop-in reweight L1" (近场 sparse 3D 修 L1 blend weights)
- Paper value: figure (5/7 cam-pair 深度 viz) + NEG 论据 (sparse stereo alone 不足以驱动 dense reweight)
- 跟 Pi3 / VGGT NEG 收敛 ("AV ring cam 的 3D-aware 重建 brittle")

---

## 3.8 新-E — HDR 跨相机色彩补偿

### 任务
7 个相机独立跑 AE/AWB, 同一片天空在不同 cam 里 luminance 差 50+ levels. 用全局 LS 解一组 gain+bias 让相邻 cam 重叠区颜色对齐.

### 直觉
- 同一物理点在 cam A 看是 (R_a, G_a, B_a), cam B 看是 (R_b, G_b, B_b)
- 物理上 R_a ≈ R_b (除了曝光差), 但 cam 独立 AE/AWB 让数值不同
- 用**重叠区像素对**当约束, 解 cam 之间的 affine 变换 (3 gain + 3 bias = 6 参数/cam)

### 算法步骤
1. **Render slabs to ERP** (用 L1 球面投影), 得到每 cam 的 ERP slab + valid_mask
2. **Extract overlap correspondences**: 同一 ERP pixel 在 cam_i / cam_j 都 valid → 算一对 (RGB_i, RGB_j)
3. **Filter**: ≥ 50 pixel pairs per (i, j); 中位数 3× 过滤 parallax outliers (RANSAC-lite)
4. **Build LS system**: 36 free params (cam_0 锁 identity, 解剩 6 cam × 6 params)
   - 对每对 (i, j) 重叠像素: `(g_i * RGB_i + b_i) - (g_j * RGB_j + b_j) = 0`
5. **scipy.optimize.least_squares** with **Huber loss** + Tikhonov regularization + box bounds → 50 iter 收敛
6. **Apply correction** before multiband blend: `corrected = gain * raw + bias`

### 用到的 CV 概念
- §2.27 HDR + 跨相机色彩补偿
- §2.28 RANSAC-lite (outlier filtering)
- §2.14 ERP (overlap correspondence space)
- §2.15 Multi-band blending (下游)

### 代码
- `code/waymo2panorama/color/hdr_gain_estimate.py` (~210 LOC)
- `scripts/phase3/run_hdr_compensation.py` (~290 LOC)

### 结果 (4 anchors 0/60/90/150 mean)
- **重叠区 luminance gap 16.62 → 13.61** (Δ +3.01 levels)
- **18.1% reduction** average
- **Anchor 60 dramatic**: rear_right ↔ side_right 对 45 → 14 (-68% 戏剧性曝光修复)

### 失败模式
- Specular highlights (玻璃反光) 当 outlier 被 RANSAC 丢, 不影响最终拟合
- Moving objects (peds / vehicles crossing seam) 同上 — Huber loss 鲁棒
- Sky vs shadow opposite trends → 6-param 不够细 (Wave 1.5 enhancement: 多 region)

### Takeaway
**Drop-in preprocess winner**. 任何 stitching baseline (L1 / L2 / IPM / Pi3) 都可以加它. **视觉上立刻显著改善** (跨 cam 颜色一致性). Paper Section 5 "Per-Camera Color Consistency" subsection 主产出.

---

## 总结表 — 8 路线快速复习

| ID | 核心 CV 概念 | 数字 | Paper value |
|---|---|---|---|
| L1 | 球面投影 + Laplacian | 12.34 dB baseline | **强 baseline, paper main** |
| L3 | Pi3 + Sim(3) + forward-splat | -3.15 dB vs L1 | **主 NEG** |
| IPM (T14) | IPM ground plane | +0.05 dB full, +0.20 dB ground | **第 1 正向 method (marginal)** |
| 新-A | 柱面投影 | +24.9 pp coverage | Baseline 对照 |
| 新-B | Graph-cut min-cut | -12.4% seam grad | **视觉 figure** |
| 新-C | IPM 多区域 + RANSAC | +0.20 dB ground (4× T14) | **Method win** |
| 新-D | Wide-baseline stereo (LightGlue + DLT) | 5/7 对成功 | Partial + NEG |
| 新-E | LS + Huber 6-param 色彩 | -18% lum gap | **Drop-in preprocess** |

---

**下一章**: [04_external_baselines.md](04_external_baselines.md) — 3 个外部 NEG (OmniStitch / Depth Pro / Temporal Pi3) 详细讲解
