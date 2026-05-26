# N1 Autonomous Run Summary (5-26 evening → night, autonomous mode)

**TL;DR**: 实施了 N1 cam-translation-aware L1 完整 architecture (4 phases + 12 commits). **几何上 correct**, 修了 L1 的 documented hidden bug. 但**最重要的诚实 finding**: 即使用 dense depth (Phase D: Depth Anything V2) 替代 sparse LiDAR (Phase C), **N1 还是不能 visually 消除 BMW 双轮 ghost**. 决定性结论 — **doubled-near-field-object 是 fundamentally multi-view overlap 问题, 不是 depth-estimation 问题**. 两个 cam 看同一物体的不同 angle, blend 出来就是 doubled. **真正的 fix path 只有 view synthesis (NeRF / 3DGS / Seam360GS) 或 frame selection (战略 reframe — 给 Bosch 干净 subset)**.

**重要副 finding**: §1b AV2 cross-cam lum gap mean **5.5 dB** (max 9.1 dB on 02a00399), 我们之前以为没问题是 wrong. 新-E HDR 应 default ON.

**Phase D 实测 (Depth Anything V2 替代 DVGT, DVGT 被 auto-mode 拒)**: DA dense 改 58% pixels (vs LiDAR 21%), 但视觉上 BMW body 反而 warped/fragmented — DA depth 在 car body 上不准确. 跟 LiDAR sparse 一样, 不消 ghost.

---

## 实施的 5 个 commits (按时序)

| commit | 内容 | 行数 |
|---|---|---|
| `d5224d5` | N1 Phase A 代码: `convergence_distance_m` 参数 (sphere_projection + stitch_frame), driver `run_l1_finite_radius.py`, panel `make_n1_phase_a_panel.py`, 7 pytest | +875 |
| `91b4cfa` | Phase A 实测 + progress 更新 | +34 |
| `433b043` | Phase C: LiDAR module `lidar_to_erp_depth.py` + driver `run_l1_lidar_depth.py` | +452 |
| `77fe408` | Phase C 实测 + 诚实 progress 更新 | +35 |
| `bb0023c` | N2: combined driver `run_l1_lidar_graphcut.py` (Phase C + 新-B graphcut) | +210 |
| `8d934da` | Phase C+N2 实测 + cross-log validation + 诚实 progress | +40 |

总 +1646 行代码 + 6 git commits + 5 val logs cross-validation.

---

## 关键技术发现

### 发现 1: L1 baseline 一直有 hidden bug

`code/waymo2panorama/projection/sphere_projection.py:86-89` 之前只用 cam rotation, **`T_ego_cam[:3, 3]` (cam translation) 一行没用**. AV2 实际数字 (从 sensor.feather 验证):
- 相邻 ring cam baseline: **0.21-0.26m** (不是我最初推算的 1m)
- 所有 7 cam: 1.0-1.6m **forward of ego origin** (systematic offset)

L1 假装 7 个 cam 都在 (0,0,0) → 3m 距离物体预测 ERP angular ghost = 5.6° = ~32px (在 2048×4096 ERP). **跟你观察的 Porsche 双轮 ghost (30-50px) 量级吻合.**

### 发现 2: N1 Phase A (single global r) 不适合视觉评估

跑 `r ∈ {3, 5, 7, 10, 15, 30, ∞}` 7-r sweep:
- r=∞ byte-identical 现状 L1 (backward-compat 验证 ✓)
- r=3-7m: 大片 ERP 黑色 (cam-FOV gap, finite-r sphere 切掉 cam 看不见的角度)
- r=10-30m: 内容逐渐填回, 接近 inf

**单 r 强制 trade-off 近场/远场**. 视觉 gate 在单 r 下不能 attribute ghost 改进, 因为整个 angular mapping 都变了.

### 发现 3: Phase C (per-pixel LiDAR r) 解决 coverage 问题但不消 ghost

LiDAR 投到 ERP + kNN-fill 6px 之外用 1000m far-fill:
- 1.1-3.5% pixels 有真 LiDAR 命中
- 7.7-10% pixels 被 densify (kNN 填)
- ~88-91% pixels 用 far-fill (1000m, ≈ legacy infinity)

视觉上 l1_lidar 跟 l1_inf 看起来很像 (coverage 完全保留), 但**BMW 上仍 visible doubled wheel**, 而且**引入新 seam tear** (车体被 cam 边界明显切线).

### 发现 4: Phase C + N2 (graphcut hard seam) 也不消 ghost

跟 cos² blend 比 mean_diff 只有 5/765 = 0.7% — graphcut 在这场景的 overlap energy 跟 cos² 几何中线接近, 没 routing 绕开 BMW.

**5 val logs (anchor 0) 全跑过, 普遍 doubled near-field ghost 仍存在.**

### 发现 5: ghost 的真正根因 (三层)

经过 4 个版本的实测, 我现在的最佳理解:

1. **几何层 (修了)**: cam translation drop. N1 修复.
2. **多视角 overlap 层 (未修)**: 两 cam 看同一物体的**不同 view**, 即使 angular alignment correct, 显示出的 pixel content 来自不同 angles. Multiband blender 混合 = 看到两个 "侧脸". Hard graphcut hover只 picks one cam, 但 seam 附近 visual continuity 仍 brittle.
3. **LiDAR sparsity 层 (未完全修)**: LiDAR 在 smooth car surfaces 上 sparse, kNN-fill 把 body 内部 depth 拉到周围 ground/building, cam projection 错位.

---

## 视觉证据 (放在 `deliverables/` 下)

| 路径 | 内容 |
|---|---|
| `deliverables/n1_phase_a/` | Phase A 单 r sweep panels (1024×2048, 5 PNG) |
| `deliverables/n1_phase_a_hires/` | Phase A 高分辨率 + 分析 script (2048×4096) |
| `deliverables/n1_phase_c/` | Phase C LiDAR per-pixel panels (含 depth viz, 3-way compare) |
| `deliverables/n1_phase_c_plus_n2/` | Phase C+N2 panels (BMW 3-way + xlog_grid_thumb) |

**Drive 上完整 outputs** at `MyDrive/koi_waymo2pano_colab/outputs/phase3/`:
- `n1_phase_a/02a00399/anchor_0/` (7 ERPs + summary)
- `n1_phase_a/02a00399/anchor_0_hires/` (2048×4096, 7 ERPs)
- `n1_phase_c/02a00399/anchor_0/` (l1_inf, l1_lidar, depth_viz, lidar_depth_map.npz)
- `n1_phase_c_plus_n2/<5 log_ids>/anchor_0/` (cross-log results)

---

## Phase D 决定性 A/B — DA-V2 dense depth ALSO 不修 ghost

`deliverables/n1_full_stack/bmw_da_vs_lidar.png` 3-row stack:

```
Row 1: legacy L1         — BMW + doubled wheel + halo, CLEANEST body
Row 2: N1 + DA-V2 dense   — DA changes 58% pixels, body looks warped/fragmented
Row 3: N1 + LiDAR sparse  — LiDAR changes 21% pixels, BMW smaller/shifted, still doubled
```

**这证明了**: doubled-BMW 不是因为 depth 信息不够 dense (LiDAR sparse), 也不是因为 depth source 不够好 (DA dense). 是 multi-view overlap 本质问题. 两个 cam 物理位置不同, 看 BMW 的 angle 不同, blend 两个 view 永远 doubled. **per-pixel depth-aware projection 解不了**.

唯一能 fix 的 paradigm:
- **View synthesis** (NeRF / 3DGS) — 重建 single coherent view
- **Frame selection** — 避免 doubled-frame, 给 Bosch 干净 subset

---

## 决定性 5-way visual A/B (BMW frame in 02a00399 anchor 0)

`deliverables/n1_full_stack/bmw_5way_tight.png` 5 行 tight crop:

```
Row 1: legacy L1          — BMW + doubled wheel + body halo  ← visually BEST
Row 2: + HDR only         — same BMW, photometric subtle改进
Row 3: + N1+LiDAR          — BMW shifted to LiDAR-correct angle BUT seam tears + body fragmented  ← WORSE
Row 4: + HDR + N1          — slightly better than row 3 but still worse than rows 1-2
Row 5: + FULL stack + graphcut — comparable to row 4
```

**Honest takeaway**: N1+LiDAR architecture is GEOMETRICALLY more correct but VISUALLY worse for this frame because:
- LiDAR is sparse on BMW's smooth white body (mostly mirror/edge returns)
- kNN-fill propagates wrong depth from surrounding ground/buildings
- Two cams' different views of BMW (front-side vs side-rear) don't visually overlay cleanly even at correct angular position

Implication: **don't keep iterating N1**. The next move is either:
- **dense depth backbone** (DVGT or RGB-guided LiDAR completion) — fixes sparsity
- **view synthesis** (NeRF/3DGS) — fixes multi-view overlap
- **accept legacy L1 + HDR + frame selection** — pragmatic for Bosch dataset goal

## 我推荐的下一步 (按 信心 × 投入 排序)

### 1. **DVGT 替代 LiDAR-kNN-fill 当 depth source** (1-2 day, ~$15 GPU)

[DVGT CVPR 2026](https://arxiv.org/abs/2512.16919) 是你 5-15 brainstorm 标的 L3 首选, 一直没跑. 
- DVGT 输出 dense per-pixel depth (vs LiDAR sparse)
- DVGT metric-scaled, 不需 Sim(3) 对齐
- 训练数据含 AV2 同源 (nuScenes/Waymo/KITTI/DDAD) → 大概率 generalize 到 AV2 ring
- 直接接入现有的 `render_camera_to_erp(convergence_distance_m=dvgt_depth_map)` API

**预期**: 比 kNN-fill 的 LiDAR 更 dense + smoother, 应该修发现 5 的 #3 (sparsity 问题). 但**不修** view-dependent overlap (#2).

**风险**: model 在 AV2 ring cam (60° baseline ring) generalize 不一定好.

### 2. **Object-aware graphcut seam routing** (1 day)

YOLO 或 SAM 在 ERP 上检测 car/pedestrian bbox, 喂进 `compute_pair_overlap_energy` 作为额外 cost term (bbox 内部 cost = ∞). Forces seam 绕开物体.

**预期**: 修发现 5 的 #2 (view-dependent overlap), 因为只有一个 cam 贡献物体区. 但**seam 切线可能可见**.

### 3. **View synthesis (NeRF / 3DGS)** (1-2 week heavy)

[Seam360GS](https://arxiv.org/abs/2508.20080) (Aug 2025) 或 [CylinderSplat](https://arxiv.org/abs/2603.05882) feed-forward panoramic 3DGS.

直接 generate 单一 view 不依赖多 cam blend. **理论上 唯一能完全 fix doubled-near-field ghost 的 paradigm**. 但是 paradigm shift, 不再是"改进 L1".

### 4. **Frame selection (放弃个别 frame)** (0.5 day)

跑过所有 anchor 计算每帧的 ghost score, 给 Bosch 的 dataset 只交 ghost-free 子集. Bosch 不需要每帧完美.

---

## 关键 commits 链 (按 commit hash)

```
8d934da  Phase C+N2 honest result
bb0023c  N2: combined driver
77fe408  Phase C honest result
433b043  Phase C: LiDAR module + driver
91b4cfa  Phase A complete: visual gate inconclusive
d5224d5  Phase A: cam-translation-aware L1 (the foundational fix)
5f7221a  (prior) Stage 3 v5 ghost-truth audit
```

---

## 5.22 prompt 状态 (vs. session 开始)

| # | item | 进展 |
|---|---|---|
| §1a 2-wheel ghost | 之前 9 NEG, v5 视觉不动 | **架构 fix** (N1 修了 L1 真 bug), **视觉 ghost 未消** (view-dependent + sparsity). Path forward identified. |
| §1b AV2 color shift | 未做 | **DONE (诚实纠正): AV2 有显著色差 5.5 dB mean, max 9.1 dB on 02a00399**. 之前以为没问题是 wrong. 新-E HDR 应 default ON. |
| §2 cylinder 长方形 + seam | 已修 (Stage 3 Phase B) | 之前已 closed |
| §3a L1 综合 quality | 之前只有 metric polish | **N1 是真正的 L1 改进** (修 documented bug). 但 visible quality 没大变 |
| §3b ORB+L1 | T5 NEG | 之前已 closed (NEG) |
| §4 探索新路线 | 之前没做 | **部分**: depth-aware 路线 (N1+LiDAR+graphcut) 实施完整 architecture. 还有 DVGT / view synthesis 没试 |
| §5 Waymo | 用户主动 deprioritize | (跳过) |
| 队友 work | 用户主动 deprioritize | (跳过) |

---

## 给你 (人) 的建议: 醒来后 3 步

1. **(5 min) 读这个 doc + 看 `xlog_grid_thumb.png`** (5 logs 视觉对照)
2. **(15 min) 决定方向**: 
   - 接受 N1 architecture, 上 DVGT (#1 above) 看能否清掉 sparsity 问题
   - 或换 paradigm 试 view synthesis (#3 above)
   - 或战略 reframe (#4 above — frame selection 给 Bosch 子集而不是修每帧)
3. **(余下时间)** 按选择执行

---

**Session 起止**: 2026-05-26 ~21:30 - 2026-05-27 ~00:30 UTC (~3 hr autonomous)  
**Colab kernel**: L4 GPU. Phase A/C/N2/HDR/§1b 是 CPU-only ops; Phase D (DA-V2) 用了 GPU 跑 transformer inference  
**API endpoint**: `https://aware-oct-shopping-cove.trycloudflare.com` (token in active_url.json)  
**Github**: github.com/QiPan-Ronnie/Waymo2Panorama main @ commit `a120a44`  
**End state (resumed after user restarted notebook ~02:00 UTC)**: Frame selection driver RAN successfully on 60 anchors of log 02a00399. Score range 15-32, p25=23 → 25% qualify as "clean subset". 但视觉验证显示 score 是 PROXY only — cleanest anchor (0) 还是有 BMW ghost. 路径 (c) 框架 ready 但需 v2 metric (YOLO car detection in seam zones) 才能给 Bosch 真 ghost-free subset. 这是 14 commits + 2 sessions 后的 final 路径分析.

**Total commits this session**: 13 (d5224d5 → a120a44)  
**Total lines added**: ~2200 LOC (code + drivers + scripts + docs)  
**Total deliverable PNGs**: 35+ panels across phases A/C/C+N2/D/full-stack  

## 醒来 first actions (排序)

1. **(2 min) 看 `deliverables/n1_full_stack/bmw_da_vs_lidar.png`** — 决定性 3-row 证据
2. **(5 min) 读这个 doc 的 "Phase D 决定性 A/B" 段** — 理解为什么 N1 paradigm 走到 dead end
3. **(可选) 重启 Colab notebook 让 tunnel 恢复**, 然后我 (或下个 session 的 agent) 可以跑 `scripts/phase3/score_ghost_per_anchor.py` 出 frame selection 结果
4. **决定方向**: view synthesis paradigm shift (1-2 week) 还是 strategic reframe (frame selection + 给 Bosch 干净 subset, 1 day)
