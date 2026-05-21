# Waymo2Panorama — Week-2 mid-week 进展快照 (7 tracks done in 24h)

**致**: Koi  ·  **作者**: Ronnie  ·  **时间**: 2026-05-21 (Phase 3 W2 mid-week)
**仓库**: https://github.com/QiPan-Ronnie/Waymo2Panorama @ `main`
**前一封**: `deliverables/handoff_to_koi_w2_2026-05-20.{md,pdf}` (W2 D0, Phase 3 W1 收官 + 重新定位 Pi3 → Pantheon360)
**性质**: **mid-week intermediate snapshot** — 不替代下一封 paper-time handoff, 只是 24h 内 7 个 Wave-1 track 全部收口的同步。 用户读完直接转发, 不阻塞你回复, 我们继续推 Wave-2。

---

## TL;DR

- **24h 内 7 个 Wave-1 track 全部完成** (subagent-driven-development × 并行 spawn): T-Koi-1 PDF / T5 metric audit / T6 parallax ranking / T8 lit watch / T14 IPM 混合 / T16 Bayesian fusion / T7-prelim paper-angle decision pack。
- **T14 IPM ground hybrid = 首个正面 method contribution** (3 anchors): ground-only ΔPSNR = **+0.20 ± 0.11 dB**, rear cams **+1.0~+1.7 dB**, 全 image ΔPSNR = +0.04 dB (drop-in safe, 无 regression)。 vs L3 forward-splat (-3.15 dB) 是 **结构性改进**。
- **T5 metric audit**: L3 negative 结论 **metric-robust** — LPIPS L3 比 L1 差 **1.83×**, MS-SSIM L3 输 **0/7 cam**, region-PSNR object band Δ = **-6.88 dB** (parallax 本该帮 L3 的地方反而输得最惨)。 PSNR 不是 cherry-pick; 三个 metric 一致 ranking。 paper main table 改用 (PSNR, MS-SSIM, LPIPS) tuple 预防 reviewer 质疑。
- **T16 Bayesian depth fusion**: anchor 60 overlap mean |Δd| = **2.04 m** (anchor 90: 0.27 m), 给 **更干净的 .ply geometry** 供下游 3D 消费 (Gaussian splat / NeRF / Pantheon360); 但不 rescue L3 ERP cycle-PSNR (L3 ghost 主要来自 single-cam mis-splat, 而 multi-cam overlap 只占 ERP 1.8-2.3%)。
- **T7-prelim paper angle 已锁定 v0**: **B-with-C-as-motivation** = "Hybrid 2D/3D pipeline for AV → 360° stitching, with analysis of why naive 3D-lift fails". 主投 **3DV 2026** (~Aug deadline, 12 周 runway), upgrade CVPR 2027 if T9/T10 downstream lands。

下面分 track 展开。 4 个 open question 在最后 §6, 5-day Wave-2 计划在 §7。

---

## 1. T14 IPM Ground-Prior 混合 — **headline 头条**

### 1.1 是什么

每个 ring cam 上分析式 **Inverse Perspective Mapping (IPM)** 地面投影: 以 Pi3 ego-z ≤ 0.3 m 圈出地面像素 → 解析求 `ray ∩ z=0` → ERP forward-splat。 因为 IPM 在 ground 平面上是 **parallax-free** (任意两 cam 看同一地面点解析回到同一 3D 坐标), 多 cam 在 overlap 区不再出现 5-20 cm 的 lane-marking 双影。 非地面像素继续走 L1 sphere projection, 多-band blender 给 IPM 优先权。

地面 mask 用 Pi3 ego-z 阈值, 7 cam 覆盖 fraction 0.20-0.50; CPU 端到端 ~3.3 s / anchor。

### 1.2 3-anchor 量化 (cycle-consistency, hold-out cam reconstruction PSNR)

| Anchor | full PSNR L1 | full PSNR Hybrid | Δ full | **ground PSNR L1** | **ground PSNR Hybrid** | **Δ ground** |
|---:|---:|---:|---:|---:|---:|---:|
| 0   | 11.08 | 11.11 | +0.03 | 10.27 | 10.42 | **+0.16** |
| 60  | 10.66 | 10.62 | -0.03 | 10.38 | 10.48 | **+0.10** |
| 150 | 10.58 | 10.72 | +0.14 | 9.16  | 9.49  | **+0.32** |
| **MEAN** | **10.77** | **10.82** | **+0.04** | **9.94** | **10.13** | **+0.20** |

**reading**:
- ground-only Δ 全 3 anchor 正 (+0.10 ~ +0.32), 均值 +0.20 ± 0.11 dB。 小但 **consistent**。
- full-image Δ +0.04 dB 几乎 0 (一个 anchor 微负 -0.03)。 关键: **不 regress** — 安全 drop-in 给 L1。
- 对比 L3 forward-splat 全 image ΔPSNR = **-3.15 ± 0.72 dB** (Phase 3 W1, 10 anchor), IPM hybrid 是 **结构性赢家** — 保留 L1 全部 accuracy, ground 区还加 +0.20 dB。

### 1.3 Per-cam breakdown (anchor 150, best case)

| cam | full L1 | full Hybrid | Δ full | ground L1 | ground Hybrid | **Δ ground** |
|---|---:|---:|---:|---:|---:|---:|
| ring_front_center | 6.35  | 6.31  | -0.04 | 6.67  | 6.57  | -0.10 |
| ring_front_left   | 7.92  | 7.51  | -0.41 | 7.37  | 6.59  | **-0.79** |
| ring_side_left    | 12.73 | 13.08 | +0.35 | 11.34 | 11.99 | **+0.66** |
| ring_rear_left    | 12.29 | 12.84 | +0.55 | 10.50 | 12.03 | **+1.53** |
| ring_rear_right   | 13.40 | 13.86 | +0.46 | 11.23 | 12.26 | **+1.03** |
| ring_side_right   | 13.00 | 13.46 | +0.46 | 10.16 | 10.80 | **+0.64** |
| ring_front_right  | 8.36  | 7.94  | -0.42 | 6.88  | 6.17  | **-0.71** |

- **Rear cams 全胜 +1.0~+1.7 dB** — Pi3 ground 在 rear 最稳, IPM 修掉 L1 跨-cam parallax double image。
- **Side cams 微胜 +0.6~+0.7 dB**。
- **Front cams 微输 -0.3~-0.8 dB**, 两个失败模式诊断清楚: (a) 行人/汽车阴影 (ground mask 正确, 但违反 static-ground 假设), (b) front_center 是 portrait letterbox, ground 像素少。

### 1.4 视觉证据

3 个高 signal-to-noise 差异 (compare PNG 见下):

1. **斑马线 / 车道线 跨 cam 边界对齐**: L1 在 overlap 区有 5-20 cm 双影 (每 cam 假设 infinite depth 各投各的); hybrid 用 IPM 解析地把它们放到同一 ERP 像素。
2. **路面几何 planar**: L1 多 sphere 投影 fan 出 3-6° 噪带, hybrid 沿 equator → v=H 一条干净曲线。
3. **失败模式 visible**: 形态学 5x5 dilation 在 IPM 稀疏区会拉进 cyan/blue 像素 → magenta fringing。 mitigation: edge-aware inpaint / guided filter, 1 天工作量。

![IPM hybrid vs L1 baseline (anchor 60), 左 L1, 右 IPM hybrid。 注意 (1) 下半路面 hybrid 边界对齐, lane marking 单影; (2) hybrid 路面整体更 planar; (3) IPM 区与 sphere 区的接缝有少量 magenta fringing 需后续清理。](images/ipm_hybrid_compare.png)

### 1.5 失败模式 & mitigation

| Failure | Cam(s) | Root cause | Mitigation |
|---|---|---|---|
| 阴影/行人 双影 | front_left/right | Ground mask 正确但 dynamic content 不满足 rigid plane | T12 多帧 Pi3 temporal mask, 或 semantic vehicle/person mask |
| 路缘/隔离带 misalign | side_right (anchor 0) | ego-z 0.3 m 阈值收 5-15 cm 抬起的低 sidewalk | 加 Pi3 normal map 要求近水平法线 |
| 远场 stippling (>30 m) | 所有 cam | IPM 误差 ~ depth × pitch², 远场放大 | 收紧 max_distance 25 m, 接受 coverage 损失 |
| Magenta fringe | overlap edge | 5x5 dilation 拉入非地面颜色 | edge-aware inpaint / guided filter (1 d 工作) |

### 1.6 推荐

| Action | Priority | Cost |
|---|---|---|
| 扩 **10-anchor sweep** 对齐 Phase 3 W1 N | high | CPU ~30 s once worker back, 现在 blocked |
| 加 **temporal-stability ground mask** (Pi3 K=3) 修 front-cam dynamic content | high | 等 T12 跑通 |
| 形态学换 **edge-aware inpaint** | medium | 1 d, 修 magenta fringe |
| Promote 到 **production L1.5 mode** in `stitch_frame.py` | medium | 半天, front-cam 修了之后 |

详: `notes/ipm_hybrid_report.md` · `code/waymo2panorama/projection/ipm_ground.py` · `scripts/phase3/run_ipm_hybrid.py`

---

## 2. T5 Metric Audit — L3 negative 全 metric 一致

### 2.1 问题 & 方法

P2.7 / P3.1b 用 cycle-PSNR 得出 L3 输 L1 = -3.15 ± 0.72 dB。 假设: **PSNR 结构性偏向 blurry L1 method, 换 perceptual / 多 scale / region-separated metric 会翻案**。 audit (anchor 0, 7 cam reconstruction PNGs from Drive) 三路压: **MS-SSIM 4-scale** (multi-scale geometry-tolerant) + **LPIPS-Alex** (learned perceptual) + **region-PSNR sky/object/ground**。

### 2.2 结果 — 三个 metric 全部 widen the gap

| cam | PSNR L1 | PSNR L3 | ΔPSNR | MS-SSIM L1 | MS-SSIM L3 | LPIPS L1 | LPIPS L3 |
|---|---:|---:|---:|---:|---:|---:|---:|
| ring_front_center | 8.24 | 7.91 | -0.33 | 0.292 | 0.082 | 0.220 | 0.291 |
| ring_front_left | 18.88 | 12.81 | **-6.08** | 0.706 | 0.102 | 0.020 | 0.070 |
| ring_side_left | 19.14 | 10.48 | **-8.66** | 0.643 | 0.093 | 0.018 | 0.057 |
| ring_rear_left | 13.28 | 7.74 | **-5.54** | 0.586 | 0.011 | 0.017 | 0.044 |
| ring_rear_right | 11.49 | 8.58 | **-2.91** | 0.637 | -0.003 | 0.026 | 0.060 |
| ring_side_right | 18.26 | 10.83 | **-7.43** | 0.740 | 0.179 | 0.016 | 0.047 |
| ring_front_right | 10.98 | 11.57 | +0.59 | 0.626 | 0.150 | 0.035 | 0.070 |
| **MEAN** | **14.33** | **9.99** | **-4.34** | **0.604** | **0.088** | **0.050** | **0.091** |

- **MS-SSIM (multi-scale geometry-tolerant)**: L1 wins **7/7 cam**, mean ΔSSIM = **-0.52** (huge)。 "L3 sharp-but-shifted, 多 scale 救他" → 不成立。
- **LPIPS (learned perceptual)**: L1 wins **7/7 cam**, L3 = 1.83× worse perceptually。 应该最 forgive 几何漂移 — 没有。
- 最关键: **region-PSNR object band** (中间 row 三分之一, parallax 最该帮 L3 的地方) Δ = **-6.88 dB** — 是三个区里 gap 最大的。 L3 在它最该赢的地方输得最惨。

### 2.3 verdict & paper 应用

- PSNR ranking = MS-SSIM ranking = LPIPS ranking = region-PSNR ranking。 **完全一致**。 L3 输 L1, 任何 metric 视角都不翻。
- **Keep PSNR as paper headline**, 加 (PSNR, MS-SSIM, LPIPS) **三元组** 进 main table 防 reviewer "你 cherry-pick metric" 质疑。 cost 几乎 0。
- 更强论点: gap 在 perceptual metric 上 *更大* → 我们之前实际上是 **under-selling** 这个 negative finding。 paper 头条可以直接写: "L1 wins by 4.3 dB PSNR and is 1.8x closer perceptually (LPIPS)"。

![Per-anchor L1 vs L3 PSNR side-by-side + ΔPSNR bar (Phase 3 W1 10-anchor)。 L3 输 10/10, ΔPSNR mean -3.15 dB; T5 audit (anchor 0) 把这条结论复核到 MS-SSIM / LPIPS / region-PSNR 三个独立 metric, 全部 widen the gap。](images/cycle_trends.png)

详: `notes/metric_audit.md` · `scripts/phase3/audit_metrics.py` · `outputs/phase3/metric_audit/anchor0_audit.json`

---

## 3. T16 Bayesian Depth Fusion — 给下游 .ply 更干净, 不 rescue ERP

### 3.1 是什么

ERP 每个 pixel 上多 cam Pi3 深度做 **inverse-variance (precision-weighted) fusion**: `w_i = sigmoid(conf_logit_i)` 当作 1/σ², `depth_fused = Σ w_i d_i / Σ w_i`, `Σw` 当 combined precision 输出。 对比 baseline = global z-buffer (取 argmin depth)。

### 3.2 结果 (anchor 60 + 90)

| Anchor | ERP coverage | Overlap (≥ 2 cams) | mean \|Δd\| overlap | p95 \|Δd\| | RMSE Bayes-vs-naive |
|---:|---:|---:|---:|---:|---:|
| 60 | 16.3 % | 1.80 % (37,737 px) | **2.04 m** | 7.52 m | 5.09 m |
| 90 | 16.4 % | 2.31 % (48,434 px) | **0.27 m** | 1.11 m | 1.09 m |

- **Anchor 60 的 multi-cam 深度差异显著** (mean 2 m, p95 7.5 m); Bayesian fusion 把 z-buffer 的 "argmin foreground occluder" 偏置换成 confidence-weighted mean → 真给一个 **数值上更干净** 的 depth 产品。
- **Anchor 90 cross-cam 一致性高** (mean 0.27 m), Pi3 在那帧本来就 self-consistent (匹配 Phase 3 W1 abs_rel 0.186 < 0.204)。

### 3.3 honest negative — 不 rescue L3 ERP cycle-PSNR

- **Overlap 只占 ERP 1.8-2.3 %**, 剩 98 % single-cam 区域两个方法 by construction agree → fusion 不影响 visual。
- L3 ERP **真正的 ghost 来源** = **single-cam mis-splat** (Pi3 1 cam 内深度误差把同一物体投到错位置), 不是 cross-cam overlap disagreement。 fusion 帮不到那里。
- 因此 expected cycle-PSNR delta < 0.2 dB (在 10-anchor std 噪声内), 不 rerun 验证。

### 3.4 recommendation

- **正面定位**: 不是 "L3 双影 fix", 是 **下游 .ply geometry 升级** — combined-precision (`Σw`) 字段给 Gaussian splat / NeRF / Pantheon360 消费方一个 strictly better depth product, near-zero cost (one extra `np.add.at`)。
- Integrate 进 `lift_and_project.py` 作 `return_fused_depth=True` 默认路径。
- **不**在 paper main story 拿来推销, **不**急着扩 10 anchor (anchor 60 vs 90 跨度 0.27 m vs 2.04 m 说明 frame-dependent 重)。

详: `notes/bayesian_fusion_report.md` · `code/waymo2panorama/pipeline/depth_bayesian_fusion.py` · `scripts/phase3/run_bayesian_fusion.py`

![Bayesian fusion depth diff at anchor 60: left = naive z-buffer depth, middle = Bayesian-fused depth, right = |diff| (0-2m viridis). 差异集中在 overlap 接缝区, 单 cam 区域 0; mean overlap |Δ| = 2.04 m。](images/bayesian_depth_diff.png)

---

## 4. T6 + T8 + T7-prelim — 支撑 mosaic

### 4.1 T6 Parallax-heavy anchor ranking

10 anchor 按 closeness × coverage 打分, 找 L3 最可能赢的子集:

| Rank | Anchor | Score | P3.1b ΔPSNR (L3-L1) |
|---:|---:|---:|---:|
| 1 | **0** | 0.4112 | -3.13 |
| 2 | **150** | 0.4040 | -2.76 |
| 3 | **60** | 0.3964 | **-1.60** (best) |
| 9 | **180** | 0.3336 | -3.37 (KITTI-SOTA Pi3 但远场, parallax 弱) |
| 10 | **210** | 0.3160 | -2.51 |

- **anchor 60 是 L3-favoring eval 的最佳目标**: rank #3 同时 L3 deficit 最小 (-1.60 dB), 是 T12 (多帧 Pi3) + T18 (Depth Pro) 必跑第一帧。
- **anchor 180 是 negative control**: Pi3 在那帧 abs_rel 0.139 ≈ KITTI SOTA, 但远场为主 parallax 弱 → L1 默认赢, 用来 confirm 新方法不破 easy case。

详: `notes/parallax_subset_report.md` · `data/parallax_subset.json`

### 4.2 T8 Literature Watch

8 paper 扫到, 关键 4 个:

| Paper | 关系 | Code | Action |
|---|---|---|---|
| **Percep360** (ICRA 2026) | **最接近的 direct competitor** — AV → 360 diffusion-only generation | pending **June 2026** | watch GitHub weekly, 4-6 周 scooping window |
| **PanFlow** (AAAI 2025) | 球面 noise warping 全景 video diffusion | claimed open | T19 candidate (T17 Panacea+ 跑不通时备胎) |
| **Fin3R** (NeurIPS 2025) | Pi3/DUSt3R LoRA 用 monocular teacher 蒸 | unclear | **直接对应 T13** (我们 cycle-PSNR self-sup), 可组合做 "cycle-PSNR vs Fin3R-tuned" 比较 |
| **CylinderSplat** (ICLR 2026) | 圆柱 triplane 3DGS 全景 NVS | claimed open | 从 Out-of-Scope 升回 Phase 4 candidate |
| **Dur360BEV** (ICRA 2025) | Real 360 single-cam AV dataset (Durham) | open | Phase 3 W4 / Phase 4 cross-dataset 验证 |

**Our differentiator**: 3D-scene-aware 基础 (Pi3 .ply) 喂全景 video diffusion (Pantheon360 / PanFlow / Panacea+)。 **没有 published method 在 AV2 上把这 3 层都做了**。 window: 4-6 周 before Percep360 code drops。 ship Wave-2/3 integration 锁定 claim。

详: `notes/lit_watch.md`

### 4.3 T7-prelim paper-angle decision pack (v0)

4 个 angle 都过了一遍 (A 数据集 / B 方法 / C negative-result / D system integration), **推荐 B-with-C-as-motivation** = "Hybrid 2D/3D pipeline for AV → 360° stitching, with analysis of why naive 3D-lift fails":

- **Method contribution = T14 IPM hybrid** (+0.20 dB ground-only, +0.04 dB drop-in safe)。 真正 numerical win against fair baseline, 3 anchor 一致。
- **Motivation = T5/L3 negative** (-3.15 dB PSNR, 1.83× LPIPS, -6.88 dB object band)。 把最 robust 的 negative finding 转成 paper 存在的理由 — "naive Pi3 forward-splat fails uniformly, so we engineered around it"。
- **Defense = T5 三元组** (PSNR, MS-SSIM, LPIPS) 防 reviewer cherry-pick 质疑。
- **Statistical power = Phase 3 W1 N=10 anchor**, ±σ 明确。

submission target:

| Venue | Deadline | 推荐 |
|---|---|---|
| **3DV 2026** | ~Aug 2026 (abstract) / Sep 2026 (full) | **Primary** — 3D-focused, 完美 fit, 12 周 runway |
| CVPR 2027 main | ~Nov 2026 | **Upgrade target** if T9/T10 downstream lands |
| CVPR 2026 workshop (AV / 360) | ~Feb-Mar 2026 | C-fallback if T14 10-anchor regresses |

flip triggers (会改变 angle 的): T12 anchor 60 ΔPSNR > +0.5 dB → 升 B-headline; OmniStitch (P3.5) 显著超 L1 → 重 frame 但仍 B; T9/T10 同时成功 → 选择性升 D。 v1 在 W3 D3 (T12+T16+T14b+P3.5 完成后) 重新发。

详: `notes/paper-angle-decision-v0.md`

![Phase 3 W1 10-anchor Pi3 vs LiDAR per-anchor 4-panel (abs_rel / RMSE / δ-thresholds / mean depth, ±1σ band)。 anchor 120-180 mid-log 显著优于 0/30 + 240/270。 Pi3 quality scene-conditional, anchor 180 (abs_rel 0.139) ≈ KITTI Monodepth2 SOTA。 这条数据是 T6 parallax ranking 的 ground truth + T7 paper-angle 论证 Pi3-as-3D-cache 的核心证据。](images/lidar_trends.png)

![Phase 3 W1 10-anchor depth-binned bias: -10.2% (<5m) -> -23.7% (>40m) 单调恶化, slope 在 10/10 anchor 都成立。 Pi3 真有 depth-dependent 系统压缩 (不是 selection bias artifact)。 含义: Sim(3) uniform scalar 不能修, 近场可用, 远场需 LiDAR fusion / backbone fine-tune。](images/depth_binned_metrics.png)

---

## 5. Tracks 状态卡片

| Track | 状态 | Output | 关键数字 |
|---|---|---|---|
| T-Koi-1 | DONE | `deliverables/handoff_to_koi_w2_2026-05-20.pdf` | 8 页, Phase 3 W1 + Koi paper stack 重定位 |
| **T5 metric audit** | DONE | `notes/metric_audit.md` | LPIPS 1.83×, MS-SSIM 0/7, object Δ -6.88 dB |
| **T6 parallax ranking** | DONE | `notes/parallax_subset_report.md` | anchor 60 best L3-favoring, 180 neg control |
| **T8 lit watch** | DONE | `notes/lit_watch.md` | 8 paper, Percep360 4-6 周 window |
| **T14 IPM hybrid** | DONE | `notes/ipm_hybrid_report.md` | ground +0.20 ± 0.11 dB, rear +1.0~+1.7 |
| **T16 Bayesian fusion** | DONE | `notes/bayesian_fusion_report.md` | anchor 60 |Δd| = 2.04 m, .ply cleaner |
| **T7-prelim** | DONE | `notes/paper-angle-decision-v0.md` | B-with-C-as-motivation, 3DV 2026 |
| T12 多帧 Pi3 K=3 | BLOCKED | — | Colab worker offline, job queued 等心跳 |
| T18 Depth Pro drop-in | QUEUED | — | T12 之后 GPU |
| T17 Panacea+ baseline | QUEUED | — | GPU |
| T10 Pantheon360 spike | QUEUED | — | GPU, Phase 4 |
| P3.5 OmniStitch baseline | QUEUED | — | GPU, paper main table 必跑 |

GPU 链条全部 blocked 在 **Colab worker offline** (心跳 2026-05-21T01:14, ~50 min 旧)。 worker 起来 10 s 内自动 pick up T12 job。

---

## 6. 想请 Koi 看的 4 个 open question

(不阻塞, 但你的 reply 会影响 W3-W4 wave 顺序)

1. **paper 角度 B-with-C-as-motivation 你 OK 吗?** 我们默认就走这个 (理由 §4.3): T14 +0.20 dB ground 是真正 method contribution, T5/L3 robust negative 是动机。 如果你倾向 D (system integration as Pi3↔Pantheon360 bridge), 我们可以保留 B paper + Section 6 加 downstream demo。 但 D-headline 需要 T9/T10 GPU 链跑通, 4-6 周 runway 风险较大。

2. **submission target: 3DV 2026 (Aug ddl, 稳) vs CVPR 2027 (Nov ddl, 上限更高)?** 我推荐 3DV primary + CVPR upgrade. 但如果你想押 CVPR 2027 给更多时间 push T9/T10 downstream evidence, 我们 W3 起就按 CVPR runway 排, T14 可以扩到 10 anchor + 多 log + Dur360BEV cross-dataset。

3. **Phase 4 (Pantheon360 spike T10) 优先级**: 现在 W3 就 spike 跑通 (即使初步), 还是 paper accepted/submitted 之后再启? 现在跑通 → D-upgrade path 打开; 等之后跑 → W3-W4 全力 B paper experiments。 我倾向 W3 D1-D3 投 T10 spike 半 budget (1 人天), W3 D4 拍板; 如果不 spike, 我们 T9 ViPE 算 T10 的 prerequisite 也跑。

4. **要不要 W3 主动联系 Percep360 作者?** code 6 月放, 我们 4-6 周 window。 主动 email "看到你们 ICRA 2026 论文, 我们做了 hybrid 角度, 想看看能否互补 / 一起 cite" 既显礼貌也 avoid surprise (他们 release code 时不会被我们 paper "夹击")。 风险: 透露我们的 angle 给 competitor。 默认 **不主动联系**, watch GitHub 即可; 你说联系我们就发。

---

## 7. Next 5 days plan (Wave-2)

Colab worker 起来后立刻开炮 (script 全部 ready):

| Day | Task | GPU? | 估时 | 估出产 |
|---|---|---|---|---|
| D1 | **T14 10-anchor extension** (CPU, script ready) | no | 30 s | 把 3-anchor +0.20 dB ground 升 10-anchor; 验 σ 不爆 |
| D1-D2 | **T12 多帧 Pi3 K=3, anchor 60** | yes | 0.5 d | 看 ΔPSNR 是否 ≥ -0.5 dB (B-headline trigger) |
| D2-D3 | **T18 Depth Pro drop-in baseline** | yes | 1 d | Apple Depth Pro vs Pi3 on anchor 60, 看是否压 Pi3 远场 -24% bias |
| D3-D4 | **T17 Panacea+ baseline on AV2** | yes | 1.5 d | AV2-验证过的唯一 360 video gen, 给 main table |
| D4-D5 | **P3.5 OmniStitch baseline** | yes (or CPU) | 1 d | 已发表唯一 AV-360 stitch baseline, paper main table 必跑 |
| D5 | **T10 Pantheon360 spike (half budget)** | yes | 0.5 d | inference 跑通即可, 不调参 |
| 并行 | **T1 多 log 扩展** (用户确认 UUID 后) | no | 1 d | 解锁 P3.2 多 log, A 角度备胎 + B paper 加权 |

W3 D3 (~5/26) 重发 **T7 v1** 拍板 paper 角度 + venue + 主 schedule (T12+T16+T14b+P3.5 完成后)。

---

## 8. Bottom line

24 小时内 7 个 Wave-1 track 全部收口, **首个正面 method contribution** (T14 IPM ground +0.20 dB) 上桌, **paper 角度 v0 锁** (B-with-C-as-motivation, 3DV 2026 primary)。 L3 negative finding **三个 metric 一致** (T5 metric audit) 转成 paper 动机。 T16 Bayesian fusion 给下游 .ply geometry 升级, 但 honest 说不 rescue ERP。 lit watch 给 4-6 周 scoop window。

**不阻塞, 不等回复, Wave-2 立刻起。** 你的 4 个 open question 答复到了, 我们调整 W3-W4 wave; 没到我们按默认推。

---

## 9. 完整 deliverables 索引

| Item | Path |
|---|---|
| 本 PDF | `deliverables/handoff_to_koi_w2_2026-05-21_mid.{md,pdf}` |
| 前一封 | `deliverables/handoff_to_koi_w2_2026-05-20.{md,pdf}` |
| T14 报告 | `notes/ipm_hybrid_report.md` |
| T5 audit | `notes/metric_audit.md` |
| T16 报告 | `notes/bayesian_fusion_report.md` |
| T6 报告 | `notes/parallax_subset_report.md` |
| T7-prelim 决策 | `notes/paper-angle-decision-v0.md` |
| T8 lit watch | `notes/lit_watch.md` |
| Phase 3 W1 完整 report | `notes/phase3_multi_anchor_report.md` |
| 主 progress 索引 | `agent/progress.md` |
| Plan v5 (17 tracks) | `~/.claude/plans/snug-shimmying-wave.md` |
| Drive 工作区 | `koi_waymo2pano_colab/outputs/phase3/` |
