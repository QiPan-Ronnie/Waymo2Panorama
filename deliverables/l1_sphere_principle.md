# L1 球面 baseline 原理 (for Xihan)

> 这份是 5.22 meeting 后 Xihan 提的 `xihan task.md` §1 的回应 — "AV2 那個 §1.1 L1 球面 baseline 研究一下原理". 顺带把 multiband blend 也讲清楚 (因为 L1 baseline 是 sphere + multiband 两段).
>
> 代码索引: `code/waymo2panorama/projection/sphere_projection.py` + `code/waymo2panorama/blending/multiband.py`.
> 2 个新视觉范例放在 §6.

---

## 0. TL;DR

L1 baseline 把 N 个 ring cam 拼成 ERP 全景的过程 = **每个 ERP 像素发一条 ray, 找出哪些 cam 能看见这条 ray, sample 出像素, 再多频带混合**. 完全没用 depth, 没用 feature matching, 没用 BA. 假设是 **"所有内容都在球面无穷远处"** (即纯旋转, 忽略 cam 位置).

| 优点 | 缺点 |
|---|---|
| 数学简单 (一次矩阵乘 + pinhole) | 近处物体 (~10m 内) 产生 ghost (双轮子 BMW 现象) |
| CPU 跑 1024×2048 ERP < 5 s | overlap 区两 cam 看到同一物体的不同 view, blend 后变 doubled feature |
| 远处 (>30m) 几乎完美 | 不修跨 cam 曝光差 (后续 L2 HDR 才管) |
| 无新依赖, 只用 cv2 + numpy | 接缝处可能有 cos² feather 衰减导致的微弱亮度 dip |

---

## 1. ERP 坐标系约定 (重要 — 跟 Waymo 端对接时容易踩坑)

AV2 用的 ERP 排版:

```
              v=0, phi=+π/2 (天顶)
                ┌─────────────────────┐
                │                     │
   u=0          │                     │   u=W
   theta=+π    │                     │   theta=-π
                │                     │
                │                     │
                └─────────────────────┘
              v=H, phi=-π/2 (天底)
```

- `u` 从左到右, 对应 **azimuth `theta`** 从 +π → -π (即向右扫等于 ego 坐标系 CW 旋转).
- `v` 从上到下, 对应 **elevation `phi`** 从 +π/2 → -π/2.
- ego 坐标系 (AV2): **x 向前, y 向左, z 向上**, 右手系.
- 公式 (`sphere_projection.py:88-89`):
  ```
  theta = π - (u + 0.5)/W * 2π     # 注意 π - ... 而不是 ... - π, 这是 2026-05-19 修过的镜像 bug
  phi   = π/2 - (v + 0.5)/H * π
  ```

⚠️ **跟 Waymo 对接的 sanity check**: 找一段 ERP, 看 ego 前进方向上的店招/路牌, 字应该是正向不是镜像的. 如果镜像就是 `theta` 符号搞反了.

---

## 2. Sphere projection: 1 个 cam → 1 个 ERP slab (legacy L1 模式)

`sphere_projection.render_camera_to_erp(image, K, T_ego_cam, erp_hw, convergence_distance_m=None)`

#### Step 1 — ERP 像素网格 → 球面角度

```python
u_idx, v_idx = np.meshgrid(np.arange(W), np.arange(H))
theta = π - (u_idx + 0.5)/W * 2π
phi   = π/2 - (v_idx + 0.5)/H * π
```

#### Step 2 — 角度 → ego 坐标系下的单位 ray

```
d_ego = [cos(phi)·cos(theta),   # 前
         cos(phi)·sin(theta),   # 左
         sin(phi)]              # 上
```

这是 spherical → Cartesian 的标准变换. 每个 ERP 像素现在对应一条从 ego 原点射出的单位 ray (即假设观察点在 ego 原点).

#### Step 3 — Ray 转到 cam 坐标系

```
d_cam = R_cam_ego · d_ego          # R_cam_ego = T_ego_cam[:3,:3].T
```

**关键 — legacy L1 模式只用旋转, 忽略平移 `T_ego_cam[:3, 3]`**. 这是 L1 的核心假设, 等价于 "假装所有 cam 都在 ego 原点" 或者 "假装所有物体都在球面无穷远". 这是后面 ghost 的数学根因.

(如果传 `convergence_distance_m=r`, 就走 N1 模式: 先把 ray 推到距离 r, 算 3D 点, 再减 cam 平移, 再转 cam 系. 这就是 ghost 试图修但 5.26 NEG 的方向.)

#### Step 4 — Pinhole 投影

```
u_img = fx · (X_cam / Z_cam) + cx
v_img = fy · (Y_cam / Z_cam) + cy
```

只有 `Z_cam > 0` (前方) 且落在 image 边界内的 ERP 像素才有效, 其余 mask 掉.

#### Step 5 — 双线性 sample

`cv2.remap(image, u_img, v_img, INTER_LINEAR)` 把 cam 图像采样到 ERP 网格.

#### Step 6 — cos² feather weight

```
cos_axis = clip(d_cam[..., 2], 0, 1)    # = cos(angle to cam 光轴)
weight   = cos_axis ** 2
```

cam 光心朝向中心的像素权重 = 1, 越靠 cam 视野边缘越小, 边缘处 ≈ 0. 这给 blending 一个自然的 "靠中心的 cam 更可信" 的 prior.

#### Output

`(erp_rgb, erp_alpha, erp_weight)` — alpha 标 "这个 cam 有没有覆盖该像素", weight 给后续 blender 用.

---

## 3. Multiband blend: N 个 slab → 1 个 ERP

`multiband.multiband_blend(slabs, weights, num_bands=5, wrap=True)`

Burt & Adelson (1983) 经典 Laplacian pyramid blend, 加了 ERP 横向 wrap 处理.

#### 直觉

Naive feather (按 weight 加权平均) 会在 overlap 区把两 cam 的高频细节糊掉. Multiband 的解法:
- 把每个 slab 拆成 N+1 层 (Laplacian pyramid): **高频层 = sharp 细节, 低频层 = 大致颜色/亮度**
- 把 weight 也拆 (Gaussian pyramid): **高频层用 sharp 的权重 → 边界清晰; 低频层用 smooth 的权重 → 跨 cam 颜色无缝过渡**
- 各层独立加权求和后, 倒推 (pyrUp + 残差累加) 回原分辨率

#### 公式 (每层 `lvl`)

```
blended_lvl = Σ_i  laplacian_i[lvl] · gaussian_weight_i[lvl]  /  Σ_i  gaussian_weight_i[lvl]
```

#### ERP wrap 处理 (`multiband.py:25-31, 82-95`)

ERP 横向最后 1 列和第 1 列在物理上相邻, 但 `pyrDown/pyrUp` 不知道这事. 所以:
1. 拼接前在水平方向左右各 circular-pad `pad_w` 像素
2. `pad_w` 向上取整到 `2^num_bands` 的倍数 (确保金字塔下采样到底也是整数)
3. 拼完再裁掉 pad

不做这一步, ERP 接缝 (theta=π) 会有一道明显竖线.

---

## 4. 整个 L1 pipeline

```
for cam in 7 ring cams:
    slab_i, alpha_i, weight_i = render_camera_to_erp(img_i, K_i, T_i, ERP_HW)

erp = multiband_blend(slabs, weights, num_bands=5, wrap=True)
```

代价: 7 × (sphere project ~0.6 s) + multiband ~1.5 s = ~6 s/anchor @ 1024×2048 (CPU).

---

## 5. 为什么 L1 在远处对, 近处错

设两 cam 中心间距 `b ≈ 0.5 m` (典型 ring baseline), 物体距离 `d`. 这个物体在两 cam 里的视角差 (parallax angle):

```
α ≈ b / d  (rad, 当 d >> b)
```

把这个 α 翻成 ERP 像素位移 (W=2048 ERP):

```
Δu_erp ≈ α · W / (2π)
```

| d (物体距离) | α (rad) | Δu_erp (像素) |
|---|---|---|
| 100 m | 0.005 | 1.6 |
| 30 m | 0.017 | 5.4 |
| 10 m | 0.05 | 16 |
| **3 m (近场 BMW)** | **0.167** | **54** ← 双轮子!! |
| 1.5 m | 0.33 | 109 |

L1 假设了 d = ∞, 所以 Δu_erp = 0. 实际 3m 处的 BMW, 两 cam 各自的 ERP 投影错开 54 像素 → multiband blend 把两份错位的车身加在一起 → **doubled wheel**.

这就是为什么远处建筑天空 L1 完美, 近场车辆 ghost. 5.26-5.27 整个 N1/depth/OF 工作都在试图修这个.

---

## 6. 2 个新范例 (AV2 raw, 1024×2048)

合并面板: `deliverables/xihan/l1_examples_panel.png` (2048×2144, 2 行带 caption).

### Example A — `0bae3b5e anchor 30` (downtown intersection, **far-field 干净 case**)

`deliverables/l1_baseline_diverse/0bae3b5e_a030_L1_multiband_1024x2048.png`

场景: 城市十字路口, 前后方有中距离 (~15-30m) 的车, 周围建筑物 10m+. 看 ERP 整圈: 建筑物边缘连续, 天空无接缝, 路面连续, 红绿灯单一. **demonstrates: L1 在 far-field-dominant 场景 production-ready**. 给 Waymo 端: 如果你的 L1 接通后跑 highway 场景, 期待这种品质.

### Example B — `fbee355f anchor 30` (parking lot + bridge underpass, **near-field ghost case**)

`deliverables/l1_baseline_diverse/fbee355f_a030_L1_multiband_1024x2048.png`

场景: ego 在停车场, 右侧 ~2m 处有一辆白色卡车 (`www.foothillanim...` 字样可见), 上方是高架桥. 看右侧白卡车的位置 — overlap 区你能看到 cab/cargo 接缝处 ghosting; 底部地面在 cam 接缝 (theta=±π/N) 上有明显的曲线 distortion. **demonstrates: L1 在 < 3 m 近场车上的 failure mode** = 5.27 hard_select pipeline 试图修复的核心 case.

### 比对总结

两个例子放一起说明: **L1 不是 "好 vs 坏"** 的二元判断, 而是 **scene-dependent**. Far-field 城市 / highway 场景 L1 够用; 近场 (parking, traffic jam) ghost 显著, 需要后续 hard_select + HDR + OF.

---

## 7. 对 Waymo 端的可移植性提示

1. **坐标系约定不同要修**: Waymo ego 是 +x 前 +y 左 +z 上 (跟 AV2 一致, 好事). 但 cam intrinsics format 不同, Waymo 给的是 `[fx, fy, cx, cy, k1, k2, k3, p1, p2]` 9-tuple, 不是 3×3 K matrix. 自己拼 K = `[[fx,0,cx],[0,fy,cy],[0,0,1]]`.
2. **Distortion**: Waymo cam 用 polynomial radial + tangential distortion. AV2 sphere_projection 假设 pinhole (no distortion). 要么先 `cv2.undistort` 原图, 要么扩展 step 4 加 distortion 项.
3. **Rolling shutter (Xihan 提到)**: Waymo cam 不是 global shutter, jelly effect 在高速场景很明显. L1 sphere 完全不补偿, 这是 Waymo 拼接的额外难度. AV2 cam 是 global shutter, 我们没遇到这问题.
4. **Cam 数量**: Waymo 有 5 front-facing + 3 rear = 8 个 (跟 xihan task.md 一致), AV2 是 7 ring. 公式不变, 多塞几个 slab 进 multiband 就行.
5. **Image 尺寸**: Waymo 5 个 front cam 是 972×1079 (这次 Xihan 数据), 3 个 rear 是 972×587. AV2 是 2048×1550. 不影响算法, 只影响 K 的 `cx,cy`.

---

## 8. Quick eval 协议 (给 Xihan)

把这套 L1 baseline 接到你 Waymo loader 上后, sanity check 顺序:

1. **单 cam smoke**: 跑 front_center 单 cam ERP, 看图像中央条带是否清晰, 字是不是正向. (验证坐标系 + intrinsics)
2. **2 cam pair**: 跑 front_center + front_left, 看 overlap 区 (ERP 左中) 是否有合理 blend, 没明显接缝.
3. **8 cam full**: 全跑, 看是否 360° 闭环 (theta=π 接缝处没竖线).
4. **量化**: 跑 NCC pano-vs-winner-cam (脚本 `scripts/phase3/measure_overlap_ncc.py`). AV2 上 multiband 基线是 0.6461. Waymo 数字会有差异但量级类似 (0.5-0.7 范围).

如果某步坏掉, 多半是 intrinsics/extrinsics 解析的问题, 不是算法的问题.
