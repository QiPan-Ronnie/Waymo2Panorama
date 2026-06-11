# Waymo2Panorama CVPR 投稿机会评估

**日期** 2026-06-12
**作者** 研究策略分析师 agent
**用途** 供项目负责人(koi)决策:是否、以何种打包方式、按何种补漏顺序冲 CVPR。
**素材来源** `agent/2026-06-11-project-summary-for-koi.md`(技术全貌)、`agent/2026-06-11-literature-positioning.md`(文献定位)、大脑最新检索结论。
**事实纪律** 不发明任何实验数字;所有我方数字均取自上述材料,可逐条溯源。外部论文内部数字一律不引用。

---

## 1. TL;DR

- **最强候选一句话**:把 AV 环形相机的**异步交错快门(staggered trigger ±22.5ms)当作全景合成的第一公民**——用一条 `err≈v·Δt·(W/2π)/Z` 的预测模型统一解释跨相机 doubling,以 EMC(ego 自运动)+ OMC(物体运动)双补偿 + 证据演算管线在 5 场景上零参数消除。这是一个 **analysis 驱动 + 物理量化 + 一行级核心修复**、文献中暂无直接先例的角度(候选 A)。
- **投稿可行性评级:中(偏强,但有一道硬门槛)。**
  - 支撑"偏强"的:角度新颖(快门第一公民)、故事完整(误诊→证伪→量化→修复→泛化)、资产扎实(5 场景 v2.2 全集 + 无 LiDAR 消融 + 12 git tag + 零参数单文件管线)。
  - 拖到"中"的两个前置闸门:(i) 仅 AV2 单数据集验证,而全文最强卖点恰是"general / 零场景参数";(ii) 定量指标体系目前是 eyes-first,缺标准化数值口径。
- **最大风险:单数据集 + 定性为主。** CVPR 审稿人会用"你声称 general 却只在 AV2 上跑、且没有可比的定量表"两点合力打回。
  - **第一杠杆 = Waymo 迁移(DB-95)**:把"中"抬到"强"的唯一硬证据。
  - **第二杠杆 = 定量指标体系**:无参考质量 + 任务驱动 + 用户研究三件套。
  - 两者不补,角度再新也会卡在 borderline。

---

## 2. 候选贡献矩阵

> 四个候选各按:核心主张 / 新颖性证据 / 已有实验资产 / 缺口 / 单独成文可行性。

### A. Time-Resolved Panorama Compositing(异步快门为第一公民)

- **核心主张**:环形多相机拼接的近场 doubling 长期被误诊为深度/disocclusion 问题;其真正成因是**每相机曝光时刻交错**——交错既位移**物体**(OMC),也位移 **ego 自身**(EMC)。把时间作为合成的显式变量,用 `err≈v·Δt·(W/2π)/Z` 预测 ERP 像素位移,并以 per-camera 曝光时刻位姿插值 `T_cam(t_i)=T_ego(t_i)·T_ego_cam` 修复。
- **新颖性证据**:实测 stagger 表(front_center 0 / front_left −12.5 / front_right +12.5 / rear ∓7.5 / side_left +22.5 / side_right −22.4 ms);BMW 那辆"轿车"实为 17.7 m/s 行驶,35ms 跨相机偏移 → 0.62m → ≈16 ERP px,**与观测 doubling 量级吻合**;静止 X3 尾部重影**证伪"纯物体运动"假说**,锁定 ego 自运动分量(±22.5ms → 光心位移最多 22.7cm,**与相机间基线同量级**)。大脑检索:RS 行级校正/硬件同步/VIO 时间戳均有工作,但**环相机间异步触发在拼接/合成层的系统补偿无直接命中**。
- **已有实验资产**:DB-84/86 完整尸检链;EMC 已成标准 base 组件(`cen_depth_b1_emc`);静止 X3 + 行驶 Porsche 的 before/after;无 LiDAR 下 OMC 测得**相同 du=+6**(机制对 LiDAR 无关,按构造成立)。
- **缺口**:(1) `err≈v·Δt·(W/2π)/Z` 目前是定性吻合,**缺逐场景 predicted-vs-measured 散点/表**;(2) "AV 拼接无先例"是负检索,**需正式 related-work 复核**;(3) 单数据集(同一套 AV2 stagger),Waymo 不同 stagger 是天然第二证据但**未跑**。
- **单独成文可行性:中-强。** 是四个里最"可独立成短文/workshop"的——analysis + 一行修复 + 证伪实验自成闭环。但单独成主投稿略单薄,**最适合做主投稿的核心钩子**(见 §3)。
- **评级小结**:新颖性最高、故事最干净、风险集中在"负检索是否被推翻 + 跨数据集吻合是否成立"两点,均可前置消除。**这是全文的卖点支点。**

### B. Evidence-Calculus 框架(八规则 source-faithful 合成系统)

- **核心主张**:面对拼接缺陷,**不用生成先验掩盖,而用证据可信度门控 + 无证据则 abstain** 逐层物理消除;八条规则把 selection(答 WHO/WHERE)、view-morph(答 HOW,有等于配准残差的精度地板)、consensus(答时间填充)严格分工,贯穿"绝不让渲染穿过单样本估计器"纪律。
- **新颖性证据**:与全部最近邻(Percep360 / MultiViewPano / DiT360)正交——**它们是唯一的非生成、source-faithful + abstain 路线**;DiT360 的 seam-completion NEG(在路面发明车、融化切口)**从生成侧反证**接缝缺的是几何证据而非生成先验;规则8 深度证据门控(coherence over absolute position)+ 估计器原则是设计哲学层贡献。
- **已有实验资产**:`scripts/phase3/db89_ghost_recovery.py` 全栈单文件零场景参数;八层确定性栈完整表;DB-83/85/87 三轮否定(九+变体)的预注册 kill-clause 记录;native-truth 验收法。
- **缺口**:**新颖性在系统层而非组件层**——DP/min-cut 缝、Poisson/谐和迁移(Perez'03)、Beier-Neely view-morph、Surround360 overlap-strip flow 均有出处。诚实表述必须是 "a principled, evidence-gated integration",**不能宣称组件首创**;审稿人易质疑"系统型论文 = 工程堆叠"。
- **单独成文可行性:弱-中。** 单独投易被打成"incremental engineering"。**最适合做主投稿的系统/方法章节**,为 A 提供"为什么补偿后还要这么多规则才干净"的完整答案。
- **评级小结**:作为独立贡献偏弱,但作为支撑 A 的"完整工程现实"不可或缺,且 source-faithful + abstain 的设计取向本身就是对生成系的鲜明立场——挂在 A 下面价值放大,单飞价值缩水。

### C. 数据集与基准(AV2-360 + 评估协议)

- **核心主张**:发布 AV2-360 全景数据集(当前 75 张,**可扩到数千张**)+ 完整球面 v7 + 评估协议(无参考质量 + 任务驱动下游收益),服务正在快速增长的全景 VQA / 生成 / 世界模型社区。
- **新颖性证据**:下游生态确有需求且 faithful 拼接是共同上游——Panorama-Language Models(arXiv 2603.09573,用 nuScenes "geometry-based panoramic synthesis" 建全景 VQA 基准)、TanDiT(2506.21681)、DynamicScaler(2412.11100)、BEV-VAE(2507.00707)、Percep360(2507.06971)**多家以"某种全景化 AV 数据"为输入/基准**。我们提供的是**source-faithful + abstain 标注**的全景,区别于生成式数据。
- **已有实验资产**:5 场景 v2.2 全集(`*_segcomposite.png` ×5 + EMC base + 对比 board);零参数管线可批量化;native-truth 面板作为 per-image faithfulness 裁定法。
- **缺口**:**75 张离"数据集论文"体量差很远**;需扩到数千张 + 标准化 split + baseline 跑分表 + 至少一个下游任务的实证收益;发布需清理 license/坐标契约(DB-94)。
- **单独成文可行性:中(但工作量大)。** 数据集/benchmark track 是真实赛道,但当前体量和评估协议成熟度都不够,**更适合做主投稿的"实验与发布"支柱**,用主论文的方法生成数据、用主论文的指标定标。
- **评级小结**:有真实下游需求托底(多家全景下游以 AV 全景为输入/基准),但 75 张的体量决定它现在是"主投稿的发布物 + 未来的独立 dataset paper 种子",而非当下可独立成文的 benchmark。

### D. AV2 标注时间滞后 ~4m 的 dataset analysis

- **核心主张**:AV2 annotation box 相对图像滞后约 **4m / 0.2s**(`align_audit.png`:YOLO mask 反投影精确落车上,box 投影落其后约 100px/4m),回溯解释了 DB-83 起的九连败。
- **新颖性证据**:这是一个对**整个 AV2 下游有用**的数据集级观察(若成立)。
- **已有实验资产**:DB-89 对齐审计图 + 九连败溯源链。
- **缺口**:**最高优先自查**——必须先排除我方时间戳对齐/外参链路自身误差,确认是数据集属性而非 pipeline 假象;单点观察需扩到多 log/多帧统计才稳。
- **单独成文可行性:弱(单独成文)。** 体量是 short note / blog 级。**最适合并入主投稿的 dataset analysis 小节或一个 box**,既增信(解释了为什么"box 只做身份、mask 做几何")又提供社区价值。
- **评级小结**:高杠杆的小贡献——若自查通过,它用一张图解释了我方一条核心设计规则的由来,顺带给社区一个有用警告;但它依赖自查结论,自查不过则只能撤回,不可硬挂。

### 候选速查表

| 候选 | 新颖性 | 已有资产成熟度 | 主要缺口 | 单独成文 | 在主投稿中的角色 |
|---|---|---|---|---|---|
| A 异步快门 compositing | 高 | 高(机制+证伪已落盘) | 负检索复核 + 跨数据集吻合 | 中-强(可单飞短文) | **主贡献 / 卖点钩子** |
| B 证据演算框架 | 中(系统层) | 高(单文件零参管线) | 组件级非首创,防"堆叠"质疑 | 弱-中 | 系统 / 方法章节 |
| C AV2-360 数据集与基准 | 中(需求托底) | 低(仅 75 张) | 体量 + 评估协议成熟度 | 中(工作量大) | 实验与发布支柱 |
| D AV2 标注滞后分析 | 待定(依赖自查) | 中(单图+溯源链) | 自查排除自身 bug | 弱 | analysis 小节 / box |

---

## 3. 推荐打包方案

**结论:A 为主贡献 + B 做系统/方法章节 + C 做实验与发布 + D 并入 analysis 小节 = 一篇主投稿。**

理由:四者单独都偏单薄或体量不足,但**钩子(A)+ 系统(B)+ 数据/评估(C)+ 增信分析(D)** 合起来是一篇结构完整、有新角度、有可发布资产的 CVPR 主论文。A 提供"为什么读这篇"的新颖钩子;B 回答"补偿后为什么还需要这么多机制";C/D 提供可复核的实证地基。

**为什么不拆成多篇**:A 单飞 → workshop/short 体量,主新颖点但缺系统与数据支撑,易被嫌"小";B 单飞 → 易被打成 incremental engineering;C 单飞 → 75 张体量不够;D 单飞 → note 级。**合并是把"单点新颖 + 系统完整 + 可发布资产"凑成一篇有分量的主投稿的最优解**;拆分只在 Waymo 迁移失败、general 主张被迫收窄时才考虑(届时 A 可降级走 workshop 保底,见 §5)。

**适配的 track**:CVPR 主会(method + analysis),而非纯 dataset track——因为最强贡献是方法/分析(A+B),数据集(C)是支撑而非主体。若 CVPR 窗口紧或 Waymo 未及时跑通,**A 单独走 CV workshop / short paper 是已识别的保底路径**。

### 论文骨架

**题目候选**
1. *Time-Resolved Panorama Compositing: Shutter-Aware Multi-Camera Surround Stitching for Autonomous Driving*
2. *Evidence over Hallucination: Source-Faithful 360° Panoramas from Asynchronous Multi-Camera Rigs*

**Abstract 要点**(5 点)
1. **问题再定义**:AV 环相机全景的近场 doubling 长期被误诊为深度/disocclusion 问题;我们指出真因是**异步交错快门**(staggered trigger ±22.5ms)——它既位移**物体**也位移 **ego 自身**(实测光心位移最多 22.7cm,与相机间基线同量级)。
2. **方法**:提出 time-resolved compositing——`err≈v·Δt·(W/2π)/Z` 预测 ERP 像素位移,EMC(ego 自运动)+ OMC(物体运动)双补偿,核心修复是一行位姿插值 `T_cam(t_i)=T_ego(t_i)·T_ego_cam`。
3. **系统**:残留缺陷用 **evidence-calculus**(八条规则:证据门控 + abstain,绝不发明几何)逐层消除;与生成式全景路线(Percep360 等)正交且互补——它们掩盖缺陷,我们物理还原。
4. **验证**:AV2 **5 场景零参数泛化 + 无 LiDAR 优雅降级**;迁移到 Waymo(不同相机数/布局/stagger)进一步证明 general[**条件于 DB-95 跑通**]。
5. **发布**:AV2-360 数据集 + 评估协议(无参考质量 + 任务驱动下游收益),服务全景 VQA / 生成 / 世界模型下游生态。

**5 个实验设计**(各含:目的 / 做法 / 产出 / 工作量)

1. **Waymo 迁移(最大缺口,最高优先)**
   - 目的:把"general / 零场景参数"从口号变成第二数据集硬证据。
   - 做法:全栈仅靠 loader 级改动跑 Waymo(5 相机、不同布局、不同 stagger 时序、不同标注格式);任何需要改算法(而非 loader)的修复必须是 evidence-principled + scene-agnostic,否则诚实记为数据集特定限制。
   - 产出:Waymo 上的 5 场景全集 + EMC/OMC 在**新 stagger 时序**下的 predicted-vs-measured 吻合表(这正是 A 的预测模型的跨数据集验证)。
   - 工作量:大。**这是把"general"落到纸上的唯一实验,也是 borderline→accept 的最大杠杆。**

2. **逐层消融**
   - 目的:证明八层栈每一层都在解一个被量化的物理误差,而非凑数(反"工程堆叠"质疑)。
   - 做法:八层(虚拟中心 / 深度 / 光度 / EMC / 运动物身份 / 接缝几何 / 时间填充 / 光度收尾)逐层 on/off。
   - 产出:在 5(+Waymo)场景上量化各层贡献;复用已有我方数字——虚拟中心 18-96× depth render-back p90 缩减、跨相机色阶 58-88% 削减、EMC du=+6、无 LiDAR 4-10 灰阶差。
   - 工作量:中(机制已落盘,主要是系统化跑表)。

3. **下游条件评估(任务驱动)**
   - 目的:把 source-faithfulness 翻译成下游任务指标,证明"更干净的拼接 = 更好的条件基底"。
   - 做法:用我们的全景作首帧/条件喂给全景生成或世界模型(Percep360 / Cosmos 风格世界模型指标口径),对比"naive 拼接 base vs 我们 base"。
   - 产出:下游收益数值(无参考质量提升 + 感知/重建指标提升)。
   - 工作量:中-大(需对接下游消费者 + 坐标契约 DB-94)。

4. **与 homography / MultiViewPano 基线对比**
   - 目的:在公认基线上确立我们的相对位置。
   - 做法:同输入下并列经典 homography 拼接、MultiViewPano(training-free 多视图→360)与我们。
   - 产出:无参考质量 + faithfulness(native-truth warp 对比)双口径表;预期我们在 faithfulness 上领先、在"无空洞完整度"上由 abstain 让出但诚实标注。
   - 工作量:中(需复现基线)。

5. **用户研究 / 无参考质量(eyes-first 标准化)**
   - 目的:把"用户眼睛抓到的缺陷"(本项目几乎每个突破的触发器)量化为可报告数值,堵住"只有定性"的攻击。
   - 做法:招募评估者对 doubling / seam / faithfulness 做 forced-choice A/B,辅以无参考质量指标(借 Percep360 协议)。
   - 产出:偏好率 + 无参考质量表 + `err≈v·Δt·(W/2π)/Z` 的 predicted-vs-measured 散点。
   - 工作量:中。

---

## 4. 投稿前必须补的实验/工作清单(按工作量排序,诚实)

> 按"小→大"排,但**优先级**上 Waymo 迁移与定量指标体系是 borderline→accept 的关键。

1. **正式 related-work 复核(小,但 gating)**:把"AV 环相机异步触发在拼接层无系统补偿"这条负检索做成正式 related-work 二次确认(rolling-shutter 校正 / 硬件同步 / VIO 时间戳 / 计算摄影 staggered-HDR 各自划清边界)。**没过这关,A 的新颖性主张站不住。**
2. **AV2 标注滞后自查(小-中,gating D 与规则1)**:多 log/多帧统计确认 ~4m 滞后是数据集属性而非我方时间戳/外参 bug。**这是规则1"box 只做身份"的合法性根基,也是 D 能否对外声称的前提。**
3. **定量指标体系(中,gating)**:当前 eyes-first,需补**无参考质量指标 + 任务驱动下游指标 + 用户研究**三件套,给出可比数值表。`err≈v·Δt·(W/2π)/Z` 的 predicted-vs-measured 散点也在此补齐。
4. **5 场景地面/天空全量验收(中)**:接触阴影(DB-96,icebox)与天空 outpaint(DB-93,需 A100)目前未全量收口;主投稿至少要把它们的状态在 5 场景上明确标注为"已建模/abstain/未建模限制",不能留模糊。
5. **Waymo 迁移(大,最大缺口,最高优先级)**:DB-95。全栈仅靠 loader 级改动(相机数/布局、快门时序、标注格式)跑通 Waymo。**这是全文最强卖点的唯一硬证据**;能跑通则 general 主张成立,需改算法则收窄 claim 为"AV2 + 同类 rig"。建议作为投稿前硬门槛。

**建议执行顺序(先 gating 后体量)**:
- **第一批(低成本 gating,先做以决定 claim 边界)**:related-work 复核(1) + AV2 标注滞后自查(2)。两者结论直接决定 A 的新颖性表述强度与 D 是否保留。
- **第二批(并行启动,决定 accept 概率)**:Waymo 迁移(5)+ 定量指标体系(3)。这是把"中→强"的两根杠杆,工作量最大,应尽早并行开。
- **第三批(收尾)**:5 场景地面/天空全量验收(4),在投稿前把 icebox/abstain 状态在 5(+Waymo)场景上标清。

---

## 5. 风险与对策

**风险 1 — 系统型论文被拒(B 的固有风险)**
- 表现:审稿人把全文读成"已知组件(DP 缝 / Poisson / Beier-Neely / Surround360 flow)的工程堆叠,无单点首创"。
- 对策:**钩子集中在异步快门(A)**——让"为什么新"全部压在 time-resolved compositing + EMC/OMC 物理分析上;B 表述为 "principled evidence-gated integration",并用逐层消融(实验 2)证明每一层都在解一个被量化的物理误差,而非凑规则数。

**风险 2 — 单数据集(最大风险)**
- 表现:"声称 general / 零场景参数,却只在 AV2 一套 rig + 一套 stagger 上跑。"
- 对策:**Waymo 迁移(DB-95)是必做项而非可选项**;不同 stagger 的 Waymo 恰是 EMC/OMC 预测模型的天然第二证据。若迁移失败,**诚实收窄 claim**(去 general 大词,改"AV2 + 同类 ring rig"),把失败点写成 limitation——比硬撑 general 被当场拆穿安全得多。

**风险 3 — 生成天空的 faithful 争议**
- 表现:"你一边主张 source-faithful,一边用 DiT360 生成天空,自相矛盾。"
- 对策:**明确分层标注**——全景显式区分 measured(源忠实重建)/ outpainted(天空,生成)/ abstain(未建模,如接触阴影)三层;天空 outpaint 走 object-gate + 区域约束,在论文中定位为**可选下游层**而非核心 claim 的一部分,核心 faithfulness 主张只覆盖 measured 区。

**风险 4 — 定量薄弱**
- 表现:eyes-first 不可比,审稿人要表格而我们以 native-truth 面板 + 多场景 A/B 为主。
- 对策:清单第 3 项三件套(无参考质量 + 任务驱动 + 用户研究);把已有物理量化数字(stagger 表、18-96× depth 缩减、58-88% 色阶削减、du=+6、4-10 灰阶无 LiDAR 差)系统化成主表,辅以下游任务指标。

**风险 5 — 负检索被推翻**
- 表现:related-work 复核后发现 AV 拼接异步快门补偿存在近似先例。
- 对策:提前做(清单第 1 项);即便出现先例,**EMC(ego 自运动分量,而非仅物体)+ 静止物证伪 + AV 全景场景化**仍可作为 narrower 但成立的新颖点保留,表述退到 "first to ... in AV ring-camera panorama with ego-motion shutter compensation"。

---

## 一句话决策建议

值得冲 CVPR,角度(异步快门第一公民)是稀缺且可防守的真新颖点;但**先用低成本的两项 gating(related-work 复核 + 标注滞后自查)确定 claim 边界,再并行投入 Waymo 迁移 + 定量指标体系两根大杠杆**。Waymo 跑通 = 强投稿;Waymo 不通则诚实收窄到"AV2 + 同类 rig",A 单独走 workshop 保底。**不建议在 Waymo 与定量两项都没影前定稿主投稿**——那等于把最强卖点留在嘴上,正中审稿人下怀。

---

## 6. Sources

- Percep360(arXiv): https://arxiv.org/abs/2507.06971
- Percep360(代码): https://github.com/Bryant-Teng/Percep360
- MultiViewPano(OpenReview, ICLR 2026 投稿): https://openreview.net/forum?id=uYXHqNg87h
- Panorama-Language Models(arXiv 2603.09573,nuScenes geometry-based panoramic synthesis 全景 VQA 基准): https://arxiv.org/abs/2603.09573
- TanDiT(arXiv 2506.21681,切平面 DiT 全景生成): https://arxiv.org/abs/2506.21681
- DynamicScaler(arXiv 2412.11100,全景视频生成): https://arxiv.org/abs/2412.11100
- BEV-VAE(arXiv 2507.00707,多视图生成): https://arxiv.org/abs/2507.00707
- 内部素材:`agent/2026-06-11-project-summary-for-koi.md`、`agent/2026-06-11-literature-positioning.md`、`agent/progress.md`、`agent/decision_briefs.md`(DB-93..96)。

> **核实状态**:Percep360 / MultiViewPano 三条主链经大脑 WebSearch 核实存在。新增五条下游生态链(2603.09573 / 2506.21681 / 2412.11100 / 2507.00707 + Percep360)来自大脑最新检索,标注为"可信但投稿前需正式 related-work 复核"。所有"AV 拼接无先例"负检索结论、AV2 标注滞后是否为数据集属性,均为投稿前 gating 自查项,未独立二次确认前不对外定稿。
