# 自学专属讲解 — Waymo2Panorama 项目从头到尾

**写给**: 我 (Qi), CV 基础不太好, 需要傻瓜友好的项目讲解
**对应工作**: 2026-05-15 到 2026-05-21 这两周做的所有事
**风格**: 每个 CV 概念都用 1-2 句话先解释 "这是干啥的", 再讲我们用它做了什么

---

## 阅读顺序 (建议线性读)

| 章节 | 内容 | 估时 | 看完能做啥 |
|---|---|---|---|
| **[01_project_overview.md](01_project_overview.md)** | 项目要解决啥, 为啥难, 怎么评测 | 20 分钟 | 30 秒讲清楚项目 |
| **[02_cv_foundations.md](02_cv_foundations.md)** | 所有用到的 CV 概念, 各 1-2 句话 | 60 分钟 | 不被 jargon 卡住 |
| **[03_methods_walkthrough.md](03_methods_walkthrough.md)** | 8 条拼接路线深度讲解 | 90 分钟 | 跟老师讨论方法不卡 |
| **[04_external_baselines.md](04_external_baselines.md)** | 3 个外部 NEG baseline (OmniStitch / Depth Pro / Temporal Pi3) | 30 分钟 | 答 reviewer "你跟谁比过" |
| **[05_findings_and_paper.md](05_findings_and_paper.md)** | 核心发现 + paper 角度决策 + 研究方法论 | 30 分钟 | 懂为啥这个 paper 能发 |

**总计**: 约 3-4 小时读完, 之后再回去看代码就完全不卡了.

---

## 怎么用这个 self_learning/

**Mode A — 系统学习**: 按 01 → 05 顺序读, 边读边看对应代码文件 (每章都指明).

**Mode B — 问题驱动**: 跟人讨论时遇到不会的概念 → 跳到 02 找那个概念 → 30 秒补上.

**Mode C — 复习用**: 已经懂了, 但要给别人讲 → 看 01 和 03 复习核心数字 + 故事线.

---

## 配合其他材料

| 材料 | 用途 |
|---|---|
| `../deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.pdf` | 给 Koi 的 final, 13 页 11 图 |
| `../deliverables/meeting_cram.md` | 5 分钟会议讲稿 |
| `../deliverables/learning_plan.md` | 系统 CV 学习路径 (含外部资源) |
| `../agent/handoff.md` | 给未来 agent 的接手文档 |
| `../agent/progress.md` | 每条 track 完成时间线 |

---

## ⚠️ 重要心态

1. **不要怕"我数学不够"** — 我们用到的数学 95% 是矩阵乘 + 几何投影, 看具体代码比看公式快.
2. **代码先, 概念后** — 永远先打开对应代码看看, 再读概念解释.
3. **不懂就跳过, 别死磕** — 学完一遍后第二遍读就懂了, 强行死磕第一遍效率低.
4. **写自己的总结** — 每章读完用自己的话写 200 字总结, 写不出说明没懂.
