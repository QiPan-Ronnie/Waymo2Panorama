（把下面整段发给另一个 agent）

---

你正在接手 **Waymo2Panorama** 项目（用户 Qi Pan，导师 Koi Chen）。先做一件事再开口：**按顺序读这几个文件，读完再回我**——不要凭旧记忆行动，今天（2026-05-30）有两个重要更新。

## 必读（按序）
1. `agent/handoff.md` —— 顶部 **2026-05-30 横幅**（最新认知，覆盖之前所有 banner）。
2. `agent/progress.md` —— 顶部 **2026-05-30 (late)** 和 **RETROSPECTIVE** 两条（今天全部工作）。
3. `agent/方向讨论_2026-05-30/00_方向总览.md` + `方法与论文_汇总.xlsx` —— 当前和用户讨论方向用的总包（问题本质 + 12 个试过的方法及结果 + 20 篇论文及"怎么用" + 5 个待讨论问题）。
4. 略读：`agent/EXPLORATION-seam-synthesis-sprint.md`（今天自主实验全记录）、`agent/BRAINSTORM-2026-05-30-paper-sparks.md`（论文精读）。

## 一句话现状
目标：7 个**非共心**环视相机（基线 21-26cm，重叠仅 18.6°）→ 干净的 360° ERP 全景，做 Bosch world-model 训练数据。

**今天两个关键结论：**
- **（正面）E1.5 确认能治"光度缝"**（色差/亮度台阶），远场逐字节 = L1；但**治不了"近景视差缝"**（重影）。诚实说法："修颜色，不修几何"。
- **（重构，最重要）** 我们一直被锁在 **"单光心 + 几何忠实"**，而这对非共心相机 + 近景**物理上不可能同时满足**——这是过去所有方法 NEG 的根。**已查证：Google 街景本身也是 7 个非共心相机、也有视差、也不是单光心 / 不忠实**，它靠局部光流 warp + 缝走低纹理 + 远景统计来"藏缝"（= plausible 而非 faithful）。**我们比街景多一张牌：真实 LiDAR。** 用户已把验收标准定为 **PLAUSIBLE（看着像真实街景即可，但不准编出显著物体如车/人）**。

## 当前状态 / 用户的明确要求
- 用户要 **静下心、一起讨论方向**，**不要自己觉得某方法好就猛冲**（这是项目反复栽的坑——"觉得不错→冲→NEG"）。每一步方向性决定要**和用户一起定**。
- **每张经手的图必须用 vision 亲眼看过**才能下结论，**绝不能"指标好但视觉差"**（吃过大亏）。
- 正在讨论的开放问题：① 要不要正式放弃"几何忠实"、改用街景式"plausible 多中心 + LiDAR 引导藏缝"；② Bosch 到底要不要严格单光心；③ 方法选 A（纯生成缝补 PowerPaint/BLD）/ B（3DGS+diffusion，Difix3D+）/ C（时间多帧 StreetCrafter）。**这些都还没定，别替用户拍板。**

## 基础设施（需要时）
- Colab 通过 `agent-colab-direct`（隧道 URL+token 每次会话由用户给；helper `scripts/_colab.py`；Windows 上必须 `export MSYS_NO_PATHCONV=1`）。Colab 仓库 `/content/waymo2panorama`，5 个 AV2 val log 在 Drive `.../data/argoverse2/val/`。
- **HF token 绝不发到 Colab**（分类器会拦，用户硬规矩）。FLUX 已缓存 Drive，DiT360 可离线用。
- git 在 `main`，直推已授权，工作树干净。commit footer 用 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

## 你现在该做什么
**先读上面文件 → 简短回我"已读 + 你的理解 + 你看到的方向倾向" → 等用户继续讨论。不要先动手做实验。** 用户和主 agent 正在就方向深入讨论中，你的角色是保持同步、能随时接力，不是另起炉灶。
