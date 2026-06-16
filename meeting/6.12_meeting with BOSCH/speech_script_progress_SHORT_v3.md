# BOSCH Progress Talk — 短版讲稿 v3(以你自己的叙事为主干,~4 分钟,中文为主 / EN 对照)

> 中文 = 你顺出来的版本(微调了三处技术表述);英文 = 对应口语翻译。
> 每页标了"PPT 对应点",讲到哪句指哪里。

---

## Slide I — 封面 (10s)

**中:** 大家好,快速汇报一下全景项目的进展:把车上 7 个透视相机的图,拼成一张完整的 360° 全景。

**EN:** Hi everyone — quick progress update: turning the car's seven perspective cameras into one complete 360-degree panorama.

---

## Slide II — Dataset (25s)

**中:** 数据用的是 Argoverse 2:7 个环形相机正好全 360° 覆盖,20Hz,还带 LiDAR、3D 框和逐相机时间戳——这个时间戳后面会变得很关键。Waymo 其实有两个数据集,但各缺一半:Perception 版只有 5 个相机、后方有缺口;End-to-End 版有 8 个相机但低分辨率、几乎不重叠、也没有 LiDAR。所以先做 AV2,Waymo 迁移留在路线图上。

**EN:** We use Argoverse 2: seven ring cameras give exactly full 360° coverage, at 20 Hz, with LiDAR, 3D boxes, and per-camera timestamps . i was making a decision between waymo and AV2. Waymo actually has two datasets, each missing half: Perception has only 5 cameras with a rear gap; End-to-End has 8 but low-res, barely overlapping, and no LiDAR. So AV2 first; Waymo migration stays on the roadmap.

> PPT 对应:三个大数字卡(7 / 20Hz / ~300)+ 最后那条 Waymo 两套各缺一半的 bullet。

---

## Slide III — Challenges (30s)

**中:** 拼接难在三件事——这页先把问题摆出来,下一页讲我们怎么一个个挖出来的。第一,相机位置不同,接缝处结构会断;第二,7 个快门不同时,最多差 ±22.5 毫秒,行驶中的车在两次曝光之间真的挪了位置(右边这张图);第三,天和车底的路,所有相机都拍不到,是两块黑洞。

**EN:** 

We do have some challenges on Stitching . Three reasons.

I lay them out here. The first : cameras sit at different positions, so structures break at seams. Secondly : the seven shutters fire up to plus or minus  22.5  milliseconds apart — a moving car genuinely moves between two exposures (that's the diagram). Last : the sky and the road under the car are outside every camera's view — two black holes.

> PPT 对应:三张问题卡 + 右侧快门示意图。

---

## Slide IV — Our Method(核心页,~80s,你的叙事)

**中:** 这部分讲我们怎么走到现在的方案的。一开始,我一直在尝试怎么把拼接的 seam 修补得更好看,试了很多方法——但后来发现,有几个根本问题一直没被解决。
**第一个**,刨根问底才发现:全景的投影球心一直放在 ego origin,而相机实际全装在挡风玻璃上方一个 pod 里,跟球心差得太远,投影误差被放大了。所以把投影中心从 ego 原点挪到 **7 个相机的重心**——就这一个改动,错位降了 18 到 96 倍。
**第二个**是时间:AV2 的 7 台相机快门其实不同时,最多差 ±22.5 毫秒。之前强行都用 anchor timestamp 的那一个 ego pose,但车在动,这直接造成静态物体错位。现在**每台相机用它自己真实曝光时刻插值出来的 ego pose** 来投影,静态世界就对齐了。
**第三个**,接缝处运动车辆还是会出问题——一辆车可能拼出好几个轮子。一开始以为是 parallax,刨根问底发现**本质是快门时间差**:两次曝光之间车自己挪了位置,这不是视差,所以怎么对齐都拼不好。那就干脆**运动车不拼接**——整辆车从单一相机的单一瞬间渲染:YOLO 分割出轮廓、3D 框确认跨相机是同一辆,残余的快门位移用 ECC 配准量出来再补偿。多个车身的问题就从机制上消掉了。
这三步之后就是 PPT 上这行结果:**零逐场景参数,同一份代码 5 个场景全过**。

**EN:** Let me tell you how we got here. At first I kept trying to make the seams *look* better — many methods. But eventually I found the real problems underneath.
**First**, the projection centre had always been at the ego origin, while the cameras physically sit together in a pod above the windshield — far from that centre, which amplified every projection error. Moving the centre to the **cameras' own centroid** — that change cut misalignment by 18 to 96 times.
**Second**,  AV2's seven shutters fire up to ±22.5 ms apart. We used to force one anchor-timestamp ego pose onto all of them — but the car is moving, so static structures misalign. Now **each camera is projected with the ego pose of its own exposure instant**, and the static world lines up.
**Third**, moving cars at seams still broke — one car could get several wheels. We first thought it was parallax;  it's really the **shutter time difference**: the car itself moved between two exposures — that's not parallax, so no alignment could ever fix it. So: i know that **never stitch a moving car** — i choose to render the whole car from a single camera at a single instant: using YOLO segmentation gives the outline, the 3D boxes confirm identity across cameras, and the residual shutter shift is measured by ECC registration and compensated. Multi-body cars are gone by construction.
After these three: the line on the slide — **zero per-scene parameters, the same code passes all five scenes**.

> PPT 对应:①②③三个块逐个指;最后指金勾的 "Zero per-scene parameters" 行和下面的 BMW 全景。

---

## Slide V — Outpainting(~60s,你的叙事)

**中:** 场景带拼完后,就想看看能不能把剩下的黑色部分 outpaint 掉。
**地面**最早用 DiT360 调 prompt,怎么调效果都不好——后来想明白,生成的毕竟是虚构内容,跟真实路面对不上。换个思路:**往前后看几帧,车开过去之前/之后,相机是拍到过现在车底这块路的**——把拍到过的真实路面像素反投影回来就行,完全不需要生成。覆盖 94 到 100%,车道线连续。
**天空**不一样,它是唯一任何帧都没拍到过的区域,只能生成。DiT360 的全景 LoRA 是在 FLUX.1-dev **文生图**底座上训的——他们做的是 text-to-panorama,不是补全,所以直接拿 DiT360 做 outpaint 会虚构内容。

我们的做法是把DiT 360的 LoRA 移植/挂载到 FLUX.1-Fill(补全底座)上,因为两者共享 FLUX 的 DiT backbone,所以能跨载。整个pipline就是, FLUX.1-Fill 负责在黑色天空区域被mask的地方进行生成天空内容, 从DiT 360挂载过去的LoRA 负责让生成的天空符合 ERP 球面几何——极点拉伸、左右边界无缝。补完后 mask 外的像素一个字节都不动。
底下这条就是真实管线:场景带 → 加地面 → 加天空。

**EN:**  This is how i outpaint the black regions.
Act

For the **ground**, we first tried DiT360 with prompt tuning — nothing worked, because generated content is invented; it never matches the real road. Then i realize that why not **looking a few frames forward or back — before/after the car arrived, our cameras did photograph that patch of road.** So we just reproject those real pixels back — no generation at all. So i got 94 to 100% coverage, lane lines continuous.
The **sky** is different  because this is the the only region no frame ever saw, so it must be generated. But inspired by DiT360. it's panorama LoRA was trained on the FLUX **text-to-image** base — they do text-to-panorama, not completion — which is exactly why DiT360 outpainting hallucinates.(This is the production of original DiT 360 ) What we did is **transplant DiT360's LoRA onto FLUX.1-Fill, the inpainting base** — they share the same FLUX DiT backbone, so the LoRA cross-loads. The whole pipeline is: **FLUX.1-Fill generates the sky content inside the masked black-sky region, and the LoRA mounted from DiT360 keeps the generated sky on ERP spherical geometry** — pole stretching, seamless left-right wrap. After filling, every pixel outside the mask stays byte-identical — not a single byte is touched.

> PPT 对应:左栏 GROUND(INPUT/Method/OUTPUT)→ 右栏 SKY → 底部真实图流程条。

---

## Slide VI — Results (20s)

**中:** 这是目前的成果:多场景多天气的完整球面——downtown 自动判成黄昏、bmw 晴天、highway 鱼鳞云无缝延续。场景带和地面是 100% 真实像素,天空是唯一生成层,每个像素的来历都有记录。

**EN:** Here's where we are: complete spheres across scenes and weathers — downtown auto-detected as dusk, bmw sunny, highway's cloud field continued seamlessly. Scene band and ground are 100% real pixels; the sky is the single generated layer — every pixel's provenance is documented.

---

