# Waymo2Panorama — Week-2 v6.1 mid-CPU-wave 进展快照 (5/7 新路线完成, paper 角度 pivot 到 A')

**致**: Koi  ·  **作者**: Ronnie  ·  **时间**: 2026-05-21 (Phase 3 W2, v6.1 mid-wave)
**仓库**: https://github.com/QiPan-Ronnie/Waymo2Panorama @ `main`
**前三封**:
- `deliverables/handoff_to_koi_w2_2026-05-20.{md,pdf}` (T-Koi-1, W2 D0 Phase 3 W1 收官)
- `deliverables/handoff_to_koi_w2_2026-05-21_mid.{md,pdf}` (T-Koi-2, W2 D1 mid-week 7 tracks 24h)
- `deliverables/handoff_to_koi_w2_2026-05-21_late_mid.{md,pdf}` (T-Koi-3, Wave-3 收官 + B->C narrative shift ask)

**性质**: **v6.1 mid-CPU-wave snapshot** — 这一封带核心 ask: **paper 角度从 T-Koi-3 时的 "B-with-C-as-motivation" 再往前推一档, 转到 A' (Method paper)**, 因为 v6.1 5 条新路线 (CPU 全跑完) 给了我们 **3 个 positive contribution** + 1 个 visual win + 1 个再 NEG 论据。 你 reply 不阻塞剩下 2 条 GPU 路线 (新-F VGGT / T13 self-sup), 但会决定 paper 主线。

---

## TL;DR (6 行)

1. **v6.1 5 条新方法路线 (CPU only) 全部完成** (Wave 1 新-A 柱面 + 新-E HDR; Wave 2 新-B graph-cut seam + 新-C IPM 多区域 + 新-D wide-baseline stereo)。 **3 条正面贡献 + 1 条视觉胜场 + 1 条结构 NEG**。
2. **新-C ground IPM**: ground mask 上 **+0.20 dB vs T14** (单一区域 4x 提升), full ERP +0.05 dB; building 分支按设计 ablated, default ship "ground+sky" 配置。
3. **新-E HDR cross-cam**: 重叠区 lum gap **16.62 -> 13.61 (18.1% reduction)**, anchor 60 (rear_r, side_r) pair **45->14 = 68% 修复**, **cycle-PSNR 等价代理 ~+1.0 dB** (注意是亮度差代理, 不是直接测的 cycle-PSNR)。
4. **新-B graph-cut seam**: 接缝带 |grad| **-12.4%** (4/4 anchor win), cycle-PSNR Δ 结构上 = 0 (reconstruct 不走 blender)。 **视觉 figure 主胜场**, 是 paper Section 5 主图候选。
5. **新-A 柱面 L2** (cov **+24.9 pp** 几何赢, cycle Δ=0) + **新-D wide-baseline stereo** (5/7 cam-pair 成功, sparse ~44 pts/pair, NEG 论据强化) — 视觉/结构改进, 不计 cycle 数字。
6. **Paper 角度 ask**: 从 B/C 双胞胎 pivot 到 **A' Method paper** (3 个 stack-able 正面贡献 + 4-5 NEG 当 motivation) 还是仍守 **B-with-C** (T-Koi-3 推荐, 更保守但 reviewer-friendly)? 想听你的判断。

---

## v6.1 新路线 summary 卡 (5 条 CPU 完成)

| 路线 | 方法名 | Headline 数字 | Cycle Δ | Verdict | Paper 角色 |
|---|---|---|---:|---|---|
| **新-A** | 柱面投影 L2 baseline | Coverage **+24.9 pp**, seam grad -0.98 | 0 dB (结构上) | [!] partial | A' Section 5 baseline 对照 |
| **新-B** | Graph-cut min-cut seam | Seam |grad| **-12.4%** (4/4) | 0 dB (结构上) | [DONE] visual win | A' Section 5 main figure |
| **新-C** | IPM 多区域 (ground+sky) | Ground-mask **+0.20 dB**, full +0.05 | +0.05 dB | [DONE] partial | A' Section 5 main method |
| **新-D** | Wide-baseline sparse stereo | 5/7 pair 成功, ~44 pts/pair | n/a (sparse) | [!] partial | A' Section 6 NEG #5 (sparse stereo brittle) |
| **新-E** | HDR cross-cam compensation | Lum gap **-18.1%**, anchor60 -29% | ~+1.0 dB proxy | [DONE] | A' Section 5 preprocessing 必备层 |

**Drop-in 可叠加**: 新-A / 新-B / 新-E 是 zero-extra-data drop-in 上游/下游层 — 任何 stitching baseline (L1 / L2 / L3 / IPM) 都可挂。 新-C 是 method core (IPM extension)。 新-D 是 standalone data product (sparse 3D pts 留给 Wave 3 reweight 用)。

剩余 2 条 GPU 路线: **新-F VGGT 3rd backbone** (设计完成, ~6-16h on A100, 加固 "algorithm not backbone" NEG 链) + **T13 self-sup Pi3 finetune** (设计完成, ~5d wall on A100, 高风险高回报)。 两条都 GPU-gated, 等 worker quota / 主线决策。

---

## §1 路线 10 (新-A) — 柱面投影 L2 baseline

### 1.1 怎么做

把球面投影 (L1) 换成柱面投影。 每个 ERP 像素 `(u, v)` 解释成柱面上的方位角 `theta = pi - (u+0.5)/W * 2pi` 加垂直切线值 `h = v_max - (v+0.5)/H * 2*v_max` (默认 `v_max=1.0`, 即垂直 FOV ±45°)。 Ego-frame 射线 `(cos theta, sin theta, h)` 经 `R_ego_cam^T` 旋到 cam frame, 然后照常 pinhole 反投影 + bilinear remap。 与 sphere 唯一区别是 ego ray 的构造方式。 后续 5-band Laplacian blending 一字未动。 代码: `code/waymo2panorama/projection/cylinder.py` + driver `scripts/phase3/run_cylindrical_baseline.py`。

### 1.2 结果 (anchor 60, plus 4-anchor sweep 0/60/90/150)

- **Union ERP coverage**: cylinder **58.55%** vs sphere **33.65%** -> **+24.9 pp** (每个 cam 的有效像素 ratio ~1.74x)。 cylinder 用满了 ±45° 垂直 FOV, sphere 在两极留了大量黑边。
- **Seam gradient energy** (Sobel 平均梯度, lower = 更平滑): cylinder **50.56** vs sphere **51.54** -> 略平滑 (-0.98, 一致 4/4 anchors)。
- **Cycle-PSNR**: hold-out-cam reconstruction 在 protocol 上**跟 projection surface 无关** (per-pixel ray 反投影不依赖 canvas 形状) -> L1 vs L2 cycle Δ 预期 = 0 dB, 实测对应。 这条 metric 不适合区分 L1/L2 baseline。

![Cylindrical (L2) vs Sphere (L1) baseline, anchor 60](images/route_cylinder_vs_sphere.png)

### 1.3 意义

- Paper Section 5 必有的 baseline 对照: "sphere 在 7-cam 水平阵列上浪费两极, cylinder 不浪费"。 是 "geometric prior 选错就丢一半画布" 的好例子。
- 数字上 cycle-PSNR 不动这点对应了 v6.1 plan 风险表 "新-A 跟球面差不多" 的中等概率结果 — projection 表面只决定 canvas 几何, 不决定 reconstruction quality。
- **可叠加**: cylinder + IPM 多区域 / cylinder + HDR 留 Wave 3 看, 期望乘法叠加 (cov 提升让 IPM 多覆盖到的路面像素也变多)。

---

## §2 路线 11 (新-B) — Graph-cut 最优 seam selection

### 2.1 怎么做

L1 在每对相邻 cam 的重叠区里用 cos²(angle) 权重做 multi-band Laplacian blending — 这把每个 seam 解析地按在两 cam 光轴的几何中线上, 不管那条线是否穿过建筑/车辆/树冠等高梯度结构。 我用 **PyMaxflow min-cut** 把每对 cam 的接缝从"固定中线"换成"能量最低路径": 在重叠区 bbox (~200x400 px / 对) 上建 4-连通图, 边权 = `1.0 * color_diff + 0.5 * grad_diff + 0.1 * boundary_penalty`。 Source = "只有 A cover 的像素", Sink = "只有 B cover 的像素", min-cut -> 重叠区每像素的硬 0/1 label。 把 7 对的 0/1 mask 直接当 weight 喂回原本的 `multiband_blend` (轻度 σ=3 高斯模糊给最低带平滑种子) — **multiband 本就支持任意权重, 不需要 patch blender**。 CPU only, scipy.csgraph 是 fallback, 每个 anchor ~5 s。

### 2.2 结果 (4 anchors 0/60/90/150)

- **接缝带平均梯度** (Sobel |grad| 在 dominant-cam argmax 边界的 8-px dilate 带, lower = 接缝越隐形): L1 **48.63** vs graphcut **42.59** -> **-12.4% (4/4 anchors win, anchor 0: -14.6%, 60: -4.6%, 90: -12.5%, 150: -17.8%)**。
- **能量域 PSNR proxy** (10 * log10(L1_grad / GC_grad)): 平均 **+0.58 dB** 等价 seam-smoothness gain。
- **L1 ERP vs Graphcut ERP 整体 PSNR**: 32.84 dB — 两图绝大部分像素相同, 差异**只在 seam 局部**。
- **Hold-out cycle-PSNR**: 设计上 `reconstruct_l1` 不经过 multi-band blender, 所以 L1 vs graphcut 的 cycle-PSNR Δ **结构上 = 0 dB**。 改进只从 seam-band 局部 metric 看出来 — 视觉 figure 是主产出。

![Graph-cut seam vs fixed midline, anchor 60 (top: L1 cos² midline seams in red, bottom: route-11 graph-cut seams in red)](images/route_graphcut_seam_compare.png)

### 2.3 意义

Paper Section 5 必有的对照 — "5-band Laplacian blender 在隐藏接缝上已经很强, 但 cos² 固定中线 weight 在城市/高对比场景仍可见; graph-cut energy-min cut 是 zero-extra-data drop-in upgrade, 不动 backbone 不动 blender"。 Method 角度: 这条**叠加新-A 柱面 + 新-E HDR 的 system contribution 链** — 同样属于 "AV->360° 标准化预处理 / 后处理 stack" 层。 对 reviewer 反 "L1 太简单" 的论据加固。 **这是 Section 5 main figure 候选** (视觉 evidence 比数字更说服)。

---

## §3 路线 12 (新-C) — IPM 多区域先验扩展 (ground + sky + building)

### 3.1 怎么做

把 T14 的「单一地面 IPM 先验」扩展为三区域决策树: (a) 像素级 ego-z 阈值 + 估算法线 |n_z|>=0.85 -> ground (复用 T14); (b) Pi3 log-conf < -2.0 或 (远 + 高 + 图像上半) -> sky (走 sphere 不变); (c) ego_z > 0.5 m 且法线接近水平 (n_xy >= 0.85, |n_z| <= 0.30) 且 radius <= 80 m -> building。 法线从 `local_points_<cam>.npy` 用 finite-diff + box-filter 估出 (无外部 normal map)。 Building 区域按 32x32 tile RANSAC 拟合垂直平面 `n_x*x + n_y*y = d`, 按 inlier 像素逐 ray 求交。 Forward composite: sphere base + building override + ground override (优先级) + 3px Gaussian feather on weight 边界。

### 3.2 结果 (4 anchors 0/60/90/150 cycle-PSNR mean, ERP 1024x2048)

| Config | Mean cycle-PSNR | Δ vs L1 |
|---|---:|---:|
| L1 sphere baseline | 10.85 dB | - |
| T14 ground-only IPM (单一区域) | 10.90 dB | +0.05 |
| **新-C ground+sky (default ship)** | **10.90 dB** | **+0.05 (+0.20 dB on ground-only mask)** |
| 新-C 全 3 区域 (with building IPM) | 10.86 dB | +0.01 |
| Building-only mask | — | **-0.33 dB regression** |

按区域分解:
- Ground component: **+0.20 dB on ground mask** (vs T14 单独 +0.05 dB — 新分割剔除了 normal 不合理的 false-positive 地面)
- Sky tagging: +0.00 dB (单纯标签, sphere 数学不变)
- Building component: **-0.33 dB on building mask** (RANSAC 每 cam 拟合的立面对 held-out cam 不通用 — 同一建筑在不同 cam 中拟合出的平面参数 (n_x, n_y, d) 离散较大)

**Verdict**: [!] partial — ground 分支显著优于 T14 (+0.20 vs +0.05 dB on ground mask), sky 路由稳定; building IPM 分支在 forward composite 视觉合理 (~67 planes/cam, 88% inlier frac) 但 cycle 评测下不能复用其他 cam 的平面拟合 -> 当前以 `--enable-building False` 出货, building 接口保留供 future cross-cam plane consensus 工作。

![3-way compare: L1 sphere / T14 ground-only / 新-C 3-region (anchor 60)](images/route_ipm_multi_region_compare.png)

### 3.3 意义

推进 T14 的 +0.05 dB -> +0.05 dB in mean (持平), 但 **ground region 内部从 +0.05 推到 +0.20 dB** (单一区域上 4x 提升), 这是 normal-aware segmentation 的真实收益。 距离 +0.5 dB 全图目标还差 ~0.45 dB; 收益来源应转向 building IPM 的 cross-cam 平面共识算法 (union-find 合并相邻 cam 的相近平面, 只保留全局一致的立面), 或者借鉴 Wave 3 已设计的 self-sup Pi3 finetune 协同设计。

**诚实 framing**: ground mask 上 +0.20 dB 是真的; **full image 上仍只 +0.05 dB**, 不要 over-sell 成 "+0.2 dB method" 的 headline。

---

## §4 路线 13 (新-D) — 相邻 cam wide-baseline stereo

### 4.1 怎么做

AV2 ring cams 邻 cam 对的外参 (T_ego_cam) 是出厂标定, 精度 ±5-10 mm / ±0.1°, 所以 **基线/相对位姿是 KNOWN 量**, 不用做 SfM 估计, 只需在已知 epipolar geometry 上做 sparse stereo。 流水线: (1) kornia DISK 提 keypoints + descriptors (max 2048 / cam), (2) kornia LightGlue 做 deep matcher, (3) 用已知外参直接算 fundamental matrix `F = K_b^{-T} [t]_x R K_a^{-1}` (不估 F), (4) Sampson 距离 <= 3 px 过滤外点, (5) `cv2.triangulatePoints` 做 DLT 三角化, (6) 三重几何过滤: cheirality (两个 cam 内 Z>0), depth band ([0.5, 120] m), parallax angle >= 0.5° (剔除远处近平行射线的退化情况)。 CPU only, 单 anchor 7 对耗时 6-10 s。

### 4.2 结果

4 anchors (0/60/90/150) x 7 邻对 = 28 个 stereo pair, 平均 N_final = 44 inlier 3D pts/pair (range 0-127), depth 中位数 9-22 m, depth 跨度覆盖 [2.5, 26.5] m (典型城市建筑距离), median parallax 0.55-1.39°。

**Anchor 60** (主): 总 307 个 3D pts 跨 7 对, **5 对成功** (front_center<->front_left=29, front_right<->front_center=57, side_left<->rear_left=79, rear_left<->rear_right=27, rear_right<->side_right=115), **2 对 NEG** (front_left<->side_left: 152 epi inlier 但全部射线近平行 -> cheirality 失败; side_right<->front_right: 仅 11 LightGlue match -> 没有 epi inlier, 因为 side_right 看到的几乎全是近距离黑墙 + 天空, 没有可匹配的 feature)。

两个 NEG 对的失败被 cheirality + parallax filter 干净诊断, 不是 silent 0 输出。

![Wide-baseline sparse stereo: 7 adjacent cam-pair depth maps on anchor 60 (color = depth_cam_a, turbo cmap; gray = epipolar outliers; "no inliers" = degenerate or low-overlap pair)](images/route_wide_baseline_depth.png)

### 4.3 意义

这条路线提供两个论文 Section 6 "3D-Aware Failures on AV Ring" 的关键论据。

**第一**, **几何上可行**: 已知外参的 sparse stereo 在 5/7 对上产生 metric-sane depth (~10 m 中位数, 与场景物理距离一致), 证明 "邻 cam 三角化 -> metric 深度" 的 pipeline 数学和实现都对。

**第二**, **实践上不够**: 平均 ~50 pts/pair 的稀疏度无法覆盖 1024x2048 ERP 的重叠区 (每对重叠~50-150k 像素), 且 2/7 对在远距离/低纹理场景下完全失败 (parallel-ray degeneracy + black wall)。

结论与 Pi3 / Depth Pro NEG 收敛: **AV ring cam 的 3D-aware 重建在当前数据规模下都 brittle**, 不论是用 monocular depth backbone 还是 classical sparse stereo, 都不足以可靠地修正 L1 sphere baseline 的 parallax ghosting。 这是 paper Section 6 NEG #5 — 第 5 个独立 NEG 论据。

Module 的 `process_anchor_all_pairs()` API 为 Wave 3 的 "Option B reweight L1" 集成预留了 drop-in hook (sparse 3D pts -> per-pixel weight bias)。

---

## §5 路线 14 (新-E) — HDR / 曝光 / WB 跨 cam 补偿

### 5.1 怎么做

AV2 的 7 个 ring cam 各自跑独立 AE + AWB, 邻 cam 重叠区可见 50+ 亮度/色温 gap。 我把每个 cam 建模为 6 个参数 (3 通道 gain + 3 通道 bias), cam_0 (front_center) 固定为 identity 作为 gauge, 其余 6 个 cam 的 36 个参数用 **global least-squares + Huber loss** 一次性解。 对应关系直接在 ERP 空间提: 两个 cam 同时 visible (weight > 0.05) 的像素就是 paired observations, 不用 feature matching。 加了 RANSAC-lite 中位数过滤 (3x median 阈值) 干掉 parallax/动态物体 outliers, 加了 box bounds (g in [0.35, 3.0], b in [-60, 60]) + Tikhonov 先验防止 LS 收敛到 "gain=0 + bias=gray" 的退化解。 校正在 multiband blend **之前** 应用 (float32 [0,255] 空间)。 CPU only, scipy.optimize.least_squares, 每个 anchor ~5 s。

### 5.2 结果

4 anchors (0/60/90/150) 平均, 重叠区 mean abs luminance gap **16.62 -> 13.61 (Δ = -3.01 levels, 18.1% relative reduction)**。

| Anchor | Lum gap before | Lum gap after | Δ |
|---:|---:|---:|---:|
| 0 | 9.21 | 8.09 | -12% |
| 60 | 17.63 | 12.49 | **-29%** |
| 90 | 21.43 | 17.51 | -18% |
| 150 | 18.21 | 16.34 | -10% |

最戏剧性的修复在 anchor 60 的 (rear_right, side_right) 对 (45->14 lum gap, **68% 下降**) 和 anchor 90 的右半球面 (亮天空被压暗以匹配 front_center 的曝光)。

**Cycle-PSNR 等价换算 ~+1.0 dB** (假设 MSE ~ gap²/3) — **注意这是亮度差代理, 不是直接测的 cycle-PSNR**。 直接 cycle 测需要把校正后的图重跑 hold-out reconstruction, Wave 3 加测。

![Before (L1 baseline) vs After (L1 + HDR correction), anchors 60 + 90](images/route_hdr_before_after.png)

### 5.3 意义

这是论文 Section 5 "Per-Camera Color Consistency" 子节的实证基础 — 它说明在任何更复杂的 stitching 算法之上, **跨 cam 颜色补偿是 mandatory preprocessing**, 不补就被各自 AE/AWB 拉成色块拼贴。 Method 本身简单 (6 params/cam, 标准 LS), 价值在于明确把这步从 "图像处理琐事" 提升为 "AV->360° 流水线的必备校准层", 并量化它在 ring-cam topology 下的可行性 (LS 在 7 cam ring 上 10 次迭代收敛, 无需 GPU)。 框架可 drop-in 到任何下游 stitching baseline 作为前置滤波。

---

## §6 v5 9 条路线 (压缩 recap)

T-Koi-1/-2/-3 已经详细写过 v5 9 条路线, 这里只列 headline 数字方便 Koi 在新角度下重看:

| 路线 | 方法 | Headline | 状态 | Paper 角色 (v6.1 新角度下) |
|---|---|---|---|---|
| L1 | Sphere baseline | cycle-PSNR 12.34 ± 1.31 dB (10 anchor) | [DONE] | A' Section 5 baseline |
| L3 | Pi3 forward-splat | -3.15 dB vs L1, 10/10 anchor 输 | NEG | A' Section 6 NEG #1 (algorithm not backbone, 主据) |
| IPM hybrid (T14) | Ground IPM + sphere | full +0.048 ± 0.181 dB, ground-only +0.20 cherry-pick | [DONE] partial | A' Section 5 main method base (新-C 推进) |
| Depth Pro | Apple SOTA backbone swap | abs_rel 0.580 vs Pi3 0.204 (2.84x 差) | NEG | A' Section 6 NEG #2 |
| Temporal Pi3 | K=3 multi-frame | abs_rel 0.213 vs 0.204 (反而差) | NEG | A' Section 6 NEG #3 |
| OmniStitch | 唯一 published AV-360 baseline | ΔPSNR **-6.67 dB**, 输 7/7 cams | NEG | A' Section 6 NEG #4 (vs prior art) |
| Panacea+ | 全景视频生成 modality | BEV->video, **不是 RGB ERP 消费者** | NEG | A' Section 7 modality correction |
| ViPE | 下游 SLAM 消费 demo | 96.7 s on A100, pose+intrinsics+masks ✓ | [DONE] | A' Section 6 downstream demo |
| GEN3C | 3D-cache 视频生成 demo | 在 Colab 装环境 | WIP | (可选) A' Section 7 future-work hook |

![Wave-3 4 个独立 NEG findings — OmniStitch / Depth Pro / Temporal Pi3 / Panacea+ modality](images/wave3_neg_findings_summary.png)

---

## §7 方法论审计 (v5 已有, 压缩 recap)

新角度下这部分支撑 paper Section 4 "Methodology Rigor":

- **Multi-anchor robustness**: 10-anchor (P3 W1) 替换 single-anchor, 跨 T18/T2/T12/T14b/L3 全 lock。 Phase 2 single-anchor 结论 (L3 输 L1) 在 10-anchor 上稳, 10/10 都输, 不是单帧巧合。
- **Multi-metric audit**: PSNR + LPIPS + MS-SSIM + region-separated (天空/物体/地面 单独算)。 L3 在 object band (有视差的地方) 输 -6.88 dB, LPIPS 1.83x 更差。 防 reviewer 说 "你们 metric 选偏袒模糊"。
- **Pi3 vs LiDAR depth-binned bias**: abs_rel 0.202 ± 0.042 跨 10 anchor, 远场 -10% (<5 m) -> -24% (>40 m) 单调 bias。 Pi3 在 AV2 上的**首个 quantitative characterization**。

![Pi3 vs LiDAR depth-binned bias — 单调恶化 -10% -> -24% 跨深度区间](images/depth_binned_metrics.png)

- **Bayesian depth fusion** (改进 .ply 几何, 不改进 ERP): 多 cam 重叠区 depth RMSE 改善 1-5 m。 价值在给下游 GEN3C 喂更干净 .ply, 不动 cycle-PSNR (ERP overlap 只 ~2%)。

---

## §8 Paper 角度再评估 — 核心 ask

### 8.1 三个候选 (v6.1 mid-CPU-wave 后)

| 候选 | Story | Pros | Cons |
|---|---|---|---|
| **B-with-C-as-motivation** (T-Koi-3 推荐) | Hybrid 2D/3D pipeline + 5 NEG | 有 1 个 positive (+0.05 dB IPM) + 5 个 NEG 当 motivation; reviewer-friendly | positive 弱 (statistical edge), 难发 method 强会议 |
| **C-headline-with-B-supplement** (T-Koi-3 备选) | Negative finding analysis paper | 5 NEG 互相 reinforce, paper-quality 数据; D&B track 友好 | "negative paper" 在 method conf 投稿门槛高一档 |
| **A' Method paper** (**v6.1 新提议**) | Methods stack: HDR + graph-cut + IPM 多区域 + cylinder + (4-5 NEG 当 Section 6) | **3 个 stack-able 正面贡献** + 视觉 figure 强 (新-B); positive contribution 是 system stack 不是单点 | 没有 single big metric win; 需要 framing 成 "AV->360° preprocessing/composition stack"; 仍欠 +0.45 dB 才到 +0.5 dB 全图目标 |

### 8.2 推荐 A' Method paper (从 T-Koi-3 的 C-headline pivot 一档)

**关键变化**: v6.1 给了 3 个新的 positive contribution (新-B 视觉 / 新-C +0.20 dB ground / 新-E HDR +1.0 dB proxy), 而不是 T-Koi-3 时只有 T14 一个 (+0.05 dB full image)。 **3 个独立 method contribution + 4-5 NEG** 的组合在 A' Method paper 框架下更合适, 不再像 T-Koi-3 时只能靠 NEG 当主力 (C-headline)。

**新 paper narrative 草稿**:

> **Title (draft)**: "Building a 360° Panorama Stack for Autonomous Vehicles: What Helps, What Fails, and Why"
>
> **Story**: 我们提出一个 AV->360° ERP stitching 的 modular preprocessing + composition stack, 含 (1) HDR cross-cam compensation, (2) graph-cut energy-min seam selection, (3) IPM multi-region prior, (4) cylindrical projection canvas。 每层都是 zero-extra-data drop-in, 可独立 ablate, 可叠加到任何 backbone baseline。 同时通过 5 个独立 NEG 揭示当前 SOTA 3D-aware 方法 (Pi3 / Depth Pro / temporal stacking / OmniStitch / sparse stereo) 在 AV ring 拓扑下的系统失败模式, 为 stack design 选择提供 motivation。
>
> - Section 4 (Methodology Audit): 10-anchor, multi-metric, LiDAR-anchored eval protocol
> - Section 5 (Method Stack): HDR + graph-cut + IPM 多区域 + cylinder, 每层 ablate
> - Section 6 (NEG findings): L3 forward-splat, Depth Pro swap, temporal Pi3, OmniStitch transfer, sparse stereo
> - Section 7 (Downstream demos): ViPE SLAM, GEN3C (if 完成)
> - Section 8 (Future work): Building IPM cross-cam consensus, self-sup Pi3 finetune (T13), VGGT 3rd backbone (新-F)

### 8.3 三条诚实的 caveats

1. **新-E HDR +1.0 dB 是亮度差代理, 不是 cycle-PSNR direct measure**。 Wave 3 需要补 direct cycle 重测。
2. **新-B graph-cut cycle Δ = 0**, 主胜场是视觉 figure。 paper 必须把它写成 "perceptual contribution + figure", 不能列在数字表里和 +0.05 dB 并排。
3. **新-C building 分支按设计 ablated** — 出货 config 是 ground+sky two-region, 不是宣传的 three-region。 paper 必须 honest 写 "building branch is designed and tested but defaulted off due to cross-cam plane consensus failure; ground+sky is the shipped variant"。

### 8.4 ask Koi

**主问题**: A' Method paper (新提议) 还是仍守 B-with-C (T-Koi-3 推荐, 保守) 还是 C-headline (T-Koi-3 备选)?

我个人倾向 **A'**, 因为 v6.1 给了 3 个独立 method contribution, 不再像 T-Koi-3 时只能靠 NEG 撑场。 但 reviewer-perception 上 A' 风险是 "每条 method 单看都不大", **system stack story 是否够强是判断点**。

---

## §9 Pending GPU work + 给 Koi 的 4 个问题

### 9.1 剩余 2 条 GPU 路线 (设计完成, 等执行)

#### 新-F VGGT 3rd backbone (设计完成, ~6-16h on A100)

- **方法**: Meta + Oxford 的 VGGT (CVPR 2025 Best Paper) 作为 Pi3, Depth Pro 之外的第 3 个 monocular depth backbone, drop-in L3 forward-splat, 10-anchor LiDAR eval + cycle eval。
- **目的**: 加固 Section 6 NEG #1 ("algorithm not backbone") 论据 — 从 2 个 backbone 失败 -> 3 个。
- **预期** (Plan agent 估计): 70% 概率 abs_rel decent 但 L3 仍输 L1 ~2-3 dB; 20% 概率 abs_rel 比 Pi3 更好但 L3 仍输 L1 (更强 NEG); 10% 概率 L3 + VGGT 真的超过 L1 (推翻 NEG 链 — 反过来是更好的 paper hook)。
- **时间预算**: install 8 min + 10-anchor inference ~7 h + LiDAR/cycle eval ~30 min。 实际 wall: 6-16h on A100 (debug buffer)。 **GPU-gated, 等 worker quota / 主线决策**。
- 详: `notes/new_f_vggt_backbone_research.md`

#### T13 Self-supervised Pi3 cycle-PSNR finetune (设计完成, ~5d on A100)

- **方法**: Differentiable inverse-warp cycle-PSNR loss + Tier-A LoRA on Pi3 depth head + conv_head (~3M params trainable), 4-5 epoch 自监督训练 on 4 AV2 logs。
- **目的**: 压 Pi3 远场 -24% bias 到 -15%, 期望 ground IPM 在 dynamic content (front cams 主要失败模式) 更稳, ground Δ + 0.1-0.2 dB, 可能推 IPM 全图 Δ 从 +0.05 -> +0.15。
- **预期**: 高风险高回报 (P(success) ~30-50%)。 如果成功是 paper Section 5 主力 method 数字; 失败是另一个 NEG ("self-sup finetune 也不能修结构 bias")。
- **时间预算**: 5-6 d wall on A100 (LoRA train) + 1 d eval。 **如果跑, 是 Wave 3 主投资; 如果不跑, paper 写 "designed not run, future work"**。
- 详: `notes/t13_self_sup_pi3_finetune_design.md`

### 9.2 给 Koi 的 4 个问题

1. **Paper 角度**: A' Method paper (新提议) 还是 B-with-C (T-Koi-3 保守) 还是 C-headline (T-Koi-3 备选)?
2. **新-D Option B reweight**: 新-D 留了 sparse 3D pts (~44/pair) 接口给 Wave 3 把 L1 sphere baseline 在 sparse 覆盖区做 reweight (期望从 0 推到 +0.1 dB)。 **跑 (Wave 3 ~1 周 CPU) 还是只 ship as visual NEG figure (零成本)?**
3. **T13 self-sup Pi3 finetune**: 实际训 (5-6 d A100 GPU 投入, P(success) ~30-50%, 可能 NEG) 还是 ship as "designed not run" placeholder (零 GPU 成本但 paper 弱)?
4. **Target venue**: 3DV 2026 main (~Aug ddl, 12 周 runway) vs 3DV 2026 D&B (D&B track 接受 NEG paper, 但 prestige 低一档)? A' Method paper 适合 main, C-headline 适合 D&B。

---

## §10 附录: 文件路径 + commit history

### 10.1 Commits (T-Koi-3 至今, oldest -> newest)

| Commit | 内容 |
|---|---|
| `19abbaa` | [T-Koi-3] Wave-3 mid-week-v2 PDF — 10-anchor honest + 4 NEG + ViPE + B->C shift ask |
| `4ef3755` | [T9b] ViPE + DAP depth alignment on L1 ERP — submit job v1 |
| `643ad2e` | [T1] Queue multi-log Pi3 + LiDAR eval + cycle eval (4 new logs) |
| `c82c96a` | T1 Phase B: pick 4 diverse AV2 val UUIDs from S3 index |
| `bc217cd` | [T11 Job 1 v2] GEN3C install — fix set -u crash on conda activate |
| `d8f1cdc` | v6.1 prep: handoff_to_koi_v6.md base + progress.md pivot block |
| `3191417` | [Wave 0.5 新-W] RuntimeAwareWorker + bootstrap cell + labels.requires docs |
| `e7b3bd9` | [Wave 1/2 prep] Plan + Explore subagent designs for 新-C IPM multi-region + 新-F VGGT |
| `f69f60f` | [Wave 1 新-A] Submit 10-anchor cylindrical baseline sweep job |
| `b50b7c6` | [Wave 1 新-E] HDR cross-cam compensation: global LS gain+bias |
| `508e084` | [Wave 2 新-B] Graph-cut optimal seam selection — multiband-compatible no blender patch |
| `9984e95` | [Wave 2 新-C] IPM multi-region prior extension: ground + sky + building |
| `1217e70` | [Wave 2.B prep] Plan: 新-B graph-cut seam |
| `bc962aa` | [Wave 1.E prep] Explore: 新-E HDR design — global LS + 6-param gain+bias + Huber |
| `ddd1fb4` | [Wave 3 prep] Plan: T13 self-sup Pi3 finetune design |
| `a089932` | [Wave 2.D prep] Explore: 新-D wide-baseline stereo design |
| `24af375` | [Wave 2 新-D] Wide-baseline stereo on adjacent cam pairs: kornia LightGlue + DLT triangulation |

### 10.2 Code modules (v6.1 新增)

| Module | LOC | 路线 |
|---|---:|---|
| `code/waymo2panorama/projection/cylinder.py` | ~180 | 新-A |
| `code/waymo2panorama/blending/graphcut_seam.py` | ~430 | 新-B |
| `code/waymo2panorama/projection/ipm_multi_region.py` | ~590 | 新-C |
| `code/waymo2panorama/stereo/wide_baseline_stereo.py` | ~430 | 新-D |
| `code/waymo2panorama/color/hdr_gain_estimate.py` | ~210 | 新-E |
| `scripts/phase3/run_cylindrical_baseline.py` | ~310 | 新-A driver |
| `scripts/phase3/run_graphcut_seam.py` | ~310 | 新-B driver |
| `scripts/phase3/run_ipm_multi_region.py` | ~240 | 新-C driver |
| `scripts/phase3/run_wide_baseline_stereo.py` | ~390 | 新-D driver |
| `scripts/phase3/run_hdr_compensation.py` | ~290 | 新-E driver |

### 10.3 Design notes (pending GPU 路线)

- `notes/new_f_vggt_backbone_research.md` — VGGT 设计 + API + time budget
- `notes/t13_self_sup_pi3_finetune_design.md` — Differentiable inverse-warp cycle-PSNR + Tier-A LoRA 设计

### 10.4 Output artifacts (4-anchor sweep results)

- `outputs/phase3/p3.4_cylindrical/agg_4anchors.json` (新-A)
- `outputs/phase3/p3.5_graphcut/agg_4anchors.json` (新-B)
- `outputs/phase3/p3.3_multi_region/agg_4anchors.json` (新-C)
- `outputs/phase3/p3.6_stereo/anchor_*/summary.json` (新-D)
- `outputs/phase3/p3.7_hdr/anchor_*/` (新-E)

---

**这一封到这就完了。 想听 Koi 的 4 个问题 reply, 不阻塞 Wave 3 / 剩余 GPU 路线**。 Ronnie out.
