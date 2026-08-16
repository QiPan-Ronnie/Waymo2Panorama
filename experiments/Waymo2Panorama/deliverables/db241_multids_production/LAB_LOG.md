# DB-241 实验日志

## 2026-08-16 — 根基抢救 + 四源移植

### 起点：koi 认可的配方不在任何 commit 里

用户问「生产这个版本的代码是否有留存」。查下来：`clip_broute_rulemask.mp4`（8/14 03:02
本地）的产出代码是一段 **heredoc，从未落盘**；全仓库搜 `rulemask` 只有三处引用，
没有一处是生成器。从会话 transcript 第 2588 行（`2026-08-14T10:01:29Z`）逐字恢复。

**恢复过程发现在跑的产线和 koi 认可的不是同一个东西**：

| | koi 认可版 | 当时产线 |
|---|---|---|
| wedge | 93 帧每 4 帧取并集（24 帧） | 只取第 0 帧 |
| 条宽 | 58/60/61/62/63/68/68 | 58/59/60/60/61/68/68 |
| 实测矛盾漏网 | 0 | **5 px** |

原脚本注释写明原因：*"support boundaries wobble a few px"*。修正后逐位一致。

### 建 golden test 并做突变验证

fixture 217 KB（标定 + ego pose + 93 帧时间戳 + 两张参照栅格），**不含任何图像、不需 LiDAR**
——因为规则 mask 只是标定+pose+时间戳的函数，这也正是它能移植到无 LiDAR 的 E2E 的原因。

突变验证：把产线退回单帧 wedge → 四条断言同时触发（条宽变窄、2388 px 不同、
5 px 漏网、面积 0.2148→0.2119）。**没验证过会失败的 golden test 等于没有。**

### 端到端比对暴露 koi 那一包里的真缺陷

重跑整个 93 帧样本与 koi 收到的对比：

- RGB **0 差异 / 5.85 亿像素**（逐字节相同）
- rule_mask **0 px 差异**
- mask 通道 **0.017% 差异** ← 查下去是真缺陷

`camera_support_emc` 说"这条光线应该落在图内"，`_project` 说"实际落没落"，
两者在支持域边界**每帧约 330 px 不一致**。那些像素留黑，`domain` 仍认领 →
mask 把洞标成 KEEP。修法：`keep = written & ~kill`。

| | 假 KEEP | 丢弃真实像素 |
|---|---|---|
| koi 收到的那一包 | 33,413 | 8,310 |
| 修复前产线 | 22,947 | 0 |
| **修复后** | **0** | **0** |

### 移植挖出四个静默失败

1. `present_cameras` 拿 AV2 七相机表过滤 → nuScenes 的 `ring_rear` **被静默丢掉**，
   会产生"5 相机 + 假闭合环 + 在未渲染的车后画接缝带"
2. `load_calibration` 硬要求七相机 → 正确的 log 直接崩
3. `manifest_from_dir` 同上
4. `manifest_from_dir` **强制要求 LiDAR** → 无 LiDAR 的 Waymo E2E 完全无法产出

### 我自己的 gate 写错了一次（v15 早就警告过）

I3 gate 最初检查「KEEP 像素的颜色是否为黑」，导致 10 个 nuScenes 场景被否掉 4 个 ——
其中 scene-1077 是**夜景**（带内平均亮度 28–35），3239 个黑像素是真实暗场景内容。

v15 README 原话：*"do not rely on black pixels alone to signal missing — black is
a valid image colour. The mask channel is what disambiguates."* 我犯的正是这个错。
真不变量是 `KEEP ⊆ written`，改测这个后 10/10 全过。

### 各源实测结果

| 数据集 | 相机 | 环闭合 | 接缝带 px | mask % | 带内真实内容 |
|---|---|---|---|---|---|
| Argoverse 2 | 7 | ✓ | 58–71 | 21.5–22.6% | 70% |
| nuScenes | 6 | ✓ | 35–84 | 17.2–18.4% | 59% |
| Waymo Percep | 5 | **✗** | 34–43 | 7.9% | **27%** |

**nuScenes 帧数实证**：本地 10/10 场景 216–240 帧，全部 ≥93 ——
8.14 会上"nuScenes 不够 93 帧"被真实数据彻底证伪。

**Waymo Perception 5 相机 → 4 条接缝带**（闭合对被正确丢弃，没在 158° 车后空洞上
画假接缝）。但带内只有 27% 真实内容，比先前 48% 的估计严重得多，**须报 koi**。

### E2E 的 230 GB 问题与解法

WOD-E2E val split 全局洗牌：一段 ~200 帧散在全部 93 个分片里，朴素重建要拉 230 GB。

tfrecord 记录连续且自带长度 → 走文件只需每条记录前 ~6 KB 读出长度和 context：

- 索引全 split：**~430 MB**（93 × 1150 × 6 KB）
- 之后每个样本：**~190 MB**（只取属于它的记录）

实测分片 0：1150 条记录、读 7.1 MB、425 个不同 segment。比全下载省两个数量级。

## 待办

- E2E 索引完成后选窗产样
- 四源各扩到目标量，按 koi 的 8:2 划分 + 最小源整体 OOD
- **B7 眼验门**：每源 1 个样本发群里给 koi，附 Waymo Perception 27% 密度说明
- 向 Louison 确认 stage-2 的 loss 是否已乘 mask（koi 0814:249 用了"这一次"，
  说明是改动；stage-1 现有极性相反，无证据表明已改）
