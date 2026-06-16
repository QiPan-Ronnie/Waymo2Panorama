# Q&A 弹药库 — 算法具体细节(防追问用,中英对照)

> 按"可能被问的问题 → 可直接说的答案(含真实实现细节)"组织。所有数字来自我们的实际代码与实验记录。

---

## Q0(总清单). 你们具体用了哪些算法?/ What specific algorithms did you use?

**中:** 几何侧全部是经典算法:**多视几何投影**(内外参光线投射)、**LiDAR 多帧累积深度**、**SE(3) 位姿插值**(旋转四元数 SLERP + 平移线性插值)、**光线-平面求交**(地面反投影)、**光线-盒体 slab 相交测试**(遮挡检查)、**中值共识**(鲁棒统计)。感知侧:**YOLOv8 实例分割** + **3D框-mask IoU 身份关联**。配准侧:**ECC 增强相关系数配准**(量快门残余位移)。合成侧:**视图变形(view morphing)** + **动态规划最小误差缝** + 2px 羽化。生成侧(仅天空):**FLUX.1-Fill 扩散补全** + **DiT360 全景 LoRA**。几何零可学习参数,模型全现成权重,零训练。

**EN:** On the geometry side, everything is classical: **multi-view projection geometry** (intrinsics/extrinsics ray casting), **multi-sweep LiDAR depth accumulation**, **SE(3) pose interpolation** (quaternion SLERP for rotation, linear for translation), **ray–plane intersection** for the ground reprojection, **ray–box slab tests** for occlusion checks, and **median consensus** for robust validation. Perception: **YOLOv8 instance segmentation** plus **3D-box-to-mask IoU identity association**. Registration: **ECC (Enhanced Correlation Coefficient) alignment** to measure the residual shutter shift. Compositing: **view morphing** plus a **minimum-error dynamic-programming seam** with 2-px feathering. Generation — sky only: **FLUX.1-Fill diffusion inpainting** with **DiT360's panorama LoRA**. No learnable parameters in the geometry, all models off-the-shelf, zero training.

---

## A. 投影与虚拟中心

**Q: 虚拟中心具体怎么算的?/ How exactly is the virtual centre computed?**
**中:** 7 个环相机外参(T_ego_cam)的平移分量取平均——即相机光心的质心,在 ego 坐标系里大约在挡风玻璃上方(约前移 1.3m、上移 1.5m)。ERP 的每条视线从这个点出发。
**EN:** It's the mean of the seven ring cameras' extrinsic translations — the centroid of the optical centres, sitting roughly above the windshield (about 1.3 m forward, 1.5 m up in the ego frame). Every ERP ray originates from that point.

**Q: 挪了球心之后,深度从哪来?/ Where does depth come from once you move the centre?**
**中:** LiDAR 多帧累积:把前后数十个 sweep 的点云用各自时刻的 ego pose 统一变换到 anchor 坐标系,投影到 ERP 网格得到逐像素深度;没有命中的远处用远壳兜底。**没有 LiDAR 时优雅降级**:深度退化为地面平面 + 远壳,光度增益退化为恒等——运动车机制照常工作(只依赖图像+标注),实测同一辆车依旧完整,OMC 量出相同的 du=+6px。
**EN:** Multi-sweep LiDAR accumulation: dozens of sweeps are transformed into the anchor frame using their own ego poses, then projected onto the ERP grid for per-pixel depth; far regions without hits fall back to a far shell. **Without LiDAR it degrades gracefully**: depth falls back to ground-plane + far shell, photometric gains to identity — and the moving-object machinery still works (it only needs images + annotations); we verified the same car renders intact with the identical du=+6 px.

**Q: 18–96× 这个数怎么量的?/ How was the 18–96× number measured?**
**中:** 用 LiDAR 点做 GT:同一批 3D 点分别从 ego-origin 球心和虚拟中心投影,统计跨相机重投影残差(像素),逐场景对比——降幅 18 到 96 倍,取决于近景占比。
**EN:** Using LiDAR points as ground truth: the same 3D points are projected from the ego-origin centre versus the virtual centre, and we compare cross-camera reprojection residuals in pixels, scene by scene — an 18-to-96× reduction depending on how much near-field content the scene has.

## B. EMC(逐相机快门位姿)

**Q: ±22.5ms 哪来的?/ Where does the ±22.5 ms come from?**
**中:** AV2 七个环相机在一个帧周期内错相触发,相邻相机相差约 7ms,离 anchor 最远约 ±22.5ms。每张图的真实曝光时刻就是它文件名里的纳秒时间戳。
**EN:** AV2's seven ring cameras are staggered across one frame interval — about 7 ms between neighbours, up to ±22.5 ms from the anchor. Each image's true exposure instant is the nanosecond timestamp in its own filename.

**Q: 位姿插值具体怎么做?/ How is the pose interpolation done exactly?**
**中:** 在 ego pose 序列上,平移逐轴线性插值,旋转用四元数 SLERP——合起来是 SE(3) 插值。每台相机用它自己的曝光时间戳取插值位姿,再投影。
**EN:** On the recorded ego-pose sequence: translation is interpolated linearly per axis, rotation by quaternion SLERP — together, SE(3) interpolation. Each camera takes the pose at its own exposure timestamp, then projects.

**Q: 这跟卷帘快门是一回事吗?/ Is this the same thing as rolling shutter?**
**中:** 不是。这是**跨相机的触发时刻差**(毫秒级、帧间);卷帘快门是**单张图内逐行曝光**(行间)。AV2 环相机近似全局快门,行内可忽略;我们补偿的是跨相机那个。
**EN:** No. This is the **trigger-time offset across cameras** (millisecond-level, between frames); rolling shutter is **row-by-row exposure within one image**. AV2's ring cameras are effectively global-shutter, so the within-frame effect is negligible — what we compensate is the cross-camera offset.

## C. 运动物体(一体·一相机·一时刻 + OMC)

**Q: 怎么判定"运动"?/ How do you decide an object is moving?**
**中:** 用标注轨迹:一个 track 的 3D 框中心在时间窗内位移超过 0.5m 即记为运动目标;静止车辆走普通静态流程。
**EN:** From the annotation tracks: if a track's 3D-box centre moves more than 0.5 m within the time window, it's flagged as moving; parked vehicles go through the normal static path.

**Q: 分割用的什么?怎么和 3D 框对上?/ What segmentation, and how is it matched to the 3D boxes?**
**中:** YOLOv8x-seg(现成权重,取 person/bicycle/car/motorcycle/bus/truck)在 7 张原图各跑一遍;把 3D 框投影到每张图,与 mask 算 IoU,**≥0.3 即认领**——框给"身份"(跨相机同一辆),mask 给"几何"(精确轮廓)。细节:AV2 标注比图像滞后约 0.2s,快车上框落后车身约 4m,所以规则是"框只用于身份、不用于裁剪"。
**EN:** YOLOv8x-seg, off-the-shelf weights (person/bicycle/car/motorcycle/bus/truck), run on each of the seven images; the dataset's 3D boxes are projected into each image and matched to masks by IoU — **claimed at IoU ≥ 0.3**. Boxes provide *identity* (same car across cameras), masks provide *geometry* (the exact outline). One subtlety: AV2 annotations lag the images by ~0.2 s — on a fast car the box trails the body by ~4 m — so the rule is "boxes for identity only, never for cropping."

**Q: 渲染相机怎么选?/ How is the rendering camera chosen?**
**中:** 看这辆车的像素质心落在哪台相机的"主导区"(以虚拟中心做方位划分),由该相机**单独、完整**渲染整辆车——绝不让一辆车横跨两个来源。
**EN:** By which camera's dominant sector (an azimuth partition around the virtual centre) the car's pixel centroid falls into; that camera renders the **whole car alone** — a car never spans two sources.

**Q: ECC 具体怎么用的?/ How exactly is ECC used?**
**中:** 选定 owner 相机后,车贴回全景与邻机背景间还差一个残余平移(快门期间车自己动的部分)。在重叠条带上跑 OpenCV findTransformECC(平移模型),最大化两块图的相关系数,直接量出位移——BMW 场景 du=+6px——再补偿。注:之前 mask-IoU 量不出来,ECC 用灰度相关才量出来。
**EN:** After the owner camera is chosen, there's still a residual translation between the pasted car and the neighbouring background — the car's own motion during the shutter gap. We run OpenCV's findTransformECC (translation model) on the overlap strip, maximizing the correlation coefficient — it measured du = +6 px on the BMW scene — then compensate. Note: mask-IoU couldn't detect this; ECC's intensity correlation could.

**Q: 贴回去边缘怎么处理?/ How are the paste boundaries handled?**
**中:** 窄条带里做视图变形(view morphing)把两边拉到一致,再用动态规划找逐像素差最小的切换缝(Photomontage 思路),2px 羽化。原则:**只在两个视角本来就一致的地方过渡,绝不平均几何**。
**EN:** Inside a narrow strip we apply view morphing to pull both sides into agreement, then a dynamic-programming seam finds the minimum-difference switching path (Photomontage-style), with 2-px feathering. The principle: **transition only where the two views already agree — never average geometry**.

## D. 地面(时间反投影,零生成)

**Q: 几何上怎么做的?/ How does the ground reprojection work geometrically?**
**中:** 天底每条视线与地面平面求交(平面高度 = 后轴原点下方 0.33m,即轮底),得到钉死在大地上的 3D 点;把该点投到**其他帧**的相机里取真实像素。源相机同样用它自己曝光时刻的插值位姿(EMC 同款)。
**EN:** Each nadir ray is intersected with the ground plane (0.33 m below the rear-axle origin — wheel level), giving a 3D point fixed to the earth; that point is projected into **other frames'** cameras to fetch real pixels. Source cameras also use their own exposure-instant interpolated poses — same EMC principle.

**Q: 候选帧怎么选?/ How are candidate frames chosen?**
**中:** 资格由**几何**定:全 log 搜索自车位移 5–58m 的帧(固定时间窗在红灯场景会全军覆没——有个场景静止了 9.5 秒);再按位移分 5m 桶、每桶取时间最近的 3 帧(约 33 帧)。物理原因:7 相机全装在前挡上方,**源车只能看到自己 20–28m 外的"现在车底"区域**——所以远近桶都必须有。
**EN:** Eligibility is **geometric**: we search the whole log for frames where the ego has moved 5–58 m (a fixed time window collapses at red lights — one scene sat still for 9.5 seconds); then displacement is bucketed at 5 m, taking the 3 time-nearest frames per bucket (~33 candidates). The physics: with all seven cameras in a front pod, **a source vehicle can only see the patch under the anchor from 20–28 m away** — so near and far buckets are both mandatory.

**Q: 怎么防止取到假像素?/ How do you avoid fetching wrong pixels?**
**中:** 三层检查:① 运动物体 3D 框(膨胀 1.3×)做光线-盒体相交测试,被挡视线丢弃;② **源车自遮挡**:双盒车体模型(全长低盒 1.0m + 座舱高盒)精确光线检验——不剔除会采到源车引擎盖上的天空反光;③ 多源中值共识验证、**只用最接近中值的单一来源渲染**(混合会糊车道线)。残余不足几个百分点用扩散补孔。
**EN:** Three checks: ① ray–box slab tests against moving objects' 3D boxes (inflated 1.3×) — blocked rays are dropped; ② **source-vehicle self-occlusion**: an exact ray test against a two-box body model (full-length 1.0 m-high body + cabin-height box) — without it you sample the sky reflection off the source car's own hood; ③ multi-source median consensus validates, but **a single nearest-to-median source renders** (blending smears lane markings). The residual few percent gets diffusion hole-filling.

**Q: 为什么天底看起来比场景带软?/ Why does the nadir look softer than the scene band?**
**中:** 物理极限:天底只能被 20–28m 外的相机以 4–6° 掠射角看到,有效地面采样分辨率是分米级——我们把渲染分辨率诚实匹配到证据的光学分辨率(逐行低通),不伪造高频细节。
**EN:** A physical ceiling: the nadir is only ever visible from 20–28 m away at a 4–6° grazing angle, where the effective ground sampling distance is decimetres — so we honestly match the rendering to the evidence's optical resolution (row-weighted low-pass) instead of faking high-frequency detail.

## E. 天空(FLUX.1-Fill + DiT360 LoRA)

**Q: 模型与参数?/ Model and parameters?**
**中:** FLUX.1-Fill-dev(Black Forest Labs,~12B,bf16 推理,A100 单张约 40 秒)+ DiT360 全景 LoRA(Insta360-Research 的 adapter)。mask = 每列从顶部扫到第一个非黑像素的天空帽,9×9 膨胀;guidance 30、40 步、固定种子;原生 1024×2048 直出。
**EN:** FLUX.1-Fill-dev (Black Forest Labs, ~12B, bf16 inference, ~40 s per panorama on an A100) plus DiT360's panorama LoRA (the Insta360-Research adapter). The mask is the sky cap — per column, scan down to the first non-black pixel — dilated 9×9; guidance 30, 40 steps, fixed seed; native 1024×2048 output.

**Q: prompt 是手写的吗?/ Are the prompts hand-written?**
**中:** 自动的:取可见天空带(每列 mask 下方 20 行)的亮像素均值,三分支判别——偏红→dusk、整体亮且偏蓝→sunny、否则 overcast——5 个场景全判对。
**EN:** Automatic: we take the bright-pixel mean of the visible sky band (20 rows below the mask per column) and branch three ways — reddish → dusk, bright and blue-leaning → sunny, otherwise overcast. Correct on all five scenes.

**Q: 为什么不 fine-tune?/ Why no fine-tuning?**
**中:** 全管线零训练是有意选择:几何零可学习参数,三个模型全现成权重。learned 几何路线试过(learned 单中心、VGGT prior),实测更糊更不忠实——确定性、可审计正是世界模型首帧需要的。真要训,最值得的是全景 LoRA 在驾驶天空上微调,但 5/5 视觉过关,还没必要。
**EN:** Zero training is deliberate: the geometry has no learnable parameters and all three models run off-the-shelf. We did test learned-geometry routes (learned single-centre, VGGT priors) — blurrier and less faithful. Deterministic, auditable pixels are exactly what a world-model first frame needs. If we ever train anything, the best candidate is fine-tuning the panorama LoRA on driving skies — but with 5/5 scenes passing visually, it isn't needed yet.

## F. 系统与工程

**Q: 跑一张多久?什么硬件?/ Runtime and hardware?**
**中:** 场景带 + 地面:单 GPU(L4/A100 均可)每场景约 7 分钟,瓶颈在 CPU 几何;天空生成 A100 约 40 秒/张。离线数据生产管线,不是实时系统。
**EN:** Scene band plus ground: about 7 minutes per scene on a single GPU (L4 or A100 — the bottleneck is CPU geometry); sky generation ~40 s per panorama on an A100. It's an offline data-production pipeline, not a real-time system.

**Q: 输出规格 / 数据集?/ Output format and dataset?**
**中:** 1024×2048 ERP;已批产 75 张全景的 AV2 数据集(5 logs × 15 anchors),零失败;完整球面 v8 管线随时可扩产。
**EN:** 1024×2048 ERP; a 75-panorama AV2 set has already been produced (5 logs × 15 anchors, zero failures), and the complete-sphere v8 pipeline can scale it up anytime.

**Q: 已知残留 / 限制?/ Known residuals and limitations?**
**中:** ① 填充区没有接触阴影(按设计未建模,谐和填充缓解);② 天底软(物理极限,见上);③ 个别残留经"原图对照面板"逐一裁定为**源数据本身的内容**(玻璃反光、墙根植被),不是拼接错误。
**EN:** ① No contact shadows in filled regions (unmodeled by design, mitigated by harmonic filling); ② the soft nadir (the physical ceiling above); ③ the few remaining artifacts were adjudicated one by one against the native images and turned out to be **content of the source data itself** (glass reflections, wall-base vegetation) — not stitching errors.

---

## G. FLUX / DiT360 追问专区(中英对照,最容易被连环追问的一组)

**Q1: 天空到底是哪个模型补的?/ Which model actually fills the sky?**
**中:** 干活的引擎是 **FLUX.1-Fill**。我们检测黑天区域生成 mask,喂给 Fill,它负责在 mask 内生成天空、并延续 mask 外的真实云带;补完后 mask 外像素一个字节都不动。
**EN:** The engine is **FLUX.1-Fill**. We detect the black-sky region, build the mask, and feed it to Fill — it generates the sky inside the mask while continuing the real clouds outside it. After filling, every pixel outside the mask stays byte-identical.

**Q2: FLUX 和 FLUX.1-Fill 有什么区别?/ What's the difference between FLUX and FLUX.1-Fill?**
**中:** 同一家族、共享同一个 DiT 骨架,但训练目标不同:**FLUX.1-dev 是文生图**——输入只有文字,从噪声凭空作画;**FLUX.1-Fill 是补全模型**——输入是图 + mask + 文字,训练目标就是"补的内容必须和 mask 外的真实内容无缝衔接"。
**EN:** Same family, same DiT backbone, different training objectives: **FLUX.1-dev is text-to-image** — text in, image from scratch; **FLUX.1-Fill is a completion model** — image + mask + text in, and its training objective is literally "what you fill must seamlessly continue the real content outside the mask."

**Q3: DiT360 的 LoRA 是在哪个底座上训的?为什么能挂到 Fill 上?/ Which base was DiT360's LoRA trained on, and why does it load onto Fill?**
**中:** 在 **FLUX.1-dev(文生图底座)**上训的——DiT360 做的是 text-to-panorama。能跨载是因为 dev 和 Fill **共享同一个 DiT backbone**,LoRA 是按网络层的"插槽"做的,插槽相同就能互插。训练是别人的,移植是我们的。
**EN:** On the **FLUX.1-dev text-to-image base** — DiT360 does text-to-panorama. It cross-loads because dev and Fill **share the same DiT backbone**; a LoRA is shaped to the network's layers, and identical layers mean it plugs into either. They trained it; the transplant is ours.

**Q4: 为什么不用 DiT360 本体?它不是也能做补全吗?/ Why not use DiT360 itself — doesn't it also do completion?**
**中:** 它**结构上就没有补全能力**:文生图底座的网络入口只有文字,**没有接收"已有图像 + mask"的通道**。当年我们硬做过——RF-inversion、init-image、trimap 等外挂技巧,本质是把已有图像当"微弱建议"而不是硬约束,失败实验都归档了:缝隙补全凭空发明小汽车、整图反演把整条街重画、天空补出与场景无关的"明信片天"。**不是 prompt 没调好,是工具的类别错了**。Fill 在每一步去噪时都被强制盯着 mask 外的真实像素——这是结构差异,不是调参差异。
**EN:** Because **architecturally it cannot do completion**: a text-to-image base has no input channel for an existing image and mask. We did force it back then — RF-inversion, init-image, trimap hacks — which treat your image as a weak *suggestion*, not a hard constraint, and the failures are archived: invented cars at seams, whole streets repainted, postcard skies inconsistent with the scene. **It was never a prompt problem — it was the wrong class of tool.** Fill is conditioned on the real pixels outside the mask at every denoising step — that's an architectural difference, not a tuning one.

**Q5: 那 LoRA 在这套里到底干什么?/ So what exactly does the LoRA do here?**
**中:** 注意:**补全(把黑洞填上)从头到尾是 Fill 干的**。LoRA 只负责让生成的内容**长得像合法的球面全景**:极点处的云要横向拉伸、图的最左和最右必须无缝相接。没有它,Fill 会按普通透视照片的逻辑画云,卷回球面就穿帮。一句话:Fill 出手,LoRA 把关几何。
**EN:** To be precise: **the completion itself is entirely Fill's job.** The LoRA only keeps the generated content **on valid spherical (ERP) geometry**: clouds near the pole must stretch horizontally, and the left and right edges must wrap seamlessly. Without it, Fill paints clouds with ordinary-photo logic, which breaks once wrapped back onto the sphere. In one line: Fill does the filling; the LoRA keeps it spherical.

**Q6: 你们自己训练过什么吗?/ Did you train anything yourselves?**
**中:** **全程零训练**。几何部分没有可学习参数;三个模型(YOLOv8-seg、FLUX.1-Fill、DiT360 全景 LoRA)全是现成预训练权重直接推理;也没有任何 fine-tune 和逐场景调参——5 个场景跑同一份代码同一套权重。这是有意选择:learned 几何路线实测更糊更不忠实,而确定性、可审计正是世界模型首帧需要的。
**EN:** **Zero training throughout.** The geometry has no learnable parameters; all three models (YOLOv8-seg, FLUX.1-Fill, DiT360's panorama LoRA) are off-the-shelf pretrained weights run as-is; no fine-tuning, no per-scene tuning — five scenes, one codebase, one set of weights. That's deliberate: learned-geometry routes tested blurrier and less faithful, and deterministic, auditable pixels are exactly what a world-model first frame needs.

---

## 数字弹药库(一眼表)

| 数字 | 含义 |
|---|---|
| 1024×2048 | 输出 ERP 分辨率 |
| 7 / 20Hz / ~300 | AV2 环相机数 / 帧率 / 每 log 帧数 |
| ±22.5ms | 跨相机最大快门差(相邻约 7ms) |
| 0.7m @ 15m/s | 快门差内运动车的位移 |
| 18–96× | 虚拟中心带来的重投影残差降幅 |
| du = +6px | ECC 量出的 OMC 残余位移(BMW) |
| IoU ≥ 0.3 / 位移 > 0.5m | 框-mask 认领阈值 / 运动判定阈值 |
| 5–58m / 5m桶×3帧 | 地面候选帧的几何资格 / 选帧策略 |
| 20–28m @ 4–6° | 内圈天底唯一可见的源距离与掠射角 |
| 94.5–100% | 5 场景地面真实像素覆盖率 |
| guidance 30 / 40 步 / ~40s | 天空生成参数与耗时 |
| 0 | 训练量;逐场景参数数 |
| 5/5 | 场景泛化 / auto-prompt 命中 |
| 75 | 已批产的全景数据集张数 |
