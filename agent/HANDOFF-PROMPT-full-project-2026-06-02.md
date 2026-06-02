

---

你正在接手 **Waymo2Panorama** 项目（用户 Qi Pan / panq@usc.edu，导师 Koi Chen，队友 Xinhan）。**先按顺序读下面这几个文件再开口**——不要凭旧记忆行动；读完用 3–5 句话回报"已读 + 你的理解 + 你看到的方向倾向"，然后**和我一起讨论**，不要自己觉得某方法不错就开始猛做（这是本项目反复栽的坑）。

## 0. 一句话目标
把 **7 个非共心环视相机**（AV2，基线 21–26cm，相邻重叠仅 ~18.6°，pinhole）拼成一张**干净的 360° ERP 全景**，作为 **Bosch 自动驾驶 world-model 的训练数据**。也跑通了 **Waymo 8-cam** 输入（队友 Xinhan 的数据）。核心难点：近/中景**视差**使同一表面在相邻相机间错位 → 缝处出现**重影/硬切**；非共心相机在近景**物理上不可能**同时"单光心 + 几何忠实"。

## 1. 必读（按序）
1. `agent/README.md` —— **工作协议**：4 个 living docs（README/handoff/progress/decision_briefs）、**Experiment Decision Gate**（动手前先写 brief，含 Kill criteria + Max scope）、**3-Location 规则**（每个产物在 GitHub / 本地 / Drive 三地留存）。
2. `agent/handoff.md` —— 顶部 banner（当前共识 + 路线图，最新覆盖旧的）。
3. `agent/progress.md` —— 顶部几条（2026-06-02 协议条目 + DiT360 核查 + DiT360 outpaint DONE + E1.5/重构）。这是**实验事实流水**，最新在顶。
4. `agent/decision_briefs.md` —— **当前所有待探索方向**（DB-20260602-01..10），每条带 question/hypothesis/kill/scope。这是动手的入口闸门。
5. 略读：`agent/BRAINSTORM-2026-06-02-seam-path-forward.md`（最新发散+对抗筛选）、`agent/EXPLORATION-seam-synthesis-sprint.md`（diffusion 实验全记录）、`notes/archived/README.md`（所有历史探索路径的索引）。

## 2. 项目脉络（从最初到现在）
- **最初的 ~8 个方法（NEG 阶梯）**：L0 标定 BA 精修（视差面前可忽略）；L1 单一固定球面 R={∞,30,10,5,3}m（无单一 R 通吃）；L1 多-R 逐像素 v1/v2（物体边界 Frankenstein 重影）；L2 缝处局部 ECC 对齐（弱 NEG）；L2 DP 缝路由 v2（仍切到车/人）；L3 DiT360 掩码缝补（NEG 主解；smooth↔warp / safe↔blur 两难）；诊断性的"源-证据缝置信图"（POS 仅作元数据）。→ 当时已确立 **L1 hard_select**（刚性球面投影 + 逐像素 argmax 选相机）是最干净的视觉基线。
- **Phase 2/3 backbone+depth**（早期）：Pi3 vs DVGT（Pi3 胜）；Pi3 vs LiDAR 深度（系统性低估 ~25%）；L3 forward-splat（3D 感知前向溅射）多参数 **NEG**（-3.13 dB，Pi3 深度方差→振铃 + 视差重影）；10-anchor 鲁棒性扫描。全部细节在 `notes/archived/`（pi3_vs_lidar_report、l3_evaluation_report、phase3_multi_anchor_report 等）。
- **和队友 Xinhan 开会（~2026-05-27）**：Xinhan 给了一帧真实 **Waymo 8-cam**（帧 8e737334...-085）有色偏问题。我们部署了端到端管线（gcloud→gsutil 取 Waymo shard→解析 E2EDFrame→8-cam K/T/畸变→L1 球面 + L2 HDR + multiband），**用 L1+L2 HDR 解决了色偏/中心相机过曝**。修了两个 Waymo 专属 bug（坐标系 x=fwd→OpenCV 转换；RING_PAIRS 7-cam→8-cam）。交付 `deliverables/handoff_to_xihan_2026-05-27_brighten_and_l1.md` + `deliverables/xihan/`。
- **短期目标 / 关键重构（2026-05-30）**：用户把验收标准从"source-faithful（几何忠实）"改为 **PLAUSIBLE（看着像真实街道即可，但绝不准编出显著物体——假车/人会教错 world model）**。依据：**Google 街景本身也是 7 个非共心相机、也有视差、也不单光心、也不忠实**——它靠光流 warp 真实像素到"看起来一致"+ 缝走低纹理 = plausible 多中心。**我们比街景多一张牌 = 真实 LiDAR。** 过程要求：**先一起定方向，再动手**；停止"觉得不错→猛冲→NEG"。
- **其他 agent 的 seam 探索**：
  - **E-阶梯（2026-05-29）**：E0 `relative_warp` 标尺（验证过）；**E1.5 = seam-confined 低频 multiband**——修光度缝、远场逐字节=L1、**无重影**（当前可交付的"L1+"）；E2 LiDAR-z 深度对齐（深度精度受限，中景 smear）。
  - **diffusion-sprint（E2–E6 + #3，全 NEG）**：E2 深度重投影（所有深度源 sparse/near/dense-mono/9-sweep 全 smear/over-warp，N1 对深度误差超敏感）；E3 RAFT 光流 warp（无纹理中景 starve）；E4 SDXL-inpaint（floor）；E5 移植 finetuned DrivingForward（太糊/扭）；E6 多帧 LiDAR（仍 smear）；#3 held-out 相机 LoRA（渲染大片黑——宽基线下相机视野中心无邻居可见）。
  - **DiT360 v2→v18（16 个缝补变体，全 NEG/cosmetic/hallucinate）**：v14 trimap-clamp 保真最好但 raw≈hard_select（无效）；outpaint 系列看着完整但**虚构**。**最新（2026-05-30/06-02）**：Koi 让做的"只留中心补全 360" outpaint 4 个 case 已跑+核查——是 DiT360 **官方设计行为**（官方 Petra/悉尼示例也一样：输入 75–88% 黑→输出填满→四周全编），**不是 bug、格式没错**；DiT360 = 强生成器、**非忠实重建器**。base = **FLUX.1-dev**（~12B DiT，**非商用许可，进 Bosch 要注意**），outpaint = **training-free**（Personalize-Anything 反演+token 替换），LoRA **没在 outpaint task 上训过**。
  - **新-系列（POS 子结果）**：新-B graph-cut 缝选择（缝带 |grad| -12.4%）、新-C IPM 多区先验（地面 +0.20 dB）、新-D 宽基线稀疏立体、新-E HDR 跨相机增益补偿（亮度差 -18%）。
- **额外/历史实验**：全部在 `notes/archived/`（含可能被忘记的 `temporal_pi3_report.md`(T12) 和 `letterbox_mask_neg.md`）；baselines T2 OmniStitch / T9 ViPE / T17 Panacea / T18 DepthPro 全 NEG/no-transfer。

## 3. 当前状态（2026-06-02）
- **可交付硬产出** = **L1 hard_select + E1.5**（几何干净、修光度缝、无重影；残留=诚实的几何切口）。
- **带内忠实融合**（E2–E6/#3）已穷尽证伪——单帧宽基线近景视差，做不忠实。
- **方向收敛配方**：几何(3DGS/LiDAR)管位置(不重影) + 扩散管外观(清晰/合理) + 真实证据(邻相机/LiDAR)当**缰绳**防幻觉。
- **两条待探索路线**（见 `decision_briefs.md`，都还没拍板）：**A** 街景式 plausible 多中心（最强卡 = DB-02 Difix-on-band：限制带的 3DGS 融合 + 单步精修，远场=L1 不全局扭）；**B** DiT360 + 真实缰绳（最强卡 = DB-03 EPI-Mix：极线+LiDAR 参考注意力，深度只重加权不前向 warp → 绕开 E2-E6 的墙）。**有一个超便宜的 LiDAR 选拷贝 kill-test（DB-01）同时卡住 A、B 最强卡——动 GPU 前先做。** 还有 DB-05 缝内度量+物体安全门（基础设施，强烈建议并行）、DB-08 帧筛选旁路（保底干净语料）。

## 4. 基础设施（GitHub / 本地 / Drive 三地）
- **GitHub**：`git@github.com:QiPan-Ronnie/Waymo2Panorama.git`，分支 `main`，**直推已授权**（无需 PR）。commit footer：`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
- **本地**：`D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama\`（BaiduSync 同步；图也拉到这里）。
- **Drive**：`MyDrive/koi_waymo2pano_colab/`（panq@usc.edu）——大产物 `outputs/phase3/`、`results/`；AV2 数据 `data/argoverse2/val/`（5 个 log）；缓存 `cache/`（含 FLUX 32G + DiT360 LoRA）。
- **Colab 计算**：`agent-colab-direct` v0.1.0（公开 MIT，`github.com/QiPan-Ronnie/agent-colab-direct`）。每次会话由用户开 A100 runtime 并给隧道 URL+token；helper `scripts/_colab.py`（HTTP exec/write/read）。**Windows 上必须 `export MSYS_NO_PATHCONV=1`**。DiT360 跑法：克隆 DiT360 + 卸 torchao + 把 FLUX/LoRA 从 Drive 拷到 `/content/hf_local` 离线加载（绕 600s FUSE 超时）。
- **硬规矩**：**HF token 绝不发到 Colab**（分类器会拦，用户标准规则）；FLUX.1-dev gated + 非商用许可。

## 5. 工作准则（务必遵守）
- **每张经手的图必须用 vision 亲眼看过才能下结论**，绝不"指标好但视觉差"（吃过大亏；标尺和眼睛冲突时信眼睛）。
- **先和用户一起定方向，再动手**；任何新实验方向先在 `decision_briefs.md` 开 brief（含 Kill criteria + Max scope）。
- **不要重跑已 NEG 的方向**（尤其 copy-selection 家族 = DB-10 已 rejected；E2 深度重投影家族已死）。
- 每个产物在 progress.md 里写清 **GitHub / 本地 / Drive** 三处位置。

## 6. 你现在该做什么
**读完 1–5 → 简短回报"已读 + 理解 + 方向倾向" → 等用户一起讨论先动哪条 DB。** 不要先跑实验。你的角色是和主 agent/用户保持同步、能随时接力，并在动手前一起把方向和闸门定清楚。
