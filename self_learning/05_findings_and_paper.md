# Chapter 05 — 核心发现 + Paper 角度 + 研究方法论

---

## 5.1 三个反直觉发现 (paper 主要 contribution)

### Finding 1: 经典球面 baseline 比 SOTA 神经网络 3D-lift 强 3 dB

**事实**: L1 (1983 年的 Burt & Adelson multiband blending + 球面投影) cycle-PSNR = 12.34 dB. L3 (Pi3 CVPR 2025 SOTA 3D foundation model forward-splat) cycle-PSNR = 9.19 dB. **L1 赢 3.15 dB**, 10/10 anchor 都赢.

**为啥反直觉**: 大家以为深度学习 SOTA 模型应该秒杀经典方法.

**为啥发生**:
- L1 不假设深度 → 球面投影只用 ray direction → 远场 (>50m) 视差小, 几何精确, **远场占像素 80%+**
- L3 假设每像素都有深度 → depth 错就 splat 到错位置 → 远场 24% depth bias × 大像素覆盖 = 灾难
- AV ring cam 几何特殊 — 7 cam 大多共点旋转, 视差小, 不利于 dense depth-based 方法

**Paper 含义**:
- Section 4 "Why simple wins": 反直觉但稳定结果
- 让 reviewer 知道我们不是在试图 oversell 新方法, 是 honestly 报告 baseline strength

---

### Finding 2: "换 backbone 也救不了 L3"

**事实**: 同样的 L3 forward-splat pipeline, 换 4 个不同的深度 backbone:
- Pi3 (yyfz CVPR 2025): -3.15 dB vs L1
- Apple Depth Pro (CVPR 2024 SOTA monocular): 2.84× worse abs_rel
- 多帧 Temporal Pi3 K=3 (我们自创): 反而比单帧差
- OmniStitch (ACM MM 2024 published method): -6.67 dB vs L1, 输 7/7 cams

**为啥反直觉**: 大家以为换更好的 backbone 就能让 L3 反超 L1.

**为啥发生**:
- 问题不在"哪个 backbone 估深度更准"
- 问题在 **forward-splat 算法类本身** 在 AV ring 上 brittle
- 远场视差小 → 任何 backbone 远场都不准 → splat 必然鬼影
- Occlusion / 稀疏 / Z-buffer 缺失 → 算法类内禀问题

**Paper 含义**:
- Section 4 "Algorithm-class failure": **加固论据**
- 4 个独立 backbone 同方向失败 = strong evidence, 不是 cherry-pick
- 让 reviewer 难以反驳 "你换个 backbone 试试"

---

### Finding 3: 几何 + 物理先验 在 AV 域比 ML 重要

**事实**:
- IPM (路面 z=0 物理先验, 几十行数学公式): ground-only +0.20 dB on T14, +0.20 dB ground in 新-C 多区域
- HDR 跨 cam 补偿 (LS 解 6 参数, 几十行代码): -18% luminance gap
- 都比"用更大模型估更准的深度" 稳

**为啥反直觉**: 大家相信"end-to-end 神经网络解决一切".

**为啥发生**:
- AV 场景物理结构强 (路面平 / 建筑垂直 / 颜色一致) — 这些先验**100% 正确**
- 神经网络只能学到训练分布里的统计先验, 对 unseen distribution 鲁棒性差
- 利用 deterministic 物理先验比让 ML 重新学一遍稳

**Paper 含义**:
- Section 5 "Physical prior contributions": IPM 多区域 / HDR 是 paper 正面 contribution
- Section 7 "Discussion": 暗示 AV ML training data distribution 有问题, 未来工作方向

---

## 5.2 Paper 角度决策 (G3 v6 gate)

我们的 paper 角度候选有 4 个 (按"成色"排):

### Angle A — Method paper (strong wins) 【超 stretch goal, 不太可能】
**条件**: 任一新方法 ΔPSNR > +1.0 dB on full 10 anchor (not cherry-pick)
**Pitch**: "Multi-prior hybrid stitching for AV → 360°"
**Venue**: 3DV 2026 main track
**信心**: 低 — 我们最强方法新-C 也只 +0.20 dB on ground, 远没 +1.0 dB full

### Angle A' — Method paper (modest wins) 【**主推, current default**】
**条件**: 任一方法 ΔPSNR > +0.5 dB (cherry-pick OK) OR 多个 stack-able 小 contribution
**Pitch**: "Multi-prior hybrid stitching for AV ring → 360° (with negative-result analysis of why prior 3D-lift fails)"
**3 stack-able positives**: IPM ground +0.20 dB / HDR -18% color gap / Graph-cut visual win
**4 NEG**: L3 / OmniStitch / Depth Pro / Temporal Pi3
**Venue**: 3DV 2026 main track (~Aug 2026 ddl), upgrade CVPR 2027 (~Nov ddl) if VGGT lands
**信心**: 中高 — 现有数据已经支撑

### Angle B-with-C — Hybrid (现 prelim, 保守 fallback)
**条件**: 新方法弱, 但分析强
**Pitch**: "Hybrid 2D/3D pipeline + characterization of 5 failure modes"
**Venue**: 3DV 2026 D&B track (Datasets & Benchmarks)
**信心**: 高 — 即使 Koi 觉得正向不够, 也能 ship

### Angle C — Negative-only (最 conservative)
**条件**: 所有新方法都 NEG
**Pitch**: "Why all naive AV → 360° stitching methods fail"
**Venue**: NeurIPS 2026 D&B / CVPR workshop
**信心**: 高 — 但 paper value 最低 (D&B 比 main track 影响力小)

### 当前决策
**Default: A' Method paper**. 等 Koi 看完 PDF 反馈再 lock G3 v6 gate.

---

## 5.3 NEG (Negative result) 在研究里的角色

**误区**: 学生常以为"我没做出 SOTA, 所以 paper 没价值".

**真相**: 高质量 NEG 是 paper 重要组成部分, 尤其在:
- **已建领域** (e.g., AV ML) — 大家想知道**哪些 reasonable 方向走不通**
- **方法学 contribution** — 看你怎么测 NEG (是否严谨, 是否 cross-metric)
- **未来工作 motivation** — 把 NEG 写成"为啥需要 X 而不是 Y"

**写好 NEG 的 3 个要求**:

1. **Apples-to-apples 比较**: 我们 cycle-PSNR 在同一 10 anchor 上, 同一评测协议, 跨 4 个 backbone. 不能是"你这次跑 5 anchor, 我跑 10 anchor" 这种比较.

2. **Multiple datapoint**: 单 anchor / 单方法 NEG 不够 (可能是巧合). 我们用 **10 anchors × 4 backbones** 多 datapoint reinforce.

3. **Mechanism 解释**: 不只是"X 输给 Y", 而是"X 输给 Y 是因为 mechanism Z". 我们的 mechanism: 远场视差小 + Pi3 -24% bias + forward-splat 无 z-buffer + AV ring 共点几何.

**reviewer-proof NEG**: 我们做了 T5 metric audit (LPIPS / MS-SSIM / object-band PSNR) 跨 metric 验证 L3 NEG. 这样 reviewer 不能说"你选错了 metric".

---

## 5.4 项目方法论 — 给未来研究的 lessons

### Lesson 1: Baseline 先建好再做新方法

**我们做法**: Phase 1 全力建 L1 baseline. Phase 2 才上 Pi3.
**避免**: 先做"高级方法" → 没 baseline 对照 → 不知道是 baseline 太强还是新方法太弱.

### Lesson 2: Evaluation 协议先定再开跑

**我们做法**: Phase 1 末就定了 cycle-PSNR 协议. 之后所有方法用同样协议.
**避免**: 每个方法用不同 metric → 没法横向比.

### Lesson 3: Metric Audit 拦截 reviewer 质疑

**我们做法**: T5 metric audit 跨 PSNR / LPIPS / MS-SSIM / object-band 验证 L3 NEG metric-robust.
**避免**: 单 metric 报告 → reviewer 说"你 cherry-pick metric".

### Lesson 4: 时间盒 + Risk Register

**我们做法**: 每个 track 估时 + risk register 写下 "if X fails, fallback Y". Plan v6.1 17 tracks 每个都有 exit condition.
**避免**: 在"高 risk 高 upside" 上无限投入 → 一个失败拖死整个 project.

### Lesson 5: 严格的工程纪律 (防御教训)

**8 个 防御教训** (handoff.md 里):
1. `set -e -o pipefail` + `cmd | head` = SIGPIPE 死
2. `conda activate` + `set -u` = unbound var 死
3. HF gated repo 即使有 token 也要 click access (新-F VGGT 实测)
4. Worker sorts jobs **alphabetically** 不是 created_at
5. Tar conda env to Drive 救命 (Colab disconnect 后 5 min 恢复 vs 50 min 重装)
6. 不能远程关 Colab runtime (用户必须 manual disconnect)
7. Pi3 vs VGGT input pipeline 差异 (tensor vs file path)
8. eval_cycle 用 Sim(3) 对齐 (VGGT / Depth Pro 要给 pose=T_ego_cam 让 Sim(3) collapse to identity)

每个 lesson 都是浪费 1+ 小时 + 一些钱学到的 — 写下来给未来的自己 (和 agent).

### Lesson 6: Single Source of Truth

**我们做法**: `agent/progress.md` 每个 track 完成时 append 4 行 (怎么做 / 结果 / Deliverables / Status / Next). 永远是最新状态.
**避免**: 多个 PROGRESS_X.md 散落 → 状态不一致 → agent 接手时分不清最新版.

---

## 5.5 给老板的 30 秒 elevator pitch (背下来)

> "我做了一个**自动驾驶 7 相机 → 360° 全景拼接**的 paper. 反直觉发现: 经典球面投影 baseline (1983 年的方法) 比 SOTA 神经网络 3D-lift (Pi3 CVPR 2025) 高 3 dB; 换 4 个不同 backbone (Pi3 / Depth Pro / Temporal Pi3 / OmniStitch) 都救不了. 物理先验 (IPM 路面 / HDR 跨 cam 颜色) 比 ML 稳. 投 3DV 2026 main track, A' Method paper 角度."

---

## 5.6 接下来 (post-handoff)

### 短期 (1-2 周内)
1. **等 Koi 反馈** (异步, 我已交付 PDF)
2. **G3 v6 决策**: 锁定 paper 角度 (default A')
3. **开始 paper draft v0**: 用 T7 v1 subagent or 自己写

### 中期 (paper draft 期)
4. **可选: 新-F VGGT 加固** (~1 天 A100, 若 HF access 申请下来)
5. **可选: 新-D Option B reweight L1** (若 paper 需要更强 sparse stereo 论据)
6. **T1 multi-log full eval** (~1 天 GPU, 5 logs × 10 anchors)

### 长期 (Phase 4+, paper 投完之后)
7. **T13 self-sup Pi3 finetune** (5-6 天 A100, 风险高, 仅 paper 写完后做)
8. **CylinderSplat / Street-Gaussian** (per-scene 训练, 论文角度 future work)
9. **Pantheon360 / GEN3C / Cosmos** 下游集成 (v6.1 pivot 之后 paused)

---

## 5.7 最终自检 — 读完 self_learning/ 后你应该能

- [ ] 30 秒讲清楚 project (任务 / 评测 / 主发现)
- [ ] 说清楚 8 条路线名字 + 一句话方法 + verdict + 关键数字
- [ ] 列出 3 个核心发现 + 为啥反直觉
- [ ] 说明 5 个 NEG datapoint + 为啥构成 strong evidence
- [ ] 选 1 条路线 (e.g., 新-C IPM 多区域), 描述算法步骤 + 用到的 CV 概念
- [ ] 答 reviewer 问题 (见 `meeting_cram.md` §⑦)
- [ ] 理解为啥默认推 Angle A' (Method paper modest wins)

**做到了 → 你已经懂这个 project 了, 可以跟人深聊**.
**做不到 → 回头看相应章节, 第二遍读会快 3 倍**.

---

## 5.8 配合 deliverables/ 用

| 你想做啥 | 看哪个 |
|---|---|
| 跟同学讲 5 分钟 | `meeting_cram.md` |
| 系统学 CV (外部资源) | `learning_plan.md` |
| 给 Koi 报告 | `handoff_to_koi_w2_2026-05-21_v6cpu_done.pdf` |
| 给未来 agent 交接 | `agent/handoff.md` |
| 看进度时间线 | `agent/progress.md` |
| **彻底搞懂项目** | **本目录 self_learning/ 5 章** |

---

恭喜读完 5 章 self_learning. **你现在应该比 90% 见过这个 project 一面的人懂得多**. 接下来去打开任意一个 code file 读 30 分钟, 你会发现都看得懂了.

— 写于 2026-05-21
