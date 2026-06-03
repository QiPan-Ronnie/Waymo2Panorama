---

你正在接手 **Waymo2Panorama** 项目（用户 Qi Pan / panq@usc.edu，导师 Koi Chen，队友 Xinhan，合作方 Bosch）。**先按顺序读下面的文件再开口**——不要凭旧记忆行动；读完用 3–5 句话回报"已读 + 你的理解 + 你看到的方向倾向"，然后**和用户一起讨论**，不要自己觉得某方法不错就开始猛做（这是本项目反复栽的坑：觉得不错 → 猛冲 → NEG）。

## 0. 一句话目标
把 **7 个非共心环视相机**（AV2，基线 21–26cm，相邻重叠仅 ~18.6°，pinhole，外加真实 LiDAR）拼成一张**干净的 360° ERP 全景**，作为 **Bosch 自驾 world-model 的训练数据**。也跑通了 **Waymo 8-cam** 输入（队友 Xinhan 的数据）。验收标准 = **PLAUSIBLE（看着像真实街道，接近 Google 街景 / Meta 360）**，**但绝不能编出显著物体**（假车/假人会教坏 world model）。核心物理难点：近/中景**视差**使同一表面在相邻相机间错位 → 缝处重影/硬切；非共心相机在近景**物理上不可能**同时"单光心 + 几何忠实"。

## 1. 必读（按序）
1. `agent/README.md` —— 工作协议：4 个 living docs（README/handoff/progress/decision_briefs）、**Experiment Decision Gate**（动手前先写 brief，含 Kill criteria + Max scope）、**3-Location 规则**（每个产物在 GitHub / 本地 / Drive 三地留存）。
2. `agent/handoff.md` —— 顶部 banner（当前共识 + 路线图，最新覆盖旧）。
3. `agent/progress.md` —— **顶部 3 条**（2026-06-03 ★DiT360 SESSION SYNTHESIS = 本次结果与位置索引；DB-18 探索程序；code-review 修 BEV bleed bug）。这是**实验事实流水**，最新在顶。
4. `agent/decision_briefs.md` —— **当前 active 队列**：**DB-14**（下一次 GPU 跑：DiT360 忠实**细缝**）+ **DB-19**（组合 pano + 多锚点泛化 + 重判 ground/full outpaint）。这是动手的入口闸门。
5. 记忆：`memory/MEMORY.md` 索引，尤其 `waymo2pano-seam-direction.md`、`waymo2pano-dit360-findings.md`（跨 session 结论）。
6. 略读历史：`agent/HANDOFF-PROMPT-full-project-2026-06-02.md`（上一版）、`agent/EXPLORATION-seam-synthesis-sprint.md`（diffusion 实验全记录）、`meeting/5.22_meeting with xihan/本次prompt.md`（Bosch 会议 + 8 个方法 brainstorm 全表）。

## 2. 项目脉络（从最初到现在）
### 2.1 最初的 ~8 个方法阶梯（全部 vision-judged，定下 L1 基线）
L0 标定 BA 精修（视差面前可忽略）；L1 单一固定球面 R={∞,30,10,5,3}m（无单一 R 通吃）；L1 多-R 逐像素 v1/v2（物体边界 Frankenstein 重影）；L2 缝处局部 ECC 对齐（弱 NEG）；L2 DP 缝路由 v2（仍切到车/人）；L3 DiT360 掩码缝补（NEG 主解，smooth↔warp / safe↔blur 两难）；诊断性"源-证据缝置信图"（POS 仅作元数据）。→ 确立 **L1 hard_select**（刚性球面投影 + 逐像素 argmax 选相机）= 最干净视觉基线。早期 Phase 2/3：Pi3 胜 DVGT；Pi3 深度比 LiDAR 系统低估 ~25%；L3 forward-splat 多参数 NEG（-3.13 dB）。细节全在 `notes/archived/`。完整 **5 轴** menu（轴 A 换 blending / 轴 B 加 minimal depth / 轴 C 换 projection / 轴 D 生成式 / 轴 E 改任务定义；约 11 个 candidate 未押过 chip）见 `meeting/5.22_meeting with xihan/本次prompt.md` 附录。（注："~8 个方法阶梯" 和 "5 改进轴" 是两回事，别混。）

### 2.2 和队友 Xinhan 开会（~2026-05-22/27）+ Bosch 对接
Bosch 有个自驾 world model，输入普通图质量不行，**输入 panorama 数据效果好**，但 panorama 数据集太少、收集不现实 → 我们的任务 = **更好地生成 panorama 训练数据**。每周要向 Bosch 推进。Xinhan 做 **Waymo 8-cam** 拼接（帧 `8e737334...-085`），他的问题：(a) 某 camera 在阴影里 → 半黑半正常的**色差**，想用 blending 把阴影相机 brighten；(b) ORB feature-point 拼接左右严重变形；(c) Waymo **rolling shutter / jelly effect**（不是 9 格同时曝光，高速移动会糊）。我们部署了端到端 Waymo 管线（gcloud→gsutil 取 shard→解析 E2EDFrame→8-cam K/T/畸变→L1 球面 + L2 HDR + multiband），**用 L1+L2 HDR 把阴影相机 brighten，解决了色差/中心相机过曝**（缝处 |ΔY| 40.86→33.36 = **-18%**；在 5 个 Waymo segment、含 2 个夜景上泛化），修了两个 Waymo 专属 bug（坐标系 x=fwd→OpenCV；RING_PAIRS 7→8 cam）。交付 `deliverables/handoff_to_xihan_2026-05-27_brighten_and_l1.md` + `deliverables/xihan/l1_on_waymo/`。**会议待办（部分仍开放）**：(a) 用户让我们**把最好的 L1 跑到 Waymo 上**确认是否真泛化（已做 → `l1_on_waymo`）；(b) 确认 AV2 是否也有色差 = **开放问题**（用户原话"好像没有但我也不确定，需要你确定一下"，未做对照实验）；(c) 用户提的"ORB feature-point + 方法1 结合"修 AV2 双轮重影 —— **注意：ORB+L1 hybrid 我们 5.22 前已 3 次 NEG，那条路死了，别再跑**。AV2 的 **overlap 双轮重影**（同一辆车 2 个轮子）最终由后来的 `_seamroute` object-moat 缝解决（见 2.5）。

### 2.3 关键重构：source-faithful → PLAUSIBLE（2026-05-30）
用户把验收从"几何忠实"改为 **PLAUSIBLE**：**Google 街景本身也是多个非共心相机、也有视差、也不单光心、也不几何忠实**——它靠光流 warp 真实像素到"看起来一致" + 缝走低纹理 = plausible 多中心。**我们比街景多一张牌 = 真实 LiDAR。** 硬约束：plausible 可以，但**绝不能编出显著物体**。

### 2.4 其他 agent 的 seam 探索（带内忠实融合 = 已穷尽证伪）
- **E-阶梯（2026-05-29）**：E0 `relative_warp` 标尺；**E1.5 = seam-confined 低频 multiband**（修光度缝、远场逐字节=L1、无重影）= 早期可交付 "L1+"；E2 LiDAR-z 深度对齐（中景 smear）。
- **diffusion-sprint（E2–E6 + #3，全 NEG）**：E2 深度重投影（所有深度源 sparse/near/dense-mono/9-sweep 全 smear，对深度误差超敏感）；E3 RAFT 光流 warp（无纹理中景 starve）；E4 SDXL-inpaint（floor）；E5 移植 finetuned DrivingForward（太糊/扭）；E6 多帧 LiDAR（仍 smear）；#3 held-out 相机 LoRA（宽基线下渲染大片黑）。
- **DiT360 v2→v18（16 个缝补变体，全 NEG/cosmetic/hallucinate）**：v14 trimap-clamp 保真最好但 tau5 raw≈hard_select（细缝几乎无效）；outpaint 系列看着完整但**虚构**。DiT360 base = **FLUX.1-dev**（~12B DiT，**非商用许可，进 Bosch 要注意**）；outpaint = training-free（Personalize-Anything 反演），LoRA 没在 outpaint 上训过。
- 结论（截至 2026-06-02）：**带内单帧宽基线近景视差，做不忠实**。可交付硬产出 = L1 hard_select + E1.5。

### 2.5 ★ 本次接手（2026-06-03，我 = 上一个 agent）做的事
**(a) 几何忠实交付物收敛 = `scripts/phase3/_seamroute.py`**：align + **object-moat min-cut 缝**（绕开车/人）+ **virtual-centre select**（单源、不 average → 消除重影）。Ghost 没了、清晰、beat view_none，BMW/0bae/2c65 验证过。= **source-faithful 天花板**。产物 `deliverables/ghostkill/G_bmw_pano.jpg`（用户判定"最接近目标"，但近地缝**弯成波浪形**=近地 kink）。
**(b) 逃出"slab-stitching 局部最优"（codex round-8 对抗）= BEV GROUND ATLAS**：把 7 个相机投到 LiDAR 地平面做 top-down 连续路面图 → 渲染回 ERP 地面带（`_bev_ground.py` → `SR_bmw_bevfinal_1024x2048.png`）。**路面 = representation-fixable**（连续车道线、无 per-camera 切口、无重影），但 ERP 可见收益**不大**（可见近地带仅 ~1.66%）；**curb = off-plane 物理 floor**（不变）。已被 IPM / reroute / line-snap / FB-loosen **共 4–5 次独立 vision 证实**：非生成式路面已到天花板，近地 kink + curb 是物理 floor。
**(c) 物理 floor 的另一面 = 黑天/黑地（垂直 FoV 硬件限制）→ 只有生成能补。**
**(d) DiT360 探索（A100，本次大目标）——两个结论：**
  - **T1 隐藏波浪缝 = 取决于掩码宽窄。** 我**误跑**了一版"**宽** ground-risk 掩码(5.56%) + tau{20,50}" → DiT **造小车 + 糊掉无纹理切口**（object-gate FAIL = **NEG**）。**这不是我们之前的方法。** 之前可信方法 = **v14 trimap 细缝：r008 核(~1.6%) + tau5 轻触**（用户判"其实也可以"）。**未探索的正确格子 = 细掩码 × 中等 tau**（不是宽 × 高）→ 已写进 **DB-14**（下一次 GPU 跑的方法 of record）。
  - **★ T2 sky-only OUTPAINT = POSITIVE（本次 WIN）。** 只补水平线以上的黑天带（opmask_sky, tau50, guid2.8）→ 整个上半球填满**连续自然天空**，楼顶**逐字节保真**，**object-gate PASS（零造物）**，vision 干净。组装好的成品 = `results/dit360_outpaint_v2/sky_t50_s0/sky_t50_s0_corecompose.png`（Drive；sky 掩码 ~37% 帧）；本地 `deliverables/dit360_v2/op_sky_t50_s0.png` + `sky_roofline_cmp.jpg`。和 2026-05 被拒的"全幅 outpaint 造车"的区别 = **约束（只补天空 + object gate）**。**目前最像 Google-Map 的全景 = bevfinal + sky-outpaint。**
  - **object-safety gate** = `_object_gate.py`（torchvision fasterrcnn，flag 生成区 net-new 显著物体）= 所有生成输出的判官，已 /code-review 硬化。

## 3. 当前状态 + 下一步（2026-06-03）
- **source-faithful 天花板** = `_seamroute.py` + BEV 地面层 → `SR_bmw_bevfinal_1024x2048.png`。残留 floor：近地波浪 kink、grazing curb、out-of-FoV 黑天/黑地（全是物理/硬件）。
- **生成式（只在物理 floor 上、被掩码 + object gate 约束）已知**：sky-outpaint = WIN（可用，标注"generated sky"）；wide-mask 补缝 = NEG（造车 + 糊无纹理切口）。（两个 verdict 都成立，但用 /code-review **硬化后的 object gate** 的正式重判仍 PENDING — 在 resume plan 上；D2 是连"弱 gate"都没过，所以 NEG 稳。）
- **下一次 GPU 跑（见 decision_briefs.md，**需要用户开 A100 给隧道**）**：
  - **DB-14**：DiT360 **细缝**（r008 × tau{5,8,12}）在 bevfinal 上 — 找"细掩码 × 中等 tau"是否能轻微平滑波浪缝又不造物（gate）。
  - **DB-19**：组合 pano（bevfinal + 细缝 + sky-outpaint）+ 重判 ground/full outpaint（已跑、判定被隧道中断）+ 多锚点 0bae/2c65 泛化。
- **未押过的牌（可讨论）**：RF gamma/eta 扫描、yaw-SELECT（不是 average，median 会糊边）、cube-space 检查（py360convert）、把 LiDAR/邻相机当"缰绳"的 reference-attention（DB-03 EPI-Mix 思路，绕开 E2-E6 深度墙）。

## 4. 基础设施（GitHub / 本地 / Drive 三地 + Colab）
- **GitHub**：`git@github.com:QiPan-Ronnie/Waymo2Panorama.git`，分支 `main`，**直推已授权**（无需 PR）。commit footer：`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
- **本地**：`D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\`（Windows，PowerShell；BaiduSync 同步；图也拉到这里）。
- **Drive**：`MyDrive/koi_waymo2pano_colab/`（panq@usc.edu）——大产物 `results/`、`outputs/phase3/`；AV2 数据 `data/argoverse2/val/`；缓存 `cache/huggingface/`（FLUX.1-dev 32G + DiT360 LoRA）。fileId 见 `memory/drive-folder-ids-koi-waymo2pano.md`。
- **Colab 计算**：`agent-colab-direct` v0.1.0（helper `scripts/_colab.py`，`exec`/`put`/`get`，env `COLAB_URL`/`COLAB_TOKEN`）。**每次会话由用户开 A100 runtime 并给隧道 URL+token**（runtime 易因机器睡眠 / trycloudflare 530 掉线；心跳 = Drive `runtime/active_url.json` 的 timestamp）。**Windows 上必须 `export MSYS_NO_PATHCONV=1`。**
- **★ DiT360 在 Colab 跑的 INFRA 配方（可复用，省 50 分钟）**：(1) FLUX 32G 从 Drive FUSE 加载会超时 → `cp -r cache/huggingface/hub /content/hf_cache/hub` 一次，然后 `HF_HOME=/content/hf_cache HF_HUB_OFFLINE=1` → 加载 18–49s；(2) LoRA 加载崩 `ImportError: torchao 0.10.0` → `pip uninstall -y torchao`（只是量化后端，fp16 不需要）；(3) DiT360 代码 `git clone https://github.com/Insta360-Research-Team/DiT360.git /content/DiT360`（external/ gitignored，pa_src 在官方 repo）；(4) object gate 用 torchvision fasterrcnn（别用 ultralytics → 避免 env churn）；(5) PersonalizeAnything **tau 范围 0–100，官方默认 50**（越小越保真/越接近 no-op）——**细缝补全用 tau≈5–12（保真），outpaint 用 tau50（真生成）**。
- **硬规矩**：**HF token 绝不发到 Colab**（分类器会拦）；**绝不碰 `secrets/`**；FLUX.1-dev = gated + 非商用许可（进 Bosch 注意）。

## 5. 工作准则（务必遵守）
- **每张经手的图必须用 vision 亲眼看过才能下结论**，绝不"指标好但视觉差"（吃过大亏；标尺和眼睛冲突时信眼睛）。
- **先和用户一起定方向，再动手**；任何新实验方向先在 `decision_briefs.md` 开 brief（Kill criteria + Max scope）。brief **做完 → 归档进 progress.md → 从 briefs 删除**（briefs 保持短队列）。
- **不要重跑已 NEG 的方向**：copy-selection 家族、E2 深度重投影家族、wide-mask DiT 补缝、full-frame center-outpaint、**ORB+L1 hybrid（5.22 前 3 次 NEG）**，全部 rejected。
- **不要把"宽掩码补缝"和"细缝补全"搞混**：细缝(r008)=保真可控；宽掩码=造物。
- 每个产物在 progress.md 里写清 **GitHub / 本地 / Drive** 三处位置（用户要 复盘）。
- **codex (gpt-5.5 xhigh) 当对立面**：动 GPU / 下大结论前，把真实 seam 图喂给 codex 做对抗（log 存 `agent/codex_logs/`），防止陷入局部最优。

## 6. 你现在该做什么
**读完 1–5 → 简短回报"已读 + 理解 + 方向倾向" → 等用户一起讨论先动哪条 DB（DB-14 细缝 还是 DB-19 组合/泛化）。** 不要先跑实验。GPU 现在大概率没开——先确认用户是否已开 A100 给新隧道。你的角色 = 和用户保持同步、随时接力、动手前一起把方向和闸门定清楚。当前最值得看的产物：`deliverables/dit360_v2/op_sky_t50_s0.png`（sky-outpaint WIN）、`deliverables/ghostkill/G_bmw_pano.jpg`（几何天花板，波浪缝）。
