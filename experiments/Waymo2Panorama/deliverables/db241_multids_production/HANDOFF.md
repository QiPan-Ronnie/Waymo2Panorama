# DB-241 交接（2026-08-16 本机停机，准备转 Colab）

本机所有产线已停，GPU 与内存已释放。以下是"现在有什么、在哪里、怎么接着跑"。

---

## 1. 产物

| 位置 | 内容 |
|---|---|
| `E:\w2p_data\dataset_out\` | **930 个样本**，四源，逐个通过消费端校验 |
| `E:\w2p_data\db241_delivery\` | 交付结构（62 GB，硬链接零额外占用） |
| `E:\w2p_data\koi_eyecheck_2026-08-16\` | koi 的 B7 眼验包，每源一个白天样本 + clip |
| `w2p-db236` 分支 `db236-av2-scene-band` | 全部代码，未推送、未动 main |

### 样本分布

| 源 | 样本 | 相机 | 环 | 接缝带 | mask 占带 | 带内真实像素 | 帧间隔抖动 |
|---|---|---|---|---|---|---|---|
| Waymo E2E | 339 | 8 | 闭合 | 26–31 px | 11.2% | 57% | cv 0.000 |
| Argoverse 2 | 222 | 7 | 闭合 | 56–71 px | 21.5% | 70% | cv 0.000 |
| Waymo Perception | 199 | 5 | **开口** | 33–45 px | 7.9% | **27%** | 均匀 |
| nuScenes | 170 | 6 | 闭合 | 35–85 px | 18.0% | 59% | **cv 0.20** |

### 交付划分（`db241_delivery/split.json`）

- **OOD holdout = nuScenes**（170，当前最小源，按 koi「取量最小的整个留出」）
- train/test 8:2：AV2 178/44 · E2E 271/68 · Perception 159/40
- **给 Louison 的 = 608 个**

> OOD 由**实际产量**决定，不是预先指定。继续产出后重跑打包器会重算，
> 若 nuScenes 反超则 holdout 会换源 —— 这点在交给 Louison 前要冻结。

---

## 2. 契约落实情况（koi 08-14）

| 要求 | 状态 |
|---|---|
| 93 帧同质 | ✅ |
| 不补地面/天空、无反投影 | ✅ |
| 接缝直接规则涂黑 | ✅ **逐位复现 koi 认可的那版**（golden test 守住） |
| loss 只算白区 | ✅ README 明写；`keep_px_not_written=0` 由代码强制 |
| 最小源整体 OOD | ✅ 自动按产量选 |
| 其余 8:2 划分 | ✅ 按 scene id 确定性划分 |
| README 写清给谁/留谁 | ✅ 从磁盘实际文件生成 |
| **车头两版 50/50** | ❌ **没做，且如实标注** —— 见 §4 |

---

## 3. 代码地图

```
agent/db240_rule_dataset/
  db240_koi_reference.py     从 transcript 逐字恢复的 koi 认可配方（参照物）
  db240_rule_dataset.py      产线：uniform_time_indices / rule_mask / render_frame / produce
  db240_gpu.py               GPU 路径（与 CPU 逐位一致，8.4× 热点提速）
agent/db241_multids_production/
  db241_driver.py            adapter 输出 -> 产线 -> v15 布局包，含 K1/K2/I3 gate
  db241_waymo_tfrecord.py    Waymo tfrecord -> 伪 AV2（含强制去畸变）
  db241_nuscenes_cams.py     nuScenes camera-only 转换（免下 13.75 GB/分片的 LiDAR）
  db241_e2e_index.py         E2E range 索引（230 GB -> 每样本 190 MB）
  db241_batch_{av2,e2e}.py   批量产出（支持 W2P_SHARD 分片并行）
  db241_supervisor.py        无人值守监管：多 worker、磁盘回收、GPU 解释器
  db241_validate.py          消费端校验（含查重、冻结画面、抖动报告）
  db241_reclaim.py           删除已消费的源 log
  db241_koi_eyecheck.py      B7 眼验包生成
  test_db241_golden_rule_mask.py + golden/   217 KB fixture，五条断言
agent/db181_multids_snapshot/   抢救出来的 25 个未跟踪 adapter
```

### 关键环境事实（Colab 上要重建）

- **GPU 解释器**：本机用 `D:\miniconda3\envs\CSCI699\python.exe`（torch 2.10+cu128）。
  Colab 直接用默认环境即可，`W2P_GPU=1` 开启，不可用会自动回落 CPU。
- **Waymo 需要 GCS token**：`W2P_GCS_TOKEN_FILE` 指向一个文件，外部进程定期刷新。
  **token 只活 1 小时，索引/长跑必须热更新** —— 这个坑踩过，表现是 61 个分片全 401。
- **公开数据源免登录**：AV2 `s3://argoverse`、nuScenes `s3://motional-nuscenes`（有纯相机分包）。

---

## 4. 三件必须由人决定的事（未解决，不要当成已完成）

### ① 车头两版没做 —— 已如实标注，不要当成做了

koi 要求车头黑/保留两版按场景各半。产线接受 `hood_variant="black"` 但**只有同时传
`hood_mask_path` 才生效，而从来没人传过**。曾有一半样本 manifest 写着 `black` 而数据相同 ——
**196 个标签已改正**，现在 manifest 记录**实际应用的**版本 + `hood_mask_applied: false`。

**没有凭猜造 mask**，三条理由：v15 的 AV2 车头 mask 是 1+92 时代几何、对不齐现在的渲染；
用「车头是唯一相对车静止的物体」实测，AV2 车身在画面**最左右（车后）**、带内仅约 0.02%；
凭猜画一张就是在真实像素上涂黑，违背"真实优先"。

**请 koi 定**：(A) 每 rig 做验证过的车身 mask，(B) 确认不做，(C) 只对 AV2 做。

### ② Waymo Perception 带内只有 27% 真实像素

koi 0731 说「就算 open waymo 只有 252 度也没关系」——判断依然成立，但实测密度更低：
去畸变裁边后方位覆盖 **202°**、空洞 **158°**；最窄相机垂直仅 **±12.1°**。
叠加后一个样本约 **73% 是黑的**。眼验包 README 里已写明，请他看到数字再拍板。

### ③ ⚠️ stage-2 的 loss 是否已乘 mask —— 优先级最高

koi 0814:249 对 Louison 说「你算 loss 的时候记得**这一次**就是要完全只算景硕给你 mask 的地方，
绝对不要算那些黑色」。「这一次」说明是**改动**；而 Louison 0727:110 描述 stage-1 的极性
**正好相反**（白区不算 loss、mask 区算）。四份转写稿**没有一处证明代码改了**。

**这条不落实，前面所有 mask 工作在训练端等于没做。** 只需向 Louison 问一句。

---

## 5. Colab 上继续跑的要点

1. **`db241_supervisor.py` 可直接复用**，改 `OUT`/`TOKEN`/`GPU_PY` 三个常量即可
2. **worker 数按内存定，不是按核数** —— 本机每 worker 峰值 524 MB，
   开 30 个在 31 GB 上直接 MemoryError；Colab 内存更小，建议先测再定
3. **磁盘**：每样本约 66 MB。`db241_reclaim.py` 删除已消费源 log（本机回收过 49.5 GB）
4. **打包用硬链接**，不要复制 —— 复制会让数据集在同一卷上存在两份
5. **每次改产线后跑 golden test**，它是 koi 认可版的唯一保险

---

## 6. 本轮修掉的缺陷（都记在 commit 里）

**koi 收到的那一包里的真缺陷**：33,413 px 假 KEEP（把洞标成真值让模型算 loss）→ 现在 0。

**移植时的静默失败**：`present_cameras` 丢掉 nuScenes 的 `ring_rear`（会造出假闭合环）、
`manifest_from_dir` 强制要 LiDAR（无 LiDAR 的 E2E 完全无法产出）。

**我自己犯的七个**，每一个都是独立校验层或用户人眼抓到的，**没有一个是产线自己报的**：

1. I3 gate 拿颜色当违约判据 → 误杀 4 个 nuScenes 夜景
2. `ex.map` 单点故障吞掉 24 个分片、约 80 分钟工作
3. token 过期被当成网络抖动 → 61 个分片全 401
4. 67 个空目录被计入样本数
5. 车头两版是假的（一份数据两个标签）
6. 固定临时路径并发竞争 → 2 组 E2E 样本内容重复
7. AV2 抓取器走过 split 末尾后**空转 95 轮**，日志全是"成功"

**用户人眼抓到的两个**（我全部检查都漏掉）：

8. **E2E 视频不动** —— context 带帧号导致每相机只存进 1 张源图，渲了 93 遍
9. **nuScenes 卡顿** —— 源节奏 50/100/100 ms，产线按文件序号取帧原样搬进视频

后两个已变成自动检查（冻结画面检测 + `frame_gap_cv` 记录）。
