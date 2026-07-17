# DB-146 evidence-gated spectral inverse

## 结论

**DONE — KILL / NOT PRODUCTION。**

DB-146 成功实现了一个不会伤害 A 基线的训练内证据门，但没有证明
sensor-native inverse 能在任何开发集 dry/high 地面块上稳定保留正增益。
最终 6 个开发块和 1 个未见 log 的 2 个块全部在读取 outer held-out 前选择
回退 A。安全门通过，增益门失败，因此不得接入 db89、db144 或 v15。

这不是“代码没跑通”。相反，它把 DB-145 的表面正收益进一步拆开后发现：
同一候选频带可在两个结构化 inner folds 改善，却在另一个完整相机/连续时间折
大幅退化。以 dry-straight/high 的 `lp1` 为例，中位 inner gain 为正，但最坏折
robust MAE / median L2 分别退化约 54.0% / 54.5%。这说明被恢复的细节不是
跨视角稳定的地面真值，而是视角权重、混叠相位或观测模型失配的一部分。

## 冻结协议

- 开发集沿用 DB-145 r3 的 3 logs × high/low 共 6 个冻结 patch。
- outer-training 只按完整相机或连续时间块做 3-fold 内验证；outer held-out
  在 `INNER_FROZEN` 的 SHA256 写盘前不可读取。
- 固定候选为 `A + LPσ(B-A)`，`σ=8/4/2/1/0 cell`。
- 任一 inner fold 的 robust MAE 或 median RGB L2 退化即否决；同时检查
  cross-fold correction agreement 和 checker/Nyquist 能量。
- 每个 fit/evaluation operator 最多使用 60,000 个真实像素。预算在 source
  views 间水位均分，再在每个 source 的图像平面均匀取样；选择完全不读取 RGB
  值或重建结果。这既限制 EWA 图的资源峰值，也防止单个近距离高像素视角支配
  目标函数。
- 泛化 log `02678d04-cc9f-3148-9f95-1ba66347dff9` 未参与规则开发；其
  high/low patch 和 outer split 只按几何自动冻结。

实验冻结代码提交为 `046564c`。L4 隧道在实验期间返回持续 HTTP 530 后，
按用户授权切换到本机 RTX 5070 Ti Laptop 12GB；算法和阈值未因硬件改变。

## 最终结果

正数表示 B 相对 A 的 outer held-out 改善；D 是门控后的最终输出。

| patch | inner 选择 | B robust | B median L2 | D vs A |
|---|---:|---:|---:|---:|
| dry-straight/high | A | +8.41% | +14.59% | 0.00% |
| dry-straight/low | A | +10.27% | +9.57% | 0.00% |
| dry-turn/high | A | -47.44% | -145.25% | 0.00% |
| dry-turn/low | A | +1.66% | +1.39% | 0.00% |
| wet-or-specular/high | A | -9.10% | -1.34% | 0.00% |
| wet-or-specular/low | A | +10.95% | +9.30% | 0.00% |
| unseen-dry/high | A | -8.33% | -11.33% | 0.00% |
| unseen-dry/low | A | -4.75% | -3.47% | 0.00% |

开发集和未见集的所有 D 都满足相对 A 的 1% 非退化门。全分辨率聚合板已人工
检查：D 与 A 一致，无新增棋盘格、彩边或 moiré；B 在 dry-turn、wet/low 和
unseen/low 中存在明显棋盘格、拉丝或结构漂移。视觉门通过，效用门失败。

## 产物索引

- `final_aggregate/verdict_metrics.json`：8 块自动门、outer metrics 和 SHA 链。
- `final_aggregate/verdict_board.png`：8 块 A/B/D、safe mask、uncertainty、
  held-out render/error 的总览。
- `development_evidence/final_046564c/`：6 个开发块的完整
  `inner_decision.json`、`INNER_FROZEN`、`metrics.json`、`COMPLETE` 和图像证据。
- `unseen_manifest.json`：未见 log 的几何冻结清单。
- `unseen_evidence/final_02678d_046564c/`：未见 high/low 两块完整证据。
- `verdict.json`：最终人工签署判决。

保留结论：v15 的 A（真实多帧中值/world-BEV + 诚实黑）仍是生产答案。
若未来继续研究，必须换一个能解释跨相机 photometric inconsistency / 几何
微偏差的新观测模型并另立 brief；不能继续放宽本门、挑 outer 赢的 patch，
也不能把生成先验伪装成真实恢复。
