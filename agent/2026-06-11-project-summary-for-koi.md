# Waymo2Panorama 项目技术总结(给 koi)

**日期** 2026-06-11
**目标** 多相机 AV 透视图 → 360° ERP 全景(1024×2048)的 GENERAL 算法;下游为 Cosmos 风格世界模型(Xinhan)的首帧条件。
**最难验证场景** AV2 `02a00399`(代号 BMW,含一辆高速行驶的车 / 旁有一辆静止 X3)—— 注意:这只是最硬的验证用例,**从来不是目标**;每一步都按"是否对所有场景成立"来判定。

---

## 1. 执行摘要

- 在最难验证场景(BMW)上,**所有用户可见的缺陷类别都已关闭**,或经 native-truth 面板裁定为**源数据本身的真实内容**(玻璃幕墙反射、店招紫色、墙根植被色),而非拼接 bug。
- 算法是**零场景参数**的 general 管线(`scripts/phase3/db89_ghost_recovery.py`):中心由标定算出、颜色增益由 LiDAR 对应算出、深度由累积算出,没有任何逐场景手调。
- 通过两道 north-star 大关:**(a) 5 场景零参数泛化**(bmw / clean / highway / downtown / crowd 同一份代码跑通,无新增 artifact 类、graceful degradation);**(b) 无 LiDAR 优雅降级**(清空 LiDAR 后全景仍连贯,运动车仍单一完整,OMC 测得同样的 du=+6,降级仅表现为墙面视差软化 + 略强的曝光台阶)。
- 12 个 git tag 记录里程碑(从 `v0.1-l1-mvp` 到 `v2.2-harmonic-fill`),其中 6 个是 Fable 5 重构期的核心节点。
- 最终交付:`deliverables/db89_ghost_recovery/` 下 5 场景 v2.2 全集(`*_segcomposite.png` ×5 + EMC base + 对比 board)。

一句话定性:这不是"消除一切接缝"的魔法,而是一个**source-faithful(源忠实)+ evidence-gated(证据门控)+ abstain(无证据则退让)**的诚实管线 —— 能解的接缝干净解,无法判定的区域诚实退回 L1/EMC,绝不发明几何。

---

## 2. 演进编年

> 阶段 A–E 是把整个 CV/graphics 解空间走穷尽并三重否定的过程,合计约占全篇 1/3;真正的突破在阶段 F。

### 阶段 A — L1 baseline(2026-05-17)
最初的拼接:sphere projection + **multi-band blending**(Voronoi/cos² 加权羽化)+ ERP wrap fix(修复了 mirror bug,commit `885b5da`),tag `v0.1-l1-mvp`。
**发现**:近场出现 doubling 重影(虚影/ghost)。
**为什么不够**:重影的根源是**对两份未对齐的拷贝做了 averaging**(`0.5·A(x)+0.5·B(x−d)`,d>1px 时字面上就是两份渲染叠加)。

### 阶段 B — L1 hard select(单源选择)
把加权混合换成 **hard_select**(每个像素只取一个相机,纯 argmax,从不混合)。
**发现**:averaging 造成的 ghost 消失了;残留被清晰地区分为两类 —— **determinable seam**(可判定接缝:纹理共视的中远表面,有证据可对齐)与 **under-determined residual**(欠定残差:无纹理墙面 + 大视差遮挡近物,无证据)。
**为什么不够**:hard_select 把"颜色混合 ghost"换成了"硬切穿过近结构 → 一份被切断/阶梯化的拷贝";且早期一条歧路(L3 = Pi3 7-view + forward-splat 点云)被量化否定:cycle-PSNR L3 输 L1 **10/10**(-3.13 dB),forward-splat 不优于 L1。

### 阶段 C — ghostkill / seamroute / A1·G·BEST 变体探索(DB-13 至 DB-44)
围绕 hard_select 这条 source-faithful 主线做了大量接缝拓扑与对齐探索:
- **ghostkill**(`_ghostkill_compare.py`):证明 ghost = averaging,**single-source PICK**(把两相机都重投到 ego 中心、按 cos² 权重 PICK 高权者,从不平均)无 ghost。
- **seamroute / object-moat min-cut**(`scripts/phase3/_seamroute.py`):自定义向量化 DP 竖直接缝,代价 = 跨视 RGB 残差 + LiDAR-near + 膨胀近物当作 near-∞ 护城河 → 把硬切引到"远处一致走廊",让整个近物来自单相机。曾是阶段性 deliverable。
- **A1**(Surround360 风格 overlap-strip 光流 view-interp)/ **G** / **BEST**:在重叠带上做 flow 对齐 + 单源合成 + sky/out-of-FOV outpaint。
- 配套地基:line-snap、Poisson tone(E1.5 低频混合)、virtual-centre select。
**发现**:这些方法对**可判定接缝**是干净的 L1++(far-field 字节级一致,仅编辑 ~2-3%);但所有 source-faithful 变体在**宽基线近场 doubling** 上都是**深度精度受限**的 —— copy-SELECTION 选"较不错的拷贝"≈L1,copy-MIXING 混两份错拷贝 = ghost,depth-REPROJECT 需要无源能干净提供的稠密深度。
**为什么不够**:DB-36/DB-40 还确立了硬禁令 —— 任何"修接缝"若需发明几何(fake red-line / fake pole)一律 REJECT;在 ego-origin 中心下,近场曲线/墙根残差是物理地板。

### 阶段 D — DiT360 生成修补(2026-06-03)
试 DiT360(FLUX.1-dev + LoRA,RF-Inversion + PersonalizeAnything)在 A100 上做生成式填补。
**发现 — 一正一负,边界清晰**:
- **sky-only OUTPAINT = WIN**:只对地平线以上黑带做 outpaint(`opmask_sky`,tau=50,guidance=2.8),整个上半球填出连续自然天空,**屋顶线字节级保留、object-gate PASS(无发明物体)、vision-clean**。关键 = constraint(只限天空区)+ object-gate。最佳 Google-Maps 风格全景 = bevfinal + sky-outpaint。
- **SEAM-completion = NEG**:用稍宽的近地接缝带在 tau 20-50 下做填补会**在路面发明小汽车 + 把无纹理切口融化**,过不了 object-gate。
**结论**:生成只许补天,不许碰缝。基础设施配方也固化(本地 FLUX 缓存、卸载 torchao、torchvision object-gate、tau=50)。

### 阶段 E — 学习型 / 3D 探索(DB-45 至 DB-78,概要)
为突破阶段 C 的"深度墙"探索了一批学习/3D 路线,几乎全部否定:
- **DrivingForward**(AAAI'25 feed-forward 3DGS):zero-shot + AV2 微调后能融成单中心,但 head-to-head 输给 CPU deliverable —— **soft / shredded / band-limited**(波浪建筑、物体周围撕裂),比 L1 还差。
- **VGGT 稠密证据 / layered_target_raycaster**(DB-58..70):稠密深度证据审计反复失败(`aggregate_success=false`),source-faithful renderer 权限被门控阻断;DB-68/69/70 的 seam-reroute / ground-plane local alignment 被 vision 否定(接缝变锯齿)。
- **UniDepthV2 稠密度量深度**(EXP-B):p50 残差 0.3-0.6m 极好,但 hold-out p90 = 8-15m,**几何墙第 3 次独立证明**。
- **DB-78 flow view-interp** 5 场景定量:edited_frac 稳定 2.47-2.78%,far-warp ≤0.22px,机制场景无关 —— 是"不差的结果",但视觉增益 SUBTLE,不是"接缝消失"。
**为什么不够**:阶段 E 的结论是 —— 在 **c\*=ego-origin** 这个被默认了的虚拟中心下,几何/学习路线都被同一道墙挡住。这个隐藏假设正是阶段 F 的突破口。

### 阶段 F — Fable 5 第一性原理重构(2026-06-09 至今,主体)

> **转折点**:p13 的第一性原理审计发现 —— **ERP 虚拟中心从 L1 起就被钉死在 ego 原点,从来不是一个设计变量。** `sphere_projection.py` 注释"treat every ring camera as if at the ego-vehicle origin"。这一个被忽略的输入,把过去所有"深度墙"数字放大了 5-20×。

**误差源四分类**(把模糊的"接缝问题"拆成可独立攻击的物理量):
1. **虚拟中心**(选错球心)→ DB-80 解决
2. **光度**(曝光/白平衡 + 色边)→ DB-81 解决
3. **视差**(多中心几何)→ 在正确中心下缩到 thin-band 残差
4. **时间**(异步快门)→ DB-84/86 发现并解决

**DB-80 — 虚拟中心 = 环相机质心。** 实测 BMW 标定:环相机距 ego 原点 1.81-2.18m,但距**环相机质心** `[1.363,-0.004,1.445]` 仅 0.27-0.30m,且在扇区内质心偏移近乎与视线共线(有效垂直基线 b_perp 仅 0.01-0.06m)。深度感知 render-back 误差 ∝ b_perp(`err≈(W/2π)·b_perp·δZ/Z²`)。把球心从 ego 原点移到质心,5 场景全局 depth render-back p90 缩减 **18-96×**(BMW 84→4.7、clean 147→5.8、highway 38.8→0.4、downtown 11.7→0.1、crowd 67.9→1.6 cam px);深度容差放宽约 20×,平面填充即可覆盖约 98% 原本被迫 abstain 的近地。**DB-79 的"深度无法重开接缝"被正式 re-scope**:那是 (深度, c\*=ego-origin) 的性质,不是深度本身的性质。新最佳几何 base = `cen_depth`。

**DB-81 — 颜色。** P1:对每个被 ≥2 环相机共视的累积 LiDAR 点取各相机 RGB → ring-closed 最小二乘(log 域)解 per-camera 增益 → 渲染时施加。跨相机颜色台阶削减 **58-88%**(4/5 场景),黄昏 highway 从 7-patch 拼贴变为色调统一。P2(径向 CA)= 诚实 NEG:**AV2 图像无可测横向色差**;紫色 fringe 归因于源数据 ISP 阴影色噪(native 图里就有),不是 CA、不是 JPEG、不是管线。

**DB-82 — 鲁棒性 + 无 LiDAR。** 5 logs × 3 anchors × 3 variants:多 anchor 结构一致,无遮挡泄漏 / 无运动物撕裂;**无 LiDAR 优雅降级首次落盘 A/B** —— 纯平面深度与全 LiDAR 在全景尺度均差仅 4-10 灰阶。

**DB-84 — 第四个误差源:异步快门。** 调试一个错误填充区时发现 AV2 环相机**曝光时刻交错**(实测表:front_center 0、front_left −12.5ms、front_right +12.5ms、rear ∓7.5ms、side_left +22.5ms、side_right −22.4ms)。BMW 那辆"轿车"实为 **17.7 m/s 行驶**;35ms 跨相机偏移 → 0.62m → ≈16 ERP px,正好是观测到的 doubling。**DB-83 的尸检被改写**:doubling 从来不是深度/disocclusion,是 per-camera 拍摄时刻的运动视差。

**DB-86 — EMC(自运动快门补偿),DB-80 以来单笔最大近场修复。** 用户在**行驶的 Porsche 和静止的 X3 上都**标了重影 —— 静止物证伪了"纯物体运动"。第一性原理:交错快门也位移 **ego** 自身;在实测 7.66/9.22 m/s 下,每个相机真实光心距其标定锚时位置最多 **22.7cm**(与相机间基线同量级)。管线自 L1 起对 LiDAR 做了 per-return 时间补偿,却**从未补偿相机曝光时刻** —— 一个隐藏了几十次渲染的不对称。修复就一行:`T_cam(t_i) = T_ego(t_i)·T_ego_cam`(位姿插值到每相机曝光时戳)。静止 X3 尾部重影消失,行驶 Porsche 头尾重影基本消失。**EMC 成为标准 base 组件(`cen_depth_b1_emc`)。**

**DB-83/85/87 — 三轮否定(共九+变体),定位出缺失输入。** 所有基于 annotation box 的对象工具(box-footprint lock、ray-OBB depth、moat、occlusion test、silhouette、soft-steering)都以不同方式失败 —— 反复产生"box 空气边距把车印在车旁"的 ghost wheels。**九连败的尸检指向同一个缺失输入:需要图像级轮廓(image-level silhouette),box 几何精度根本够不着。**

**DB-88 — YOLO 分割合成,7 次尝试中首次把行驶车渲染成"单一且完整"。** YOLOv8x-seg 逐相机分割(只定 ownership,零生成内容),实例按 IoU 匹配到 EMC 位姿下的运动轨迹框。RULE1:落在所选相机 mask 内的射线 ← 该相机;RULE2:被毒化的背景射线 → 下一个干净相机;全毒化的 penumbra → EMC 兜底。**决定性变量 = c_own 必须是 EMC-Voronoi-DOMINANT 相机**(在物体方向 b_perp 最小),不是最正对的相机 —— 这样车身与周围残留共享拍摄时刻、自然衔接(最正对的选择会把车身相对自己的残留位移 16px)。

**DB-89 — 数据集级发现:AV2 标注框滞后图像 ~4m/~0.2s。** 对齐审计(`align_audit.png`):YOLO mask 反投影精确落在行驶车上,而 annotation box 投影落在其后方约 100px/4m。**自 DB-83 起每个 box 驱动的工具都在用一个滞后 4m 的框来引导** —— 这回溯性地解释了整条 DB-83 九连败链。架构由此收敛为六条证据演算:
> ① **box 只做身份匹配,mask 做几何**(对标注滞后免疫);② 正向 mask 证据处处可信,**负向证据在距图像边界 1% 内不可信**(否则把车自身边缘的破碎 mask 读成背景 → 把杆子填进车里);③ 多重认领的歧义实例**可否决(poison)但绝不断言(body)**;④ 一个 body / 一个相机 / 一个时刻,completeness-first,无单相机看全时用 **OMC**(物体侧 EMC,从两相机重叠带的 mask 对齐测位移);⑤ ghost 仅在测得位移 > 2px 量化时存在(du≈0 时填它会破坏接触阴影);⑥ **TIME 是最后手段**(仅全毒化像素,三重门控)。机制重写:OMC 测得所有 pair du≈0 → 双 A-pillar 其实是**深度视差**(13m 的车画在墙深),统一物距投影即可合并。

**DB-90 — ECC-OMC 错切补偿 + view-morph + DP 内容缝。** 用户的眼睛抓到 v12 的"收敛"Porsche 仍读作两辆车 —— 16× 取证:每条长线在 butt-joint 处阶梯 1-2px(ECC 后测得高达 9px 仿射错配)。**根本洞察:mask-IoU 的 OMC 对 5-15px 位移是盲的;硬逐像素 ownership(选择)有一个等于配准残差的精度地板。** 分工:**证据演算答 WHO/WHERE,view-morph 答 HOW**(ECC-affine 测得 du=+6 并去错切 + Beier-Neely alpha-ramp 几何插值)。**规则7**:混合只在视角一致处合法 —— 玻璃反射的视差由反射源深度(店面 20m+)而非车身(13m)决定,无法配准,任何 blend 都会 ghost → 几何插值但内容由 min-difference DP 缝 winner-take-all。

**DB-91 — 规则8 深度证据门控 + 3源时间共识 + native-truth 验收法。** 用户 A/B milestone vs L1 圈出 3 处残留(grain × 2 + 绿色突起)。诊断纪律见效:label-flapping / poison 假说被数据 KILL(flap≤0.21%);EMC-vs-composite A/B 把 grain 定位到 **base render** —— **EDT 最近邻深度是单样本估计器**,在细结构上逐像素翻转 × 基线 = 采样跳变 = grain(L1 因用平面/ego 中心深度而免疫)。修复:(1) EDT 深度场 medianBlur(5);(2) **规则8 深度证据门控** —— 逐像素重投仅在深度证据可信处(近 LiDAR 支撑 AND 与 8× 下采样大尺度中值一致)合法,否则退回大尺度鲁棒深度(L1 式局部平面),修玻璃幕墙与深度悬崖;(3) 时间填充升级为 **3 源共识**(3 个最佳独立 frame/cam,逐通道中值)+ 邻域一致性 abstain。**估计器原则入演算:绝不让渲染穿过单样本估计器,用邻域/时间中值。** 残留 3 处经 **native-truth 面板**(把主相机按渲染器自己的深度 warp 到 ERP = 忠实渲染 ground truth)裁定为**源内容**(玻璃艺术品+反射、墙根植被)→ BMW 达到**源保真度**。

**DB-92 — 北极星双关:泛化 + 无 LiDAR。** 全栈零参数跑通 bmw+downtown+crowd:无崩溃、无新 artifact 类;downtown 7 合成/16 unmatched、crowd 9/48 unmatched(小行人)优雅降级到 EMC;警车、侧文字、行人、步行者、骑行者全完整。**无 LiDAR 消融 PROVEN**:fallback 已进仓库算法(identity 增益、ground-plane/far-shell 深度、自解除 LiDAR 门控时间填充);清空 LiDAR 后全景仍连贯,OMC 测得**完全相同的 du=+6**(对象机制按构造只依赖图像+标注证据,LiDAR 无关)。**general 算法北极星双半全部满足。**

**v2.1 去紫边 / v2.2 谐和填充(收尾)。**
- **v2.1**:YCrCb 外科手术 —— 仅 magenta 带(Cr>136 AND Cb>136)的像素把色度拉向中性(最大权 0.75,5px 羽化,亮度不动),**0.5% 像素改变**,真正紫色的店招保留,Porsche 挡风玻璃紫斑消除。tag `best-pano-v2.1-defringe`。
- **v2.2**:用户的"绿色裂缝"(ECC-OMC 去错切腾出的带,被时间填充进了几何对但光度错的背景 —— 投射阴影不在任何证据通道里)。最终修复 = **per-pixel 最近 ring 谐和偏移**(harmonic-lite Poisson,Perez'03 近似):每个填充像素继承其最近 ring 像素的高斯平滑光度偏移,阴影衰减传递到车下,匹配区得 ~0 偏移。零场景参数;机制可证只触碰填充 blob(BMW 仅 259 填充 px,其他场景 0 = 不动)。tag `v2.2-harmonic-fill`。
- **用户检查点(反单点过拟合)**:"要 general 方法,不是对一张图磨像素";验收标准升级为多场景 A/B 回归,band 内残留纹理差记为已知的**未建模接触阴影**限制,不再迭代。

---

## 3. 最终算法

### 八层确定性栈(误差源 → 机制)

| 层 | 误差源 / 缺陷 | 机制 | 来源 |
|---|---|---|---|
| 1 虚拟中心 | 球心选错(放大一切深度误差 5-20×) | ERP 球心 = 环相机质心(min-b_perp 单源选择) | DB-80 |
| 2 深度渲染 | 近场视差 doubling | 深度感知单源 render-back(LiDAR 近胜 + EDT + 平面填充,medianBlur 去采样 grain) | DB-80/91 |
| 3 光度 | 跨相机曝光/白平衡台阶 | LiDAR 对应 ring-closed log 增益 | DB-81 |
| 4 自运动快门 | ego 在 ±22.5ms 内位移 22.7cm | per-camera 曝光时刻位姿 EMC | DB-86 |
| 5 运动物体身份 | 行驶车跨相机重影 | YOLO-seg 实例 + box 身份匹配 + Voronoi-dominant c_own | DB-88/89 |
| 6 接缝几何 | butt-joint 1-9px 错配 | ECC-OMC 去错切 + Beier-Neely view-morph + DP 内容缝 | DB-90 |
| 7 时间填充 | 真互遮挡空洞 | 3 源时间共识 + 邻域 abstain + 谐和光度偏移 | DB-91/v2.2 |
| 8 光度收尾 | 紫色 fringe | YCrCb 色度 clamp(0.5% 像素) | v2.1 |

### 证据演算八条规则(零场景参数)
1. **box 只做身份,mask 做几何**(抗标注滞后)。
2. 正向 mask 证据处处可信;**负向证据在边界 1% 内不可信**。
3. 歧义(多重认领)实例**可否决,不可断言**。
4. 一个 body / 一个相机 / 一个时刻;无相机看全时用 OMC。
5. ghost 仅在测得位移 **> 2px 量化**时存在。
6. **TIME 是最后手段**(仅全毒化像素,三重门控)。
7. **混合只在视角一致处合法**(玻璃/反射 winner-take-all DP 缝)。
8. **深度证据门控**:逐像素重投仅在深度可信处合法,否则退回大尺度鲁棒深度(coherence over absolute position)。

> 贯穿原则:**绝不让渲染穿过单样本估计器**(用邻域/时间中值)。

---

## 4. 验证与交付

### 5 场景结果(v2.1 全集,fresh L4 重生成)

| 场景 | log:anchor | 合成对象 | unmatched(优雅退回 EMC) |
|---|---|---|---|
| bmw(最硬) | 02a00399:a000 | 4 | 6(唯一有时间填充的场景,259 px) |
| downtown | 9f871fb4:a030 | 7 | 16 |
| crowd | fbee355f:a030 | 9 | 48(小行人) |
| clean | 0bae3b5e:a030 | 15 | 108 |
| highway | 2c652f9e:a030 | 3 | 15 |

所有 unmatched 都优雅降级到 EMC base,无崩溃、无新 artifact 类、逐场景 vision 验证通过。

### 无 LiDAR 消融
纯平面深度 vs 全 LiDAR 全景尺度均差 4-10 灰阶;运动 Porsche 仍渲染为一辆完整车;OMC 测得相同 du=+6。降级 = 墙面视差软化 + 略强曝光台阶,无 cliff。

### git 里程碑 tag(12 个)
早期:`v0.1-l1-mvp` · `v0.2-d1-resolved` · `v0.2-l3-mvp` · `v0.2-w2p004-validated` · `v0.3-acq-mcp-shipped` · `v0.4-acq-mcp-v012-robust`
Fable 5:`db90-v3-porsche-solved` · `db91-grain-consensus-fixed` · `db92-generality-pass` · `best-pano-v2-5scenes` · `best-pano-v2.1-defringe` · `v2.2-harmonic-fill`

### 交付物路径
- **主交付**:`deliverables/db89_ghost_recovery/` —— `*_segcomposite.png` ×5(最终全景)+ `*_emc.png`(EMC base)+ `*_db89_board.jpg`(对比 board)+ `DB89_remote_result.json`。
- **算法**:`scripts/phase3/db89_ghost_recovery.py`(全栈单文件,零场景参数)。
- 第一性原理分析:`agent/2026-06-09-fable5-firstprinciples-analysis.md`。

---

## 5. 方法论收获

- **眼睛优先于指标**:DB-86 静止 X3、DB-90 "仍是两辆车"、DB-91 三处 grain —— 几乎每个突破都由用户的眼睛触发;PSNR 在 L3/3DGS 上误导过(测光度匹配,不测几何干净度)。
- **先隔离变量再修**:虚拟中心是被默认了几十次渲染的隐藏输入变量;隔离它(只改球心)是 18-96× 缩减的全部来源。
- **选择 vs 插值 vs 共识的分工**:selection 答 WHO,view-morph 答 HOW(且有配准地板),consensus 答时间填充;混用会撞各自的天花板。
- **反单点过拟合(用户检查点)**:BMW 是验证用例,永远不是目标;验收必须多场景 A/B,机制须可证只触碰目标区域。
- **诚实演算 > 修补**:brief 的预注册 kill-clause 多次在恰当时机阻止了 patch-on-patch 螺旋(DB-83 九连败、DB-87 EMC-only)。

---

## 6. 已知边界与下一步

| 项 | 状态 | 说明 |
|---|---|---|
| **接触阴影**(DB-96) | icebox | 唯一剩余的可见 artifact 类(填充带显示无阴影背景);现由谐和填充缓解,按设计未建模 —— 留给下游生成层或下游标记时再处理。 |
| **天空 outpaint**(DB-93) | queued,**需 A100** | v2.2 全景上半球仍为黑;已验证的 DiT360 sky-only outpaint(gate-clean)待在 5 张 v2.2 上集成;阻塞在用户批准 FLUX/A100。 |
| **Xinhan 中心契约**(DB-94) | queued,需对接 | 确认下游 Cosmos 消费者用的点云首帧中心 = 我们的环相机质心(z≈1.44m 相机高度),否则全景相对点云会偏 0.5-1.5m。 |
| **Waymo 迁移**(DB-95) | queued,**the big one** | 真正的泛化大关:全栈仅靠 loader 级改动(相机数/布局、快门时序、标注格式)能否跑通 Waymo(5 相机、不同 stagger)—— 任何需要改算法(而非 loader)的修复必须是 evidence-principled 且 scene-agnostic,否则记为数据集特定限制。 |

---

*本汇报事实来源:`agent/progress.md`(完整编年史)、`agent/decision_briefs.md`(活跃队列 DB-93..96)、memory 中 `waymo2pano-seam-direction.md` / `waymo2pano-dit360-findings.md`。所有数字均可溯源,未发明。*
