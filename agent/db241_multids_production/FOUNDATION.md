# DB-241 根基代码契约

**这份文件回答一个问题：交给 Louison 的每一个像素，是由哪几行代码决定的。**

背景：koi 认可的 `clip_broute_rulemask.mp4`（AV2 00a6ffc1，2026-08-14）
其产出代码当时是一段 heredoc，**从未落盘**，2026-08-16 从会话 transcript
逐字恢复。为避免同类事故重演，本文把根基显式钉死，并用 golden test 守住。

---

## 1. 三层结构

```
  ┌─ CORE ─────────────────── 与数据集无关，四个源共用，改动需过 golden test
  │   ERP 网格      H=1024  W=2048  DIRS  DIRS_FLAT
  │   投影          _project(cal_c, X)            ← 目前纯 pinhole
  │   采样          bilinear_rgb
  │   位姿          load_ego_interp  emc_poses     ← Slerp + 线性插值
  │   支持域        camera_support_emc             ← 深度无关，1px 内缩
  │
  ├─ ADAPTER ──────────────── 每个数据集一份，只做"翻译"，不含任何判断
  │   present_cameras(log)      → 本场景实际存在的相机
  │   load_calibration(log)     → {cam: K, R, t, shape}
  │   manifest_from_dir(log,k)  → {anchor_ts, cam_ts{cam: ns}}
  │   load_images(log, cam_ts)  → {cam: HxWx3}
  │
  └─ PRODUCER ────────────── koi 认可的配方本身，与数据集无关
      adjacent_pairs(cams, pose, sup)   环邻接，按光轴方位角排序
                                        不闭合的环不强行闭合
      rule_mask(...)                    24 帧 wedge 并集 → 逐对包围列
                                        + 4px 裙边 + 子午线 roll → 整条矩形
      render_frame(...)                 旋转-only 重采样，warp 恒为 0
      produce(...)                      93 帧 + 93 mask + manifest
```

**关键性质：CORE 与 PRODUCER 都不需要 LiDAR、不需要标注、不需要深度。**
规则 mask 只是标定 + ego pose + 曝光时间戳的函数。这既是它能跑在笔记本上的原因，
也是它能移植到**完全没有 LiDAR 的 Waymo E2E** 的原因。

---

## 2. 文件清单（依赖闭包，除此之外产线不依赖任何东西）

| 文件 | 层 | 作用 |
|---|---|---|
| `db238_screening/db238_screen.py` | CORE + AV2 ADAPTER | ERP 网格、`_project`、AV2 的标定/清单/取图。**目前 CORE 与 AV2 适配混在一起**（见 §5 债务） |
| `db239_seam_mask/db239_seam_mask.py` | CORE | `load_ego_interp` / `emc_poses` / `camera_support_emc` / `bilinear_rgb` |
| `db240_rule_dataset/db240_rule_dataset.py` | PRODUCER | 现行产线 |
| `db240_rule_dataset/db240_koi_reference.py` | 参照 | 从 transcript 恢复的原始配方 + 两条不变量 |
| `db241_multids_production/test_db241_golden_rule_mask.py` | 守门 | golden test |
| `db241_multids_production/golden/` | 基准 | 217 KB，见 §3 |

CORE 实际被用到的 API 面（已核对，仅此）：
`SC.H W DIRS DIRS_FLAT CAMERAS ADJACENT _project load_calibration load_images
manifest_from_dir` · `SM.load_ego_interp emc_poses camera_support_emc bilinear_rgb`

---

## 3. Golden test —— "确定下来"的实质

`golden/` 里是 koi 认可那一次运行的**输入与输出**，共 217 KB：

```
calibration/egovehicle_SE3_sensor.feather   4.6 KB
calibration/intrinsics.feather              5.3 KB
city_SE3_egovehicle.feather                 168 KB
manifest_93.json                            36 KB   93 帧 × 7 相机时间戳
seam_rule_mask_KOI_APPROVED.png             3.0 KB  ← 基准输出
seam_union_measured.png                     5.5 KB  ← 93 帧实测矛盾并集
```

**不含任何图像**（651 张 JPEG 一张都不需要），因此离线、秒级、随处可跑。

测试断言五条：

1. 7 相机环必须闭合
2. 条宽 == `[58, 60, 61, 62, 63, 68, 68]`
3. mask 与 koi 认可的 PNG **逐位相同**
4. **实测矛盾像素 0 个落在 mask 之外** —— 这条才让"blanket"名副其实
5. 面积 0.2148 ± 0.0005 of band —— 即使仍是超集，悄悄变宽也会被抓

**已做突变验证**：把产线退回旧的单帧 wedge，四条断言同时触发
（条宽变窄、2388 px 不同、5 px 漏网、面积漂到 0.2119）。
一个没验证过会失败的 golden test 等于没有。

```bash
python agent/db241_multids_production/test_db241_golden_rule_mask.py
```

---

## 4. 三条不可让步的不变量

**I1 — blanket 必须是严格超集。** 规则 mask 必须吞掉整窗口实测矛盾的每一个像素。
违反即意味着我们声称涂掉了接缝、实际没涂干净。

**I2 — wedge 取整窗口并集，不取单帧。** EMC 下每帧各相机旋转不同，
支持域边界逐帧抖动几个像素。原脚本注释原话：*"support boundaries wobble a few px"*。
实测：单帧版漏 5 px（违反 I1），24 帧并集版漏 0。

**I3 — KEEP 只能标在真的采样到了像素的位置（`written`，不是 `domain`）。**

`camera_support_emc` 说"这条光线应该落在该相机图内"，`_project` 说"它实际落没落"。
两者在支持域边界上**每帧约 330 px 不一致**：那些像素留黑，却被 `domain` 认领。
把它们标成 KEEP，等于告诉 Louison"这里是真实传感器像素、请算 loss"——
而那里是个洞。**这正是 mask 契约存在的理由，违反它等于契约失效。**

端到端实测（AV2 00a6ffc1，93 帧）：

| | 假 KEEP（黑像素标成真） | 丢弃的真实像素 |
|---|---|---|
| koi 收到的那一包 | **33,413 px** | 8,310 px |
| 修复前的产线 | 22,947 px | 0 |
| **修复后（现行）** | **0** | **0** |

修复方式：`render_frame` 返回 `written` 而非 `domain`，`keep = written & ~kill`。
**RGB 输出不受影响，93 帧仍与 koi 收到的逐字节相同**（max\|diff\| = 0 / 5.85 亿像素）。
manifest 新增 `keep_px_that_are_black` 字段，量产时必须为 0。

> 注：这项修复使 mask 通道与 koi 那一包有 0.017% 差异。差异**全部是删除假 KEEP**，
> 方向单调向"更诚实"。koi 认可的是视频（RGB），RGB 未变。

---

## 5. 已知技术债（不影响当前交付，移植前须处理）

- **D1 CORE 与 AV2 适配混在 `db238_screen.py`**：同一文件里既有 ERP 网格和投影，
  也有 AV2 的 S3 抓取、LiDAR 载入、筛选逻辑（21 KB）。移植第二个数据集时应先切开。
- **D2 `_project` 是纯 pinhole，无畸变**。Waymo Perception `k1=0.347`
  → 图像边缘径向位移约 **71 px**，比整条接缝带（58–68 px）还宽，**必须处理**。
  Waymo E2E `k=0.0736` → 约 6.8 px，超过 4 px 裙边，同样要处理。
  nuScenes 官方原图已去畸变，是唯一零成本的。
- **D3 无 EMC 时的补偿**：E2E 逐相机时间戳全为 0，只能静态 rig。
  AV2 上实测对照：静态版每对最多窄 2 px、漏 9 px 矛盾。
  `SKIRT_PX` 4 → 6 即可覆盖，代价 mask 多约 0.7%。
- **D4 `ELEV_DEG=35` 与 `SKIRT_PX=4` 是 AV2 上定的**，其他 rig 需复核。

---

## 6. ⚠️ 当前最大风险：**根基代码全部未提交**

`git status` 显示 `agent/db238_screening/`、`db239_seam_mask/`、`db240_rule_dataset/`、
`db241_multids_production/` **全部为未跟踪 `??`**。分支 `db236-av2-scene-band`，
HEAD `6857398`。

**koi 认可的配方目前不在任何 commit 里。** 它已经丢过一次（heredoc 未落盘），
这次是靠 transcript 捞回来的；transcript 不是永久存储。

同样状况还有 `.worktrees/db213-root-artifact-fixes` 里 17 个 `db181_multids` adapter
（nuScenes 那条最省事的移植路径依赖其中之一），也全部未跟踪。

**建议：提交到当前特性分支（不动 main、不 push）。等你一句话。**
