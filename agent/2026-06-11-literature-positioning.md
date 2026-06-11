# Waymo2Panorama 文献定位笔记

**日期** 2026-06-11
**作者** 研究分析师 agent
**用途** 帮团队理解"我们的工作在 2025-26 文献坐标系中的位置"——谁是最近邻、我们差异在哪、新颖性几何、下一步动作。
**配套** 技术细节见 `agent/2026-06-11-project-summary-for-koi.md`。
**事实纪律** 本笔记只用已核实存在的链接 + 我方已落盘事实;凡未核实处显式标注「待核实」,不发明任何外部论文的实验数字。

---

## 1. TL;DR

- **我们在哪**:做的是「零场景参数的 source-faithful 多相机透视→ERP 全景」算法——证据演算 + 异步快门补偿(EMC/OMC)+ view-morph + DP 内容缝 + 深度证据门控,AV2 上 5 场景验证,定位为世界模型(Cosmos 风格)首帧的**忠实条件输入**。本质是一个**确定性、可解释、可优雅降级的几何/光度重建管线**。
- **谁最近**:最近邻是 **Percep360**(arXiv 2507.06971,自称"首个自动驾驶全景生成方法")和 **MultiViewPano**(ICLR 2026 投稿,training-free 多视图→360);稍远是 360 生成系(**DiT360**——我们用过、**SceneDreamer360** 等)。**全部是生成式(diffusion)路线;我们是唯一的非生成、source-faithful 路线。**
- **我们的差异化**:同样面对"拼接全景有缺陷"这一事实,**生成系把缺陷当作要被扩散模型掩盖/重画的噪声**,我们**把缺陷当作可用证据逐层消除的物理误差**(虚拟中心、光度、视差、异步快门四源)。两条路线**互补而非竞争**:我们更干净的拼接 = 他们更好的条件基底;他们的生成修复 = 我们 icebox 残留(接触阴影、nadir)的下游选项。**异步快门补偿(EMC/OMC)这一物理分析在 AV 环形相机拼接文献中检索未见先例。**

---

## 2. 最近邻工作逐个对照

### 2.1 Percep360(arXiv 2507.06971)— 最近邻 / 互补上游

**它做什么**
"Hallucinating 360°: Panoramic Street-View Generation via Local Scenes Diffusion and Probabilistic Prompting"。自称**首个自动驾驶全景生成方法**。以**拼接全景(stitched panorama)为基底**,用 Local Scenes Diffusion(LSDM)克服"针孔采样的固有信息损失",用 Probabilistic Prompting(PPM)做可控生成;声称生成数据在**无参考(no-reference)质量指标**上超过原始拼接图,并提升下游感知模型表现。代码:github.com/Bryant-Teng/Percep360。(实验数字一律「待核实」——不在我方素材内。)

**与我们的关系:互补,且天然是上下游**
- 它**承认拼接全景是有缺陷的基底**,然后用 diffusion **生成掉**这些缺陷;我们**把同一批缺陷逐层物理消除**(DB-80 虚拟中心 / DB-81 光度 / DB-86 EMC / DB-88-91 运动物+缝)。**这正是我们最强的对照论点**:面对同一个事实,Percep360 选择"用更强的生成先验覆盖",我们选择"用更强的证据演算还原"。
- 它的 LSDM 是**像素级生成器**,无 abstain 概念;我们是**源忠实 + 无证据则退让**,绝不发明几何(DB-36/40 硬禁令)。两者价值取向相反但可串联。

**我们能借它什么**
- LSDM 是我们 icebox 残留的现成下游修复候选:**接触阴影**(DB-96)与 **nadir/天空**(DB-93,我们已验证 DiT360 sky-only outpaint 可用)正是"局部小区域生成填补"——Percep360 的 LSDM 范式与之对口。
- 它的**无参考质量指标 + 下游感知收益**评估协议,可直接借来量化我们的全景质量(我们目前以 native-truth 面板 + 多场景 A/B 为主,缺标准化数值口径)。

**它验证了我们什么**
- 验证了**"AV 透视→全景"这个问题本身值得做、且 2025 年仍是新坑**(它敢自称"首个")。
- 反向验证了我们的差异化定位是真空地带:它把"拼接质量"当作要被生成绕过的常数,**说明把拼接质量本身当作可独立攻克的研究对象,在当前文献里基本没人系统做**——这正是我们的位置。

### 2.2 MultiViewPano(OpenReview uYXHqNg87h,ICLR 2026 投稿)— 方法学最近邻

**它做什么**
Training-free,从**任意位姿/FOV** 的输入图生成 360 全景:多视图扩散 + pose-aware stitching。(细节「待核实」,仅据素材摘要。)

**与我们的关系:方法学竞争 + 部分互补**
- 共同点:都接受**多张任意位姿透视图**作输入、都目标 360、都强调 **training-free / 零训练**(我们是零场景参数,精神一致)。
- 分叉点:它**用扩散补全/生成**未覆盖区域;我们**用证据 + abstain**,无覆盖处退让而非幻想。它的 "pose-aware stitching" 与我们的 DB-80 虚拟中心选择是**同一痛点的两种答案**——它把位姿差异交给扩散吸收,我们把它建模为可计算的几何误差(球心 = 环相机质心,b_perp 最小化)。

**我们能借它什么**
- 若需补我们当前不覆盖的大空洞(如车顶 nadir 反打、超出环相机 FOV 的区域),它的 pose-aware 多视图扩散是参考方案。
- 它作为 **ICLR 2026 同期投稿**,是判断"360 生成赛道拥挤度 + 评审口味"的最新风向标。

**它验证了我们什么**
- 验证了 **training-free / 零参数** 是当前可发表的有效卖点;我们的"零场景参数 + 5 场景泛化 + 无 LiDAR 优雅降级"是更强版本的同一主张。

### 2.3 DiT360 / SceneDreamer360 等 360 生成系 — 远邻 / 下游工具箱

**它们做什么**
- **DiT360**(我们已实测):FLUX.1-dev + LoRA 的全景生成。我方结论已固化——**sky-only outpaint = WIN**(gate-clean 上半球填充),**seam-completion = NEG**(在路面发明车、融化无纹理切口)。详见 `waymo2pano-dit360-findings.md`。
- **SceneDreamer360**:text-driven、3D-consistent 全景高斯(panoramic 3DGS)。属"从文本/少量条件长出 3D 一致全景"的生成系,与我们"从真实多相机还原"取向相反。

**与我们的关系:下游工具,不是竞争**
- 这些是**生成层**,消费我们的输出而非替代它。我们的全景 = 它们更忠实的条件;它们的生成 = 我们 abstain 区的可选填补。
- DiT360 我们已划清边界:**生成只许补天,不许碰缝**——这条经验对评估 Percep360 的 LSDM「能否安全补接触阴影」直接可复用(预期同样需要强 object-gate + 区域约束,否则会发明内容)。

**它们验证了我们什么**
- DiT360 的 seam-completion NEG **从生成侧反证了我们的核心命题**:接缝处缺的是**几何/物理证据**,不是生成先验——硬塞生成会发明内容。这是我们"证据演算 + abstain"路线的独立旁证。

### 对照速查表

| 维度 | 我们(Waymo2Panorama) | Percep360 | MultiViewPano | DiT360 / SceneDreamer360 |
|---|---|---|---|---|
| 范式 | 确定性几何/光度重建 | diffusion 生成 | 多视图 diffusion | diffusion 生成 |
| 对"拼接缺陷"的态度 | 可消除的物理误差 | 要被生成掩盖的噪声 | 交给扩散吸收 | 不直接处理 |
| 训练 | 零场景参数 | 需训练 LSDM/PPM(待核实) | training-free | 需 LoRA(DiT360) |
| 源忠实 / abstain | 是 / 是 | 否 / 否 | 否 / 否 | 否 / 否 |
| 异步快门处理 | 是(EMC/OMC) | 未见 | 未见 | 不适用 |
| 关系 | — | 互补上游(我=条件) | 方法学竞争+互补 | 下游工具箱 |

---

## 3. 新颖性评估(诚实)

> 原则:把"真首创""工程综合""数据集发现"分开标定,不混为一谈;每条给出可被反驳的边界。

### (a) AV 环形相机异步快门的系统分析与补偿(EMC/OMC)— 最强新颖点

- **现状**:异步/交错快门(staggered shutter)导致运动物 ghosting,在**安防/HDR/计算摄影**领域早被知晓("1ms 错位 ≈ 1px 鬼影"是工程常识);但检索 `multi-camera rig asynchronous shutter compensation compositing` 等组合,**未发现 AV 环形相机拼接中系统建模并补偿异步快门的工作**。
- **我们做了什么是新的**:不仅补偿**物体**运动(OMC),还指出交错快门**位移 ego 自身**(实测 ±22.5ms → 光心位移最多 22.7cm,与相机间基线同量级),并以一行位姿插值修复(EMC,`T_cam(t_i)=T_ego(t_i)·T_ego_cam`)。静止 X3 的尾部重影证伪了"纯物体运动"假说——**这是把已知物理现象首次搬进 AV 全景拼接并量化其几何后果**。
- **诚实边界**:底层物理非我们发明;新颖性在于**问题定位(AV 环相机 + ego 自运动)+ 量化(22.7cm 与基线同量级)+ 管线级修复**。是论文 **analysis 章节**的核心素材,但应表述为"首次在该场景系统处理",而非"首次发现快门交错"。**「AV 拼接无先例」这一负检索结论本身待第三方复核**——建议正式投稿前再做一轮窄域文献确认。

### (b) 证据演算 / abstain 框架 — 工程综合,非单点首创

- 八条规则的**单个构件**多有出处且我方已引用:DP/min-cut 内容缝(Graphcut Textures / Photomontage 系)、Poisson/谐和光度迁移(Perez '03)、Beier-Neely view-morph、Surround360 风格 overlap-strip 光流。
- **新颖性在系统层**:把这些构件用**统一的"证据可信度门控 + 无证据则 abstain"**原则编排,并贯穿"绝不让渲染穿过单样本估计器"的纪律。这是**有价值的工程综合 + 设计哲学**,应诚实表述为"a principled integration",不宜宣称组件级首创。

### (c) AV2 标注框滞后图像 ~4m / ~0.2s — 可能值得独立 note

- DB-89 的对齐审计(`align_audit.png`)显示:YOLO mask 反投影精确落车上,而 annotation box 投影落其后约 100px/4m;回溯解释了 DB-83 起的九连败。
- **若可复现且非我方坐标系/时间戳处理 bug**,这是一个对整个 AV2 下游有用的**数据集级观察**,适合做成简短技术 note 或主论文的一个 box。
- **诚实边界**:必须先排除我方时间戳对齐/外参链路自身的误差(**优先级最高的自查**),确认是数据集属性而非我们的 pipeline 假象后再对外声称。

### 新颖性分级小结

| 主张 | 等级 | 表述建议 |
|---|---|---|
| EMC/OMC 异步快门补偿 | 强(领域内首次系统处理,待负检索复核) | "first to systematically model & compensate … in AV ring-camera panorama" |
| 证据演算 + abstain | 中(工程综合 + 设计哲学) | "a principled, evidence-gated integration" |
| AV2 标注滞后发现 | 待定(需排除自身 bug) | 先自查,再决定是否独立 note |

---

## 4. 行动建议(谨慎)

1. **借 Percep360 的评估口径自测,但不急于联系作者**。先把我们的 5 场景/75 张结果跑一遍**无参考质量指标 + 下游感知收益**(Percep360 的协议),拿到可比数值后再决定是否与作者交流"用我们的拼接做更干净的 LSDM 基底"。**先有自己的硬数据,再谈合作**——避免空手对话。

2. **EMC/OMC 走 short paper / workshop 窗口判断**。最强、最可独立成文的就是异步快门那条(analysis 驱动 + 一行修复 + 静止物证伪)。但**两个前置闸门必须先过**:(i) 第三方复核"AV 拼接无先例"的负检索;(ii) AV2 标注滞后的自查。两者清掉后,EMC/OMC + 证据演算整体可投 CV workshop 或 short paper;**不要在 Waymo 迁移(DB-95)未过前**宣称"general method 已证",以免被审稿人用单数据集打回。

3. **Waymo 迁移(DB-95)对论文是必要而非可选**。我们的北极星就是 general,Percep360/MultiViewPano 都以"泛化/training-free"为卖点。**只在 AV2 上验证 = 把最强卖点(零场景参数泛化)留在嘴上**。建议:把 Waymo 迁移作为投稿前的硬门槛——能仅靠 loader 级改动跑通,则 general 主张成立;若需改算法,诚实记为数据集特定限制并相应收窄 claim。

4. **DiT360 经验直接迁移到评估 Percep360 的 LSDM 边界**。我们已知"生成只许补天、不许碰缝"。在引入任何下游生成修复(接触阴影 DB-96 / nadir DB-93)前,**复用我们的 object-gate + 区域约束验收法**判定 LSDM 类方法是否会发明内容——这是低成本、高价值的一步,且能反哺论文的 limitations/对照章节。

---

## 5. Sources

- Percep360(arXiv): https://arxiv.org/abs/2507.06971
- Percep360(代码): https://github.com/Bryant-Teng/Percep360
- MultiViewPano(OpenReview, ICLR 2026 投稿): https://openreview.net/forum?id=uYXHqNg87h
- DiT360 / SceneDreamer360 / Surround360 / Photomontage / Perez '03 / Beier-Neely:为我方已用/已引方法,精确引用条目见各 decision brief 与 `waymo2pano-dit360-findings.md`,此处不重复粘贴外链。

> **核实状态**:三条主要外链(arxiv 2507.06971 / github Bryant-Teng/Percep360 / openreview uYXHqNg87h)经大脑 WebSearch 核实存在。所有标注「待核实」处(Percep360/MultiViewPano 内部实验数字、"AV 拼接无先例"负检索结论、AV2 标注滞后是否为数据集属性)尚未独立二次确认,正式对外引用前需补做。
