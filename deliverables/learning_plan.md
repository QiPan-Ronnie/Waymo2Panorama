# Waymo2Panorama — CV 学习指导 (新手向, 跟着我们这个项目学)

**对象**: Qi Pan (你), CV 基础不太好, 想系统理解我们这个 paper 在做什么
**风格**: 傻瓜式渐进, 每个概念都对应到本仓库里的一个具体文件 / 函数 / 数字, 边看代码边学
**时长**: 快速版 ~1 周 / 深度版 ~3-4 周
**前置要求**: Python 基础 + 会读 numpy + 会跑 git/conda (已具备)

---

## 起步建议 (先花 1 小时)

不要先翻教材, 先花 1 小时把**项目长什么样**搞清楚, 后面学概念才有锚:

1. **打开 `deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.pdf`**, 翻一遍 (15 分钟). 不用每个数字都看懂, 重点看图: §1.1 L1 sphere ERP 长什么样? §1.2 L3 forward-splat 那个鬼影怎么飘的? §1.6 IPM 多区域的三色 mask 图?
2. **打开 `agent/handoff.md`**, 看 "8 stitching routes" 那张表 (5 分钟). 现在你脑子里有 8 个名字 + 一句话 verdict.
3. **打开 `outputs/phase3/p3.1_multi_anchor/anchor_060/image_FRONT_CENTER.png`** (或类似), 看 1 张原始 504×504 输入 (5 分钟). 这是 AV2 一个相机的原图.
4. **打开 `code/waymo2panorama/projection/sphere.py`**, 随便扫一遍 (10 分钟). 看不懂没关系, 知道"这里把图变成 ERP"就行.

OK 现在你脑子里有: **AV2 7 个相机 → 8 种方法 → 一个 360° 球面图**。 开始学。

---

## 学习路径总览

| Phase | 主题 | 必学? | 时长 (快/深) | 学完能干啥 |
|---|---|---|---|---|
| **0** | 数学 + 图像 ndarray 基础 | 必 | 4h / 1d | 看懂代码里的 (H, W, 3) 数组操作 |
| **1** | 针孔相机模型 + 3D 几何 | 必 | 1d / 3d | 理解为啥需要 K 矩阵, 怎么从 2D 像素回到 3D 点 |
| **2** | 多视图基础 (世界 / ego / cam 坐标系) | 必 | 1d / 3d | 理解为啥 AV2 给了 T_ego_cam, 怎么用 |
| **3** | 经典图像拼接 (L1 路线) | 必 | 1d / 3d | 看懂 L1 球面 baseline 怎么把 7 张图拼成 ERP |
| **4** | 单视图深度估计 (Pi3 / Depth Pro) | 必 | 1d / 2d | 理解为啥神经网络能估深度, 为啥 AV 远场会失败 |
| **5** | 3D-lift forward-splat (L3 路线) | 必 | 1d / 3d | 理解为啥 L3 看起来"高级"反而输给 L1 (paper 主 NEG) |
| **6** | 进阶 stitching (新-B/C/D/E) | 应 | 2d / 1w | 理解 graph-cut seam / IPM / wide-baseline stereo / HDR 各自的物理直觉 |
| **7** | 自监督 + 研究方法论 | 选 | 1d / 3d | 理解 cycle-PSNR 当 loss 怎么用 (T13), 怎么用 NEG 写 paper |

**总计**: 快速版 8 天 / 深度版 4 周

---

## Phase 0 — 数学 + 图像 ndarray 基础 (4h)

### 概念 (15 分钟读完)
- **矩阵 × 向量**: 你之前肯定见过, 我们代码里全是 `K @ p` (相机内参乘 3D 点) 这种操作. 关键: 维度对得上, 结果维度 = 左矩阵行数 × 右矩阵列数.
- **齐次坐标**: 3D 点 `(x, y, z)` 写成 4D `(x, y, z, 1)`, 这样平移就能用矩阵乘法表示 (而不是加法). 我们代码里到处是 `torch.cat([pts, ones], -1)` 就是这个.
- **SE(3) = 4×4 矩阵 (R | t; 0 0 0 1)**: 表示一个 rigid transform (旋转 + 平移). AV2 的 `T_ego_cam` 就是 SE(3), 把 cam-frame 点变到 ego-frame.
- **图像在 numpy 里 = (H, W, 3) uint8**: H 行 W 列每像素 3 通道 (R/G/B). `img[v, u]` = 第 v 行第 u 列 (注意 v 在前, 跟数学里 (x, y) 反着).

### 资源 (4h)
- **3Blue1Brown 线性代数本质** (YouTube): 第 3 / 4 / 7 集, 各 10 分钟 (矩阵作为变换, 矩阵乘法, 行列式) — **必看**, 比任何教材直观
- **OpenCV Python tutorial — Basic Operations on Images**: 1 小时看完, 跑一遍 `img.shape` / `img.dtype` / `cv2.resize` 等
- 选: 任何 numpy 入门教程 (你应该已经会了)

### 上手 (30 分钟)
打开 `code/waymo2panorama/data_io/av2_loader.py`, 找到 `T_ego_cam` 在哪里被 load. 打印一个 `T_ego_cam` 看它长什么样 (应该是 4×4 矩阵). 然后看 `T_ego_cam[:3, 3]` 是 cam 的平移 (在 ego frame 里的位置, m), `T_ego_cam[:3, :3]` 是旋转 (3×3).

### 检验
- 你能解释 `pts_ego = T_ego_cam @ pts_cam` 这行代码每个维度吗?
- 一张 H=504, W=504 的图, `img[100, 50]` 是哪个像素的 RGB?

---

## Phase 1 — 针孔相机模型 + 3D 几何 (1d)

### 概念
- **针孔相机模型**: 3D 点 `(x, y, z)` 投到 2D 像素 `(u, v)` 的公式是 `u = fx*x/z + cx, v = fy*y/z + cy`. 这就是为啥远的东西小, 近的大 (除以 z).
- **K 矩阵 (内参)**: `[[fx, 0, cx], [0, fy, cy], [0, 0, 1]]`. fx/fy 是焦距 (像素单位), cx/cy 是光心 (图像中心附近). AV2 每个 cam 都有自己的 K.
- **外参 (extrinsics)**: 也是 SE(3), 描述 cam 在 world (或 ego) 里的位置 + 朝向. 通常给你 `T_ego_cam` (从 cam frame 到 ego frame).
- **Back-projection**: 知道像素 `(u, v)` + 深度 `z` → 反推 3D 点 `pt = z * K^-1 @ [u, v, 1]^T`. 我们 `run_depth_backbone_swap.py` 里 `depth_to_cam_points()` 就是这个.
- **Letterboxing**: 原图是矩形 (1550×2048), 但神经网络通常吃方形输入. Letterbox = pad 黑边变方形 → resize 到 504×504. K 也要相应改变 (`rescale_K_for_letterbox()`).

### 资源 (1d)
- **First Principles of Computer Vision (Shree Nayar, Columbia)** — YouTube 系列, 看 "Image Formation" + "Imaging Sensor" 两集, 各 30 分钟 — **极推荐**, 视觉直观
- **Multiple View Geometry (Hartley & Zisserman) 第 6 章 "Camera Models"** — 只读 §6.1 (针孔模型). 教材是经典但难, 入门只读这一节
- 跳过: Hartley & Zisserman 完整版 (太硬, 等深度版再回来)

### 上手 (1h)
1. 打开 `scripts/phase3/run_depth_backbone_swap.py`, 找 `letterbox_to_square()` 函数. 跑一遍 (mental run): 输入 1550×2048 → 输出 504×504. 看 pad 和 resize 的逻辑.
2. 找 `rescale_K_for_letterbox()`. 为啥 `K[0, 2] += pad_left` (改 cx)? — 因为光心在原图里在某个位置, pad 之后这个位置在新图里向右挪了 pad_left 像素.
3. 找 `depth_to_cam_points(depth, K)`. 看公式: `x_n = (uu - cx) / fx; pts = stack([x_n*d, y_n*d, d])`. 这就是上面 back-projection 公式的向量化版.

### 检验
- AV2 的 `FRONT_CENTER` 摄像头, 焦距大概 1800 像素 (1550×2048 原图). 如果一个物体在 50 米远, 实际宽 1 米, 它在图像里宽多少像素?
  - 答案: `pixel_width = 1800 * 1 / 50 = 36 像素`

---

## Phase 2 — 多视图 + 坐标系 (1d)

### 概念
- **坐标系层级 (AV2)**: `world` (全局) → `ego` (车辆中心) → `cam_FRONT` / `cam_REAR_LEFT` / ... (每个相机自己的). 我们工作主要在 `ego` 和 `cam_*` 之间转.
- **AV2 extrinsics 是固定出厂校准的**, 精度 ±5-10 mm 平移 / ±0.1° 旋转. 不需要在线估计.
- **关键转换**:
  - `pts_cam → pts_ego`: `pts_ego = (T_ego_cam[:3, :3] @ pts_cam.T + T_ego_cam[:3, 3:4]).T`
  - `pts_ego → pts_cam_other`: `pts_cam_other = T_cam_other_ego @ pts_ego_homo`, 其中 `T_cam_other_ego = inv(T_ego_cam_other)`
- **Hold-one-out (cycle-PSNR)**: 把 7 个 cam 中第 i 个挡住, 用剩下 6 个推断第 i 个看到啥, 跟 ground truth (第 i 个的真实图) 比 PSNR. 这是我们 paper 的 main metric.
- **为啥 cycle-PSNR 重要**: 不需要 ground truth panorama (AV 数据没有), 用 hold-out 就够了.

### 资源 (1d)
- **CMU 16-385 Computer Vision** Lecture 12 "Two-view geometry" — slides 公开, 重点看 epipolar geometry intro (前 20 页)
- **AV2 Sensor Guide**: https://argoverse.github.io/user-guide/datasets/sensor.html — 看 "Cameras" 一节, 知道 7 个 cam 的物理布局
- 跳过: 完整 two-view geometry (太深, 我们用经典 stereo 但不算 fundamental matrix)

### 上手 (1.5h)
1. 打开 `scripts/phase2/eval_cycle_consistency.py`. 找 `reconstruct_l1()` 函数. 它做了啥?
   - 输入: holdout cam 名字 + 其他 6 个 cam 的 K / T_ego_cam / RGB
   - 输出: 推断的 holdout cam 视图 (uint8 H W 3) + 它的 valid mask
   - 内部逻辑: 对 holdout 视野里每个像素 → 反推 ray → 沿 ray 跨 6 个其他 cam 找哪个能看到 → 取那个 cam 的 RGB
2. 跑一次 (在你本地或 Colab):
   ```bash
   python scripts/phase2/eval_cycle_consistency.py --pi3-dir outputs/phase3/pi3_cache/anchor_060 --output-dir /tmp/test
   ```
   看输出: 每个 cam 的 PSNR_L1 / PSNR_L3 / Δ.

### 检验
- 为啥 L1 baseline (球面 + sphere projection) 比 L3 (Pi3 forward-splat) 更稳? — 因为 L1 不假设深度, L3 假设每个像素都在某个 z 处 splat 出去, 错了一个 像素就飘到错的位置.
- 7 cam ring 里, `FRONT_CENTER` 和 `REAR_LEFT` 几乎没 overlap, 那 cycle-PSNR 怎么算? — `valid_mask` 只在有 overlap 的像素生效, 没 overlap 的不算分母.

---

## Phase 3 — 经典图像拼接 (L1 路线) (1d)

### 概念
- **Equirectangular Projection (ERP)**: 球面 → 2D 矩形的标准映射 (类似世界地图). 横轴 = 经度 (-180° ~ 180°), 纵轴 = 纬度 (-90° ~ 90°). 我们的 ERP 是 1024×2048.
- **Sphere projection**: 假设场景在远处 (深度 → ∞), 每个像素对应一个方向 (单位球面上的点). 7 cam 的所有像素都投到同一个球面 → 再 unwrap 成 ERP.
- **L1 baseline 流程**:
  1. 每个 cam: 像素 (u, v) → ray direction (球面上的点)
  2. 把 ray direction 转到 ego frame (用 `T_ego_cam` 的旋转部分)
  3. ray direction → ERP 像素 (lat, lon) 坐标
  4. 7 cam 的 slab 用 multi-band Laplacian 混合接缝处
- **Multi-band Laplacian blending (Burt & Adelson 1983)**: 把图像分解成不同频率层 (高频细节 / 低频颜色), 每层用不同宽度的 mask 混合 → 接缝在低频上消失 (颜色过渡), 但高频细节保留. 这是为啥 L1 的接缝看起来"魔法般无缝".
- **Cosine² feathering**: 每个 cam 边缘的权重不是硬切, 是 `cos²(angle to center)`, 中心权重 1.0 边缘 0.0. 让 blending 平滑.

### 资源 (1d)
- **Richard Szeliski "Computer Vision: Algorithms and Applications" 第 9 章 "Image Stitching"** — 免费 PDF (Szeliski 个人主页). §9.1 (motion models) 必读, §9.3 (compositing / blending) 必读.
- **"Multiband Blending" tutorial** (OpenCV docs) — 30 分钟可视化教程
- 跳过: §9.2 (global alignment) — 我们用 AV2 truth extrinsics, 不算 alignment

### 上手 (2h)
1. 打开 `code/waymo2panorama/projection/sphere.py`. 找 `sphere_to_erp_pixel(lat, lon)` 之类的函数. 公式: `u_erp = (lon + π) / (2π) * W_erp`, `v_erp = (lat + π/2) / π * H_erp`.
2. 打开 `code/waymo2panorama/blending/multiband.py` (或类似). 看 Laplacian pyramid 怎么 build (gaussian downsample + subtract).
3. 跑一次 L1 baseline:
   ```bash
   python scripts/phase3/stitch_frame.py --anchor-idx 60 ...
   ```
   出图 → 看 `outputs/.../anchor_060/erp_L1.png` → 找接缝在哪里 (front_l/front_c, front_c/front_r 边界处).

### 检验
- ERP 图为啥两极那么扭曲? — 经线在两极重合, 但 ERP 是矩形所以两极被拉伸成一整行像素.
- 为啥 L1 在 cycle-PSNR 12.34 dB 是"强 baseline"? — 它不假设深度, 在远场 (>50m) 视差很小时几何精确, 错误集中在近场 (车 / 行人), 但近场只占像素少数.

---

## Phase 4 — 单视图深度估计 (Pi3 / Depth Pro) (1d)

### 概念
- **Monocular depth estimation**: 输入 1 张图 → 输出每像素深度 (z 值, 米). 没有 stereo, 完全靠学习先验 (大物体看起来近, 平行线消失到远处, etc.).
- **Metric vs relative depth**:
  - Metric: 输出真实 z 米数 (Pi3 / Depth Pro / VGGT 都是 metric, 训练数据有 LiDAR ground truth)
  - Relative: 只输出"哪里近哪里远", 没绝对尺度 (MiDaS / DepthAnything 早期版本)
- **Pi3 (CVPR 2025)**: Permutation-equivariant 3D foundation model, 输入多张图 (我们用 7 cam) 一次 forward, 输出每像素的 3D 点 `local_points` (cam frame) + `points` (cam-0 frame) + `conf` (置信度 logits) + 估计的 cam poses.
- **Apple Depth Pro (2024)**: 单图输入, 输出 metric depth. SOTA on KITTI 但**在 AV2 上 abs_rel 0.58 vs Pi3 0.20** — 2.84× 差. 说明"哪个模型 SOTA"跟"哪个模型适合 AV 场景"是两回事.
- **远场 bias (-24% at 40m)**: Pi3 在 40m 处估的深度系统性低 24% (估的比真实近). 我们 T12 多帧 temporal Pi3 试图修这个, 没成功 — bias 是 structural 不是 single-frame 信息不足.

### 资源 (1d)
- **Apple Depth Pro paper** (arXiv 2410.02073) — 30 分钟读完 abstract + 方法概览. **必读** 因为是我们的 NEG 实验之一.
- **Pi3 paper** (arXiv 2501.11017 or 2503.XXXX) — 我们做的就是基于这个, 1 小时通读
- **MiDaS 系列 review blog** (任意 monocular depth survey blog) — 30 分钟扫一遍, 知道这一类方法的 history

### 上手 (2h)
1. 跑一次 Pi3 inference (如果本地有 Pi3 clone):
   ```bash
   python scripts/phase2/run_pi3_one_frame.py --anchor-idx 60 ...
   ```
   出 `local_points_FRONT_CENTER.npy` (504, 504, 3) — 每像素的 3D 点. 第 3 维就是 metric depth z.
2. 打开 numpy 看一下: `np.load('local_points_FRONT_CENTER.npy')[..., 2]` 应该是 [0.5m, ~80m] 范围.
3. 打开 `agent/progress_T18_addendum.md` 看 Depth Pro NEG 结果 — abs_rel 0.58 怎么算的, 为啥说明"换 backbone 也救不了 L3".

### 检验
- 为啥 monocular depth 在远场容易错? — 远场视差小 (同一物体不同视角看起来差不多), 没有视差信号, 全靠学习先验 (而 AV 场景的远场 = 道路灭点 / 天空 / 远建筑, 训练分布稀疏).
- 为啥我们用 7 cam 一起喂 Pi3 反而比单帧好? — Pi3 是 permutation-equivariant 多视图模型, 多视图能 cross-check (但限制是 7 cam 几乎共点旋转, 视差仍然小).

---

## Phase 5 — L3 forward-splat 路线 (paper 主 NEG) (1d)

### 概念
- **L3 流程**: Pi3 估每像素 3D 点 → Sim(3) 对齐 (因为 Pi3 输出在自己的 world frame, 跟 AV2 ego frame 不同) → forward-splat 投到 ERP 球面 → 跨 cam 累加.
- **Sim(3) 对齐 (Umeyama 算法)**: 7 自由度变换 (旋转 R + 平移 t + 尺度 s), 解 `argmin_{R,t,s} ||s*R*pi3_cam_pos + t - av2_cam_pos||²`. 我们 anchor 60 实测 scale=1.0346, mean residual 0.157 m — Pi3 自己的 frame 跟 AV2 几乎对齐 (residual << cam spacing).
- **Forward-splat**: 对每个像素 (u, v, z) → 算它在 ERP 上的位置 → 把这个 RGB "投" (splat) 到 ERP 那个位置.
- **为啥 L3 输给 L1 (paper 主 NEG)**:
  1. **深度错误 = 位置错误**: 远场 depth 错 24% → splat 到错位置 → 跨 cam 拼接时位置不一致 → 鬼影
  2. **遮挡处理差**: forward-splat 没 z-buffer, 远的可能覆盖近的
  3. **稀疏覆盖**: depth 不连续处 splat 出大洞
- **Inverse-warp (cycle-PSNR 用的)**: 跟 forward-splat 反过来 — 在目标像素位置 inverse 查源像素. 可微 (`grid_sample`), 是 T13 self-sup loss 的基础.

### 资源 (1d)
- **Szeliski 第 11.2 章 "Stereo correspondence"** — 看 forward-warp vs backward-warp 的对比图
- **agent/progress.md** 顶部 L3 NEG 那几个 entry — 我们自己的实验结论比任何教材都直接
- **deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.md §1.2** — L3 详细写法

### 上手 (2h)
1. 打开 `code/waymo2panorama/pipeline/lift_and_project.py`. 找 `apply_sim3_to_points()` 和 `forward_splat_to_erp()` 之类的函数.
2. 打开 `scripts/phase2/eval_cycle_consistency.py` 的 `reconstruct_l3()`. 跟 `reconstruct_l1()` 对比 — L1 只用 K/T, L3 还用 `cam_points_ego` 和 `cam_conf`.
3. 看 deliverables/images/route_l3_ghost_anchor_060.png (或 progress 里贴的) — 视觉上的 ghost 看起来什么样.

### 检验
- L3 在 anchor 60 输 L1 -3.15 dB, 10/10 anchor 都输. 为啥说这是"结构性失败"而不是"巧合"? — 因为换了 Depth Pro (T18) / 多帧 Pi3 (T12) / 不同 anchor 都输 — 故障模式跟 backbone 无关, 是 algorithm-class problem.
- 如果我们能修 Pi3 远场 bias, L3 能反超 L1 吗? — 也许 (T13 self-sup 就在赌这个), 但风险高 (训练不收敛) + 即使修了仍然有 occlusion / 稀疏 / splat 抖动等 algorithm-内禀问题.

---

## Phase 6 — 进阶 stitching: 新-B/C/D/E (2d)

每条 ~30 分钟概念 + 30 分钟读代码. 4 条 = 4h. 加 mock 跑 = 1d.

### 新-B Graph-cut seam (Boykov-Kolmogorov)
- **直觉**: 不用固定 cam 边界做接缝, 让算法自动找"最不显眼"的路径 (沿低颜色梯度走, 像沿着马路画一条线而不是穿过建筑)
- **算法**: 网格图 → 每条边权重 = 颜色差 + 梯度 + 边界距离 → min-cut 把图切成两半 → cut 路径 = seam
- **资源**: Boykov-Kolmogorov 原始 paper (2001) 或 PyMaxflow 文档
- **代码**: `code/waymo2panorama/blending/graphcut_seam.py`, `notes/new_b_graphcut_seam_design.md`

### 新-C IPM 多区域
- **IPM (Inverse Perspective Mapping)**: 如果你知道地面是平的 (z=0), 那像素 → 3D 点是个解析公式 (不需要神经网络估深度). 把地面像素精确投到 ERP.
- **多区域**: 不只地面, 还有天空 (z >> 0, 球面投影) + 建筑立面 (垂直平面, RANSAC 拟合)
- **资源**: 任意 IPM 入门 blog (autonomous driving 教材常用)
- **代码**: `code/waymo2panorama/projection/ipm_multi_region.py`, `notes/new_c_ipm_multi_region_design.md`

### 新-D Wide-baseline stereo
- **直觉**: 邻 cam (front_left 和 side_left) 距离 ~1m, 看到部分相同景物 → 经典两视角立体几何能恢复 sparse 3D 点
- **流程**: DISK 抽特征 → LightGlue 匹配 → 已知外参 (T_ego_cam) 构造 fundamental matrix F → 几何过滤 (Sampson distance) → DLT 三角化 → cheirality 过滤 (Z 必须 > 0)
- **资源**: Hartley & Zisserman 第 9 章 "Epipolar geometry" (这一章是经典, 必读), LightGlue paper (ICCV 2023)
- **代码**: `code/waymo2panorama/stereo/wide_baseline_stereo.py`, `notes/new_d_wide_baseline_stereo_research.md`

### 新-E HDR / 跨 cam 色彩补偿
- **问题**: 7 个 cam 各自跑 AE/AWB, 同一片天空在不同 cam 里亮度可能差 50+ levels.
- **方法**: 每 cam 6 参数 (3 gain + 3 bias) — `corrected = gain * raw + bias`. 用重叠区像素对儿当约束, scipy.optimize.least_squares + Huber loss 全局解一组参数. cam_0 锁 identity, 解剩下 36 参数.
- **资源**: 任意 image color transfer survey (Reinhard 2001 是经典, 但我们用更简单的 6-param)
- **代码**: `code/waymo2panorama/color/hdr_gain_estimate.py`, `notes/new_e_hdr_compensation_research.md`

### 检验 (整个 Phase 6)
- 新-B 在 cycle-PSNR 上几乎没动, 但 deliverable 还是说"win". 为啥? — 因为 cycle-PSNR 不经过 blender, seam 选择只影响最终 visual ERP, paper 论据是"视觉上接缝消失"而不是"PSNR 涨". Metric 选错就看不到 win.
- 新-D 5/7 cam-pair 成功, 2/7 失败. 失败原因? — 远场天空 / 建筑近平行射线导致三角化数值病态 (cheirality 过滤后变 0 pts) + 某些 cam 视野被近距离黑墙占满找不到对应.

---

## Phase 7 — 自监督 + 研究方法论 (选学, 1d)

### 自监督 (T13 想做的)
- **概念**: 没有人工标注 GT 时, 用数据本身的一致性当 supervision. 我们的例子: hold one cam → 用剩下 6 cam 重建它 → 跟真实图算 L1 loss → backprop 进 Pi3 depth head.
- **关键挑战**: photometric loss notorious 难收敛 (monodepth 文献里大部分论文都谈这个). 解法: SSIM term, edge-aware smoothness, 多分辨率 loss.
- **资源**: Monodepth2 paper (ICCV 2019) — 经典 self-sup depth, 必读. 我们 T13 设计基本上是 Monodepth2 改造成 ERP-aware.
- **代码 (未实现)**: `notes/t13_self_sup_pi3_finetune_design.md` 有完整设计

### 研究方法论
- **Negative result 怎么写 paper**: 我们 paper 主要价值之一是"4 种 backbone (Pi3, Depth Pro, Temporal Pi3, VGGT) 都不能让 L3 反超 L1". 写好 NEG 需要:
  1. **Apples-to-apples 比较** (我们 cycle-PSNR 在同一 10 anchor 上)
  2. **Multiple datapoint** (单 anchor 不够)
  3. **Mechanism 解释** (为啥失败 — 远场 bias / occlusion / 稀疏)
- **Paper angle decision tree**:
  - 有 ≥1 method-win → A' (Method paper, 主推)
  - 全是 NEG → C (Negative-only, D&B track)
  - 1-2 marginal positive + 强 NEG → B-with-C (混合, 我们 v5 的预案)
- **资源**: ICCV/CVPR/3DV reviewer guidelines (公开), "How to write a great research paper" (Simon Peyton Jones)

### 检验
- 我们当前 8 route + 3 NEG, paper 角度推 A'. 如果再加 新-F VGGT 也输 (大概率), 论据从"3 backbone fail"加固到"4 backbone fail (含 Meta CVPR 2025 Best Paper)". 这值得 5-15 美元 + 1 天 A100 吗? — 看你的优先级: 论据强度的 marginal gain 是否 > 时间机会成本.

---

## 资源总清单 (一站式)

| 资源 | 类型 | 必/应/选 | 哪个 phase |
|---|---|---|---|
| 3Blue1Brown 线性代数本质 (B站/YouTube) | 视频 | 必 | 0 |
| First Principles of Computer Vision (Shree Nayar) | 视频 | 必 | 1 |
| Multiple View Geometry (Hartley & Zisserman) | 书 | 必 §6.1, 应 §9 | 1, 6 |
| OpenCV Python tutorial | 在线 | 必 | 0 |
| Szeliski "Computer Vision" (免费 PDF) | 书 | 必 §9.1/9.3, 应 §11.2 | 3, 5 |
| CMU 16-385 lectures (slides) | 课件 | 应 | 2, 6 |
| AV2 Sensor Guide | 文档 | 必 | 2 |
| Apple Depth Pro paper | paper | 必 | 4 |
| Pi3 paper | paper | 必 | 4 |
| Monodepth2 paper | paper | 选 | 7 |
| LightGlue paper | paper | 选 | 6 |
| Boykov-Kolmogorov 2001 | paper | 选 | 6 |
| Burt & Adelson 1983 (multiband) | paper | 选 | 3 |
| Szeliski 第 9.3 章 blending | 书 | 必 | 3 |

---

## 我建议的"傻瓜版"最小路径 (3 天)

如果只有 3 天, 这样最 ROI:

- **Day 1 上午**: 起步建议 (1h) + Phase 0 数学 (3h) — 必须熟悉数组操作 + 矩阵乘法
- **Day 1 下午**: Phase 1 相机模型 (4h) — 看 Shree Nayar 视频 + 跑 letterbox / depth_to_cam_points 例子
- **Day 2 上午**: Phase 2 多视图 + 坐标系 (4h) — 重点 cycle-PSNR 概念 + 跑 1 次 reconstruct_l1
- **Day 2 下午**: Phase 3 经典拼接 (4h) — 看 Szeliski blending + 跑 1 次 L1 stitching
- **Day 3 上午**: Phase 4 单视图深度 (4h) — Pi3 / Depth Pro 概念, 看 1 次 local_points
- **Day 3 下午**: Phase 5 L3 NEG (4h) — 理解 paper 主 NEG, 看 reconstruct_l3

3 天后你应该能:
- 自己解释 L1 vs L3 为啥 L1 赢
- 看懂 deliverables 那 8 路线表
- 跟 Koi 讨论方法时不会卡壳

进阶 (新-B/C/D/E + T13) 可以按需扩.

---

## 给你的 4 个 mindset 建议

1. **不要怕"我数学不够"**: 现代 CV 大部分实现是 numpy/torch 一两行, 数学直觉比公式重要. 看 3Blue1Brown 视频比看教材有用 10×.
2. **代码先, 概念后**: 永远先打开我们仓库里的实现文件, 读 30 分钟代码再去看公式. 看公式时脑子里有具体函数能对得上.
3. **遇到陌生术语先 grep**: `grep -rn "Sim3" code/` 比 Google 快 — 我们项目里术语都在代码里有具体实现.
4. **每个 phase 学完写 200 字总结**: 用自己的话写, 不抄别人. 写不出来说明没真懂. 我推荐写在 `learning_notes_<phase>.md` 里, push 到 repo, 我可以 review.

---

## 找我帮忙的常见姿势

- "X 概念我看不懂" → 我给你拆 + 在我们代码里找对应例子
- "我跑了 Y 出了 Z 错" → 我 debug + 解释 root cause
- "Phase N 学完了, 给我出几道题" → 我出 4-5 道题, 难度递增
- "我想自己实现一个 mini 版的 L1" → 我给你脚手架, 你填关键函数, 我 review
