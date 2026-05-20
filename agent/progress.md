# Waymo2Panorama Progress

> **Latest: 2026-05-20 ~09:01 UTC** — **Phase 2 主线全部完成 (tag `v0.2-l3-mvp`)**。
> Phase 1 (L1) ✅ · Phase 2 D1 (Pi3 vs DVGT → **Pi3 胜**) ✅ · Phase 2 P2.3-P2.5 (Sim3 + lift_and_project + .ply) ✅ · Phase 2 P2.6 (L1 vs L3 视觉) ⚠️ negative · **Phase 2 P2.7 (cycle-consistency 量化) ✅ — L3 mean PSNR 8.65 vs L1 11.78, ΔPSNR = -3.13 dB → L3 forward-splat 量化也输**。 P2.9 evaluation report + P2.10 tag 完成。 **下周: Pi3 vs LiDAR GT depth + 多 sequence 扩展 + 寻找 parallax-heavy frame**。

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
| 3 | OmniStitch baseline + 多 sequence + diffusion polish + D8 paper 角度 | ⏸️ 未启动 |
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

## 下周计划 (Tier 排序, P2.7 完成后更新)

| Tier | 任务 | 估时 |
|---|---|---|
| **1** | **Pi3 vs LiDAR GT depth 对比** — AV2 自带 LiDAR, 投到 cam 算 absolute relative error。 给 Pi3 几何精度一个 ground-truth-anchored 数字 | 1 天 |
| **1** | **多 sequence 扩展** — 现在只 1 log × 1 anchor, 扩到 1 log × 10 anchors (一段 5s) + 3 log 共 30 anchors。 验证 L1/L3 metric 的 frame-to-frame variance | 2-3 天 |
| **1** | **寻找 parallax-heavy frame** — 系统扫 frame, 找近物 + cam 重叠区, 给 L3 真正有机会的场景 | 1 天 |
| 2 | Phase 3 OmniStitch baseline (Track D) — 三方对比 L1 / OmniStitch / L3 | 2 天 |
| 2 | Argus / Percep360 diffusion polish — 填 ERP 上下黑边 + 接缝 | 2 天 |
| 2 | D8 paper angle 决定 — 看 Phase 3 数据 | 关键决策点 |
| 3 | 3DGS / proper raycast L3 ERP (Phase 4 候选) — 让 L3 视觉真正超 L1 | 1-2 周 |
| 4 | Pantheon360 集成 (Phase 4) + Waymo Track B 启动 | Phase 4 |

---

## Update log

| Date (UTC) | Update |
|---|---|
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
