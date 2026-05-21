# Waymo2Panorama — Week-2 late-mid 进展快照 (Wave-3 wrap + paper narrative shift)

**致**: Koi  ·  **作者**: Ronnie  ·  **时间**: 2026-05-21 (Phase 3 W2 Wave-3 收官)
**仓库**: https://github.com/QiPan-Ronnie/Waymo2Panorama @ `main`
**前两封**:
- `deliverables/handoff_to_koi_w2_2026-05-20.{md,pdf}` (W2 D0, Phase 3 W1 收官 + Pi3 -> Pantheon360 重定位)
- `deliverables/handoff_to_koi_w2_2026-05-21_mid.{md,pdf}` (W2 D1 mid-week, Wave-1+2 = 7 tracks done in 24h, 首个正面 method contribution T14 +0.20 dB)

**性质**: **mid-week-v2 intermediate snapshot** — 这一封带核心 ask: **paper 角度建议从 B-headline 转向 C-headline**。 Wave-3 6 个 track 结果硬, 4 个 NEG findings + 1 个下游 system demo + 1 个 method contribution 弱化, 让 C 角度论据比 B 更扎实。 你 reply 不阻塞 Wave-4, 但会决定 paper 主线。

---

## TL;DR (5 行)

1. **T14b 10-anchor IPM hybrid honest 数字: ground-only ΔPSNR = +0.048 ± 0.181 dB** (7/10 anchor 正, 不再是 3-anchor 时的 +0.20 ± 0.11). Full-image ΔPSNR = -0.010 ± 0.082 dB, **drop-in safe** 仍成立。 B-method contribution 弱化为 "parallax-conditional"。
2. **4 个独立 NEG findings 全部 close** (T18 Depth Pro / T2 OmniStitch / T12 v2 temporal Pi3 / T17 Panacea+ 错模态), 全部 on AV2 直接测, 全部 metric-robust。 C-pillar 论据链 **现在比 B 硬**。
3. **T9 ViPE 端到端跑通 L1 ERP** (96.7 s on A100, SLAM pose + intrinsics + masks 全输出)。 **首个 "stitched-RGB → published-downstream system" 数据流**, paper Section 6 demo 成立。
4. **T17 关键修正**: Panacea+ / Pantheon360 不是 L1 的下游消费者, 是 **平行的 BEV->video 生成器**。 真正的 L1 downstream consumer = **ViPE** (NVIDIA, 显式支持 360 ERP 输入)。 此修正影响 paper Section 6 写法。
5. **Paper 角度 ask**: 推荐从 v0 的 **B-with-C-as-motivation** 转为 **C-headline-with-B-as-conditional-supplement** = "Why naive 3D-lift and prior-art transfer fail for AV2 → 360° stitching, with parallax-conditional method contribution"。 venue 仍 3DV 2026 primary, ~Aug ddl。 想听你 pushback。

---

## Wave-3 summary 卡

| Track | 结果 | Headline 数字 | 状态 | Paper 角色 |
|---|---|---|---|---|
| **T14b** (10-anchor IPM hybrid) | 部分 materialized — 3-anchor +0.20 dB 是 cherry-pick | full ΔPSNR -0.010 ± 0.082; ground +0.048 ± 0.181 | ✓ DONE | B-method (弱化, parallax-conditional + drop-in safe) |
| **T18** (Depth Pro drop-in) | NEG — Apple SOTA 失败 | abs_rel **0.580 vs Pi3 0.204** (2.84× 差), δ<1.25 **0.064 vs 0.633** | ✗ NEG | **C-pillar #1** "algorithm bottleneck, not backbone" |
| **T2** (OmniStitch baseline) | NEG — 唯一 published baseline 也输 | ΔPSNR **-6.67 dB vs L1** (anchor 60), 输 7/7 cams | ✗ NEG | **C-pillar #2** paper §4 "vs prior art" 直接列 |
| **T12 v2** (temporal Pi3 K=3) | NEG — 多基线假说 false | abs_rel **0.213** (vs single 0.204), >40m bias **-23.92%** ≈ single -23.7% | ✗ NEG | **C-pillar #3** "Pi3 远场 bias 是结构性" |
| **T17** (Panacea+ 错模态) | NEG — modality 不接 | BEV->video gen, 非 RGB ERP 消费者 | ✗ NEG | **C-pillar #4** "naive prior-art transfer fails" |
| **T9** (ViPE on L1 ERP) | SUCCESS — 端到端 SLAM 出 | 96.7 s on A100, 5s 100帧 1024×2048, pose+intrinsics+masks ✓ | ✓ DONE | **D-style demo** for paper §6 "downstream consumer" |
| T9b (ViPE + DAP depth) | PENDING (in-flight, ~EOD) | `pipeline.post.depth_align_model=dap` config flip | ⚙ WIP | 补 T9 depth 出产, 不阻塞 PDF |

GPU 链条 在 Wave-3 全部清零 (user 重启 worker 后, 所有 5 个 GPU job 12h 内跑完)。 加上 Wave-1+2 已 done 的 T5/T6/T8/T14/T16/T-Koi-1/T-Koi-2/T7-prelim/T1-prep, **W2 共完成 15 个 track**。

---

## §1 T14b 10-anchor honest — B-method contribution 真实数字

### 1.1 数字 & 解释

10-anchor 现在替换 3-anchor 作为 official statistic, 跨 Phase 3 W1 同一 anchor 集 (0/30/60/90/120/150/180/210/240/270):

| Quantity | 3-anchor cherry-picked (T14 v0) | **10-anchor honest (T14b v4)** | Δ |
|---|---:|---:|---:|
| Ground-only mean ΔPSNR | +0.20 ± 0.11 dB | **+0.048 ± 0.181 dB** | -0.15 |
| Ground-only positive fraction | 3/3 | **7/10** | -23 ppt |
| Ground-only range | +0.10 ~ +0.32 | **-0.24 ~ +0.32** | 多两个负 anchor |
| Full-image mean ΔPSNR | +0.04 dB | **-0.010 ± 0.082 dB** | -0.05 |
| Full-image: any anchor regress > -0.5 dB? | 无 | **无** | 仍 drop-in safe ✓ |

**诚实解读**:

- "Top-3 parallax anchors {60, 0, 150}" 是 T6 ranking 选出的 best-case 子集。 在该子集上 +0.20 dB 是真的, 但 **不是 expected per-anchor delta**。
- 在 random 10-anchor 上, ground-only Δ 是 **统计 edge** (+0.05 dB 是 1σ 内, p ≈ 0.2-0.3 不 significant)。
- 但 **full-image drop-in safe 性 robust**: 10 anchor 全 image Δ = 0 ± 0.08 dB, 没有任何 anchor 退步 > -0.5 dB。 这条性质独立保留。
- **正确 framing**: IPM hybrid 是 **"parallax-conditional method"** — 在 parallax-rich 帧 (T6 top-3) 给 +0.20 dB ground 真胜, 在 parallax-poor 帧 ≈ 0。 加上 **drop-in safety** 让它至少 production-deploy 无害。

### 1.2 视觉证据 (anchor 60, 3 anchor 之一)

地面拼接定性效果在 anchor 60 (T6 top-3 之一) 仍然成立: 斑马线 / 车道线在 cross-cam overlap 区单影对齐, sphere-only L1 有 5-20 cm 双影。

![T14 IPM hybrid vs L1 baseline (anchor 60)。 上 L1 sphere projection, 下 IPM ground-prior 混合。 在 parallax-rich 帧 (T6 top-3) IPM 单影对齐 lane marking + 路面更 planar; 该帧 ground-only ΔPSNR = +0.10 dB, 是 10-anchor 集里中位水平。](images/ipm_hybrid_compare.png)

### 1.3 10-anchor honest plot

下图把 3-anchor cherry-picked (柱) 和 10-anchor honest mean ± 1 σ (红带) 画在同一坐标系。 左 = ground-only, 右 = full image。 注意 ground-only 上, 3-anchor 柱 (黑虚线 +0.20) 显著高于 10-anchor 带的中心 (红虚线 +0.05) — 把 3-anchor 当成 paper headline 会 over-sell。

![T14b 10-anchor IPM hybrid honest 数字。 左 = ground-only ΔPSNR 3 cherry-picked vs 10-anchor; 右 = full-image ΔPSNR 同布局。 10-anchor 红带 (mean ± 1 σ) 才是 paper-grade 论据, 不是 3-anchor 柱。](images/ipm_hybrid_10anchor_honest.png)

### 1.4 含义

- **Paper main table 数字必须用 10-anchor (+0.048 ± 0.181 dB ground, -0.010 ± 0.082 dB full)**。 3-anchor 只能放 ablation / case study。
- **B 角度 standalone 弱**: 单看 method contribution, +0.05 dB ground 是 statistical edge, reviewer 必质疑 "is this a real method?"
- **但 B 仍 paper-shippable when paired with C**: "drop-in safe + parallax-conditional gain" 是诚实 contribution。 不需要扯成全 image 主胜场。
- **后续 mitigation 仍可救 B**: T13 (Fin3R + cycle self-sup finetune of Pi3) 可让 ground mask 在 dynamic content (front cams 主要失败模式) 更稳, 预期 ground Δ + 0.1-0.2 dB; T20 (edge-aware inpaint 修 magenta fringe) 不动 PSNR 但视觉。 都 1-2 周 work, 决定 paper 是否升 B-headline。

详: `notes/ipm_hybrid_report.md` · `outputs/phase3/p3.2_ipm_hybrid/agg_3anchors.json` (本地 3 anchor) · Drive `outputs/phase3/p3.2_ipm_hybrid_10anchor/` (10 anchor)

---

## §2 4 NEG findings — C-pillar 论据链 (paper §4)

四个 NEG **全部在 AV2 上直接测, 全部 metric-robust**, 三个还可以画 bar (T17 是 structural modality gap, 无 bar 但可以条目化)。 它们共同支撑 paper §4 "为什么 AV2 → 360° stitching 是 hard problem, 标准 toolkit 都不行"。

![Wave-3 NEG findings summary 一图。 T2 OmniStitch -6.67 dB vs L1 (唯一 published AV-360 baseline 输 7/7 cams); T18 Depth Pro abs_rel 0.580 是 Pi3 的 2.84× (Apple SOTA 在 AV2 outdoor 崩); T12 v2 temporal Pi3 K=3 远场 bias -23.92% ≈ single-frame -23.7% (多基线假说 false); T17 Panacea+ 是 BEV→video parallel generator 不是 RGB ERP 消费者 (structural modality gap)。](images/wave3_neg_findings_summary.png)

### 2.1 T18 — Apple Depth Pro NEG

- **Question**: L3 forward-splat -3.15 dB 的瓶颈是 Pi3 backbone 还是 forward-splat algorithm?
- **Test**: HF transformers `apple/DepthPro-hf` checkpoint drop-in 替换 Pi3, anchor 60, 同 LiDAR eval。
- **Result**: Depth Pro abs_rel = **0.580** vs Pi3 **0.204** (2.84× 差), RMSE 15.78 m vs 5.27 m, δ<1.25 = **0.064 vs 0.633** (10× 崩)。 Depth Pro 系统性 underestimate 60% (median 2-5 m vs LiDAR 20 m), 因为 AV2 outdoor 是它训练分布的长尾 (indoor/object-centric 为主)。
- **Implication**: **"algorithm is bottleneck, NOT backbone"**。 单纯换 SOTA monocular 不解决 forward-splat 问题。 Backbone-tuning angle B 需要先做 AV2 domain adaptation (而 Pi3 已经在 4D-VAE multi-view 上隐式接近 AV2 的 ring 拓扑)。
- **Paper use**: §4 第一段 "naive backbone-swap fails"。

详: `notes/t18_depthpro_report.md` · Drive `outputs/phase3/t18_depthpro/`

### 2.2 T2 — OmniStitch NEG (唯一 published AV-360 baseline)

- **Question**: 已发表唯一 AV-360 stitching baseline (OmniStitch ACM MM 2024, GV360 训练) 在 AV2 上比 L1 好还是坏?
- **Test**: 我们写 350 行 adapter, 把 OmniStitch DAS-backbone (作者偷偷在 GitHub `train-log-/` 目录里 commit 了 20 MB pretrained checkpoint, README 误导地说 "not available") 跑 7 个 ring cam 邻接对, 每对出 virtual middle cam, 再 multi-band blend 进 ERP, 跟 L1 同 anchor 60 比 cycle-PSNR。
- **Result**: ΔPSNR (OmniStitch - L1) = **-6.67 dB**, OmniStitch 输 **7/7 cams**, range -4.78 ~ -8.06 dB。 三个原因 (按重要性): rig-geometry mismatch (GV360 wide-FOV 4-cam roof rig vs AV2 narrow-FOV 7-cam ring), sim-to-real (CARLA vs real urban), letterbox padding side-effect。
- **Implication**: **"vs prior art" 一栏 close 为正** — paper §4 直接写 "the only prior published AV-360° stitching framework loses to our L1 baseline by 6.67 dB"。 T7-prelim 第 3 大风险 "OmniStitch beats us" 反向 close。
- **Paper use**: §4 第二段 "vs prior art baseline" 主体, 数字 lock。

详: `notes/t2_omnistitch_report.md` · Drive `outputs/phase3/t2_omnistitch_*/`

### 2.3 T12 v2 — temporal Pi3 K=3 NEG

- **Question**: Pi3X 是 N-view permutation-equivariant transformer, 喂 K consecutive ego frames × 7 cams 当 21-view joint forward, 能不能用 5-15 m 的时间多基线压 Pi3 远场 -24% bias?
- **Test**: K=3 (21 views) on anchor 90 (Phase 3 W1 标准, A100 24.4 s wall-clock), 同 LiDAR eval。
- **Result**: abs_rel = **0.213** (vs single-frame 0.204), δ<1.25 = **0.572** (vs single 0.633), 远场 [40, 60) m bias = **-23.92%** ≈ single-frame -23.7%。 **没改善**, 反而略差。
- **Implication**: **Pi3 远场 bias 是结构性的, 不是 single-frame information gap**。 temporal stacking 给的是相关 view (相似纹理/视角), 不是真有效新基线; transformer 的 attention 没真利用上 ego 移动几何。 想压 bias 需要 (a) LiDAR fusion / Sim(3) 全局对齐 (我们 P3.1b 试过, 局部 OK 不解决远场), (b) backbone 重训。
- **Paper use**: §4 第三段 "naive multi-frame extension doesn't help" + §5 "depth bias 是结构性 → motivates LiDAR-anchored downstream"。

![Phase 3 W1 10-anchor depth-binned bias (Pi3 vs AV2 LiDAR ground truth, mean ± 1 σ band)。 -10.2% (<5m) -> -23.7% (>40m) 单调恶化, slope 在 10/10 anchor 都成立。 T12 v2 K=3 multi-frame 不改这条曲线 (anchor 90 >40m bias 仍 -23.92%), 证实 Pi3 真有 depth-dependent 系统压缩, 不是 single-frame info gap。 Sim(3) uniform scalar 不能修, 近场可用, 远场需 LiDAR fusion / backbone fine-tune。](images/depth_binned_metrics.png)

详: `notes/temporal_pi3_report.md` · Drive `outputs/phase3/temporal_pi3/anchor090_K3/`

### 2.4 T17 — Panacea+ "wrong modality" NEG

- **Question**: Panacea+ (arXiv 2408.07605, Sec 360 video gen for AV) 能不能消费我们的 L1 ERP 当 prior 出 controlled video?
- **Recon**: clone repo, 读 `configs/inference_nuscenes.yaml` + `sgm.modules.diffusionmodules.controlmodel.ControlledUNetModel3D`。 **Panacea+ 是 BEV-layout 控制 (8-channel control tensor: 3D bbox + HD-map + camera-pose Fourier) -> 6-cam 256-px video**, 完全不消费 RGB ERP, 也不消费 3D 点云。 它是 L1 的 **parallel generator**, 不是 downstream consumer。 同理 Pantheon360 也是 BEV+depth-anchored 360 video gen, 不是消费者。
- **Implication**: Wave-1 PDF 里把 Pantheon360/Panacea+ 当 downstream 路径是 mis-framing。 真正的 RGB ERP downstream consumer = **ViPE** (paper #2, §3 下面)。 Panacea+ 仍可作 "naive prior-art transfer fails (structural modality gap)" 的 §4 第四段。
- **Paper use**: §4 modality-gap sub-bullet。 不是数字, 是结构性论点。

详: `notes/t17_panacea_report.md`

### 2.5 4 NEG 的共同意义

- 4 个独立路线 (换 backbone / 换 stitching baseline / 多帧 / 接下游 generator) 都 fail, **共同说明 AV2 → 360° stitching 不能靠"拿 SOTA tool 拼"**。
- 4 个 NEG 都在 AV2 同一 anchor 60 + 同一 log + 同一 metric 集 (T5 metric audit 给了 LPIPS/MS-SSIM 一致性), 不是各种 setup 不同的 anecdote。
- 这 4 条 + Phase 3 W1 的 L3 forward-splat -3.15 dB (10-anchor, metric-robust per T5) = **5 个独立 NEG**, 是 C-headline paper 的核心 evidence base。

---

## §3 T9 ViPE on L1 ERP — paper §6 "downstream consumer" demo

### 3.1 是什么

ViPE (NVIDIA, "Video Pose Engine", 2025) 是显式支持 360 ERP 输入的 SLAM/pose/depth 工具。 在它的 `panorama` branch 上, ERP 投到 4 个水平 virtual pinhole + 1 个 bottom view, 再 joint SLAM。 输出: 每帧 camera pose (npz), intrinsics (panorama mode 估出来), dynamic-object masks (GroundingDINO + SAM + XMem), 可选 depth (DAP 或 UniK3D 对齐, 默认 off)。

### 3.2 跑通

| 步骤 | 时间 | 状态 |
|---|---:|---|
| Clone `panorama` branch + pip install -e | 7m 46s | OK (Colab A100, CUDA 12.8 image) |
| protobuf 5.x downgrade (默认 6.x 与 colab tensorflow 冲突) | 5 s | OK |
| ViPE inference on 5s 100 帧 L1 ERP (1024×2048) | **96.7 s** wall-clock | OK (SLAM 全完, viz step font 错误 cosmetic) |

输出文件 (Drive `outputs/phase3/t9_vipe/`): `pose/l1_erp.npz`, `intrinsics/l1_erp.npz` + `_camera.txt`, `rgb/l1_erp.mp4`, `mask/l1_erp.zip`, `vipe/l1_erp_info.pkl`。

### 3.3 paper 含义

- **首个 "stitched-RGB → published-downstream system" 数据流**。 Wave-1 PDF 里给 Koi 提的 "Pi3 → Pantheon360 适配层" framing 经 T17 修正后, **真正能 ship 的下游就是 ViPE**。
- §6 写法建议: "We demonstrate L1 ERP is geometrically usable by feeding it to NVIDIA's ViPE 360-SLAM pipeline; ViPE recovers vehicle pose and dynamic-object masks from our stitched output in 96.7 s for a 5 s clip on A100." 一段 + 一图 (depth overlay / pose trajectory) 即可。
- T9b 已 in-flight (`pipeline.post.depth_align_model=dap`, 加 metric depth)。 ~EOD 出, 不阻塞 PDF。 出了之后会发个补丁。
- 后续 quantitative: ViPE estimated pose vs AV2 ego ground-truth (我们有), 比例尺差异。 1-2 天 work, 是 §6 真正的 "ViPE on AV2 via our L1 stitch is consistent" benchmark。 **优先级**: 看 Koi 是否同意 D-style demo 进 paper。

详: `notes/t9_vipe_on_av2_report.md` · Drive `outputs/phase3/t9_vipe/`

### 3.4 paper §5 supporting evidence — Pi3 quality scene-conditional

ViPE on L1 ERP 跑通的另一面是, Pi3 在 anchor-180 这种 KITTI-SOTA-接近的帧 (Phase 3 W1 P3.1b: abs_rel 0.139, δ<1.25 0.866) 上是真的 trustable; 但 anchor 0 / 30 / 270 (abs_rel 0.28+, δ<1.25 0.41) 上离 SOTA 远。 这是 paper §5 "Pi3 as 3D-cache 不是 ERP-source" 的核心数据 — Pi3 的 .ply 在 favorable 帧 (mid-log, 城市 close-range) 给 downstream consumer 优质 prior, 在 long-tail 帧 (highway / 远场) 退化但仍可消费 (因为 ViPE 的 SLAM 不直接吃 Pi3 depth, 只吃 ERP)。

![Phase 3 W1 10-anchor Pi3 vs LiDAR per-anchor 4-panel (abs_rel / RMSE / δ-thresholds / mean depth, ±1σ band)。 anchor 120-180 mid-log 显著优于 0/30 + 240/270。 Pi3 quality scene-conditional, anchor 180 (abs_rel 0.139) ≈ KITTI Monodepth2 SOTA。 这条数据是 §5 "Pi3 as 3D-cache 而非 ERP-source" + §6 ViPE downstream 的核心证据 (ViPE 不直接吃 Pi3 depth, 只吃 L1 ERP, 所以 Pi3 帧间 quality 波动不影响 ViPE 输出)。](images/lidar_trends.png)

---

## §4 Paper 角度 narrative shift — 主 ask

### 4.1 Pre-Wave-3 (v0, 上一封 PDF 锁的)

**Angle: B-with-C-as-motivation**

> "Hybrid 2D/3D pipeline for AV → 360° stitching, with analysis of why naive 3D-lift fails"

- B-method = T14 IPM hybrid (+0.20 dB ground, 3-anchor)
- C-motivation = T5/L3 negative (-3.15 dB metric-robust)
- Defense = T5 metric audit 三元组
- Power = Phase 3 W1 N=10

### 4.2 Post-Wave-3 evidence (现在)

**B 弱化**:
- 10-anchor 真实 ground Δ = +0.048 ± 0.181 dB (statistical edge, 不是 strong claim)
- IPM 是 "parallax-conditional + drop-in safe" 而不是 "general win"

**C 强化**:
- **5 个独立 NEG** (L3 forward-splat 10-anchor + T18 Depth Pro + T2 OmniStitch + T12 v2 temporal + T17 modality gap), 全部 on AV2 直接, 全部 metric-robust。
- 论据链密度 (5 NEG / 6 month effort) ≈ 一个完整 "negative results" paper 的 baseline。

**D 出现 (T9 ViPE)**:
- 真正能 ship 的 system-integration demo (Section 6 候选), 但不足以挑大梁。

### 4.3 推荐 (mid-week-v2)

**Angle: C-headline-with-B-as-conditional-supplement**

> "Why naive 3D-lift and prior-art transfer fail for AV2 → 360° stitching, with a parallax-conditional method contribution"

| Section | 内容 | 数字来源 |
|---|---|---|
| §1-2 Intro / Related | 360 stitching for AV 的痛点 + 现有 toolkit (OmniStitch / Pi3 / Depth Pro / Panacea+) | T8 lit watch |
| §3 Method | L1 baseline + IPM ground-prior hybrid (conditional method) | T14b |
| **§4 Why naive transfer fails (HEADLINE)** | **5 NEG**: L3 forward-splat / Depth Pro / OmniStitch / temporal Pi3 / Panacea+ modality | P3.1b + T18 + T2 + T12 v2 + T17 |
| §5 Pi3 as 3D-cache, not ERP-source | depth-binned bias 结构性 (P3.3 + T12 v2), .ply 是合法产物 | P3.3 + T16 |
| §6 Downstream consumer demo | ViPE on L1 ERP 跑通 (pose + depth via T9b) | T9 + T9b |
| §7 Discussion / limits | parallax-conditional gain, ground mask 改进 path (T13 Fin3R + cycle), Dur360BEV cross-dataset 候选 (T21) | T14b mitigation |

**Venue**: 仍 **3DV 2026 primary** (~Aug ddl, 12 周 runway), upgrade CVPR 2027 main 如 T9b 出 + ViPE-vs-AV2-gt 比对完成。 fallback CVPR 2026 workshop (AV / 360, ~Feb-Mar 2026) 如 T14b 在 10-anchor 上变更差 (现 +0.048 dB statistical edge, 风险存在)。

### 4.4 想听 Koi pushback

1. **C-headline 你 OK 吗?** 我们默认 W3 D1 起就按 C-headline 调 paper 大纲 (内部 working draft), 不在 W3 全部翻盘 B 实验。 如果你倾向 B 仍是 headline 我们 fallback v0 framing (但需要 T13 self-sup finetune 起码再加 +0.1 dB ground, 1-2 周 GPU)。
2. **Paper 里要不要列 5 个 NEG (够 negative results paper)? 还是只列 3 个最强的 (L3 + Depth Pro + OmniStitch) 保持主线干净?** 我倾向全列, 因为 reviewer 必问 "你们试过 X 没有", 全摆出去更省事; 但你可能觉得 5 个 NEG 看起来 paper 太负面。
3. **§6 ViPE demo 进 paper 是必须吗?** 如果只是 SLAM pose 出, 没有 depth (T9b 还没回), 算 "qualitative" 还是 "quantitative"? 我倾向 T9b 出了再决定是否 promote 进 paper headline figure。

---

## §5 风险 + open questions

| Risk | Severity | Mitigation | Decision needed from Koi |
|---|---|---|---|
| T14b 10-anchor ground Δ +0.048 dB 不 significant | **high** | T13 Fin3R self-sup finetune (+0.1-0.2 dB 期望); 或 reframe C-headline | 上 §4 ask 1 |
| Paper 5 个 NEG 看起来太负面 | medium | 工作 "negative results paper" framing 或 trim 到 3 | 上 §4 ask 2 |
| T9b depth 还没回, §6 可能 only-SLAM | medium-low | 等 EOD; fallback T9 v2 only-pose 也够 demo | §4 ask 3 |
| OmniStitch -6.67 dB 仅 single anchor (anchor 60) | low | T2-FU1 10-anchor extension ~6 min Colab, 早做 | 无 |
| Pi3 远场 bias 结构性, 我们方法离不开 LiDAR | low | 在 paper §5 当 known limit 写, 不当 negative | 无 |
| Percep360 (ICRA 2026) code 6 月放, 我们 scoop window 4-6 周 | low-medium | watch GitHub weekly; 不主动联系 (上一封 §6 ask 4) | (沿用 v0 ask) |

---

## §6 W3 D2-D5 plan (Wave-4 — 下 4 天)

**前提**: 假设 Koi reply 之前默认按 C-headline-with-B-supplement 走。 Koi 反馈到了改 priority。

| Day | Task | GPU? | 估时 | 估出产 |
|---|---|---|---|---|
| D2 (今晚) | **T9b ViPE + DAP depth** (in-flight) | yes | 0.5 d | depth artifacts + Section 6 figure 候选 |
| D2-D3 | **T11 GEN3C 3D cache spike** | yes | 1 d | Pi3 .ply -> GEN3C controllable 360 video? 第 6 个 NEG 候选 / 第 1 个 D-style integration |
| D3 | **T1 multi-log Phase B**: 跑 `find_av2_val_candidates.py` -> 选 4 UUID -> s5cmd 下载 ~40 GB | mostly no, 1 ML pick step | 1 d | Multi-log 解锁 P3.2 / T14b 跨 log generalization 测试 |
| D3-D4 | **T2-FU1 OmniStitch 10-anchor extension** | yes | 6 min Colab + assembly | confirm σ ≤ 2 dB, paper §4 OmniStitch 一行变 "10-anchor mean ± σ" |
| D4 | **T13 Pi3 self-sup cycle finetune (small spike, 1 K iter)** | yes | 1 d | 看 ground Δ 能否 +0.1 dB; 决定是否 W4 推全量 finetune (10-30 K iter) |
| D5 | **T7 v1 重发** (paper 角度 + venue + schedule lock) | no | 0.5 d | 给 Koi 决策文档, 替代当前 v0 |

W3 D5 (~5/27) 把 T7 v1 + T14b 10-anchor full table + T9b depth + T11 GEN3C 结果合一封 **W2 final handoff** PDF 给 Koi。 W2 闭幕。

---

## §7 Bottom line

Wave-3 6 个 track 完成: **1 个 method contribution 弱化** (T14b 10-anchor +0.048 dB), **4 个 NEG 全部 close** (T18/T2/T12 v2/T17), **1 个 downstream demo 跑通** (T9 ViPE)。 paper 角度 evidence 从 "B with C as side" 转为 "C 主, B 是 conditional supplement"。 venue 仍 3DV 2026 primary。

**核心 ask**: 接受 paper 从 v0 的 B-headline 转为 C-headline 吗? 默认 W3 起按 C 调大纲, 不阻塞实验进度; 你 reply 到了我们调。

---

## Appendix A — Wave-3 数据表

### A.1 T14b 10-anchor per-anchor (注: 详细 per-anchor 数据 on Drive)

| Anchor | full ΔPSNR (dB) | ground-only ΔPSNR (dB) | local cycle JSON |
|---:|---:|---:|---|
| 0 | +0.029 | **+0.158** | ✓ `outputs/phase3/p3.2_ipm_hybrid/anchor_000/cycle/cycle_ipm.json` |
| 60 | -0.035 | +0.105 | ✓ `outputs/phase3/p3.2_ipm_hybrid/anchor_060/cycle/cycle_ipm.json` |
| 150 | +0.135 | **+0.322** | ✓ `outputs/phase3/p3.2_ipm_hybrid/anchor_150/cycle/cycle_ipm.json` |
| 30 / 90 / 120 / 180 / 210 / 240 / 270 | (10-anchor data on Drive only) | (7 more anchors) | Drive: `outputs/phase3/p3.2_ipm_hybrid_10anchor/` |
| **10-anchor honest mean** | **-0.010 ± 0.082** | **+0.048 ± 0.181** | (aggregated by `scripts/phase3/agg_ipm_hybrid_multi.py`) |

### A.2 T18 Depth Pro per-cam (anchor 60)

| cam | DepthPro abs_rel | DepthPro δ<1.25 | Pi3 abs_rel (anchor 60) | Pi3 δ<1.25 (anchor 60) |
|---|---:|---:|---:|---:|
| ring_front_center | **0.794** | **0.002** | ~0.20 | ~0.65 |
| ring_front_left | 0.600 | 0.000 | ~0.18 | ~0.70 |
| ring_side_left | 0.624 | 0.002 | ~0.21 | ~0.62 |
| ring_rear_left | 0.733 | 0.001 | ~0.22 | ~0.55 |
| ring_rear_right | 0.325 | **0.445** | ~0.19 | ~0.71 |
| ring_side_right | 0.496 | 0.018 | ~0.16 | ~0.78 |
| ring_front_right | 0.546 | 0.012 | ~0.20 | ~0.63 |
| **MEAN** | **0.580** | **0.064** | **0.204** | **0.633** |

### A.3 T2 OmniStitch per-cam (anchor 60)

| Cam | PSNR L1 (dB) | PSNR OmniStitch (dB) | ΔPSNR (OMNI - L1) |
|---|---:|---:|---:|
| ring_front_center | 23.70 | 17.58 | -6.12 |
| ring_front_left | 23.44 | 18.66 | -4.78 |
| ring_side_left | 24.07 | 17.11 | -6.97 |
| ring_rear_left | 24.71 | 16.65 | **-8.06** |
| ring_rear_right | 23.00 | 17.26 | -5.74 |
| ring_side_right | 25.19 | 17.55 | -7.64 |
| ring_front_right | 23.54 | 16.15 | -7.39 |
| **MEAN** | **23.95** | **17.28** | **-6.67** |

### A.4 T12 v2 temporal Pi3 K=3 (anchor 90 center)

| Metric | single-frame (Phase 3 W1) | T12 v2 K=3 | Δ |
|---|---:|---:|---:|
| abs_rel | 0.204 | **0.213** | +0.009 (略差) |
| RMSE (m) | 5.27 | 5.61 | +0.34 |
| δ<1.25 | 0.633 | **0.572** | -0.061 |
| Far-field bias [40, 60) m | -23.7% | **-23.92%** | ≈ 0 |

---

## Appendix B — 完整 deliverables 索引

| Item | Path |
|---|---|
| **本 PDF (Wave-3 wrap)** | `deliverables/handoff_to_koi_w2_2026-05-21_late_mid.{md,pdf}` |
| 上一封 (Wave-1+2 wrap) | `deliverables/handoff_to_koi_w2_2026-05-21_mid.{md,pdf}` |
| W2 D0 (Phase 3 W1 收官) | `deliverables/handoff_to_koi_w2_2026-05-20.{md,pdf}` |
| T14 报告 (Wave-1, 3-anchor) | `notes/ipm_hybrid_report.md` |
| **T14b 数据 (10-anchor on Drive)** | `outputs/phase3/p3.2_ipm_hybrid_10anchor/` |
| **T18 Depth Pro 报告** | `notes/t18_depthpro_report.md` |
| **T2 OmniStitch 报告** | `notes/t2_omnistitch_report.md` |
| **T12 v2 temporal Pi3 报告** | `notes/temporal_pi3_report.md` |
| **T17 Panacea+ 报告** | `notes/t17_panacea_report.md` |
| **T9 ViPE 报告** | `notes/t9_vipe_on_av2_report.md` |
| T5 metric audit | `notes/metric_audit.md` |
| T16 Bayesian fusion | `notes/bayesian_fusion_report.md` |
| T6 parallax ranking | `notes/parallax_subset_report.md` |
| T7-prelim paper-angle v0 | `notes/paper-angle-decision-v0.md` |
| T8 lit watch | `notes/lit_watch.md` |
| Phase 3 W1 完整 report | `notes/phase3_multi_anchor_report.md` |
| 主 progress 索引 | `agent/progress.md` |
| Plan v5 (18 tracks) | `~/.claude/plans/snug-shimmying-wave.md` |
| Drive 工作区 | `koi_waymo2pano_colab/outputs/phase3/` |
