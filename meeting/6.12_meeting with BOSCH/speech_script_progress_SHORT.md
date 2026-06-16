# BOSCH Progress Talk — 短版讲稿(~3 分钟,EN / 中文对照)

---

## Slide I — Title (10s)

**EN:** Quick update on the panorama project — from the car's perspective cameras to a **complete 360° panorama**.

**中:** 全景项目快速更新——从车载透视相机到**完整 360° 全景**。

---

## Slide II — Dataset (20s)

**EN:** We use **Argoverse 2**: 7 ring cameras, full 360°, 20 Hz, plus LiDAR, 3D boxes and per-camera timestamps. Waymo's two datasets each miss half of that — 5 cameras with a rear gap, or 8 low-res cameras with no LiDAR — so AV2 first; Waymo migration stays on the roadmap.

**中:** 我们用 **AV2**:7 环相机全 360°、20Hz,带 LiDAR、3D 框和逐相机时间戳。Waymo 两个数据集各缺一半(5 相机后方缺口 / 8 个低清相机但没 LiDAR),所以先 AV2,Waymo 迁移在路线图上。

---

## Slide III — Challenges (25s)

**EN:** Three reasons stitching is hard: cameras sit at **different positions** — seams break; they fire at **different instants** — up to ±22.5 ms apart, so a moving car is photographed **in two places**, which no alignment can fix; and **sky and ground** are outside every camera's view.

**中:** 拼接难有三个原因:相机**位置不同**——接缝断裂;**时刻不同**——快门相差 ±22.5ms,行驶的车被拍在**两个地方**,对齐治不了;以及**天和地**所有相机都拍不到。

---

## Slide IV — Method (45s)

**EN:** We removed each cause instead of hiding seams. **One:** project from the **cameras' centroid** instead of the vehicle origin — residuals drop 18–96×. **Two:** project each image with the ego pose of **its own exposure instant** — the static world aligns exactly. **Three:** each moving object is rendered **whole, from one camera at one instant** — segmentation gives the mask, 3D boxes the identity, ECC measures the residual shift — so ghosted or seam-cut cars are **impossible by construction**. Zero per-scene parameters; all five scenes pass with the same code.

**中:** 我们不藏缝,逐个消灭成因。**一**:从**相机重心**投影而非车辆原点——残差降 18-96 倍。**二**:每张图用**它自己曝光时刻**的位姿投影——静态世界完全对齐。**三**:运动物体整辆从**单一相机单一瞬间**渲染——分割给轮廓、3D 框给身份、ECC 量残余位移——鬼影车和被切开的车**机制上不可能出现**。零逐场景参数,同一份代码 5 场景全过。

---

## Slide V — Completing the Sphere (30s)

**EN:** The two black regions get opposite treatments. **Ground: no generation** — the road under the car was photographed by our own cameras seconds earlier or later; we reproject those **real pixels**, 94–100% coverage. **Sky** is the only region no camera ever saw, so it's the only generated layer — FLUX.1-Fill diffusion inpainting; everything outside the sky mask stays **byte-identical**.

**中:** 两块黑区,待遇相反。**地面:零生成**——车底的路几秒前后被自己的相机拍到过,反投影**真实像素**回来,覆盖 94-100%。**天空**是唯一没人拍到过的区域,也是唯一生成层——FLUX.1-Fill 扩散补全;天空 mask 之外**字节级不变**。

---

## Slide VI + VII — Results & Next (30s)

**EN:** Results: complete spheres across scenes and weathers — dusk auto-detected on downtown, sunny on bmw — with every pixel's provenance documented. Next: **Waymo migration** as the generalization gate, the format contract with the world-model team, and scaling up the dataset. Questions welcome.

**中:** 成果:多场景多天气完整球面——downtown 自动判黄昏、bmw 晴天——每个像素来历可查。下一步:**Waymo 迁移**当泛化闸门、与世界模型团队定格式契约、数据集扩产。欢迎提问。
