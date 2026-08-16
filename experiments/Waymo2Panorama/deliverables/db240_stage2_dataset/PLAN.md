# DB-240 — stage-2 训练数据集（多数据集 · 直接接缝 mask · 93 帧同质）

立项 2026-08-14，来源：当日 BOSCH 会议（转写稿见
`meeting/8.14_meeting with BOSCH/recording/*.docx`，本地副本
`scratchpad/meeting_0814_transcript.txt`）。上游 DB-239（B-route 与 mask 裁决），
下游 = Louison 的 stage-2 微调。

## 1. 会议裁决（数据契约的重大简化）

| 项目 | v15 现状 | 08-14 新规 | 出处 |
|---|---|---|---|
| 帧结构 | 1+92（首帧补成完整 360） | **93 帧同质**，全部 band-only，破洞不补 | koi 00:42:04「你现在 93 个直接全部用就好了。因为 Louison 的 stage-1 已经练成了，他已经可以吃这种破洞，完全不用补」 |
| 地面 | worldMap / MFSR v10 填充 | **不处理** | 00:42:16 景硕「那我地面都不用管了？」koi「对，这样直接 93 个」 |
| 首帧天空/地面补全 | FLUX | **不处理**（含在"不用补"内） | 同上 |
| 接缝 | DB-239 三档待定 | **直接 mask**（koi 当场批准） | 00:36:01「我觉得可以去用这个，这样就好」 |
| 车头 | 一律挖掉 | **两版本，按场景 50/50 分配；一个场景只给一种** | 00:44:13–00:45:28 |
| 反投影 | 有 | **明确不做** | 00:43:35「先不要有任何反投影」 |
| 数据源 | 仅 AV2 | **AV2 + Waymo Open + Waymo E2E + PandaSet(待验)**；nuScenes 出局 | 00:48:55「不够 93 帧的就先不用」 |
| 交付物 | A/B 双 mask | **RGB 视频 + 对应黑白 mask**（一一对应） | 00:38:09 |

**同时解决了 DB-239 挂起的问题**：koi 00:38:14 当场指示 Louison
「你算 loss 的时候，要完全只算景硕给你 mask 的地方，绝对不要算那些黑色」——
逐帧 mask 乘进 loss 的契约由 koi 亲自确认，不再需要我们单独去问。

## 2. 任务分解（景硕）

### A. VSR 收尾（小，已近完成）
- A1 结论：FlashVSR 最好（koi 与周老师一致）；其余普遍一般，部分产生形变/伪纹理。
- A2 做一页 summary slide（第 10 页）：模型性能排名 + 每个模型贴一张 example crop 对比。
- A3 结果文件夹共享给全组（会末已在群里放链接并确认可打开）。

### B. 数据集（主线）
- B1 **重做生成规格**：93 帧同质、不补地面/天空、接缝直接 mask、无反投影。
- B2 **车头两版**：(a) 车头黑 mask，(b) 车头保留原样。按场景 50/50 分配，
  **同一场景不得同时给两种 mask**（koi：会让模型看两次同一件事，浪费算力）。
- B3 **四个数据源各抽 ~500**（总量约 2000，与 stage-1 同量级）。
- B4 **留一个整数据集作 OOD**：取样本量最小的那个，完全不打包给 Louison。
- B5 **其余数据集内部再分 train/test**（7:3 或 8:2，比例自定）。
- B6 **README 写清**：哪些包给了 Louison、哪些留下、划分依据。
- B7 **每个数据集先发 1 个样本到群里给 koi 眼验**，通过后再整包交付。
- B8 **原始 7 个 independent perspective view** 也打包（或以文件夹编号可追溯），
  供 Louison 做 1×3 对比图时切出 GT。

### C. 下游（Louison，需景硕配合的接口）
- C1 从现有 2000-step checkpoint **继承训练**，不得从零（koi：从零 100% 打烂）。
- C2 loss 只在 mask 白区计算。
- C3 出图 1×3：GT | stage-1 | stage-2 微调后；训练集内与 OOD 各做一遍；
  先看满版 360，再看切出的 perspective。

## 3. 风险与待验证

1. **PandaSet 帧数存疑（最高优先级）**：DB-179（07-29）记录 PandaSet 为
   103 scenes × 8s @10Hz = **80 帧 < 93**，与会上「PandaSet 应该是够的」冲突。
   若确认不足，按 koi 的同一条规则（不够 93 帧不用）应出局 →
   只剩 3 个数据源，再扣掉 1 个 OOD holdout，**训练集只剩 2 个源**，
   需要回头和 koi 确认是否接受，或对 PandaSet 做降帧率适配。
2. **最小数据集 = OOD holdout**：需先统计四个源的实际可用样本量再定；
   若 PandaSet 出局，holdout 的选择要重新排。
3. **跨数据集 mask 语义对齐**：AV2 之外三个源的 mask 生成路径与阈值须与 AV2 同口径，
   否则 stage-2 会学到"不同数据集黑得不一样"。
4. **基建可用性**：`agent/db181_multids/` 已有四个 adapter
   （waymo_e2e / waymo_perception / pandaset / nuscenes）且各自有 pilot 记录
   （DB-216/220 E2E、DB-217 nuScenes、DB-225 PandaSet），
   本次是在已验证 adapter 上扩量，不是从零适配。

## 4. Gates

- G1 四源样本量统计 + PandaSet 帧数核实 → 定 holdout 与配额（**先于任何量产**）。
- G2 每源 1 个样本过 koi 眼验。
- G3 整包交付前 README 完整（packaged / held-out / 划分依据 / 车头版本比例）。

## 5. 时间

会上估计：数据打包尽快 → Louison 训练 3–4 天、一周内稳妥 → 出结果后排 baseline 与实验。
投稿目标：ICLR（约 9 月底）为主，CVPR（约 11 月）为底线；等 stage-2 结果再定。
