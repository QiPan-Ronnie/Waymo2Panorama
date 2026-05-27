现在是这样的, 让我们再来更新一下进度和后续的计划

那天我和我的队友进行了开会, 我们后续整个项目要和BOSCH进行合作 并且每周要有推进.


## 队友的问题
现在开完会的进度是这样的, 我的队友的进度在这个ppt @"D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\meeting\5.22_meeting with xihan\Bosch 2026_5_22.pptx". 他主要进行的是Waymo的数据集的拼接, 然后他的问题是这样的. 你看他的ppt的第6张的 右上角的那辆车,出现了严重的色差: 就是左半边是黑的 右半边是正常的
![[assets/新建 Markdown/file-20260525031818418.png]]
   就是我现在给你的图, 他觉得可能是数据集原本  摄像头可能在阴影处导致的这个问题. 这样如果我们想用diffusion 补全整个panorama的时候 diffusion 学习可能会有问题. 现在他想解决这个色差问题, 但是我不知道我们的8个中是否有这个问题 还是说是waymo数据集的问题不是AV2数据集的问题, 可能需要拿我们的最好的方法来在这上面做一下, 进行一下对照试验. 他的问题好像是解決Waymo色階，用blending的色階把有陰影的camera picture brighten

另外他的问题还有一个是你看这个ppt的第9页. 这个是他用了推进的ORB feature point detection, 想解决一些问题具体什么问题我忘了, 但是拼接出现了很严重的问题, 就是
   ![[assets/新建 Markdown/file-20260525032019931.png]]
   你能看到中间的两个其实拼接的不错, 接缝拼接起来了, 但是左边和右边出现了严重的变形



## 公司的事宜
然后还有一些公司的事宜是他想和我对接一下, 我们做这个拼接是想干啥? 其实就是BOSCH公司那边有个自驾world model, 但是介于现在输入的图片质量不太行, 但是他们测试了输入panorama的图片数据不错, 所以想试试panorama的, 但是介于panorama的数据集太少了, 而且找人收集不现实, 所以就准备想试试如何能更好的得到panorama的数据集, 所以我们要干的就是这个


## 我的进度目前的问题

### 1. 

其实他们觉得我们目前的方法1不错, 拼接的很好, 比如说
![[../../deliverables/images/l1_erp.png]]

这张图其实拼接的不错
但是有个问题是这样的: AV2的数据集本身数据图片之间有一定程度的overlap, 所以现在其中有个overlap的点是![[assets/新建 Markdown/file-20260525033032701.png]]
这辆车你会发现有2个轮子了哈哈哈. 所以需要解决,

而且我们的好像没有他的那种色差问题, 但是我也不确定. 需要你确定一下


### 2. 

你看
![[assets/新建 Markdown/file-20260525033423194.png]]
这个图, 这个图是来自@[handoff_to_koi_w2_2026-05-21_v6cpu_done.pdf](file:///D:/BaiduSyncdisk/2024%20to%20future/koi%20chen/experiments/Waymo2Panorama/deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.pdf) 这个pdf的
是其中的 1.4 新-A 柱面投影 L2 这个方法的对比图, 应该是和我们第一个最强最有潜力的方法对比的图, 然后你会发现![[assets/新建 Markdown/file-20260525033515902.png]]
现在上面的这张图我红框标记出来的有一个很突兀的长方形, 确认一下是原图就有还是说我们拼接出了问题. 这个可能需要去原图去查证 我会给你我们用过图片的所有出处在这个文章的最后

另外我发现每个图的拼接处都有这种拼接痕迹, 不知道是否能去掉,但是我们的第一个iL1 ERP 全景输出 (anchor 60, 1024×2048) 这个图就没有这些阴影但是在Cylinder (顶) vs Sphere (底), anchor 60 这个图做对比的时候就有这些拼接的白色痕迹了, 感觉需要解决一下吧

### 3. 自己的方法的改进
另外我们目前8个方法下真的就是第一个方法最好吗, 是否还有改进的空间? 这个方法是否能继续探索下去呢?

这个overlap的问题跟队友开完会说 好像有一个感觉是可以采用ORB feature point detection和我们方法1结合的方法去进行? 可能需要探索

我们自己的本身这个方法感觉就有很多需要探索的, 看看如何能再改进

### 4. 其他路线的探索


其他路线是否还能再探索呢, 是否还有潜在的更好的方法可以用呢? 感觉需要再进行深挖

### 5. 我们的最好的方法放到waymo数据集上

想把我们的目前的最好的L1这个方法放到waymo上试试呢, 看看是否真的有效 还是只是AV2数据集不错?


## 一些他补充的可能的方向和知识点

他的现在的方法好像是distance-to-boundary blending这个, 然后他说waymo上面做rolling shutter 会出现waymo: rolling shutter , 不是9个格子同时, 有细微差别, 高速移动可能会糊掉. jelly effect这个问题好像需要重视

---

## [附录] 改进方向 Brainstorm (Claude 整理, 2026-05-26)

> 背景: Stage 3 v5 ghost-truth audit 之后 (2026-05-26 ~20:00) 的反思. 9 个 attempt (T4 v1/v2/v3 + T5 v1/v2/v3 + WS4 A2/B1 + Stage 3 v5) **全在同一根子轴上** (sparse displacement post-warp), 在 Porsche/BMW 2-wheel ghost 上视觉 NEG. 但项目方法库实际有 **5 根改进轴**, 其余 4 根里至少 **11 个 candidate 完全没押过 chip**. 下面是按"离 L1 的改动幅度"排序的完整 menu, 供 §1a/§3a/§3b/§4 决策参考.

### 轴 A — 留在 L1 输出, 换 blending / 选择策略 (改动最小)

| # | 方法 | 现状 | 关键 insight | 成本 |
|---|---|---|---|---|
| A1 | Sparse-disp post-warp (TPS/RBF) | ❌ 9 NEG (现状已锁死) | A2/B1 sparse stereo + TPS 在空白区瞎插值, anchor 不够密 | — |
| A2 | **Dense optical flow displacement** (RAFT per-pixel flow in overlap) | **未试** (WS4 D8 计划过但被 cancel) | Sparse 失败的根因 = anchor 不够密 + TPS 瞎插值. Dense flow 每个 overlap pixel 都有自己的 flow vector, 直接补这两个缺口 | 1-2 day, ~4 A100h |
| A3 | **Graphcut hard seam A/B on ghost** | ✅ 新-B 已 ship (-12.4% \|grad\|) **但从未在 2-wheel ghost 上 A/B 过** | Min-cut 给每个 pixel 选**一个** source cam, 完全不 blend → 没 multiband halo. 决定性答案: hard seam 修 ghost 或不修 | 0.5 day, $0 |
| A4 | **Winner-take-all per-pixel** (cos² feather → argmax) | **未试** | 每个 pixel 选 cos(ray to optic axis) 最大那个 cam. 比 graphcut 更激进 | 0.5 day, $0 |
| A5 | **Object-aware seam routing** (YOLO/SAM 检测 cars → graphcut 加 "经过 car bbox" = ∞ cost) | **未试** | 如果 ghost 物体是 detectable (车/人), 强制 seam 绕开. 不需 depth, 不需新 backbone | 1 day, ~轻量 GPU |

### 轴 B — 加 minimal depth, 不换主架构 (paper potential 最高)

| # | 方法 | 现状 | 关键 insight | 成本 |
|---|---|---|---|---|
| B1 | **L3 (Pi3 forward splat) retry on AV2 raw** | 已 NEG, 但用的是**已知坏的 pi3-cache 504×504 letterbox 输入** | un-letterbox + Pi3 + bilinear upsample depth → 重做 forward splat. Pi3 NEG 至少部分归因于 input 问题 | 1 day, ~4 A100h |
| B2 | **DVGT swap** (2026-05-15 brainstorm-survey 自己标的 L3 首选) | **从未跑过** | DVGT 是 driving 训的 + metric-scaled + 不需 Sim(3) gauge ambiguity. Pi3 NEG 大概率不只 paradigm 错也 backbone 错 | 1-2 day, ~6 A100h |
| B3 | **LiDAR depth anchor in overlap** (AV2 64-beam, cam-synced) | **完全未用** | 投 LiDAR points 到 ERP, overlap 区每个 pixel 按 LiDAR depth 选 winning cam (smaller ray angle). 不需任何 deep model. 用 AV2 独有硬件优势 | 1 day, $0 |
| B4 | **Dense MVS in overlap** (extend 新-D wide_baseline_stereo) | 新-D 是 sparse (44 pts/pair median), code 已 ship | 同一对 cam SGM 或 RAFT-Stereo, overlap 区每个 pixel 一个 disparity → 直接修 2-cam parallax. 不依赖 single-view depth backbone, 是 60°-baseline ring cam 的**原生**方法 | 1-2 day, ~4 A100h |
| B5 | **Finite-radius sphere / multi-sphere blend** | **未试**, 新 idea | L1 核心错是 sphere R = ∞. 改 R = 10m (median scene depth) → ghost 大幅减小. 进一步: render 在 R = {3, 10, 30, ∞} 4 个 sphere 上, 用 depth confidence 选每个 pixel 哪个 sphere. **几乎免费的 L1.5** | 0.5 day, $0 |

### 轴 C — 换 projection 几何

| # | 方法 | 现状 | 关键 insight | 成本 |
|---|---|---|---|---|
| C1 | 新-A Cylinder L2 | ✅ ship (+24.9pp coverage, cycle ~flat) | 已 ship, coverage 好但 cycle PSNR 持平 | — |
| C2 | **Adaptive surface**: cylinder near horizon + sphere near poles | **未试** | 行人/车主要在 horizon → cylinder 减 ghost. 天空/路面 → sphere 几何 friendlier. 多 surface 合成输出 | 2-3 day |
| C3 | **新-C IPM multi-region building branch** (ground+sky 已 ship, building 没做完) | ⚠️ unfinished work | building branch 接完 → 近景建筑物可能不糊. 现有 ship 数字 = +0.20 dB on ground | 1 day |

### 轴 D — Heavy lift: generative refinement / 3DGS (paradigm shift, ⚠️ 不算"改进现有方法")

| # | 方法 | 现状 | 关键 insight | 成本 |
|---|---|---|---|---|
| D1 | Diffusion polish on overlap (Percep360 / 360Anything style) | **未试** (在 2026-05-15 brainstorm) | L1 出图 → diffusion 在 overlap 区"修图". 不保证几何真实但视觉无 ghost | 1-2 week |
| D2 | PIS3R: VGGT + image-diffusion fill | **未试** | L3 lift + diffusion fill-in for large-parallax stitching | 1-2 week |
| D3 | CylinderSplat (feed-forward 3DGS panorama, 0.29s/frame) | **未试** | 直接给 panorama, 但模型 Matterport3D/Replica/360Loc 训, driving generalize 未知 | 1-2 week + per-scene tuning |

### 轴 E — 不"修" ghost, 改任务定义

| # | 方法 | 关键 insight | 成本 |
|---|---|---|---|
| E1 | **Frame selection** — 量化哪些 anchor 有 ghost, 给 Bosch dataset 只交 ghost-free subset | Bosch 要 dataset 不是 100% frame. 一个 log 100s @ 20Hz = 2000 frame, ghost 可能 < 30%. 数据集裁剪而不是算法修复 | 0.5 day audit |
| E2 | **多帧时间融合** — ego motion 让 ghost 物体在 t+5 移出 overlap 区, 取时序最干净版本 | Ghost 是 spatial-snapshot 问题, 加 time 维度可以撤掉它. 当前 pipeline **完全没 temporal fusion** | 2-3 day |

---

### 我推荐的 Quick Win 3 连击 (一起 ~2 day, 全部 $0 或极便宜)

1. **A3 graphcut on ghost A/B** (0.5 day, $0) — 复用已 ship `run_graphcut_seam.py`, 在 log 02a00399 anchor 0 的 Porsche/BMW frame 跑, 视觉比较. **答案**: ghost 是 blend 问题还是 selection 问题.
2. **B5 finite-radius sphere** (0.5 day, $0) — 改 `sphere.py` 一行 (R = ∞ → R = 10m), 跑 4 anchor 视觉对比. **答案**: L1 的 R=∞ 假设占了 ghost 多少责任.
3. **B3 LiDAR-anchored cam selection** (1 day, $0) — AV2 LiDAR + cam time-sync 现成, 投点 → overlap 区按 LiDAR depth 选 winning cam. **答案**: 能不能用纯传感器融合 (无 deep model) 解掉 ghost.

**Fallback 序列**: 如果 3 个 quick win 都 NEG, 才进 B2 DVGT (真 backbone swap, 1-2 day, ~$25). 如果 B2 还 NEG, 才到 D 类 (换路线到 diffusion / 3DGS) 或 E2 (改任务定义到 temporal fusion).

### 待跟用户确认的 4 个决策点

1. **§1a ghost 是否单一焦点**, 还是 §3 整体的 L1 改进 (coverage, cycle-PSNR, color shift) 都要管?
2. **"改进现有方法"边界** — 含轴 D (换路线到 diffusion / 3DGS) 吗? 还是严格留在 sphere baseline 框架内?
3. **Paper venue** 仍 3DV 2026 (~9 月 deadline)? 还是 CVPR/ECCV 2027 (~11 月 / 3 月) 有更多空间?
4. 心里**有没有偏好的轴**? 如果已经倾向某条, 直接深挖那条.

### 已锁死结论 (不要再投入)

- **A1 sparse-disp post-warp** (TPS/RBF, 9 attempt 全 NEG): v5 ghost-truth audit (2026-05-26 ~20:00) 决定性证明 sparse anchor 在 ghost 区 max_diff=0 或 catastrophic swirly (v9). 算法**结构性不够 dense**.
- **T4 v1/v2 weight reweight** (Option B): multiband per-pixel renormalize 让 reweight 数学 cancel, 不动 cycle-PSNR.
- **T5 v1/v2 chain warp** (perspective/similarity): chain compose 后 rear cam 飞出 canvas, 朝向不同的 ring cam **几何不可能**共享 image plane.



