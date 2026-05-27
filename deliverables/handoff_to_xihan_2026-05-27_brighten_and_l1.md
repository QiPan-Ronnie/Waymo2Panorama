# Handoff to Xihan — L1 sphere principle + Waymo brighten

**Date**: 2026-05-27
**作者**: Qi + agent (AV2 side)
**对应 ask**: `meeting/5.22_meeting with xihan/xihan/xihan task.md`

---

## TL;DR

1. **L1 sphere baseline 原理** 已经写完, 完整文档 → `deliverables/l1_sphere_principle.md`. 包括 ERP 坐标系约定 / sphere ray-cast / multiband blend / 远场对近场错的数学推导 / Waymo 移植注意点 + Quick eval 协议.
2. **2 个新 AV2 L1 范例** → `deliverables/xihan/l1_examples_panel.png` (clean far-field + ghost near-field 对照).
3. **Waymo panorama brighten** 已经在你给的 panorama (`c4b1d01f...jpg`, 4096×2048) 上跑通: **seam |ΔY| mean 40.86 → 33.36 = -18%**. 视觉上中间过曝 cam 被压下来, 8 个 cam 区域曝光更一致. 方法可以 drop-in 到你的 pipeline.
4. ORB 部分我们 5.22 之前 3 个 hybrid attempt 全 NEG, 那条路死了, 见 §5.

---

## 1. L1 sphere baseline 原理 (回应 task §1)

完整文档单独写在 `deliverables/l1_sphere_principle.md`. 8 个 section 简介:

| § | 内容 |
|---|---|
| 0 | TL;DR + 优缺点表 |
| 1 | ERP 坐标系约定 (θ, φ 公式, AV2 ego 右手系, **镜像 bug 提醒**) |
| 2 | sphere_projection 的 7 步 (网格 → 单位 ray → 旋转 → pinhole → remap → cos² weight) |
| 3 | multiband_blend (Laplacian 金字塔 + ERP horizontal wrap 处理) |
| 4 | 整个 L1 pipeline = 7 个 sphere project + 1 个 multiband |
| 5 | **为什么远处对、近处错** — 视差 α≈b/d, 3m 处的 BMW 在 2048-宽 ERP 上错位 54 px (= 双轮子的数学根因) |
| 6 | 2 个新范例 + 比对总结 |
| 7 | Waymo 移植注意点 (5 项) |
| 8 | Quick eval 协议 (单 cam smoke → 2 cam pair → 8 cam full → NCC 量化) |

代码索引: `code/waymo2panorama/projection/sphere_projection.py` + `code/waymo2panorama/blending/multiband.py`.

### 1.1 Waymo 移植 5 个坑 (从 §7 摘出, 你最容易踩)

1. **坐标系**: Waymo ego 跟 AV2 一致 (x 前 y 左 z 上), 但 cam intrinsics 是 9-tuple `[fx,fy,cx,cy,k1,k2,k3,p1,p2]` 不是 3×3 K — 自己拼 `K = [[fx,0,cx],[0,fy,cy],[0,0,1]]`.
2. **Distortion**: Waymo cam 有 radial+tangential 畸变, AV2 sphere_projection 是纯 pinhole. 要先 `cv2.undistort` 或扩 step 4.
3. **Rolling shutter (你 5.22 提到的 jelly effect)**: L1 完全不补偿. 这是 Waymo 特有难度.
4. **8 cam 不是 7**: 公式不变, 多塞 slab 进 multiband 就行.
5. **Image 尺寸**: 你这次 5×972×1079 + 3×972×587/551, 只影响 K 的 cx/cy, 算法无关.

---

## 2. 2 个新 AV2 L1 范例 (回应 task §1)

**Panel**: `deliverables/xihan/l1_examples_panel.png` (2048×2144, 上下两行带 caption).

### Example A — `0bae3b5e anchor 30` (far-field 干净)

`deliverables/xihan/l1_example_A_farfield_clean.png` (亦 `deliverables/l1_baseline_diverse/0bae3b5e_a030_*.png`)

城市十字路口, 前后中距离 (15-30m) 车, 周围建筑 10m+. ERP 整圈: 建筑边缘连续, 天空无接缝, 路面连续. **L1 在 far-field 场景就是 production-ready**.

### Example B — `fbee355f anchor 30` (近场 ghost 失败模式)

`deliverables/xihan/l1_example_B_nearfield_ghost.png` (亦 `deliverables/l1_baseline_diverse/fbee355f_a030_*.png`)

停车场, ego 右侧 ~2m 处一辆白色卡车, 上方高架桥. 看白卡车 overlap 区: 车厢/车头接缝处 ghosting; 底部地面在 cam 接缝有曲线 distortion. **= 5.27 hard_select pipeline 要解决的核心 case**.

### 结论

**L1 不是好坏二元**, 是 scene-dependent: far-field 强, near-field (<5m) 弱. 你 Waymo 端如果是 highway 场景, 直接 L1 够; 城市/停车有近车, 需要后续 hard_select / HDR / OF 三层.

---

## 3. Waymo panorama brighten (回应 task §2 — **核心交付物**)

你给的 panorama: `meeting/5.22_meeting with xihan/xihan/assets/xihan task/c4b1d01f...jpg` (4096×2048, distance-to-boundary blending 结果).

### 3.1 诊断 (`deliverables/xihan/diagnose_waymo_annotated.png`)

脚本: `scripts/phase3/diagnose_xihan_waymo_panorama.py`

从 panorama 内部检测 cam 接缝 (|dY/dx| sobel spike → 7 个内部 seam → 8 个 cam 区域), 每个区域算 median Y (BT.601):

| Region | x 范围 | Y_median | 备注 |
|---|---|---|---|
| 0 | 0-762 | 136 | 左边小内容条 (alpha 黑边占大半) |
| 1 | 762-962 | **117** | 树+棕色 billboard 阴影区 |
| 2 | 962-1151 | **116** | 棕色 billboard 主体 (阴影最深) |
| 3 | 1151-1441 | 144 | 高速路接头 |
| 4 | 1441-1786 | **194** ← 最亮 | 高速路面 + 强烈天空 (过曝 cam) |
| 5 | 1786-2296 | 163 | 中段高速 + 右侧绿化 |
| 6 | 2296-3327 | 164 | 居民区建筑 |
| 7 | 3327-4096 | 142 | 右边小内容条 (alpha 黑边占大半) |

**Y range 116-194, ratio 1.67×, gap 4.44 dB**. 这跟你 ppt 第 6 张图 "右上角车左半黑右半正常" 完全对应 — cam 接缝刚好切过那辆银色 sedan, 左边是 Y=144 cam, 右边是 Y=194 cam, 50 单位 Y 跳变直接造成半边阴影/半边过曝.

最大邻接 seam 跳变:
```
seam 0->1: ΔY = -19   (region 0 -> shadowed billboard)
seam 2->3: ΔY = +28   (billboard 阴影出来到高速)
seam 3->4: ΔY = +50   ← 最大单跳, 进入过曝 cam
seam 4->5: ΔY = -31
seam 6->7: ΔY = -22
```

### 3.2 Brighten 方法 (`scripts/phase3/brighten_xihan_waymo_panorama.py`)

镜像了 AV2 L2 HDR (`code/waymo2panorama/blending/hard_hdr_of.py:48-94` 的 `compute_hdr_gains` + `apply_hdr`), 但是工作在已合成的 panorama 上 (没有原始 7/8 slab 也能跑):

```
1. 从 panorama 检测 7 个 cam 接缝
2. 每个接缝在两侧各采 24 px 窄条, 算 median Y_left, Y_right
3. 解 lstsq: G_{i+1} - G_i = log(Y_left/Y_right)  (8 个 region 的 log-gain)
   + Tikhonov reg_lambda=0.15 (防止远端 chain drift)
   + 强制 mean(G_i) = 0 (centered, 不改全局亮度)
   + clip 到 [0.75, 1.35] 范围 (防止 alpha 边缘小内容飙到 1.7×)
4. 用 piecewise-constant gain map + ±48 px 接缝处线性 taper 避免新硬边
5. YCrCb 上只动 Y 通道, Cr/Cb 不动 → preserve hue, 只调亮度
```

**核心数学**: 在 log 空间, 把 `G_{i+1} - G_i = log(Y_L) - log(Y_R)` 写成线性方程, 加 Tikhonov, lstsq 出每个 region 的乘性 gain. 这是 AV2 端的 joint global HDR 思路套到 post-hoc panorama 上.

### 3.3 量化结果

**Seam |ΔY|** (8 个内部接缝平均, 越小越好):

| variant | mean |ΔY| | max |ΔY| | vs raw |
|---|---|---|---|
| raw (你的 distance-to-boundary) | **40.86** | 69.00 | baseline |
| CLAHE only (常见反应式做法) | 46.57 | 100.00 | ❌ +14% (CLAHE 不知道 cam 边界, 反而强化) |
| **jointhdr (我们的方法)** | **33.36** | 65.00 | ✅ **-18%** |
| jointhdr + CLAHE | 48.00 | 97.00 | ❌ CLAHE 又把 jointhdr 的修正搞坏 |

Per-region gain (clipped+centered):
```
[1.405, 0.78, 0.922, 1.027, 0.78, 1.005, 0.874, 1.405]
   ↑    ↑                      ↑                      ↑
edge  阴影 cam               过曝 cam              edge
boost (拉亮)              (压暗 0.78×)              boost
```

### 3.4 视觉对比

`deliverables/xihan/brighten_waymo_4way.png` — 4 行堆叠 (raw / CLAHE / jointhdr / both):

- **raw**: 中间一段 (region 4) 明显过曝, 左侧阴影区 (region 1-2) 明显欠曝, 接缝肉眼可见
- **CLAHE**: 全局 contrast 增加但接缝仍然 / 反而更跳
- **jointhdr ← 推荐**: 中间过曝被压, 两侧阴影被拉亮, 8 个 cam 区域 Y 接近一致. 路面+天空连续性显著提高
- **both**: CLAHE 把 jointhdr 的好处又搞坏 — 不要叠

单独输出: `deliverables/xihan/brighten_waymo_jointhdr.png` (推荐版).

### 3.5 怎么用到你 pipeline

两种集成方式:

**A. Drop-in post-hoc** (最快, 不改你 stitch pipeline):
```python
from scripts.phase3.brighten_xihan_waymo_panorama import (
    find_vertical_seams, fit_region_gains, build_gain_map, apply_y_gain_map
)
seams = find_vertical_seams(rgb_pano)        # detect cam boundaries
Y = cv2.cvtColor(rgb_pano, cv2.COLOR_RGB2YCrCb)[..., 0].astype(np.float32)
G = fit_region_gains(Y, seams)
gain_map = build_gain_map(G, seams, W=rgb_pano.shape[1])
out = apply_y_gain_map(rgb_pano, gain_map)
```

**B. 上游集成** (更彻底): 把 AV2 端 `compute_hdr_gains()` (`code/waymo2panorama/blending/hard_hdr_of.py:48`) 接到你的 8 个 cam slab 上, 在 distance-to-boundary blend **之前** 做曝光对齐. 这样接缝处根本不会出现 50 Y 的跳变, 而不是合成后再修. 推荐这条但工作量更大.

### 3.6 已知 limitation (诚实说)

1. **18% 不是 100%**: seam |ΔY| 从 40.86 降到 33.36, 不是降到 0. 剩下的 mismatch 来自 (a) 区域内部不均匀 (cam 内部本来就有 vignette), (b) 检测的接缝位置不一定 pixel-perfect, (c) gain clip 限制了极端 cam 的修正幅度.
2. **没修色相**: 只调 Y, Cr/Cb 不动. 如果某个 cam 是白平衡偏差 (不是曝光偏差), 此法看不到效果. 但你 panorama 主要是 Y 跳变.
3. **取决于接缝检测**: 我用 |dY/dx| 检测接缝, 在均匀场景 (例如全是天空) 会失败. 你 Waymo pipeline 如果能直接知道 cam ROI 边界 (从 extrinsics 推), 用它直接而不是检测, 更稳.
4. **panorama 两端边缘小内容条仍轻微 over-brighten**: 因为 region 0/7 大部分是 alpha 黑边, lstsq 在小内容上不稳, 即使 clip 到 1.35 还是看着稍亮. 不影响中央主体.

---

## 4. 给你的 Quick action 4 步

1. `git pull` 主分支拿到最新 commit
2. 看 `deliverables/l1_sphere_principle.md` 把 L1 球面拼接 mental model 建立
3. 看 `deliverables/xihan/brighten_waymo_4way.png` 4 行对比, 确认 jointhdr 是要的效果
4. 拿 `scripts/phase3/brighten_xihan_waymo_panorama.py` 跑你其他 panorama (改 PANO 路径), 看看是不是其他帧也是 -15-20% seam 改善

---

## 5. ORB 路线 (告诉你别走)

你 5.22 ppt 第 9 张 ORB feature point detection 拼接两边变形的问题, **我们 AV2 这边 3 个 hybrid attempt 全 NEG**:

| attempt | 思路 | 结果 |
|---|---|---|
| T5 v1 | ORB+homography chain warp (`code/waymo2panorama/alignment/pair_homography.py`) | NEG, rear cam chain warp 飞出 canvas |
| T5 v2 | Refined chain warp (改 sim2 + RANSAC) | NEG, 同样 chain 累积 |
| T5 v3 | Procrustes 抽 R + scipy BA (`code/waymo2panorama/alignment/rotation_refinement.py`) | NEG |

**结构性原因**: 邻 cam 朝向差 ~60°, 不重叠区 ORB 找不到 match, chain warp 累积误差 → 边缘飞出. 你看到的 "左右变形, 中间 OK" 跟我们的失败原因一致.

**推荐替代**: 直接用 hard_select pipeline (binary cam select + joint HDR + Farneback OF), 已 ship 在 `code/waymo2panorama/blending/hard_hdr_of.py`, AV2 上 NCC pano-vs-winner-cam 0.65 → 0.81 = **+25.3%** (`deliverables/NCC_FINDING.md`). 你想 ghost 也想 brightness 都解决, 一步到位.

---

## 6. 我们 AV2 这边的状态 (避免你重复)

- L1 sphere 是干净基线, ghost 在 far-field 极少
- 5.27 ship 的 hard_select + L2 HDR + L3 OF 三层 pipeline 大幅修了近场 ghost + 跨 cam 色差
- 5.27 跑过 5 个 val log 的 162 panorama, full pipeline 输出在 Drive `outputs/phase3/full_pipeline_v1/`
- 你这次 brighten 工作复用了 AV2 L2 HDR 的核心数学 (`compute_hdr_gains` 的 log-space lstsq + centered), 只是改成 post-hoc 模式

---

## 7. 文件索引

代码:
- `scripts/phase3/diagnose_xihan_waymo_panorama.py` — 诊断脚本 (找接缝 + 量化 Y gap)
- `scripts/phase3/brighten_xihan_waymo_panorama.py` — brighten 方法 + CLAHE 对照
- `scripts/phase3/build_xihan_l1_examples.py` — 生成 §2 L1 panel

文档:
- `deliverables/l1_sphere_principle.md` — L1 sphere 原理 (回应 task §1)
- `deliverables/handoff_to_xihan_2026-05-27_brighten_and_l1.md` — 本文档

视觉证据:
- `deliverables/xihan/l1_examples_panel.png` — 2 个新 L1 范例 (clean/ghost 对照)
- `deliverables/xihan/diagnose_waymo_annotated.png` — Y 跨 cam 标注图 (8 region Y 值)
- `deliverables/xihan/brighten_waymo_4way.png` — raw/CLAHE/jointhdr/both 4 行对比
- `deliverables/xihan/brighten_waymo_jointhdr.png` — 推荐版单图

数据:
- `deliverables/xihan/diagnose_waymo.json` — 诊断数字 (seam x, region stats)
- `deliverables/xihan/brighten_waymo.json` — brighten 数字 (gains, seam jumps before/after)
