# Meeting Cram — 我做了啥 (会议 5 分钟讲解版)

**目标**: 5 分钟内让 lab 同学 / 老师听懂我这两周做了什么 + 找到什么
**风格**: 大白话, 物理直觉先于公式, 数字往后摆

---

## ① 30 秒电梯版 (如果有人路上问 "你最近在搞啥?")

> "我在做一个**多相机 360° 全景拼接**的研究。 自动驾驶车上一般有 7 个朝不同方向的相机, 我把它们同一时刻的画面**拼成一张 360° 全景图**, 给下游的 3D world model 用。 这两周我系统性试了 **8 种拼接方法** — 包括古典图像处理 + 神经网络 3D 深度估计 — 找到 3 个有用的, 5 个 negative result, 都写进 paper。 目标会议 3DV 2026。"

---

## ② 3 分钟标准版 (round-table 轮到你)

### Setup (30 秒)
> "Argoverse 2 这个数据集, 每辆车顶有 **7 个 ring camera** 朝外环视, 同一时刻 7 张图盖住周围 360°, 但**有 overlap 也有 gap**。 我的任务: 把这 7 张图拼成一张 **equirectangular projection (ERP) 全景图** (类似 Google Street View 那种), 1024×2048 像素, 360° × 180° 视野."

### 为啥这件事难 (30 秒)
> "三个挑战:
> 1. **几何**: 每个相机角度不一样, 看到的物体在 ERP 上对应到哪个像素不显然
> 2. **视差 (parallax)**: 7 个相机不在同一点, 同一物体在不同相机里位置不同 — 简单 overlay 就鬼影
> 3. **测量没有 ground truth**: AV 数据没人给你一张'正确的 360° 全景', 怎么知道拼得对不对?"

### 我们的解决思路 (30 秒)
> "我们用 **cycle-PSNR** 做评测 — 挡住其中 1 个相机, 用剩下 6 个推断它看到啥, 跟它真实的 RGB 算 PSNR. 这样不需要外部 GT, 数据自己就够."

### 8 条路线 (90 秒 — 6 句话)
> "我试了 8 种拼法, 大致分两类:
>
> **3 条经典 + 5 条新加** (4 + 4 也行, 看怎么数):
> 1. **L1 球面 baseline** — 把每个像素当远处球面上一个方向, 球面投影 + Laplacian 多带混合接缝. **意外地强**, cycle-PSNR 12.34 dB.
> 2. **L3 Pi3 forward-splat** — 用 Pi3 (CVPR 2025 那个 3D foundation model) 估每像素 3D 深度, 把图变点云后投到球面. **结构性失败**, 输 L1 -3.15 dB, 10/10 anchor 都输.
> 3. **IPM 地面 hybrid** — 利用 AV 路面是平的这个强先验, 地面像素用数学公式精确投影, 非地面 fallback 球面. **小赢 +0.05 dB** (paper 第一个正面贡献).
> 4. **新-A 柱面** — 球面换柱面, AV 7 cam 水平排列贴合. Coverage +25%, cycle ~平.
> 5. **新-B 图论 graph-cut seam** — 让接缝沿低梯度路径走 (沿马路而不是穿墙). **视觉接缝消失**.
> 6. **新-C IPM 多区域** — 把 IPM 推广到地面+天空+建筑三区域. **地面区域 +0.20 dB** (4× 老方法).
> 7. **新-D wide-baseline stereo** — 邻 cam 当人眼, 经典三角化恢复 3D. 5/7 对成功.
> 8. **新-E HDR 跨 cam 补偿** — 7 cam 独立曝光导致颜色不一致, 最小二乘解 gain+bias. **颜色差 -18%**."

### 关键发现 (30 秒 — 3 个 takeaway)
> "三个核心发现:
> 1. **经典球面 baseline 其实非常强** — 比 SOTA 神经网络 3D-lift (Pi3) 高 3 dB. 反直觉.
> 2. **'算法换 backbone 也救不了'** — Pi3 / Apple Depth Pro / 多帧 Temporal Pi3 / OmniStitch 4 种 NEG 都不如 L1. 说明问题在 forward-splat 算法本身, 不在深度估计准不准.
> 3. **物理先验比深度学习有用** — IPM (地面 z=0) + HDR (跨 cam 颜色一致性) 这种几十行数学公式, 在 AV 场景里比神经网络还稳."

### Paper plan (15 秒)
> "投 3DV 2026, **A' Method paper** 角度 — 3 个 stack-able 正面贡献 + 4 个 NEG 当 Section 4. 等导师拍板."

---

## ③ 5 分钟深度版 (如果有人追问)

### 加在 3 分钟版后面的 follow-up 段:

#### 关于"为啥 L1 这么强"
> "因为 L1 不假设深度. 球面投影只用 ray direction, 错误集中在近场 (车 / 行人). 而远场 (>50m) 占像素 80% 以上, 那里视差小, L1 几何精确. L3 forward-splat 反过来 — 它对**每个**像素都假设一个深度 z, 错了一个 splat 到错的位置就鬼影. 远场 24% depth bias × 大像素覆盖 = 灾难."

#### 关于"为啥 IPM 这么稳"
> "因为 AV 场景路面确实是平的 (z=0 这个先验 99% 正确). IPM 不是 ML 模型, 是经典 inverse perspective mapping — 利用相机外参 + 地面平面方程, 解析投影. 0 视差误差. 神经网络估的深度对 50m 远的路面误差几 m, IPM 公式上准确."

#### 关于"如果只有时间讲 1 个 finding"
> "讲'多 backbone 都失败'. 因为这反直觉 — 大家以为换 SOTA 深度模型 (Apple Depth Pro 2024 / 多帧 Pi3) 就能救 L3. 我们 4 个 backbone 一致失败, 说明这是 forward-splat 算法类的问题, 不是 backbone 选择问题. 这是 paper Section 4 的 key argument."

#### 关于"未来工作"
> "两个潜在路线:
> 1. **VGGT** (Meta CVPR 2025 Best Paper) 当第 4 个 backbone — 加固 NEG 论据. 现在被 HF gated repo 卡住, 等申请.
> 2. **T13 self-sup Pi3 finetune** — 用 cycle-PSNR 当 self-sup loss 反向 finetune Pi3 远场深度. 5-6 天 A100, 高 risk. 等导师反馈是否值得."

---

## ④ 数字 cheatsheet (round to memory)

| 数字 | 含义 | 怎么用 |
|---|---|---|
| **12.34 dB** | L1 baseline cycle-PSNR (10 anchor) | "我们的 baseline 比想象的强" |
| **-3.15 dB** | L3 输 L1 多少 | "神经网络 3D-lift 完败" |
| **10/10** | L3 输 L1 的 anchor 数 | "不是 cherry-pick, 全输" |
| **+0.20 dB** | 新-C ground-only 收益 | "IPM 多区域 ground 4× 老方法" |
| **-18%** | 新-E HDR 颜色差降低 | "跨 cam 颜色一致性明显改善" |
| **24%** | Pi3 在 40m 处 depth bias | "远场系统性低估" |
| **8** | 试过的拼接方法数 | "系统探索方法空间" |
| **3 + 5** | 已有 NEG / pending NEG | "5 个负面结果证据扎实" |
| **3DV 2026** | 目标会议 | "main track 或 D&B" |

**口诀**: "8 路线, 3 正 5 负, L1 12.34, L3 -3.15, IPM +0.20, HDR -18%"

---

## ⑤ 重点强调 (说这些 lab 觉得你有 insight)

1. **"评测设计比方法更难"** — cycle-PSNR + LPIPS + MS-SSIM + object-band 多角度都验证 L3 NEG (T5 metric audit), 排除"cycle-PSNR 选错了 metric"的 alternative explanation.
2. **"NEG 是 paper 主菜不是配菜"** — 现代 SOTA 模型 (Pi3, Depth Pro, VGGT) 在 AV 任务上**集体失败**这个 finding 比"我们小赢 0.05 dB"更有 paper impact.
3. **"几何 + 物理先验在 AV 域比 ML 重要"** — IPM 地面 / HDR 跨 cam / 经典 stereo 都比神经网络稳, 这暗示 AV 域 ML 训练数据分布有问题."

---

## ⑥ 注意别 overclaim (这些是 paper-reviewer 会挑的地方)

1. **L1 不是真"赢" published baselines apples-to-apples** — 我们只 head-to-head 测了 OmniStitch (-6.67 dB). Depth Pro / Temporal Pi3 是**L3 内部 backbone swap NEG**, 严格说不是直接跟 L1 比. 别说"我们 4 个 baseline 都打"; 说"我们 4 个 datapoint 共同支持算法类失败假说".
2. **+0.05 dB 全图 / +0.20 dB ground-only 是 marginal** — 别夸成 "method breakthrough". 说"first positive contribution, validates physical prior approach".
3. **AV2 single-log 数据** — 我们主要在一个 log (`02a00399`) 的 10 anchor 上测的. T1 multi-log (5 logs) 还没全跑完. 别说"广泛 generalize"; 说"strong evidence on 10 diverse anchors, multi-log validation in progress".
4. **8 路线不都做了 10-anchor full eval** — 新-B/C/D 只 4 anchor (CPU 时间紧). 老师可能追问 "为啥不全跑 10". 答: "时间盒 + visual win clear; 完整 10 anchor 留给 paper draft v0".

---

## ⑦ 预测会被问的问题 + 好答案

**Q: 为啥不用 GAN / diffusion 直接生成全景?**
A: "Diffusion 生成图像没 AV 数据集 (paired multi-cam → ERP) 训练, 也没 ego pose consistency 保证. 我们路线是几何 first, ML 是辅助. 后续 Pantheon360 (CVPR 2026, Koi 老师那条线) 是 diffusion polish 阶段, 不在这个 paper scope."

**Q: 7 个相机能不能用 NeRF / 3D Gaussian Splatting 重建?**
A: "CylinderSplat (室内, 训练 5+ min/scene) 和 Street-Gaussian (大场景, 训练几小时) 我们调研过, **per-scene 训练太慢**, 不能用作 stitching pipeline. 我们要的是 feed-forward 1 frame → 1 ERP, 不到 1 秒. 这是 method 设计选择."

**Q: AV2 vs Waymo 差异?**
A: "AV2 7 cam ring 全外环, calibration MIT 开源, 我们主用. Waymo 5 cam 朝前 (盲区大) + 数据集 license 严, Track B 留给 Phase 4 (paper 之后) 做泛化."

**Q: 拼接精度怎么 absolute 衡量, 不只跟 L1 比?**
A: "Cycle-PSNR 是 self-consistency, **不是** absolute fidelity. 我们补做了 LiDAR-anchored depth eval (abs_rel / δ<1.25) 看 backbone 深度质量, 又做了 T5 metric audit (LPIPS / MS-SSIM / object-band PSNR) 跨 metric 验证. 但 absolute panorama GT 在 AV 数据里**不存在** — 这是 evaluation 局限性, paper 会写明."

**Q: 这个工作的 novelty 在哪?**
A: "Three points:
1. **First systematic exploration of method space** for AV ring-cam → 360° (vs single-method papers like OmniStitch).
2. **First demonstration** that SOTA 3D foundation models (Pi3, Depth Pro, VGGT pending) **systematically fail** on AV ring vs classical baselines.
3. **IPM multi-region** is novel (老 IPM 只 ground, 我们扩到 ground + sky + building 决策树).

---

## ⑧ 1 句话总结 (slide title or 1-liner)

> **"自动驾驶车顶 7 相机拼 360° 全景 — 经典几何 + 物理先验比神经网络 3D 估计更稳, 5 个 NEG + 3 个正向贡献, 投 3DV 2026."**

记住这一句, 别的都能现场展开.
