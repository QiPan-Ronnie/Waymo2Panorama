# Waymo2Panorama Progress

> **Latest: 2026-05-21 ~04:30 UTC** — **Phase 3 W2 Wave-1 + Wave-2 全部 CPU autonomous work 完成 (9 tracks / ~5h via 8 parallel subagents)**。
>
> ## Wave-1 (6 tracks):
> - **T-Koi-1** ✅ — 8 页 PDF (Phase 3 W1 + Pi3→Pantheon360 适配层定位)
> - **T5** ✅ — cycle-PSNR metric audit: **L3 negative metric-robust** (LPIPS 1.83× worse, MS-SSIM 0/7, object-band -6.88 dB)
> - **T6** ✅ — parallax ranking: anchor 60 best (rank #3 + 最小 L3 deficit), anchor 180 negative control
> - **T8** ✅ — lit watch: PanFlow + Fin3R + Percep360 (4-6 周 scoop window) + CylinderSplat 升回 Phase 4
> - **T14** ✅ — **IPM ground hybrid: 首个正面 method contribution** (ground-only ΔPSNR +0.20 ± 0.11 dB across 3 anchors, rear cams +1.0~+1.7 dB, full-image drop-in safe)
> - **T16** ✅ — Bayesian depth fusion: **修 .ply 几何 (overlap RMSE 1-5m), 不修 L3 ERP** (~2% ERP overlap, ghost 主因 single-cam mis-splat)
>
> ## Wave-2 (3 tracks):
> - **T7-prelim** ✅ — paper 角度 = **B-with-C-as-motivation**, primary venue **3DV 2026** (~Aug ddl), upgrade CVPR 2027 if T9/T10 lands. Top risk: T14 10-anchor regression
> - **T1-prep** ✅ — AV2 val UUID 选 4 个候选策略 (Miami urban + Pittsburgh highway + Detroit/DC dense + DC night) + 自动 scan script ready
> - **T-Koi-2** ✅ — 9 页 mid-week snapshot PDF for Koi (5 图含 IPM compare + Bayesian depth diff)
>
> ## 🟢 Worker UP (~03:47 UTC user restarted A100) — Wave-3 大丰收
>
> **3 个 NEG findings 综合 → paper B-with-C-as-motivation 论据链非常硬**:
>
> - **T18 ✅ DONE Depth Pro NEG**: 2.84× worse than Pi3 on AV2 (abs_rel 0.580 vs 0.204, δ<1.25 0.064 vs 0.633). **Algorithm is bottleneck, NOT backbone** — Apple SOTA monocular AV outdoor 不行。 angle C 强化, paper hook 拿下。
>
> - **T2 ✅ DONE OmniStitch NEG**: -6.67 dB vs L1 (OmniStitch 17.28 vs L1 23.95 anchor 60), 输 7/7 cams。 **唯一 published AV-360 baseline 也输 L1**, T7-prelim 第 3 大风险 (OmniStitch beats us) 反向 close 为正。 paper "vs prior art" 一栏铁稳。
>
> - **T12 v2 ✅ DONE temporal Pi3 K=3 NEG**: abs_rel 0.213 (vs single 0.204), δ<1.25 0.572 (vs 0.633), 远场 bias -23.92% (vs single 10-anchor mean -23.7%)。 **多帧时间多基线假说 false** — Pi3 远场 bias 是结构性 (not single-frame info gap)。
>
> in-flight: T14b v3 (10-anchor IPM, 修正 args 后重发 ~10 min), T9 ViPE on L1 ERP subagent (downstream consumer demo)
>
> **🎯 T17 critical insight** (Panacea+ recon DONE, inference NOT run):
> - Panacea+ 是 **parallel generator** (BEV + 3D bbox + HD-map → 6-cam video), **不消费**我们 RGB ERP
> - 同理 Pantheon360 — 它们是和 L1 平行的另一条生成路径, 不是 L1 的下游
> - **真正的 downstream consumer for L1 ERP = ViPE** (paper #2, 显式支持 360 ERP 输入 → pose + metric depth)
> - paper narrative pivot: "downstream demo" 走 ViPE-on-L1-ERP 而非 Pantheon360/Panacea+
> - Panacea+ 仍可作 paper Section 4 "naive prior-art transfer fails" 第 4 个数据点 (modality gap structural)
>
> T14b v2 silent fail (我 bash 漏传 run_ipm_hybrid.py 必需参数 --erp-h/w/--ego-z-thresh-m 等)。 v3 修正重发 ~10 min。
>
> Wave-1 deliverable confirmed: T-Koi-1 + T-Koi-2 PDFs 给 Koi async
>
> T12 v1 crashed 11s (Pi3 repo not in /content after restart). T14 subagent's Colab job (3-anchor IPM) ran 84s, eval succeeded but bash aggregator heredoc crashed — per-anchor JSON OK on Drive. Anchor 150 ground-only +0.32 dB confirms anchor-60-extension positive direction.
>
> ## 🔴 Still blocked / pending
> - **T12** (multi-frame temporal Pi3 K=3 @ anchor 60) — Colab job queued, auto-pick up 10s 内
> - **T1 Phase B** (run find_av2_val_candidates.py → pick 4 UUIDs → s5cmd 下载 ~40 GB)
> - **T14b** (extend IPM hybrid 3 anchors → 10 anchors, CPU ~30s)
> - **T18** (Depth Pro / Metric3D drop-in on anchor 60)
> - **T2** (OmniStitch baseline)
> - **T9 / T10 / T11 / T17** (ViPE on L1 / Pantheon360 spike / GEN3C 3D cache / Panacea+ baseline)
> - **T13** (self-sup cycle finetune of Pi3, training)
>
> ## Paper 角度 (locked v0)
> **B-with-C-as-motivation**: "Hybrid 2D/3D pipeline for AV → 360 stitching, with analysis of why naive 3D-lift fails". IPM hybrid 是 method contribution (+0.20 dB ground), L3 forward-splat -3.15 dB metric-robust negative 是 motivation, T5 metric audit 是 reviewer defense, T16 Bayesian fusion 是 .ply deliverable upgrade。 Primary venue 3DV 2026, upgrade CVPR 2027。
>
> ## Next actions (用户 W3 D1)
> 1. 重启 Colab worker cell — unblock T12 + 所有 GPU tracks
> 2. 把 `handoff_to_koi_w2_2026-05-21_mid.pdf` 发 Koi (异步)
> 3. (可选) Koi 反馈到了再调 priority — 默认 D 1: T12 finish + T14b 10-anchor; D 2: T17/T18; D 3: T1 multi-log; D 4: T9/T10/T11 system integration
>
> **🎯 T14 IPM ground hybrid: 首个正面 method contribution** (3 anchors)
> - 全 image ΔPSNR = **+0.04 dB** (drop-in safe, IPM hybrid ≈ L1)
> - 仅 ground 区域 ΔPSNR = **+0.20 ± 0.11 dB** (consistent 跨 3 anchors)
> - Rear cams ground-only **+1.0~+1.7 dB** (crosswalk / lane markings 跨 cam 边界对齐, 5-20 cm ghost-shifts 消失)
> - vs L3 forward-splat (-3.15 dB), IPM hybrid 是**结构性改进** — paper 角度 B (method) 现在有 concrete contribution。
> - 失败模式: front cams 动态阴影 -0.5~-0.8 dB; 后续 T20 (Fin3R + cycle combo) 可改进。
> - 下一步: Colab 复活后扩 10 anchor sweep (script 已写好, CPU job ~30s)。

> **Latest: 2026-05-21 ~00:18 UTC** — Phase 3 W2 Wave-1 早期进展。
> 启动 v5 plan (`C:\Users\14294\.claude\plans\snug-shimmying-wave.md`) 下 18 tracks 多 subagent 并行执行。
>
> **T-Koi-1**: 8 页 PDF 给 Koi (Phase 3 W1 + 重新定位为 Pi3→Pantheon360 AV2 适配层 + 5 forward path)。
> **T5 metric audit**: **L3 negative 结论 metric-robust** — LPIPS 1.83× 更差, MS-SSIM 0/7 cams, object-band PSNR -6.88 dB (parallax 本该帮 L3 的地方反而输得最惨), sky -3.78, ground -3.22. paper headline 不变 PSNR, 但 main table 加 (PSNR, MS-SSIM, LPIPS) 三元组防 reviewer 质疑 cherry-pick。
> **T6 parallax ranking**: top-3 anchors {0, 150, 60} (score 0.41-0.40), bottom {180, 210} (~0.32). 推荐 T12/T18 先跑 anchor 60。
>
> in-flight: T-Koi-2 (Wave-1 mid-week Koi PDF) + T1-prep (AV2 val UUID 候选搜索)。
>
> **T16 Bayesian fusion done**: Pi3 conf-as-inverse-variance per-ERP-pixel fusion. **修 .ply 几何 (overlap 区域 RMSE 1-5m, 建筑边界更干净), 但不修 L3 ERP cycle-PSNR** (ERP overlap 只 ~2%, L3 ghost 主因是 single-cam mis-splat, fusion 修不了)。 paper framing: ".ply 更干净 for downstream consumer" 而非 "L3 ERP 修好"。 commit `e1dbaa6`. 
>
> **Wave-1 全 7 个 CPU tracks 完成** ✅ (T-Koi-1 + T5 + T6 + T8 + T14 + T16 + T7-prelim). Wave-2 启动: T-Koi-2 (mid-week snapshot) + T1-prep (UUID 选 4 个候选)。
>
> **📜 T7-prelim Paper-angle 决定 (v0)**: 推荐角度 **B-with-C-as-motivation** = "Hybrid 2D/3D pipeline for AV → 360 stitching, with analysis of why naive 3D-lift fails". IPM hybrid (+0.20 dB ground) 作 method contribution; L3 forward-splat negative (-3.15 dB, metric-robust per T5) 作 motivation。 Primary venue **3DV 2026** (~Aug 2026 ddl, 12 周 runway), upgrade CVPR 2027 if T9/T10 downstream lands。 Top risk: T14 10-anchor extension regress (Colab worker back 后必跑)。 Re-issue T7 v1 at W3 D3 after T12 + T16 + T14b + P3.5 done。
>
> **T8 lit watch 完成**: PanFlow (AAAI 2025, alternative panoramic diffusion) + Fin3R (NeurIPS 2025, LoRA fine-tune Pi3 — 直接对应我们 T13) + CylinderSplat (ICLR 2026, 提升出 Out-of-Scope) + Percep360 (ICRA 2026 closest competitor, code pending June 2026)。 我们 hybrid (3D-aware + diffusion) 角度 4-6 周 scooping 窗口。 plan v6 候选: T19 PanFlow spike / T20 Fin3R+cycle combo / T21 Dur360BEV cross-dataset。
>
> **⚠️ BLOCKED**: T12 (temporal Pi3 K=3) submitted Colab job `phase3-t12-temporal-pi3-k3-anchor90` (commit `a95f75c`), 但 Colab worker 心跳 2026-05-21T01:14 已 ~50min 旧, worker session 断了。 **需用户重启 Colab worker cell** (scripts/cell_acq_worker.py 内容), 起来 10s 内自动 pick up job。 阻塞所有 GPU 链条 (T12/T18/T9/T10/T11/T2/T17/T13)。

> **2026-05-20 ~23:31 UTC** — **Phase 3 W1 (multi-anchor robustness) 完成**。
> 10 anchors × Pi3 + 全 metric stress test 结果: Phase 2 所有 headline 数字都在 Phase 3 1σ 内。 Pi3 vs LiDAR `abs_rel = 0.202 ± 0.042`, `δ<1.25 = 0.697 ± 0.142`。 L1 vs L3 `ΔPSNR = -3.15 ± 0.72 dB` (10/10 anchor L3 全输, range -1.60 ~ -4.22)。 Anchor 180 最佳: `abs_rel = 0.139, δ<1.25 = 0.866` 接近 KITTI SOTA。 Phase 2 conclusions **鲁棒**。 详见 `notes/phase3_multi_anchor_report.md`。 下一步: P3.2 多 log + P3.5 OmniStitch baseline + P3.6 D8 paper angle 决策。

> **2026-05-20 ~22:51 UTC** — **Phase 2 P2.11 Pi3 vs LiDAR 完成 (single anchor)**。
> Phase 1 (L1) ✅ · Phase 2 D1 (Pi3 胜) ✅ · P2.3-P2.5 (Sim3 + .ply) ✅ · P2.6 (L1 vs L3 视觉 negative) · P2.7 (cycle-consistency: L3 PSNR 8.65 vs L1 11.78, -3.13 dB) ✅ · **P2.11 Pi3 vs LiDAR: overall abs_rel 0.215, RMSE 7.70m, δ<1.25 = 65.3% (99,015 matched points)** ✅。 **关键发现: Pi3 系统性低估深度 ~25% (mean 13.96m vs 18.53m), 近场 (<15m) δ<1.25 ~0.9, 远场 (>20m) 跌到 ~0.22-0.58**。 下一步: Phase 3 (多 sequence + paper angle 决策 / OmniStitch baseline)。

---

## Phase 完成度

| Phase | 任务 | 状态 |
|---|---|---|
| 0 | Repo bootstrap, plan v0/v1/v2 | ✅ COMPLETE |
| 0.5 | AV2 API spike, 2×4 mosaic, GO 判定 | ✅ COMPLETE |
| **1** | **L1 baseline (sphere + multi-band, mirror fix)** | ✅ COMPLETE · tag `v0.1-l1-mvp` |
| **2 D1** | **Pi3 vs DVGT head-to-head → Pi3 胜** | ✅ COMPLETE · tag `v0.2-d1-resolved` |
| 2 P2.2 | Backbone 适配 AV2 (504×504 letterbox) | ✅ COMPLETE |
| 2 P2.3 | Sim(3) Pi3-world ↔ AV2 ego alignment | ✅ COMPLETE |
| 2 P2.4 | `code/.../alignment/sim3_align.py` (Umeyama) | ✅ COMPLETE |
| 2 P2.5 | `code/.../pipeline/lift_and_project.py` + `.ply` 导出 | ✅ COMPLETE |
| 2 P2.6 | L1 vs L3 视觉对比 | ⚠️ **结论 negative**: forward-splat ERP 不优于 L1, 详见 §"L3 探索结论" |
| **2 P2.7** | **Cycle-consistency PSNR/SSIM/MAE** | ✅ **DONE 2026-05-20**: L3 mean PSNR 8.65 vs L1 11.78 → **ΔPSNR = -3.13 dB**, L3 输 7/7 cam (除 front_center 微胜 0.26 dB)。 forward-splat 量化也确认输给 L1。 |
| 2 P2.8 | 多帧 temporal smoothing | ⏸️ skipped — 单帧已得出 L3 forward-splat 不优结论, 多帧不会改变 |
| **2 P2.9** | **`notes/l3_evaluation_report.md`** | ✅ **DONE 2026-05-20** |
| **2 P2.10** | **tag `v0.2-l3-mvp`** | ✅ **DONE 2026-05-20** — Phase 2 主线收官 |
| **2 P2.11** | **Pi3 vs AV2 LiDAR depth eval** | ✅ **DONE 2026-05-20**: overall abs_rel 0.215, RMSE 7.70m, δ<1.25=65.3% (n=99015). 近场 δ<1.25≈0.9, 远场跌到 0.22-0.58。 Pi3 系统性低估 ~25%。 详见 `notes/pi3_vs_lidar_report.md` |
| **3 W1 P3.3** | **Depth-binned Pi3 vs LiDAR** | ✅ **DONE 2026-05-20**: bias 单调恶化 -12.8% (<5m) → -33.8% (>40m). 证实 Pi3 是真有 depth-dependent 压缩, 不是 selection bias artifact. |
| **3 W1 P3.1** | **Multi-anchor Pi3 (10 anchors)** | ✅ **DONE 2026-05-20**: 10 anchors on A100, mean fwd 1.23s (warm), 总 74s. 详见 `notes/phase3_multi_anchor_report.md` |
| **3 W1 P3.1b** | **Batch P2.7 + P2.11 over 10 anchors** | ✅ **DONE 2026-05-20**: Phase 2 single-frame 数字 all within 1σ. abs_rel 0.202±0.042, δ<1.25 0.697±0.142, ΔPSNR -3.15±0.72 (L3 输 10/10). Phase 2 conclusions 鲁棒. |
| 3 W2-3 | P3.2 多 log + P3.5 OmniStitch baseline + P3.6 D8 paper angle 决策 | ⏸️ next |
| 3 W4 | P3.7 Pantheon360 集成 spike | ⏸️ later |
| 4 | Pantheon360 集成 + Waymo Track B | ⏸️ 未启动 |
| 5 | Paper / follow-up spec | ⏸️ 未启动 |

**整体: Phase 0-2 主线约 70%, 略超 plan v2 W1-W2 进度。**

---

## L3 探索结论 (关键 negative finding)

试过 3 种参数组合 (raw conf > 0.1 / strict conf > 0.5 dist < 40m / L1+L3 hard-mask hybrid), **视觉上都不及 L1 sphere projection**。

**根因**:
- Pi3 单目深度 ±0.3m variance → 路面在 ERP 出现"鼓包"
- L1 (parallax-naive) 和 L3 (3D-aware) 把同一物体投到 ERP 不同位置 → blend 出双影
- 天空 / 低纹理区 Pi3 conf 低, 砍掉后 ERP 大片黑色

**含义**: forward-splat to ERP **不是 L3 的正确输出形式**。 L3 的真正产物是:
- `fused_pointcloud.ply` (690K colored 3D 点, AV2 ego 米制坐标系, 9.9 MB)
- Per-view depth maps (7 张)
- 供下游 3D-aware 消费 (Pantheon360, 3DGS, depth-conditioned diffusion)

要让 L3 ERP 视觉超 L1, 需要 raycast + z-buffer 或 3D Gaussian Splatting (LiftProj/CylinderSplat-class), **这是 Phase 4 题目**。

详见: `notes/backbone_decision.md`, `deliverables/handoff_to_koi_2026-05-20.md` §6。

---

## 关键数字

| Metric | Value |
|---|---|
| AV2 anchor | log `02a00399-3857-444e-8db3-a8f58489c394` (val) · 7 ring + 2 stereo · 319 frames @ 20Hz |
| Sync delta | 22.49 ms (< 50 ms 阈值) |
| Pi3X forward (A100 bf16, 7 view joint) | **8.35 s**, peak 7.5 GB |
| Pi3 K-recovery 误差 vs AV2 真值 | +0.06% ~ +2.08% (mean ~1%) |
| **Sim(3) 对齐残差** | **mean 0.157 m, max 0.218 m, scale 1.0346** |
| L3 .ply | 690,360 colored 3D 点, 9.9 MB |
| **P2.7 cycle-consistency mean** | **L1 PSNR 11.78 vs L3 PSNR 8.65 → -3.13 dB**, L1 wins 7/7 cam on SSIM/MAE |
| **P2.11 Pi3 vs LiDAR overall** | **abs_rel 0.215, RMSE 7.70m, δ<1.25 65.3%, δ<1.25² 90.2%, δ<1.25³ 93.9%** (n=99015) |
| **P2.11 LiDAR sweep sync** | Δt = 9.8ms vs anchor (10Hz LiDAR ~50ms grid) |
| **P2.11 best cam** | ring_front_right: abs_rel 0.170, δ<1.25=91.7% (scene mean 7.05m) |
| **P2.11 worst cam** | ring_rear_left: abs_rel 0.296, δ<1.25=22.3% (scene mean 29.26m) |
| **P3.1 multi-anchor (10)** | 10 anchors × Pi3 7-cam: model load 167s (cold cache), per-anchor warm 1.23s, total 74s inference on A100 |
| **P3.1b LiDAR 10-anchor mean** | **abs_rel 0.202 ± 0.042, RMSE 5.27 ± 1.02m, δ<1.25 0.697 ± 0.142** (893k matched points total) |
| **P3.1b cycle 10-anchor mean** | **L1 PSNR 12.34 ± 1.31, L3 PSNR 9.19 ± 1.18, ΔPSNR -3.15 ± 0.72** (L3 loses 10/10) |
| **P3.1b best anchor** | 180: abs_rel 0.139, δ<1.25 0.866 (≈KITTI-tuned SOTA) |
| **P3.1b worst anchor** | 270: abs_rel 0.283, δ<1.25 0.412 |
| **P3.3 depth-bin bias** (anchor 0) | -12.8% (<5m) → -33.8% (>40m), 单调恶化 → Pi3 真有 depth-dependent 压缩 |
| **P3.3 depth-bin bias** (10-anchor mean) | -10.2% ± 11.2 (<5m) → -23.7% ± 6.8 (>40m), 单调模式 10/10 anchor 都成立, slope 结构性 |
| DVGT 尝试 | 8 次 (v1-v8), 全失败, 详见 §DVGT 失败原因 |

---

## DVGT 失败原因 (Phase 2 D1)

8 次尝试逐步深入:
- v1-v5: clone DVGT / submodule / deps / 公开 URL gate (cumulative blockers)
- v6: HF token 在 worker env 外 → `GatedRepoError 401`
- v7: HF auth OK (whoami JingShuo66), 但 DVGT 硬编码 `.pth` 文件名 HF repo 没有 (只有 `model.safetensors`) → `RemoteEntryNotFound 404`
- v8: 下 `model.safetensors` + 转 `.pth` → key naming 不兼容 (HF transformers 风格 `embeddings.cls_token` vs Meta 原生风格 `cls_token`, 几十层 ViT-L)

**需要修**: 写一层 HF↔Meta state_dict key remapper, 或 patch DVGT 跳过 dinov3 预加载。 均超出 D1 scope。

详见: `notes/backbone_decision.md`。

---

## Track 状态

| Track | 状态 | Branch | Next |
|---|---|---|---|
| **A — Main (AV2 spine)** | **active, P2.6 done (negative), P2.7 next** | `main` | Cycle-consistency 评估 |
| B — Waymo + diffusion fill | not activated | `parallel/waymo` | activates at Phase 2 完成 |
| C — DVGT vs Pi3 eval | **superseded** | — | 8 次 DVGT 尝试已纳入主线 D1, Track C 不再单独 spawn |
| D — OmniStitch baseline | not activated | `parallel/omnistitch` | activates at Phase 2 完成 |
| E — Lit watch | available anytime | `parallel/lit-watch` | user spawns when desired |
| F — Pantheon integration | not activated | `parallel/pantheon` | activates at Phase 3 end |

---

## 衍生产物 — `agent-colab-queue` v0.1.2

调试 Pi3/DVGT 时发现 `colab-mcp` 长任务不稳, 投入 ~5h 实现自研 **Drive-as-queue agent ↔ Colab 框架**:

- 仓库: https://github.com/QiPan-Ronnie/agent-colab-queue
- 架构: Agent → git push job spec → Colab worker git pull → bash 执行 → 结果写 Drive → Agent 读 Drive
- 关键修复 (v0.1.2): Windows subprocess + git 非交互模式 (`stdin=DEVNULL`, `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=Never`) — submit_job 从 200+s hang → 2-3s
- 验证: 3-shape stress test 7s 全过, 真实 MCP submit 5.07s exit=0
- tag `v0.4-acq-mcp-v012-robust`

**复用价值**: 后续 Pantheon360 / 360° diffusion 训练 / 任何长跑 Colab 任务都用它。

---

## 交付物

### 给 Koi 的 week-1 handoff
- 完整版: `deliverables/handoff_to_koi_2026-05-20.md` (14 sections, 含反思 / 时间线 / commit 索引)
- **精简版**: `deliverables/handoff_to_koi_2026-05-20_concise.md` (7 sections, 同 6 张图)
- PDF: `deliverables/handoff_to_koi_2026-05-20{,_concise}.pdf` (4.2 / 3.9 MB)
- 渲染器: `deliverables/_render_pdf.py` (pandoc + xelatex + Cambria/YaHei)
- 6 张图: `deliverables/images/` (spike_mosaic, l1_erp, l3 pc perspective+topdown, depth overlay, l1_vs_l3 hybrid)
- GitHub render: https://github.com/QiPan-Ronnie/Waymo2Panorama/blob/main/deliverables/handoff_to_koi_2026-05-20_concise.md

### Drive 工作区 (panq@usc.edu owns)
- AV2 原数据: `koi_waymo2pano_colab/data/argoverse2/val/02a00399-.../`
- L1 输出: `koi_waymo2pano_colab/outputs/l1/...` (含 .mp4)
- Pi3 7-view 输出: `koi_waymo2pano_colab/outputs/phase2/pi3_one_frame/`
- L3 .ply + depth: `koi_waymo2pano_colab/outputs/phase2/l3_pointcloud/`
- HF 模型缓存: `koi_waymo2pano_colab/hf_cache/` (Pi3X + DVGT-1 都缓存了)

### 关键 commit / tag
- `v0.1-l1-mvp` — L1 baseline 完成
- `v0.2-d1-resolved` — Pi3 backbone 选型完成
- `v0.4-acq-mcp-v012-robust` — agent-colab-queue 验证完成

---

## 已知问题

| ID | Issue | 状态 |
|---|---|---|
| W2P-001 | `colab-mcp` `open_colab_browser_connection` 行为 | **resolved (via agent-colab-queue 替代方案)** — 后续不再依赖 colab-mcp |

无新 active issue。

---

## 下周计划 (Tier 排序, P2.11 完成后更新)

| Tier | 任务 | 估时 |
|---|---|---|
| **1** | **多 sequence / 多 log 扩展** — 1 log × 10 anchors + 3 log × 各 5 anchors。 验证 L1/L3/Pi3-LiDAR metric 的 variance | 2-3 天 |
| **1** | **P2.12 depth-binned metrics** — 验证 Pi3 系统性低估是否 binning artifact, 分 5-10m/10-20m/20-40m/>40m 看 abs_rel | 半天 |
| 1 | **寻找 parallax-heavy frame** — 系统扫 frame, 找近物 + cam 重叠区, 给 L3 真正有机会的场景 | 1 天 |
| 2 | Phase 3 OmniStitch baseline (Track D) — 三方对比 L1 / OmniStitch / L3 | 2 天 |
| 2 | Argus / Percep360 diffusion polish — 填 ERP 上下黑边 + 接缝 | 2 天 |
| 2 | D8 paper angle 决定 — 看 Phase 3 数据 | 关键决策点 |
| 3 | 3DGS / proper raycast L3 ERP (Phase 4 候选) — 让 L3 视觉真正超 L1 | 1-2 周 |
| 4 | Pantheon360 集成 (Phase 4) + Waymo Track B 启动 | Phase 4 |

---

## Update log

| Date (UTC) | Update |
|---|---|
| 2026-05-20 23:31 | **Phase 3 W1 完成**: 10-anchor P3.1 + 双 batch (P3.1b lidar + cycle) on A100, 总 ~6min wall-clock。 Phase 2 所有 headline 数字 within 1σ。 Pi3 abs_rel 0.202±0.042, ΔPSNR -3.15±0.72 (L3 输 10/10)。 anchor 180 最佳 (KITTI SOTA-ish)。 `notes/phase3_multi_anchor_report.md`。 bug fix `aeaeb0a`: NaN-safe bars_png in cycle eval. |
| 2026-05-20 23:14 | **Phase 3 启动 + P3.3 完成 (CPU)**: depth-binned metrics 证实 Pi3 系统性低估**不是** P2.11 selection-bias 假说, 是真有 depth-dependent 压缩 — bias -12.8% (近场) → -33.8% (远场)。 `notes/phase3_progress_partial.md` + `scripts/phase3/`。 P3.1 multi-anchor Pi3 等 A100 (probe 显示当前是 CPU runtime)。 |
| 2026-05-20 22:51 | **P2.11 Pi3 vs LiDAR 完成**: 99k 匹配点, overall abs_rel 0.215, RMSE 7.70m, δ<1.25 65.3%。 关键发现 Pi3 系统性低估 ~25%, 近场 (<15m) δ<1.25≈0.9 (SOTA 级), 远场 (>20m) 跌到 0.22-0.58。 `notes/pi3_vs_lidar_report.md` + `scripts/phase2/eval_pi3_vs_lidar.py`。 Colab CPU 43.7s。 |
| 2026-05-20 09:01 | **P2.7 cycle-consistency 完成**: L1 mean PSNR 11.78 vs L3 8.65 → -3.13 dB, L3 量化也输给 L1。 写 `notes/l3_evaluation_report.md`, tag `v0.2-l3-mvp`, Phase 2 主线收官。 |
| 2026-05-20 08:45 | 给 Koi 的 week-1 handoff PDF 完成 (含图嵌入)。 完整版 + 精简版双输出。 `deliverables/_render_pdf.py` 自动化渲染脚本。 |
| 2026-05-20 07:35 | L3 `.ply` point cloud 导出脚本 + per-view depth maps。 690K colored 3D 点。 用户本地 Open3D 验证可视化 (`scripts/phase2/view_pointcloud.py`)。 |
| 2026-05-20 07:00-07:20 | L3 ERP 视觉迭代: raw → strict filter → soft blend hybrid → hard mask hybrid。 negative 结论: forward-splat 不优于 L1。 |
| 2026-05-20 06:55 | Phase 2 P2.3-P2.5 实现完成: `sim3_align.py` (Umeyama), `lift_and_project.py` (forward splat), `run_l3_one_frame.py` 跑通。 Sim(3) 残差 0.157m。 |
| 2026-05-20 05:25 | Phase 2 D1 — Pi3X 7-view forward 8.35s 一击命中。 |
| 2026-05-20 04:00-05:00 | Phase 2 D1 (DVGT 路线 v6-v8, 含 HF token 重试): 即使有 dinov3 access, HF safetensors 用 transformers-style keys 与 DVGT 原生 schema 不兼容, load_state_dict 满屏 unexpected keys。 验证 D1 结论: Pi3 胜。 |
| 2026-05-19 22:43 | Phase 2 D1 初版决议 (`v0.2-d1-resolved`): Pi3 by walkover, DVGT 操作性差 (5 次失败)。 后续 user 拿到 HF dinov3 access 后又试了 3 次, 加固决议。 |
| 2026-05-19 21:00-22:00 | agent-colab-queue v0.1.2 final fix (Windows subprocess + git tty 根因), 3-shape stress test 通过, tag `v0.4-acq-mcp-v012-robust`。 |
| 2026-05-18-19 | agent-colab-queue v0.1.0-0.1.1 开发 (Drive-as-queue 框架 + MCP server)。 |
| 2026-05-17 | Phase 1 L1 baseline 完成: sphere projection + multi-band blending + ERP wrap fix。 发现 + 修复 mirror bug (commit `885b5da`)。 跑出 5-10s `.mp4`。 tag `v0.1-l1-mvp`。 |
| 2026-05-16 | Phase 0.5 Spike GO ✅ — AV2 API 验证, 22.49ms 同步, 2×4 mosaic。 plan v2 (Waymo → Track B, Phase 0.5 inserted, D1/D8 deferred, parallel-tracks §14)。 |
| 2026-05-15 | Repo + brainstorm + plan v0/v1。 |
