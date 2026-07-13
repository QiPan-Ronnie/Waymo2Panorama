# DB-123 ego-body mask 之后:留黑还是填地面？给 koi 的决策文档

> 面向问题:在 **scene band** 里把 ego body（hood 反光 + roof 总成镜面反光）mask 掉之后，那块区域该 **留黑（置黑）**，还是 **生成一些基础地面内容再喂给 Cosmos**？
>
> 日期:2026-07-11 ｜ 结论按严谨程度分级,诚实标注不确定性,可直接转发。

---

## 1. 一句话结论(给赶时间的 koi)

**从"置黑(A)"起步是分布内、语义无歧义的安全选择,不是权宜之计;而"裸填充基础地面(B-裸版)"是三个选项里最差的一个——因为一旦把填充像素标进"可信 band",Cosmos 会把填充里的拉花/闪烁当真值原样固化,而不是去修复它。** 如果 koi 想要更强的 grounding,正确形态不是裸填充,而是 **门控填充(B-门控版)**:只把逐像素通过质量门(多帧一致 + 单源 + flow 残差小)的像素填进去并扩进可信 mask,其余仍留黑。排序:**A ≳ B-门控 > B-裸 > C(外部模型预填充)**。

诚实补一句:置黑不是"唯一能做的";填充的**质量上限确实更高**(有真实地面纹理总比空洞好)。但上限高的前提是"模型被允许 refine 该区域",而我们把它标进可信 band 恰恰是不允许 refine——这就是矛盾所在。下面把机理讲透。

---

## 2. 我们的 mask 语义契约:1+92 里的"黑"到底是什么

先把我们自己管线的约定钉死,再谈通用机理,否则容易被"base 模型里黑=真实黑物体"这类说法带偏。

我们的管线是 **1+92**:
- **1 张完美 frame-1 全景(f000)**:有真实地面 + 真实天空,作为该 log 的锚。
- **92 张 scene band 帧**:中间条=真实投影内容(可信),上下=黑(= Cosmos 待生成域)。
- **逐帧二值 mask**:**白 = 有内容/可信(observed)**,**黑 = 待生成(to be generated)**。

这三者一起喂给 **NVIDIA Cosmos(mask-conditioned video 模型)**,补全成 360° 视频。

关键点:**我们用的是自带 mask 通道的微调模型,"黑像素=缺失"的语义不是靠 base 模型的先验去猜,而是由训练约定直接定义的。** 这正是 video inpainting 文献的标准写法——每帧和一张 mask 拼接,mask 在观测到的像素处取 1、其余取 0:

> "Every input frame is concatenated with a mask which takes value 1 where the corresponding pixel is observed and 0 elsewhere." — *Semantically Consistent Video Inpainting* [4]

所以在**我们的**契约里,ego 去除后的区域"置黑 + 该处 mask=0"是一个**分布内、语义无歧义**的输入:模型被明确告知"这里没观测,你来生成"。这和"未微调 base 模型里一块黑=一个真实的黑色物体"是两码事——后者才是黑像素会被误读的场景,不适用于我们。

---

## 3. 为什么"置黑"是分布内的安全选择(机理)

**3.1 Cosmos 官方条件机制本身是"帧级",不认"帧内黑像素=缺失"。**
Cosmos 家族的原生条件机制是时间维的:条件帧的 latent 沿时间轴拼接、加 augment noise;Cosmos-Predict2.5 的 input masking 也是**整帧粒度**(哪几帧是条件、哪几帧要生成),官方并没有"同一帧内某些黑像素=缺失"这层语义 [3]。对于**空间**控制,Cosmos-Transfer 的控制图是按权重生效的:`w=0` 处自由生成,`w=1` 处受控 [2]。也就是说,"帧内哪块要生成"这层语义,**必须由我们自己的 mask 通道和训练约定来提供**——这恰恰是我们微调模型在做的事。结论:置黑之所以安全,不是因为 Cosmos base 天然懂黑,而是因为**我们的 mask 通道把语义补上了,且置黑与 mask=0 精确对齐**。

**3.2 官方明确警告:拿 mask 去做"视觉控制"容易诱发幻觉。**
NVIDIA Cosmos Cookbook 对控制图和 mask 的关系写得很直白:

> "The control modality is applied only to the white pixels in the mask." [1]

以及一条对我们非常关键的警告:

> "Masking Vis control is known to cause visual hallucinations and is generally discouraged." [1]

这条警告的指向是:**在可见/控制通道里塞入不干净的内容,是幻觉的诱因。** 置黑 + mask=0 恰恰避开了这个坑——我们没有往控制通道塞任何"半真半假"的像素;填充则正相反(见 §4)。

**3.3 置黑=video inpainting 标准做法,训练/推理一致即安全。**
如 §2 所引 [4],"masked 区域置零 + 二值 mask concat"就是 video inpainting 的教科书写法。只要满足两条铁律,置黑就是分布内输入:
- **黑区与 mask 逐像素对齐**(黑到哪,mask=0 到哪);
- **训练与推理一致**(训练时该区域是全黑,推理也喂全黑)。

**3.4 唯一的残余风险:latent 边界泄漏(而且它对"填充"同样存在,甚至更糟)。**
诚实提示一个二阶效应:扩散模型在 **latent 域**工作,VAE 的感受野很大,黑区的内容会渗进邻近 latent token。*Your Latent Mask is Wrong* 实测 FLUX VAE 的有效感受野约 **217px**,即 mask 边界附近约两百像素范围内,像素域的"黑"并不能在 latent 域被干净隔离 [5]。这意味着:
- 对**置黑**:黑区边界会把一点"黑"渗进邻近可信像素——影响有限,且可用羽化带 + 边界留白缓解;
- 对**填充**:泄漏的是**填充里的伪影**(拉花、错误纹理),危害方向更坏。

所以 latent 泄漏这条,不构成"选填充"的理由,反而是"填充需要额外谨慎"的又一条。

---

## 4. 为什么"填充基础地面"有固化伪影的风险(机理 + 双向证据)

这一节要诚实:**填充不是一律有害。** 学界确实有大量"部分可信内容 > 空白"的成功先例。分歧点只有一个——**模型是把这块条件当"待修复的弱提示",还是当"待保留的真值"。** 我们把填充标进可信 band,属于后者,于是拉花和闪烁会被**原样固化**。下面把两个方向都摆出来。

**4.1 正向证据:有噪的真实内容,确实常常 > 空白。**
- **DiffuEraser**:引入先验来抑制乱生成——

  > "the prior acts as a weak condition to suppress the generation of unwanted objects, mitigating visual hallucinations." [6]

  注意关键词 **weak condition**:先验是"弱提示",模型有权覆盖它。
- **FreeVS**:用 sparse but geometrically accurate 的像素做条件,把任务从"凭空生成"**降维成"补全"** [7]。
- **GEN3C(NVIDIA, CVPR 2025)**:直接拿**带洞、带拉花**的点云渲染图当条件,而且**模型被显式训练成去修复这些投影伪影** [8]。
- **ReconDreamer**:同族思路,投影残缺 + 模型 refine [9]。

这些都成立——**但前提都是"模型被允许 refine 该区域"**(条件权重是弱的,或训练目标就是修复)。

**4.2 反向证据:当模型把条件当真值,伪影被固化甚至放大。**
- **RealBasicVSR**:传播式复原会**放大**已有伪影——

  > "exaggerated artifacts, owing to error accumulation during propagation." [10]
- **Video ControlNet / ControlVideo**:逐帧条件只要不一致,输出闪烁会 **1:1 传导** [11]。填充地面在 92 帧上极难做到帧间一致,闪烁会被逐帧固化。
- **Cosmos Cookbook 的幻觉警告** [1](见 §3.2)本质也是这条。

**4.3 分野一句话:restoration 式 vs preservation 式。**
- 把填充区标成"**待修复的弱提示**"(restoration 式)→ 拉花可被模型修掉 → 填充有正收益;
- 把填充区标成"**待保留的真值/可信 band**"(preservation 式)→ 拉花被当成真的 → **原样固化**。

**我们当前把可信 band 内的内容当真值**(这也是我们"band 直采要锐、不许模型改"的一贯设计)。在这个设计下,**裸填充落在最坏的那一侧**。这就是为什么"填充上限更高"和"裸填充是最差选项"两句话可以同时为真:上限高的是 restoration 式填充,我们的管线用的是 preservation 式契约。

**4.4 自动驾驶重建的通行做法:排除,而非填充。**
NeRF/3DGS 系的 AV 重建,对不可靠区域的主流处理是 **exclude(排除/mask 掉)而不是 fill**。"真实像素投影 + 视频模型 refine"的两段式是驾驶 NVS 的主流范式(GEN3C / FreeVS / StreetCrafter / DriveX / LidarPainter / ReconDreamer)[7][8][12]——**但同样以"模型被允许 refine 该区域"为前提**。我们的 ego 去除区如果要走这条路,就必须把它交回生成域(mask=0),而不是钉进可信 band。

**4.5 通用 video inpainting 直接搬过来,还有额外三个坑。**
如果考虑用现成 inpainting 模型(ProPainter / DiffuEraser)在**外部**先把地面填好(即选项 C):
- **ERP 域失配**:标准卷积/光流在等距柱状投影上,失真随空间位置变化(*DAOVI* 明确指出 ERP 视频修复的这一失配)[13][14];
- **93 帧长序列**要分段处理,分段边界有不一致风险;
- **第二生成域风格不一致**:外部模型的"画风"和 Cosmos 对不上,接缝更难融。

所以选项 C 排最后。

---

## 5. 三案排序与门控 B 的具体形态

| 方案 | 做法 | 定位 | 预期 |
|---|---|---|---|
| **A(现行)** | ego 区置黑 + mask=0,交给 Cosmos 生成 | 分布内、无歧义、零额外风险 | **推荐起步** |
| **B-门控版** | 只填**逐像素通过质量门**的像素,并把它们扩进可信 mask;其余留黑 | restoration→preservation 的**安全子集** | **可上位**,值得小规模验证 |
| **B-裸版** | 全量填充基础地面,整块标可信 | preservation 式固化拉花/闪烁 | **预期劣于 A** |
| **C** | 外部 inpainting 模型预填充 | 引入第二生成域 + ERP 失配 + 长序列分段 | **最差,排除** |

**门控 B 到底长什么样(如果 koi 想要更强 grounding,推荐的是这个,不是裸填充):**

对 ego 去除区里的**每一个像素**,只有同时满足以下门才允许"时间反投影填充 + 标进可信 mask",否则留黑(mask=0):
1. **多帧一致**:该像素从相邻帧反投影过来的值,跨帧一致(方差/中值离散小);
2. **单源**:只由单一相机/单一投影源覆盖,避免多源平均产生 ghost;
3. **flow 残差小**:光流对齐残差低于阈值(几何可信)。

通不过的像素一律留黑,交回生成域。这套结构其实就是 **GEN3C / FreeVS 的隐含逻辑**(只保留 geometrically accurate 的稀疏像素),也与我们项目一贯的 **"宁过勿漏"** 原则同构——宁可多留黑让 Cosmos 生成,也不把不可信像素冒充真值。门控 B 的价值在于:它拿到了"填充上限更高"的那部分**真实**收益,同时避开了裸填充"固化伪影"的那部分损失。

---

## 6. 建议的决策路径

1. **A 起步(现在就是对的)**:ego 区置黑 + mask=0,按现行 1+92 契约喂 Cosmos。这是零风险基线,先把它作为对照。
2. **三案对比实验 + 眼核**:我们正在做的 threeway ablation(原图 / 精修黑化 / 时间反投影填充,frame `cd22abca`,tag `a125`,进行中)是这件事的**一手数据**——文献里**没有** black vs backprojected-fill 的受控 ablation,所以别指望论文替我们下结论,靠自己这组图眼核(遵循项目"每张图都用眼睛看、不只看指标"的铁律)。
3. **若 koi 倾向填充**:不要上 B-裸版,直接做 **B-门控版**的小规模验证(几帧几 log),对比 A / B-裸 / B-门控 三者的 Cosmos 下游输出。
4. **落地前的必做一步——和 Cosmos 训练方对齐 mask 语义约定**:确认两条铁律满足,否则一切白搭:
   - **训练/推理一致**:如果训练时该区域一律是全黑,而推理时我们喂了填充内容,就是**分布外**输入,风险不可控;若要喂填充,需确认训练侧也见过同类填充;
   - **黑区与 mask 逐像素对齐**,**羽化带(feather)归入生成域(mask=0)**,不要让羽化的半透明像素混进可信区。

**一句话决策:先 A、拿三案图眼核;要更强 grounding 就上门控 B 并与训练方确认约定;永远不上裸填充。**

---

## 7. 附:本次实验产物索引

**Drive(进行中的三案对比):** `results/db115pro/db123/`
- `threeway_a125.jpg` — 原图 / 精修黑化 / 时间反投影填充 三案对比(frame `cd22abca`,tag `a125`),black vs backprojected-fill 的一手受控数据。

**本地 vis(`experiments/Waymo2Panorama/deliverables/db115_pro/db123_ego_removal/`):**
- `egoblack_cmp.jpg` — 置黑方案对比
- `egomask_v6_vis.jpg` / `egomask_v3b_vis.jpg` — ego mask 解析结果可视化(v6 / v3b)
- `egov3c_cmp.jpg` / `egov4a_cmp.jpg` / `egov5_cmp.jpg` / `egov6g_cmp.jpg` — mask 各版本对比
- `ego_ablate.jpg` / `ego_sweep.jpg` / `ego_edges.jpg` — 消融 / 扫参 / 边缘检查
- `rear_zoom.jpg` — 车顶总成占 rear 图像的近景核验
- `overcast_check.jpg` — 阴天场景 mask 稳健性核验
- `v7_check_e28c16d0.jpg` — v7 mask 在 frame `e28c16d0` 的核验

---

## 参考文献

[1] NVIDIA **Cosmos Cookbook** — 控制图/mask 用法与警告:"The control modality is applied only to the white pixels in the mask";"Masking Vis control is known to cause visual hallucinations and is generally discouraged."(NVIDIA Cosmos 官方文档 / cosmos-cookbook)
[2] NVIDIA **Cosmos-Transfer1** — 空间控制权重语义(`w=0` 自由生成 / `w=1` 受控)。
[3] NVIDIA **Cosmos-Predict2.5** — 帧级 input masking(条件帧 latent 时间维拼接 + augment noise;整帧粒度,无帧内黑像素语义)。
[4] *Semantically Consistent Video Inpainting* — mask concat 约定:"Every input frame is concatenated with a mask which takes value 1 where the corresponding pixel is observed and 0 elsewhere."
[5] *Your Latent Mask is Wrong: ...* — **arXiv:2512.05198** — FLUX VAE 有效感受野约 217px,黑区内容渗入邻近 latent token。
[6] *DiffuEraser* — "the prior acts as a weak condition to suppress the generation of unwanted objects, mitigating visual hallucinations."
[7] *FreeVS* — sparse but geometrically accurate 像素作条件,将生成降维为补全。
[8] *GEN3C*(NVIDIA, **CVPR 2025**)— 带洞/带拉花点云渲染图作条件,模型被训练成修复投影伪影。
[9] *ReconDreamer* — 投影残缺 + 模型 refine 的驾驶重建。
[10] *RealBasicVSR* — "exaggerated artifacts, owing to error accumulation during propagation."
[11] *Video ControlNet / ControlVideo* — 逐帧条件不一致 → 输出闪烁 1:1 传导。
[12] *StreetCrafter / DriveX / LidarPainter* — "真实像素投影 + 视频模型 refine" 两段式驾驶 NVS。
[13] *ProPainter* — flow-based video inpainting(通用域,搬到 ERP 需谨慎)。
[14] *DAOVI* — 全景/ERP 视频修复失真随空间变化,标准卷积/光流失配。

> 说明:除 [5] 给出确切 arXiv 编号(2512.05198)外,其余按论文标题 + 出处标注;[1]–[3] 为 NVIDIA Cosmos 官方文档/仓库。转发时如需精确链接,可按标题在 arXiv / NVIDIA 官方文档检索,避免误引编号。
