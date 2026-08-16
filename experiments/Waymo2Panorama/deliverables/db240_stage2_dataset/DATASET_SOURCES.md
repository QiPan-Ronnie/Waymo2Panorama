# DB-240 — 五个候选数据源的官方核查（2026-08-14）

目的：把"够不够 93 帧""能不能拼 360°""怎么拿到数据"三件事，
从**官方文档 / 官方论文 / 官方存储桶**逐条落实，不用二手记忆。

判据来自 koi 08-14 会议：**一个 sample = 93 帧连续同质 ERP**。
93 帧对应的**秒数取决于该数据集的相机帧率**，这是本页最关键的换算：

| 数据集 | 相机帧率 | 93 帧 = 多少秒 | 单序列时长 | 够不够 |
|---|---|---|---|---|
| Argoverse 2 Sensor | 20 Hz | **4.65 s** | 15 s | ✓ 余量大 |
| nuScenes（sweeps） | 12 Hz | **7.75 s** | 20 s | ✓ |
| Waymo E2E | 10 Hz | **9.30 s** | 20 s（train/val）/ 12 s（test） | ✓ |
| Waymo Perception | 10 Hz | **9.30 s** | 20 s | ✓ 帧够，但相机不够（见下） |
| PandaSet | 10 Hz | **9.30 s** | **8 s** | ✗ 差 1.3 s |

---

## 1. Argoverse 2 Sensor Dataset — 合格，已在产

官方（argoverse.github.io/user-guide/datasets/sensor.html）：

- **1,000 个 log**，每个 **"approximately 15 seconds in duration"**
- **7 个 ring camera + 2 个 stereo，"20 fps imagery"**
- LiDAR **10 Hz**
- 分辨率：ring front-center **2048 × 1550**（竖）；其余 8 路 **1550 × 2048**
- **"~300 images from each of the 9 cameras"** → 每 log 每相机 ~300 帧

**S3 实测**（本机匿名 HTTP，无需账号，无需 tunnel）：

```
datasets/av2/sensor/train : 700 logs
datasets/av2/sensor/val   : 150 logs
datasets/av2/sensor/test  : 150 logs
                    TOTAL : 1000
```

→ 与官方 1000 完全吻合。每 log 300 帧 ⇒ 可切 3 个不重叠 93 帧窗口。
**500 个 sample 只需 ~167 个 log，我们已跑过 850 个 log 的判定。**

---

## 2. nuScenes — **合格**。会上"不够 93 帧"的前提用错了单位

官方规格（nuScenes CVPR 2020 论文 / nuscenes.org 传感器表）：

- **1,000 个 scene，每个 "20s long"**
- **6 个相机，Basler acA1600-60gc，1600 × 900，"12Hz capture frequency"**
- LiDAR 20 Hz，radar 13 Hz
- **标注关键帧（sample）只有 2 Hz** → 每 scene **40 个 keyframe**

**这就是分歧的根源**：会上说的"不够 93 帧"用的是 **40 个 2 Hz 关键帧**这个单位；
但 nuScenes 把**全速率相机图像也一并发布了**，放在 `sweeps/` 目录
（官方 devkit：`samples` = "Sensor data for keyframes"，
`sweeps` = "Sensor data for intermediate frames"）。

- 12 Hz × 20 s = **每相机 ~240 帧**
- 我方 DB-231 实测该 adapter 输出 **206 / 229 / 230 帧**，
  加严格六相机同步后仍有 **120 / 119 / 141 帧** —— 都 **> 93** ✓

我们**不需要**关键帧标注（B-route 无 LiDAR、无标注），只需要
`sample_data.timestamp` + `ego_pose` + `calibrated_sensor`，这三张表 sweeps 全都有。

### 下载：**有公开 S3，免注册**（本机实测 200 OK）

Registry of Open Data on AWS：`s3://motional-nuscenes/`（ap-northeast-1），
`--no-sign-request` 即可读。本机匿名列桶已成功，且**有纯相机分包**：

```
public/v1.0/v1.0-trainval_meta.tgz            0.46 GB   ← 必需（pose/calib/timestamp）
public/v1.0/v1.0-trainval01_blobs_camera.tgz 17.59 GB   ← 只有相机，不含 LiDAR/radar
public/v1.0/v1.0-trainval02_blobs_camera.tgz 16.51 GB
...  共 10 个 camera 分包，合计 ~177 GB
public/v1.0/v1.0-mini.tgz                     4.17 GB   ← 10 scene，先验证用
```

**这条通道和 AV2 同性质**（公开 S3 + 匿名读），USC 网络下已验证可用。

配额换算：严格同步后每 scene 约 120–141 帧 ⇒ **1 个 scene = 1 个 93 帧窗口**。
trainval 共 **850 scene**（700 train + 150 val），
要 500 个 sample ⇒ 约 500 个 scene ⇒ 需下 **6 个 camera 分包（~100 GB）**。
（若放宽同步容差取到 ~200 帧，则每 scene 可切 2 窗，下 3 包即可。）

---

## 3. Waymo Open Dataset · End-to-End Driving (WOD-E2E) — 合格，唯一卡点是登录

官方（waymo.com/open/data/e2e + WOD-E2E 论文 arXiv:2510.26125）：

- **4,021 个 segment**，约 12 小时
  - train **2,037** · val **479**：**"20-second"**
  - test **1,505**：12 秒（后 8 秒隐藏）
- **8 个相机，360° 全覆盖**："front, front left, front right, side left,
  side right, **rear, rear left, rear right**"
- **"10Hz camera video sequences"**，官方页面明确 train/val
  **"complete driving logs for the entire duration"，camera frames 覆盖整段**
- **无 LiDAR**（纯视觉数据集）
- 单相机 FOV 70°–90°，原始约 1920×1280

⇒ train/val 每 segment **200 帧 @10Hz**，93 帧 = 9.3 s，**每段可切 2 个窗口**。
2,516 个 train+val segment ⇒ 理论上限 5,000 个 sample，取 500 绰绰有余。

**这个数据集正好是 B-route 的理想对象**：8 相机 360° + 无 LiDAR，
而我们的 rule-mask 方案本来就不需要 LiDAR、不需要标注。

### 关于此前"连续性存疑"的记录 — 官方文档否定了这个担心

我此前按 Drive 目录名 `db220_waymo_e2e_48record_candidate`（48 records）
把 E2E 标为"连续性未验证"。官方页面写的是 train/val 段
**"complete driving logs for the entire duration"** 且为 **10Hz video sequences**。
两者不矛盾：48 很可能是**一个 tfrecord 分片里的 frame 数**，而不是一整段的长度。
**结论：官方规格上 E2E 是连续 200 帧；实际解包核对仍要做一次，但不再是阻塞性怀疑。**

### 下载：**必须用你的 Google 账号先签协议**（唯一硬门槛）

本机匿名访问 GCS 实测：

```
waymo_open_dataset_v_2_0_1                   -> HTTP 401 Unauthorized
waymo_open_dataset_end_to_end_camera_v_1_0_0 -> HTTP 401 Unauthorized
waymo_open_dataset_v_1_4_3                   -> HTTP 401 Unauthorized
```

Waymo 全部数据在 GCP 桶里，**匿名一律 401**。流程（只能由你本人做）：

1. 浏览器登录 `waymo.com/open`，用 **1jingshuo1@gmail.com** 签
   Waymo Dataset License Agreement（non-commercial research）
2. 会收到确认邮件，该 Google 身份即获得桶读权限
3. 本机 `gcloud auth login` 后
   `gsutil -m cp -r gs://<bucket>/<split>/ <本地路径>`

具体桶名以下载页显示为准（匿名 401 无法区分"桶不存在"和"无权限"，
上面的名字是常见写法，**不作为已证实事实**）。

---

## 4. Waymo Open Dataset · Perception — **帧数够，但相机拼不出 360°**

官方论文 arXiv:1912.04838（CVPR 2020）+ 官方 `dataset.proto`：

- **1,150 个 scene，每个 20 秒**，10 Hz
- **只有 5 个相机**：Front / Front-Left / Front-Right / Side-Left / Side-Right
- **每个相机水平 FOV ±25.2°（即 50.4°）**
- 分辨率：前三路 1920×1280，两侧 1920×1040
- **没有后向相机**

5 × 50.4° = **252°**，车后留下约 **108° 的洞（占 ERP 带宽 30%）**。

⚠️ 注意一个易踩的坑：`dataset.proto` 的 `CameraName` 枚举里**确实有 8 个名字**
（含 REAR_LEFT / REAR / REAR_RIGHT）——但那是 proto 被 **Motion v1.2.1（2024-03）
和 E2E** 共用后加进去的；**Perception 的 tfrecord 只填 5 路**。
看到 8 个枚举就以为 Perception 有 360°，是这次核查里最容易出的错。

⇒ **Perception 不能产出 360° ERP band**。要么排除，要么接受车后 30% 恒黑
（那已经不是 21% 接缝 mask 的量级了，是数据契约层面的改动）→ **需要 koi 拍板**。

---

## 5. PandaSet — **官方口径确认 80 帧 < 93，且总量只有 103 个场景**

三个独立来源一致：

- PandaSet 论文（arXiv:2112.12610）："more than 100 scenes, each of which is **8 seconds long**"
- 官方数据说明 / HuggingFace 镜像卡片：**"103 scenes of 8s each"，"48,000 camera images"，"16,000 LiDAR sweeps"**
- devkit 目录结构：每个序列内图像编号 **`00 … 79`**

算术自洽（这是最硬的证据）：

```
6 cameras × 80 frames × 103 scenes = 49,440 ≈ "48,000+ camera images"  ✓
2 lidars  × 80 sweeps × 103 scenes = 16,480 ≈ "16,000 LiDAR sweeps"    ✓
```

⇒ **每个序列恰好 80 帧 @10Hz，就是 8 秒**。这不是我没找对版本 —— 官方就只有 8 秒。

**而且还有第二个独立的硬伤：PandaSet 总共只有 103 个场景。**
即使把 93 帧放宽到 80 帧、每个场景只出 1 个 sample，
**上限也只有 103 个 sample，永远到不了 500 的配额。**

- 6 个相机（front / front-left / front-right / left / right / back），1920×1080，**360° 覆盖 ✓**
- **CC-BY-4.0**（唯一允许商用的一个）

### 下载：HuggingFace 镜像，免注册（本机实测 200 OK）

原 Scale AI 官网需填表；社区镜像 `georghess/pandaset` 为**单个 44.5 GB zip**，
CC-BY-4.0，本机 API 列文件已成功。

---

## 5.5 实测补录（2026-08-16，license 验证 + 字节级探针）

用户裁决更新：**Waymo Perception 保留**（车后 108° 交给 stage-1 补洞——xinhan 的
模型学过大量 mask，破洞由模型补全，数据侧诚实置 0 不算 loss）；nuScenes 确认要。
PandaSet 仍按 OOD 提案待 koi 批。

### license 已验证 ✓

`gcloud auth login 1jingshuo1@gmail.com` 后四个桶全部可读：
E2E `waymo_open_dataset_end_to_end_camera_v_1_0_0`、Perception v2.0.1 / v1.4.3、
Motion v1.3.0。E2E 规模实测：train 263 片×~3.4 GB（~890 GB）、
val 93 片×~2.5 GB（~230 GB）、test 266 片（未来隐藏，对我们无用）。

### Waymo E2E 字节级探针（HTTP Range 部分下载，未拉全片）

对 `val_...tfrecord-00000-of-00093`（2.6 GB）：

- **record0 全量解析**：一条 record = 一个时间步，**8 路相机全在、全是真 JPEG**
  （front 5 路 972×1079，rear 3 路 972×587/551）
- **标定完整且真实**：每路 9 内参（f≈1110 px + k1,k2,p1,p2,k3 畸变系数）+
  完整外参。8 相机是一个紧凑车顶 rig（t 范围 x 1.14–1.52 m、y ±0.19 m、
  z 全部 1.8065 m）→ **基线 ~0.1–0.4 m，与 AV2 front-pod 同量级，B-route 适用**
- **被抹掉的**：frame.timestamp_micros=0、image.pose=identity、
  pose_ts/trigger/readout_done=0（只有 shutter 曝光时长是真值）
  → **EMC 不可做，退化为静态 rig**（rule mask 本来就是超集，可接受）
- **全片 census（1150 条 record 逐条读头）**：**425 个不同 segment 前缀**，
  每段在本片只有 1–5 帧、后缀离散（如 n=3 min=24 max=197）
  → **整个 val split 是全局打散（tf.data 预洗牌）**：一段的 ~200 帧
  均匀散在全部 93 片里。后缀即帧号（观测到 0–236，段长 ~200–240 帧）

**生产结论**：连续性存在（`<hash>-<idx>` 分组+排序即重建 10 Hz 视频），
但**重建任何一段都需要过一遍整个 split**。可行方案 = 云端单遍 demux：
VM 上顺序流读 93 个 val 片（~230 GB，GCS 同区免费），按前缀把 record 分拣到
每段目录，然后跑 db240 producer（静态标定版）。**val 恰有 479 段 ≈ 500 配额**，
E2E 源可以只用 val split，省掉 890 GB 的 train。
每段选定 93 连续后缀窗口后只留窗口内帧，磁盘 ~100 GB 量级。
旧疑点闭案：Drive `db220_..._48record` 的 48 = 单片里的 record 数（碎片），
与"段不连续"无关——段是连续的，只是被洗牌了。

adapter 侧两个新工作项：① 畸变系数非零 → 投影需带畸变或先 undistort
（AV2 管线是纯 pinhole）；② 帧序用后缀不是时间戳，10 Hz 节奏引用官方口径。

### Waymo Perception 字节级探针

对 v1.4.3 `individual_files/validation/segment-10203656...tfrecord`（895 MB）：

- record0 **只有 5 路相机**（FRONT/FL/FR/SL/SL）——proto 枚举 8 个名字但数据只填 5，
  "252° 无后视"用真实字节坐实
- **时间戳真实**（2018-04 物理时间），前 25 条 record 间隔全部 **99.9 ms = 10 Hz**，
  **一片 = 一段按序连续视频**（~214 record ≈ 20 s），无需 demux
- 生产账：93 帧窗口每段可切 2 个；500 sample ≈ 250 段 × ~0.9 GB ≈ **225 GB 下载**
  （v1.4.3 individual_files 逐段下载即可，无洗牌问题）

## 6. 汇总裁决

（2026-08-16 更新：按用户裁决 + license 验证 + 字节级探针）

| 数据源 | 93 帧 | 360° | 下载门槛 | 能否供 500 | 裁决 |
|---|---|---|---|---|---|
| **Argoverse 2** | ✓ 300 帧 | ✓ 7 ring | 公开 S3 ✓已通 | ✓ 1000 log | **训练源①，在产** |
| **nuScenes** | ✓ ~240（sweeps） | ✓ 6 cam | 公开 S3 ✓已通 | ✓ 850 scene | **训练源②**（用户确认要；会上按 2 Hz 关键帧错算成 40 帧） |
| **Waymo Percep** | ✓ 214 实测连续 | 252°+诚实黑 108° | license ✓已验 | ✓ 1150 seg | **训练源③**（**koi 0731:66/95 + 0814:306 三次明确放行**，非我方推断；见 MEETING_DERIVED_CONTRACT.md §1.2/2.3） |
| **Waymo E2E** | ✓ 段长 200-240 实测 | ✓ 8 cam 实测 | license ✓已验 | ✓ val 479 段 | **训练源④**（全局洗牌，需云端单遍 demux；EMC 退化静态 rig） |
| **PandaSet** | **✗ 80** | ✓ 6 cam | HF 镜像 ✓已通 | **✗ 上限 103** | **OOD holdout 提案**（待 koi 批） |

### 一个正好自洽的建议

koi 的 B4 要求是「**取样本量最小的那个整个留作 OOD**」。
PandaSet 恰好在两个维度上都是最小的（80 帧 / 103 场景），
**把它定为 OOD holdout，两个硬伤同时被化解**：
OOD 只做评测、不进 Louison 的训练包，样本少是合理的，
80 帧的窗口长度也不必与训练集严格对齐。

那么训练集 = **AV2 + nuScenes + Waymo E2E 三源**，各 ~500，
每源都已确认帧数与 360° 覆盖达标。

### 决策状态（2026-08-16，会议转写稿复盘后更新）

1. ~~nuScenes 加回~~ → **确认要**（12 Hz sweeps = 240 帧实锤；
   且 0731 我方自己就报告过「只有 waymo open 跟 nuScenes 可以」，
   0814 说反了才被排除 —— 属事实更正，不是推翻 koi 的规则）
2. ~~Waymo Perception 排除？~~ → **保留，且这是 koi 的既有决定不是我们的提议**：
   koi 0731:66「就算那个 open waymo 只有 252 度，没有左右的，那也没关系」、
   0731:95「waymo open 我觉得就很赞，252 度我就只专心在这里，后面两个就算是生的」、
   0814:306「对，我觉得全部都可以上」。
   我此前「建议排除」的意见与 koi 明确裁决相悖，作废。
3. **PandaSet 转 OOD** —— 仍待 koi 批（80 帧 / 103 场景两条硬伤 + 恰为最小源）
4. ~~签 Waymo license~~ → **已验证**：1jingshuo1@gmail.com 登录后四桶全可读

### 一个许可证提醒（非阻塞）

AV2 / nuScenes / Waymo 均为**非商用研究许可**，PandaSet 是 CC-BY-4.0（可商用）。
这是给 BOSCH 的交付，建议向 koi 明确一次用途边界。
