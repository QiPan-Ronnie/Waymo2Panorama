# Chapter 02 — CV 基础概念词典 (我们用到的每个都讲清楚)

**格式**: 每个概念分 3 部分:
1. **What 这是啥** — 1-2 句话, 大白话
2. **Why 为啥用** — 1-2 句话, 在我们项目里干啥
3. **Code 在哪看** — 一行, 仓库里的具体文件

---

## 2.1 图像作为 numpy 数组

**What**: 一张 RGB 图在代码里就是一个 `(H, W, 3)` 的 numpy 数组, `uint8` 类型. H 是行数 (高度), W 是列数 (宽度), 3 是 R/G/B 三通道.

**Why**: 我们所有图像操作 (load / save / 裁剪 / resize / 像素读写) 都是 numpy 数组操作. `img[v, u]` 是第 v 行第 u 列的像素 (注意 v 在前, 不是数学习惯的 (x, y)).

**Code**: 几乎到处, 例如 `Image.fromarray(sq).save(...)` in `scripts/phase3/run_pi3_multi_anchor.py:85`.

---

## 2.2 像素坐标 vs 图像坐标

**What**: 像素坐标 (u, v) — u 是水平方向 (从左到右, 0 ~ W-1), v 是垂直方向 (从上到下, 0 ~ H-1). 跟数学习惯 (x 右, y 上) 不一样, y 是反的.

**Why**: 所有相机模型公式都用 (u, v). 你必须熟练 mental flip y 轴 (数学的 +y 对应图像的 -v).

**Code**: 每个投影函数都有 `uu, vv = np.meshgrid(np.arange(W), np.arange(H))` 这种 pattern.

---

## 2.3 针孔相机模型 (Pinhole Camera Model)

**What**: 经典物理模型, 3D 点 (x, y, z) 投到 2D 像素 (u, v) 的公式是:

**焦距**就是：相机“成像中心”到“成像平面”的距离。它决定画面是更广角，还是更放大。

```
u = fx * x / z + cx
v = fy * y / z + cy
```
这就是为啥**远的东西小, 近的大** — 除以 z.

**Why**: 这是相机成像的物理原理, 100% 准确 (除非有 fisheye / 鱼眼镜头). 所有 CV 项目第一步.

**Code**: `depth_to_cam_points()` in `scripts/phase3/run_depth_backbone_swap.py:105-128` — 这是反过来 (back-projection), 但同样的公式.

所以还要考虑：

- 焦距用像素单位表示：$f_x, f_y$
- 图像中心点，也叫 principal point：$(c_x, c_y)$

于是：
$$
u = f_x \frac{X}{Z} + c_x
$$
$$ v = f_y \frac{Y}{Z} + c_y $$

这里：

- $u$：像素横坐标
- $v$：像素纵坐标
- $f_x, f_y$：水平和垂直方向焦距
- $c_x, c_y$：图像中心，一般接近图片宽高的一半

比如一张 $640 \times 480$ 的图，中心大概是：
$$
c_x = 320, \qquad c_y = 240
$$


---

## 2.4 K 矩阵 (Intrinsics, 内参)

**What**: 描述相机自己物理参数的 3×3 矩阵:
```
K = [[fx,  0, cx],
     [ 0, fy, cy],
     [ 0,  0,  1]]
```
- `fx`, `fy` = 焦距 (像素单位, 例如 1800)
- `cx`, `cy` = 光心 (principal point, 约等于图像中心, 例如 (W/2, H/2))

**Why**: K 是相机的"身份证", 决定 3D ↔ 2D 怎么互转. AV2 每个 cam 都有自己测好的 K (出厂校准).

**Code**: `frame.calibrations[cam].K` in `code/waymo2panorama/data_io/av2_loader.py`. 形状 (3, 3) float64.

---

## 2.5 Letterboxing + K rescale

**What**: 神经网络通常要方形输入 (e.g., 504×504), 但原图是矩形 (1550×2048 for AV2). **Letterbox = pad 黑边变方形 → resize 到目标分辨率**. 这样不丢内容, 不变形.

**Why**: Pi3 / VGGT 这种模型训练时用方形输入, 我们必须 letterbox. 但 letterbox 会改变 cx, cy (因为 pad 了边, 光心位置变了) → **K 也要相应改变**.

K rescale 公式:
```python
K2 = K.copy()
K2[0, 2] += pad_left      # cx 加上 pad
K2[1, 2] += pad_top       # cy 加上 pad
K2[0, 0] *= scale         # fx 乘以 resize 比例
K2[1, 1] *= scale         # fy
K2[0, 2] *= scale; K2[1, 2] *= scale
```

**Code**: `letterbox_to_square()` + `rescale_K_for_letterbox()` in `scripts/phase3/run_depth_backbone_swap.py:78-100`.

---

## 2.6 外参 (Extrinsics, T_ego_cam)

**What**: 描述相机在车上的位置 + 朝向的 4×4 矩阵 (SE(3)):
```
T_ego_cam = [[ R(3×3),  t(3×1) ],
             [ 0  0  0,    1   ]]
```
- `R` (3×3) = 旋转矩阵 (相机朝哪)
- `t` (3×1) = 平移向量 (相机在 ego frame 里的位置, 单位米)

**Why**: 7 个相机各自在不同位置朝不同方向, T_ego_cam 把"相机坐标系里的点"转到"车 (ego) 坐标系里的点", 这样 7 个相机的 3D 信息能合在一起.

**Code**: `frame.calibrations[cam].T_ego_cam` in `code/waymo2panorama/data_io/av2_loader.py`. 形状 (4, 4).








---



## 2.7 坐标系 (World / Ego / Cam)

**What**: 一个场景里有多个 3D 坐标系:
- **World**: 全局固定坐标系 (e.g., 地图原点)
- **Ego**: 以车辆中心为原点的坐标系, 随车移动
- Cam_<name> : 以某个相机为原点的坐标系, 朝向是相机光轴

**Why**: 不同算法在不同 frame 里好做.
- Pi3 输出 `local_points` 在 cam frame
- AV2 LiDAR 输出在 ego frame
- 我们 ERP 投影在 ego frame
- 一些 SLAM 在 world frame

**坐标转换关键公式**:
- `pts_ego = T_ego_cam @ pts_cam_homo` (cam → ego)
- `pts_cam = inv(T_ego_cam) @ pts_ego_homo` (ego → cam)
- "homo" 指齐次坐标 (见 §2.8)

**Code**: 各种 transformation function in `code/waymo2panorama/pipeline/lift_and_project.py`.




---


## 2.8 齐次坐标 (Homogeneous Coordinates)

**What**: 把 3D 点 `(x, y, z)` 写成 4D `(x, y, z, 1)`. 这样**平移 + 旋转**就能用一次矩阵乘法表示, 而不是矩阵乘 + 加法.

**Why**: 让代码简洁 (一个 4×4 矩阵搞定 SE(3) 变换), 也让算法库 (e.g., OpenCV) API 统一.

**Code**: 到处, `torch.cat([pts, ones], -1)` or `np.hstack([pts, np.ones((N, 1))])`.

---

## 2.9 Back-projection (反投影: 2D → 3D)

**What**: 知道一个像素 (u, v) + 它的深度 z → **反推 3D 点 (x, y, z) in cam frame**.

公式 (针孔模型反过来):
```
x = z * (u - cx) / fx
y = z * (v - cy) / fy
# z 就是 z
```

**Why**: 神经网络估出深度 (像素级 depth map), 我们要把它变成 3D 点云做下游处理.

**Code**: `depth_to_cam_points()` in `scripts/phase3/run_depth_backbone_swap.py:105-128`.

---

## 2.10 SE(3) 变换 (刚体变换)

**What**: SE(3) = "Special Euclidean group in 3D", 即所有刚体变换 (旋转 + 平移, 没缩放没剪切). 用 4×4 矩阵表示 (见 §2.6).

**Why**: 真实物理世界的相机姿态变化都是 SE(3) (车移动 + 转弯, 不会突然变大). 我们项目所有相机变换都是 SE(3).

**Code**: `T_ego_cam` 是 SE(3). 复合两个 SE(3): `T_a_b = T_a_ego @ T_ego_b`.

---

## 2.11 Sim(3) 变换 (相似变换)

**What**: Sim(3) = SE(3) + 缩放. 7 自由度 (R + t + scale): `pts_aligned = scale * R @ pts + t`.

**Why**: Pi3 神经网络输出的点云在它自己的 "world frame", **尺度跟真实世界可能差一个常数**. 用 Sim(3) 把 Pi3 frame → AV2 ego frame, 一次性修正旋转/平移/尺度.

**Umeyama 算法**: 经典 closed-form 解, 给定两组对应点 (e.g., 7 个 cam pose pi3 / 7 个 cam pose av2), 解最优 Sim(3).

**我们实测**: scale = 1.0346 (Pi3 frame 比 AV2 大 3.5%), mean residual 0.157 m → Pi3 自己的 frame 几乎跟 AV2 对齐.

**Code**: `code/waymo2panorama/alignment/sim3_align.py` (Umeyama 实现).

---

## 2.12 球面投影 (Spherical Projection)

**What**: 假设场景**离相机很远** (depth → ∞), 每个像素 (u, v) 反推到一个**方向** (unit sphere 上一点), 不需要深度.

公式:
```python
# pixel (u, v) → unit ray in cam frame:
ray = K^-1 @ [u, v, 1]^T
ray = ray / ||ray||  # normalize 到单位球
```

**Why**: 远场视差小可以忽略, 球面投影是**最简单的几何**, 不需要深度信息 → 抗错性强 (这就是为啥 L1 强).

**Code**: `code/waymo2panorama/projection/sphere_projection.py`.

---

## 2.13 柱面投影 (Cylindrical Projection)

**What**: 像球面投影, 但是投影到**圆筒** (cylinder) 而不是 sphere. Cylinder unwrap 后是一个矩形, 比 ERP 更适合相机水平排列的情况.

公式: 像素 → cylinder coord (θ, h) where θ = atan2(x, z), h = y/sqrt(x²+z²).

**Why**: AV2 7 个相机几乎水平排列 (除了 front_center 略上倾), 柱面比球面更贴合这个几何 → 画布利用率更高 (+24.9 pp coverage).

**Code**: `code/waymo2panorama/projection/cylinder.py` (新-A 路线核心).

---

## 2.14 Equirectangular Projection (ERP)

**What**: 球面 → 2D 矩形的标准映射 (像世界地图 Mercator). 横轴 = 经度 (-180° ~ 180°, 对应 ERP 宽 0 ~ W-1), 纵轴 = 纬度 (-90° ~ 90°, 对应 ERP 高 0 ~ H-1).

公式 (球面 unit ray (x, y, z) → ERP (u_erp, v_erp)):
```
lon = atan2(x, z)         # 经度
lat = asin(y)              # 纬度
u_erp = (lon + π) / (2π) * W_erp
v_erp = (lat + π/2) / π * H_erp
```

**Why**: ERP 是 360° 全景图的事实标准格式, 下游 (Pantheon360 / VR / 360 video) 都用 ERP.

**特点**: 两极扭曲严重 (经线重合), 赤道附近最准.

**Code**: `code/waymo2panorama/projection/sphere_projection.py` 里 sphere→ERP 转换.

---

## 2.15 Multi-band Laplacian Blending (多带混合)

**What**: 经典图像融合算法 (Burt & Adelson 1983). 把图像分解成不同**频率层** (高频 = 细节边缘, 低频 = 颜色/光照), 每层用不同宽度的 mask 混合.

**Why**: 直接 alpha-blend (50% 图 A + 50% 图 B) 在接缝处颜色不连续看着假. Multi-band 让低频颜色平滑过渡, 高频细节保留, **接缝消失**.

**步骤**:
1. 每张图建 Laplacian pyramid (高斯下采样 → 减去 → 高频残差)
2. mask 也建 Gaussian pyramid (宽度逐层变大)
3. 对应层级线性混合
4. 重建

**Code**: `code/waymo2panorama/blending/multiband.py`. L1 baseline 用 5 个 band.

---

## 2.16 Cosine² Feathering (余弦平方权重)

**What**: 给图像边缘一个**软权重**, 中心权重 1.0, 边缘逐渐降到 0.0. 公式: `weight = cos²(angle_to_center)`.

**Why**: 混合多张图时, 越靠近某个 cam 中心的像素越可信. 软权重避免接缝处突变.

**Code**: `code/waymo2panorama/projection/sphere_projection.py` 里的 weight 计算.

---

## 2.17 单目深度估计 (Monocular Depth Estimation)

**What**: 输入**1 张图** (RGB), 输出**每像素深度** (z 值, 米). 没有 stereo, 完全靠**学习先验** (大物体看起来近, 平行线消失到远处, 透视, 阴影等).

**为啥能 work**: 神经网络在大数据集 (KITTI, NYU, NuScenes 等) 上学到了 "物体大小 ↔ 距离" 的统计规律.

**Why 用**: AV 没 stereo 没 LiDAR 的情况下, 单目深度是唯一能拿 dense depth 的方式.

**Metric vs Relative**:
- **Metric**: 输出真实米数 (Pi3 / Depth Pro / VGGT, 训练时有 LiDAR GT)
- **Relative**: 只输出"哪近哪远" (MiDaS 早期, DepthAnything)

**Code**: Pi3 跑 inference 在 `scripts/phase3/run_pi3_multi_anchor.py`. Depth Pro 在 `scripts/phase3/run_depth_backbone_swap.py`.

---

## 2.18 Pi3 模型 (CVPR 2025)

**What**: 由 yyfz 团队 (Salesforce + UMich) 开发的 **permutation-equivariant 3D foundation model**. 输入多张图 (1-100+) → 输出每张图的: dense 3D 点 (local_points), 相机姿态, 置信度 logits.

**关键 property**:
- **Permutation-equivariant**: 输入图的顺序不影响输出 (除了 reference frame, 通常 frame 0)
- **Multi-view native**: 7 cam 一次 forward, 不是单帧推理 7 次
- **Metric scaled**: 输出在真实米制 (与 LiDAR 同尺度, 误差几个百分点)

**我们怎么用**: 输入 7 cam 504×504, 输出 `(7, 504, 504, 3)` 的 local_points + `(7, 504, 504, 1)` conf. 第 3 通道 = z = metric depth.

**Code**: `scripts/phase3/run_pi3_multi_anchor.py`. HF model id: `yyfz233/Pi3X`.

---

## 2.19 Forward-splat (前向溅射)

**What**: 对每个源像素 (u, v) → 用深度 z 反推 3D 点 → 投到目标图像 → 把源像素的 RGB **"splat"** (溅射) 到目标像素位置.

**Why**: 是 L3 算法的核心. 从 7 cam 各自的 3D 点云 → ERP 全景图.

**问题**:
1. **稀疏覆盖**: 源像素跟目标像素不是 1:1, 会出现 hole
2. **遮挡处理差**: 远的可能覆盖近的 (没 z-buffer)
3. **深度敏感**: depth 错 → splat 到错位置 → 鬼影

**对比 Inverse-warp**: 反过来, 对每个目标像素去源图查 (e.g., `grid_sample`). 可微, T13 self-sup loss 用这个.

**Code**: `code/waymo2panorama/pipeline/lift_and_project.py`.

---

## 2.20 经典立体几何 (Two-view Stereo)

**What**: 给两张图 (相机 A, 相机 B, 拍同一场景不同角度), 知道两相机相对位置 (T_a_b) → 通过**视差 (disparity)** 恢复每个像素的深度.

**核心步骤**:
1. **Feature detection** (找关键点, e.g., DISK / SuperPoint)
2. **Matching** (匹配两图关键点, e.g., LightGlue / SuperGlue)
3. **Epipolar filtering** (用几何约束剔除错匹配)
4. **Triangulation** (DLT 三角化恢复 3D 点)

**Why**: AV2 邻 cam 距离 ~30-150 cm, 视角差异够做经典 stereo. 我们用这个做 sparse 3D 验证 (新-D).

**Code**: `code/waymo2panorama/stereo/wide_baseline_stereo.py`.

---

## 2.21 DISK + LightGlue (Feature detection + matching)

**What**:
- **DISK** (ICCV 2021): 深度学习 keypoint detector + descriptor, 抽 1024-2048 个特征点
- **LightGlue** (ICCV 2023): adaptive transformer matcher, 比 SuperGlue 快 5×

**Why**: 找两张图里**对应的同一物理点** (e.g., 路灯顶端在图 A 是像素 (u_a, v_a), 在图 B 是 (u_b, v_b)). 这是 stereo 第一步.

**Code**: `code/waymo2panorama/stereo/wide_baseline_stereo.py` 里的 `extract_pair_features()` 和 `match_with_lightglue()`.

---

## 2.22 DLT 三角化 (Direct Linear Transform)

**What**: 给两个相机的 K + T + 匹配的 2D 像素对, **线性解出** 3D 点位置.

公式 (简化): 构造 A·X = 0 的线性方程组, X 是齐次 3D 点, A 是相机 projection matrix 组合, SVD 解最小奇异值对应的 X.

**Why**: 在已知相机相对姿态时 (我们 AV2 出厂校准 ±5 mm 准), DLT 直接给出 3D 点位置.

**Code**: `cv2.triangulatePoints(P_a, P_b, pts_a, pts_b)` in `wide_baseline_stereo.py`.

---

## 2.23 Cheirality 检查

**What**: "Cheirality" = 手性, 在 stereo 里指"三角化出来的 3D 点是否在两个相机前方" (Z > 0). 远场近平行射线会数值病态, 三角化到相机背后.

**Why**: 实测发现 (新-D bug fix): front_left ↔ side_left 152 个 epi inlier 全部因为近 0° parallax 三角化到 cam 背后. 加 Z > 0.5 m 过滤后, 正确降到 0 个 NEG.

**Code**: `wide_baseline_stereo.py` 里的 `triangulate_sparse()`.

---

## 2.24 LiDAR 点云 + 深度评测

**What**:
- **LiDAR**: 激光雷达, 测每个方向的实际距离, 输出 sparse 3D 点云 (1000-100k 点/sweep)
- **作为深度 GT**: 把 LiDAR 点投到相机平面, 跟相机估的 depth 对比

**Why**: 验证 Pi3 / Depth Pro 估的深度准不准.

**评测 metric**:
- **abs_rel**: `|d_pred - d_gt| / d_gt`, 像素平均. KITTI SOTA ~0.10, Pi3 in AV2 ~0.20, Depth Pro ~0.58.
- **δ<1.25**: 比例 of `max(d_pred/d_gt, d_gt/d_pred) < 1.25`. 越高越准. KITTI SOTA ~0.95.
- **RMSE**: `sqrt(mean((d_pred - d_gt)²))`, 单位米.

**Code**: `scripts/phase2/eval_pi3_vs_lidar.py`.

---

## 2.25 IPM (Inverse Perspective Mapping)

**What**: 假设**地面是平的** (z = 0 in ego frame), 那像素 (u, v) → 3D 点 (X, Y, 0) 有**闭式解**, 不需要神经网络.

数学: 已知 K + T_ego_cam + 地面方程 z=0, 解 line-plane intersection.

**Why**: AV 场景路面 99% 是平的, IPM 对路面像素给出**精确 0 视差** 的 3D 位置. 比神经网络估的深度准.

**限制**: 不是地面的像素 (车 / 行人 / 建筑) 不能用 IPM, fallback 球面投影.

**Code**: `code/waymo2panorama/projection/ipm_ground.py` (T14 单地面) + `ipm_multi_region.py` (新-C 扩到 ground+sky+building).

---

## 2.26 Graph Cut + Min-cut (图论)

**What**: 把图像建模成**图 (graph)**, 像素 = 节点, 相邻像素之间 = 边 (权重 = 颜色差异 / 梯度). **Min-cut** = 找一条把图切成两半使总边权最小的路径.

**Boykov-Kolmogorov 算法**: 经典 max-flow / min-cut 算法 (2001), `PyMaxflow` 包实现.

**Why**: 经典图像拼接里, "**找接缝**" (seam) 是个 min-cut 问题. 用 min-cut 让接缝沿**低梯度路径**走 (沿着均匀的天空 / 马路, 不穿过建筑边缘), 接缝消失.

**Code**: `code/waymo2panorama/blending/graphcut_seam.py` (新-B).

---

## 2.27 HDR + 跨相机色彩补偿 (Color Compensation)

**What**: 7 个相机独立跑 AE (Auto Exposure) + AWB (Auto White Balance), 同一片天空在不同 cam 里可能 lum 差 50+ levels.

**6 参数模型** (我们的方法):
- 每 cam 6 参数: 3 gain (R/G/B) + 3 bias (R/G/B)
- 修正: `corrected = gain * raw + bias`
- 用重叠区像素对 RGB 算 LS 解参数

**Why**: 不补偿的话, 拼接出的 ERP 会有明显**颜色断层** (一边偏蓝一边偏黄). 视觉上看着假.

**算法**: scipy.optimize.least_squares with Huber loss (robust to outliers like specular highlights / moving objects).

**Code**: `code/waymo2panorama/color/hdr_gain_estimate.py` (新-E).

---

## 2.28 RANSAC (Random Sample Consensus)

**What**: 经典 robust estimation 算法. 流程:
1. 从数据里随机抽 minimal sample (e.g., 4 点拟合平面)
2. 用 sample 算出 model parameters
3. 看多少其他数据点 fit 这个 model (inliers)
4. 重复 N 次, 取 inlier 最多的那次

**Why**: 数据有 outlier (错误匹配 / 移动物体 / 反光) 时, 直接 least-squares 会被 outlier 拽歪. RANSAC 鲁棒.

**Code**: 新-D wide-baseline stereo 里的 epi-polar 过滤, 新-C IPM 多区域里的 building plane 拟合.

---

## 2.29 Self-supervised Learning + Cycle Loss

**What**:
- **Self-supervised**: 没人工标注 GT 时, 用**数据自己的一致性**当 supervision.
- **Cycle loss**: 我们的 cycle-PSNR (hold-one-cam reconstruction) 当 loss 反向 backprop 进 Pi3 → finetune depth head.

**Why** (T13 想做的): Pi3 远场 depth bias -24%, 用 cycle-PSNR 当 self-sup signal 训练修这个 bias.

**关键挑战**: photometric loss 在 monodepth 文献里出名难收敛. 加 SSIM term, edge-aware smoothness, 多分辨率 loss 可以稳定.

**Code (未实现)**: 设计在 `notes/t13_self_sup_pi3_finetune_design.md`.

---

## 2.30 LoRA (Low-Rank Adaptation)

**What**: 大模型 fine-tuning 技术. 不改原模型, 只在某些层旁边加 **small rank-r matrix** (e.g., r=8), 训练这个小矩阵.

**Why**: Pi3 有几亿参数, full fine-tune 容易过拟合 + 训得慢. LoRA 只训几十万参数, 快且不破坏原模型 capability.

**T13 设计**: Tier-A LoRA rank=8 在 Pi3 depth head + last 6 decoder blocks (~3M trainable params).

**Code (未实现)**: `notes/t13_self_sup_pi3_finetune_design.md`.

---

## 2.31 Cycle-PSNR (我们的 main metric)

**What**: Hold one cam, reconstruct from 6 others, PSNR vs GT.
- L1 reconstruction: 球面投影只用 ray direction, 不用深度
- L3 reconstruction: 用 Pi3 估的 3D 点 forward-splat

**Why**: 不需要外部 GT, 数据自己就够. 是 paper main metric.

**特点 / 局限**:
- ✅ 不需要 GT
- ✅ 跟实际拼接质量正相关
- ⚠️ 对 projection surface (sphere / cylinder) 不敏感
- ⚠️ 对 seam location (graph-cut) 不敏感 (因为 reconstruct_l1 不经过 blender)
- ⚠️ object 区域跟全局平均算下来可能掩盖局部 win

我们做了 T5 metric audit (LPIPS / MS-SSIM / object-band PSNR) 跨 metric 验证 L3 NEG.

**Code**: `scripts/phase2/eval_cycle_consistency.py`.

---

## 总结 — 必记 6 个概念

如果只能记 6 个, 这些 ↓ (其他遇到再查):

1. **K 矩阵** (§2.4) — 相机身份证, 3D ↔ 2D 转换
2. **T_ego_cam** (§2.6) — 相机在车上位置朝向
3. **球面投影** (§2.12) — L1 的核心
4. **Back-projection** (§2.9) — 2D + depth → 3D
5. **Forward-splat** (§2.19) — L3 的核心 (paper 主 NEG)
6. **Cycle-PSNR** (§2.31) — 我们的 main metric

---

**下一章**: [03_methods_walkthrough.md](03_methods_walkthrough.md) — 8 条拼接路线深度讲解





