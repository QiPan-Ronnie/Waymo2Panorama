# BOSCH Progress Talk — Speech Script (EN / 中文对照)

> 全程约 6 分钟。每页先英文(实际讲的),后中文(意思对照)。粗体 = 建议口头重读。

---

## Slide I — Title (15s)

**EN:** Hi everyone. Quick progress update on the panorama project: turning the car's perspective cameras into a **complete 360-degree panorama**. I'll cover the dataset, what makes this hard, our method, and where we are now.

**中:** 大家好,这是全景项目的进度汇报:把车上的透视相机变成**完整的 360° 全景图**。我会讲数据集、难点在哪、我们的方法、以及目前进展。

---

## Slide II — Dataset (40s)

**EN:** We work on **Argoverse 2**: seven ring cameras giving full 360-degree coverage, at 20 hertz, about 300 frames per log — plus LiDAR, 3D boxes, and **per-camera capture timestamps**, which turned out to be crucial later.
Why not Waymo? Waymo has two datasets and each is missing half of what we need: the Perception set has LiDAR and boxes but only **5 cameras with a rear gap** — no full ring; the new End-to-End set has 8 cameras, but it's 10 hertz, low resolution, only 1–2 degrees of overlap, rolling shutter, and **camera-only** — no LiDAR or boxes. AV2 is the only one with everything together. Waymo migration stays on our roadmap as the generalization test.

**中:** 我们用 **Argoverse 2**:7 个环形相机全 360° 覆盖、20Hz、每段约 300 帧,还有 LiDAR、3D 标注框和**逐相机的拍摄时间戳**——后面会看到这个时间戳变得多关键。
为什么不用 Waymo?Waymo 有两个数据集,各缺一半:Perception 版有 LiDAR 和标注但只有 **5 个相机、后方有缺口**,拼不出全环;新的 End-to-End 版有 8 个相机,但 10Hz、低分辨率、相邻只有 1-2° 重叠、卷帘快门、而且只有相机数据。只有 AV2 全凑齐了。Waymo 迁移仍在路线图上,作为泛化性检验。

---

## Slide III — Challenges (45s)

**EN:** Why is stitching hard? **Three physical reasons.**
First, **different positions** — each camera sits at a different spot, so the same building lands in slightly different places; structures break at seams and overlaps ghost.
Second — and this one is sneaky — **different instants**. The seven shutters fire up to **±22.5 milliseconds apart**. At 15 meters per second, a moving car travels 0.7 meters between two cameras' exposures. So the car genuinely **is in two places**. That's a *time* problem — no amount of alignment can fix it.
Third, **missing coverage**: the sky above and the ground under the car are outside every camera's view — black holes.

**中:** 为什么拼接难?**三个物理原因**。
第一,**位置不同**——每台相机装的位置不一样,同一栋楼在各自照片里的位置略有偏差;接缝处结构断裂、重叠区出鬼影。
第二个最隐蔽:**时刻不同**。7 个快门相差最多 **±22.5 毫秒**。车速 15m/s 时,一辆行驶中的车在两台相机曝光之间挪了 0.7 米——它**真的在两个地方**。这是*时间*问题,任何对齐都治不了。
第三,**覆盖缺失**:头顶的天和车底的路,所有相机都拍不到——两块黑洞。

---

## Slide IV — Our Method(核心页,~75s)

**EN:** Our approach: **don't hide the seams — remove their causes.** Three fixes, one per cause.
**Fix one, the virtual centre.** Every earlier version projected from the vehicle origin — nobody had questioned it. But the cameras actually sit together in one pod above the windshield. We moved the projection centre to the **cameras' own centroid** — pure multi-view geometry, one principled change — and projection residuals dropped **18 to 96 times**. Static structure snaps into place.
**Fix two, per-camera shutter poses.** Since the cameras fire at different instants, we stop pretending they're simultaneous: each image is projected with the ego pose **interpolated at its own exposure timestamp** — standard SE(3) interpolation. After this, the static world aligns exactly.
**Fix three, for moving objects: one body, one camera, one time.** A moving car can never be stitched from two cameras — so we never do. Each moving object is rendered **whole, from a single camera at a single instant**: YOLOv8 segmentation gives its pixel mask, the 3D boxes tell us it's the same car across cameras, and the residual shutter shift is **measured by ECC image alignment** and compensated. Ghosted or seam-cut cars become **impossible by construction**, not just unlikely.
One design rule throughout: **never average geometry** — every pixel comes from exactly one camera at one instant, so the result is deterministic and auditable. Same code, **zero per-scene parameters**, passes all five test scenes — and degrades gracefully if LiDAR is removed.

**中:** 我们的思路:**不藏缝,消灭缝的成因**。三个修复,各对一个原因。
**第一招,虚拟中心。** 之前所有版本都从车辆原点投影——没人质疑过。但相机其实都装在挡风玻璃上方一个 pod 里。我们把投影中心挪到**相机自己的重心**——纯多视几何,一个原理性改动——投影残差直接降 **18 到 96 倍**,静态结构对齐了。
**第二招,逐相机快门位姿。** 既然相机不同时拍,就不再假装同时:每张图用**它自己曝光时刻**插值出的车辆位姿来投影(标准 SE(3) 插值)。这之后,静态世界严丝合缝。
**第三招,对运动物体:一体、一相机、一时刻。** 运动的车永远不可能由两台相机拼出来——那我们就**不拼**:每个运动物体整个从**单一相机的单一瞬间**渲染——YOLOv8 分割给轮廓,3D 框告诉我们跨相机是同一辆车,残余的快门位移用 **ECC 图像配准量出来**再补偿。鬼影车和被缝切开的车**从机制上不可能出现**,不是碰运气。
贯穿始终的一条设计铁律:**绝不平均几何**——每个像素都来自确定的一台相机一个时刻,结果是确定性的、可审计的。同一份代码、**零逐场景参数**,5 个测试场景全部通过,去掉 LiDAR 也能优雅降级。

---

## Slide V — Completing the Sphere (50s)

**EN:** That gives us the scene band. Two regions remain black — and we treat them **very differently**, by what evidence exists.
**The ground: real pixels, no generation.** The road under the car *now* was photographed by our own cameras **a few seconds earlier or later**, when the car was somewhere else. So we reproject those real pixels back — occlusion-checked. Output: the bottom filled with **94 to 100 percent real pixels**, lane lines continuous through the nadir, ego hood removed.
**The sky is the only region no camera ever saw** — so it's the only place we allow generation: FLUX.1-Fill, a mask-conditioned diffusion inpainter, with a panorama LoRA for spherical geometry. It continues the *observed* clouds into the gap, and **every pixel outside the mask stays byte-identical**.
The strip at the bottom shows the real pipeline: scene band, plus ground, plus sky.

**中:** 上面给了我们场景带,还剩两块黑——我们按"有什么证据"**区别对待**。
**地面:真实像素,零生成。** 现在车底下的路,**几秒前后**车在别处时被我们自己的相机拍到过——把那些真实像素反投影回来(带遮挡检查)。结果:下半球 **94-100% 真实像素**,车道线连续穿过天底,自车引擎盖移除。
**天空是唯一所有相机都没拍到过的区域**——所以也是唯一允许生成的地方:FLUX.1-Fill(mask 条件扩散补全)加全景 LoRA 保证球面几何,把*观测到的*云延续进缺口,**mask 之外每个像素保持字节级不变**。
底部这条就是真实管线:场景带 → 加地面 → 加天空。

---

## Slide VI — Results (25s)

**EN:** Current results: complete spheres on multiple scenes and weathers — downtown auto-detected as **dusk**, bmw sunny, highway with the cloud field continued seamlessly. Scene band and ground are 100 percent real pixels; sky is the only generated layer — every pixel's provenance is documented.

**中:** 当前成果:多场景多天气的完整球面——downtown 自动判为**黄昏**、bmw 晴天、highway 鱼鳞云无缝延续。场景带和地面是 100% 真实像素,天空是唯一生成层——每个像素的来历都有记录。

---

## Slide VII — Next (20s)

**EN:** Next: **Waymo migration** as our generalization gate; settling the centre and format **contract with the world-model team** that consumes these as first frames; and scaling — a 75-panorama AV2 set already exists, we'll extend it with the complete-sphere pipeline. That's it — happy to take questions.

**中:** 下一步:**Waymo 迁移**作为泛化性闸门;和消费这些首帧的**世界模型团队敲定中心与格式契约**;以及扩产——75 张全景的 AV2 数据集已经在手,会用完整球面管线扩展。汇报完毕,欢迎提问。

---

## 可能的提问 & 一句话回答

- **Q: Why not learn this end-to-end? / 为什么不端到端学习?**
  EN: We tested learned routes — they were softer and less faithful. Classical geometry gives deterministic, auditable pixels, which the world-model consumer needs. Learning is used only where it belongs: segmentation and the sky.
  中: 学习路线试过,更糊更不忠实。经典几何给出确定性、可审计的像素,这正是世界模型下游需要的。学习只用在它擅长的两处:分割和天空。
- **Q: Runtime? / 跑一张要多久?**
  EN: Minutes per panorama on a single GPU — it's an offline data-production pipeline, not a real-time system.
  中: 单 GPU 每张几分钟量级——这是离线数据生产管线,不是实时系统。
- **Q: How hard is the Waymo migration? / Waymo 迁移难度?**
  EN: The algorithm is dataset-agnostic by design — the open question is whether loader-level changes suffice; that's exactly what the migration will test.
  中: 算法设计上与数据集无关——悬念只在 loader 层改动是否够用,迁移本身就是来验证这件事的。
