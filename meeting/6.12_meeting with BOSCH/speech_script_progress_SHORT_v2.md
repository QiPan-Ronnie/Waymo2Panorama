# BOSCH Progress Talk — 短版讲稿 v2(~4 分钟,口语化 + 衔接完整,EN / 中文对照)

> 比 v1 多了过渡句和口语节奏,每页开头都接住上一页。斜体 = 过渡句。

---

## Slide I — Title (15s)

**EN:** Hi everyone. This is a quick progress update on the panorama project. The goal in one sentence: take the car's seven perspective cameras, and produce **one complete 360-degree panorama** — good enough to serve as the first frame for a world model. Let me walk you through where we are.

**中:** 大家好,这是全景项目的快速进展汇报。目标一句话:拿车上 7 个透视相机,产出**一张完整的 360° 全景**——质量要够给世界模型当首帧。下面带大家过一遍现状。

---

## Slide II — Dataset (30s)

**EN:** First, the data. We're building on **Argoverse 2**, and the reason is simple: it's the only dataset where everything we need comes together — seven ring cameras covering the full 360, twenty hertz, plus LiDAR, 3D boxes, and per-camera timestamps. *You might ask, why not Waymo?* Well, Waymo actually has two datasets, and each is missing half: the Perception set has the LiDAR and labels but only five cameras — there's a gap at the rear. The newer End-to-End set does have eight cameras, but they're low-resolution, barely overlapping, and it ships **without LiDAR or labels**. So we start on AV2 — and Waymo stays on the roadmap as our generalization test.

**中:** 先说数据。我们基于 **Argoverse 2**,原因很简单:它是唯一一个把我们需要的东西凑齐的数据集——7 个环形相机全 360°、20Hz,外加 LiDAR、3D 框和逐相机时间戳。*可能有人会问,为什么不用 Waymo?* Waymo 其实有两个数据集,各缺一半:Perception 版有 LiDAR 和标注,但只有 5 个相机——后方有缺口;新的 End-to-End 版倒是有 8 个相机,但分辨率低、几乎不重叠,而且**不带 LiDAR 和标注**。所以先做 AV2,Waymo 留在路线图上当泛化性检验。

---

## Slide III — Challenges (40s)

**EN:** *So why is this hard? Why not just stitch like a phone does for a panorama?* Because three physical things work against us.
First, the cameras sit at **different positions** — each one sees the world from its own spot, so when you merge them, walls and curbs **break at the seams**.
Second — and this is the one that took us longest to even *see* — the cameras fire at **different instants**, up to twenty-two milliseconds apart. Sounds tiny, right? But at city speed, a moving car travels **seventy centimeters** between two cameras' exposures. So in our data, that car genuinely **is in two different places**. That's not an alignment problem — that's a *time* problem.
And third, simply: nobody photographs the sky straight up or the road under the car. Those are black holes we have to fill.

**中:** *那为什么这事难?手机拍全景不就是拼一下吗?* 因为有三个物理因素跟我们作对。
第一,相机**位置不同**——每台从自己的位置看世界,合并时墙和路缘会**在接缝处断开**。
第二——这个是我们花了最久才*看见*的——相机**开火时刻不同**,最多差 22 毫秒。听起来很小对吧?但市区车速下,一辆行驶中的车在两台相机曝光之间跑了 **70 厘米**。所以在我们的数据里,那辆车**真的在两个不同的地方**。这不是对齐问题,是*时间*问题。
第三,很朴素:没有相机拍正上方的天和车底的路。这两块黑洞得我们自己填。

---

## Slide IV — Method (60s,核心页)

**EN:** *Now, how do we deal with all three? The short answer: we stopped hiding seams, and removed their causes instead.* Three fixes, one per cause.
**Fix one.** All our earlier versions projected from the vehicle's origin — and honestly, nobody had ever questioned that. But the cameras physically sit together in a pod above the windshield. Once we moved the projection centre to the **cameras' own centroid**, the misalignment dropped by a factor of **eighteen to ninety-six**. Pure geometry — one principled change.
**Fix two.** Since the shutters don't fire together, we stop pretending they do: every image gets projected with the vehicle pose of **its own exposure instant** — just interpolating the pose to the right millisecond. After that, the static world lines up exactly.
**Fix three** is for the moving cars, where no alignment can ever help — remember, the car really is in two places. So the rule is: **never stitch a moving object**. Each one gets rendered **whole, from one camera, at one instant** — segmentation gives us its outline, the 3D boxes tell us it's the same car across views, and a small registration step measures the leftover shift and compensates it. The nice part: ghosted cars and cars cut in half by a seam aren't just *rarer* — they're **impossible by construction**.
And all of this runs with **zero per-scene parameters** — the same code passes all five test scenes.

**中:** *那这三个问题怎么办?简短的答案:我们不再藏缝,改为消灭缝的成因。* 三个修复,各对一个原因。
**第一招。** 之前所有版本都从车辆原点投影——说实话,从来没人质疑过这件事。但相机物理上就装在挡风玻璃上方一个 pod 里。把投影中心挪到**相机自己的重心**之后,错位直接降了 **18 到 96 倍**。纯几何,一个原理性改动。
**第二招。** 既然快门不同时开,我们就不再假装同时:每张图用**它自己曝光那一毫秒**的车辆位姿来投影——就是把位姿插值到正确的时刻。这之后,静态世界完全对齐。
**第三招**对付运动的车——这里任何对齐都没用,记住,它真的在两个地方。所以规则是:**永远不拼接运动物体**。每辆车整个从**一台相机的一个瞬间**渲染——分割给轮廓,3D 框确认跨相机是同一辆,再用一步小配准量出残余偏移并补偿。最妙的是:鬼影车和被缝切成两半的车不是*更少见了*,而是**机制上不可能出现**。
而且这一切**零逐场景参数**——同一份代码,5 个测试场景全部通过。

---

## Slide V — Completing the Sphere (40s)

**EN:** *With the scene band solved, two black regions remain — the ground and the sky. And we treat them in opposite ways, depending on what evidence exists.*
For the **ground**, we don't generate anything. Here's the trick: the road under the car *right now* — our own cameras photographed it **a few seconds ago**, when the car hadn't arrived yet. So we just reproject those **real pixels** back, with occlusion checks. Ninety-four to a hundred percent coverage, real road.
The **sky** is different — it's the *only* region no camera ever saw, at any time. So that's the only place we allow generation: a diffusion inpainting model continues the real, observed clouds into the gap — and everything outside the sky mask stays **byte-for-byte untouched**.
*The strip at the bottom is the actual pipeline on real data: scene band — plus ground — plus sky.*

**中:** *场景带解决之后,还剩两块黑——地面和天空。我们按"存在什么证据"反着处理这两块。*
**地面**,我们什么都不生成。诀窍在这:*现在*车底下的路——**几秒钟之前**车还没开到这儿时,我们自己的相机拍到过它。所以只要把那些**真实像素**反投影回来,加上遮挡检查。覆盖 94 到 100%,真实路面。
**天空**不一样——它是*唯一*任何时刻都没有任何相机拍到过的区域。所以只有这里允许生成:用一个扩散补全模型,把真实观测到的云延续进缺口——而天空 mask 之外的一切**一个字节都不动**。
*底下这条就是真实数据上的完整管线:场景带——加地面——加天空。*

---

## Slide VI — Results (25s)

**EN:** *And here's where we are today.* Complete spheres across multiple scenes and weathers — downtown was auto-detected as dusk, bmw came out sunny, and on the highway the cloud field continues seamlessly. The key property: the scene band and the ground are **one hundred percent real pixels**, and the sky is the single generated layer — every pixel's origin is documented.

**中:** *这就是今天的进展。* 多场景多天气的完整球面——downtown 自动判成黄昏,bmw 是晴天,highway 的鱼鳞云无缝延续。关键性质:场景带和地面是 **100% 真实像素**,天空是唯一的生成层——每个像素的来历都有记录。

---

## Slide VII — Next (20s)

**EN:** *So, what's next?* Three things: the **Waymo migration**, which is our real generalization test; settling the format contract with the world-model team who'll consume these as first frames; and scaling up — we already have a 75-panorama set, and we'll extend it with the full pipeline. That's the update — happy to take questions.

**中:** *接下来做什么?* 三件事:**Waymo 迁移**——真正的泛化性检验;和把这些当首帧用的世界模型团队敲定格式契约;以及扩产——75 张的全景集已经在手,会用完整管线扩展。汇报就到这,欢迎提问。
