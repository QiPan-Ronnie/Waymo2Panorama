# Decision Briefs — live queue

Convention: completed briefs are archived (one line each) here and recorded in full in `progress.md` (newest-first). Full history: `git log` of this file + `progress.md`.
RESULTS GO IN `deliverables/` — not `agent/` (agent/ is working/evidence scratch only).

---

# DB-146: Evidence-gated spectral inverse — 训练内证明频带，失败即回退
Status: **ACTIVE（2026-07-17,user 授权 L4 持续迭代；当前唯一 ACTIVE）。DB-145 的无门控 B 判决保持不变，本条只研究安全门控，不授权修改 v15。**
Question: 能否仅用 outer-training 内部的交叉验证，自动决定 sensor-native 逆成像在每个局部地面块可恢复到哪个空间频带，使最终结果在保留 dry/high 真实增益的同时，对 low-observability 与 wet/specular 自动回退到 A 或诚实黑？

**第一性原理：**
  - 原始像素只给出 `y = Hx + noise`；“被某个 footprint 覆盖”只说明 `H` 非零，不说明 `H` 的高频方向可逆。
  - DB-145 的棋盘纹正是近零奇异模态被放大。正确动作不是继续平滑整图，而是只放行被独立真实像素证明的频带；未证明的模态不存在恢复权。
  - outer held-out 是法官，不能参与选门。门只可由 outer-training 内部的 source-group 交叉验证产生；最终仍必须由 untouched outer held-out 和眼核判决。

**固定协议：**
  1. 沿用 DB-145 r3 的 3 logs × high/low 六块 frozen patch 与 outer train/held-out；不重选 BMW ROI，不看 outer 结果调门。
  2. outer-training 的完整 source groups 按几何有效 pixel 数确定性分成 3 folds。每 fold 用其余 groups 重建 leak-free A/B，只在该 fold 的原始相机像素上验证。
  3. 候选不是逐场景超参搜索，而是同一组固定残差频带：`A + LPσ(B-A)`，σ=`8/4/2/1/0 cell`；从粗到细逐级放行。一个频带必须在 inner folds 中改善、跨 fold 稳定，且不过度增加 Nyquist/checker 能量，否则截断。
  4. 最终 D 在全部 outer-training 上重建一次，再应用 inner 选出的最高安全频带；没通过的区域回退 A。wet/view-dependent residual 只能收紧门或 abstain，不能扩大生成模型。
  5. r3 六块规则冻结后，再增加至少 1 个未参与规则选择的新 AV2 log、high/low 两块作泛化终验；新 log 也必须先冻结 outer split，再运行同一规则。

**PASS gates：**
  - r3 outer 六块中，D 的 robust MAE 与 median RGB L2 均不得相对 A 实质恶化（容差 1%，且眼核无新棋盘/彩边/moiré）；两个 dry/high 至少保留一个明确正增益。
  - low 与 wet 的门必须能主动收紧；不得把“latent 更锐”当成功。
  - unseen-log high/low 同一规则过门；任何逐 log/逐 patch 常量、outer-heldout 选阈值、BMW 特判均直接 KILL。
  - 输出必须包含 `safe_valid / selected_band / uncertainty / fallback_reason`，mask 白只能来自通过门的真实观测反演或 A 的真实像素。

**Kill criteria / Max scope：**
  - 若 inner gate 与 outer 真值方向系统性不一致，或必须看 outer 才能选频带，KILL sensor-native 生产升级，保留 v15 A。
  - 最多固定 5 个频带、3 folds、两种无场景常数的稳定性门；不得扩成 learned gate、扩散模型、BRDF 或全量 555 样本重渲染。
  - L4 预算 12 GPU-hour；先复用现有 3 logs，只有通过 r3 才下载 unseen log。每轮必须留下 metrics 与视觉 board，但不重复做与判决无关的基础审计。

---

# DB-145: Sensor-native 各向异性地面逆成像 — pixel-footprint operator kill-test
Status: **DONE — CONDITIONAL / NOT PRODUCTION（2026-07-17）。无门控 B 已 KILL；sensor-native 假设仅保留为 observability-gated 后续候选。未修改 db89/db144/v15 或 555 个已交付样本。**
Question: 在不生成、不逐场景调参的前提下，直接从原始 AV2 相机像素及其真实地面投影足迹建立前向算子，能否比 v10/v15 的「2.5cm 网格 + 最多 6 观测 RGB 中值」恢复更多可被 held-out 原始相机验证的真实地面细节？

**为什么这条没有真正做过：**
  - v10/v15 当前所谓 MFSR 是细 world-BEV 网格上的多观测中值融合；它没有联合反解潜在纹理，也没有显式保留每个原始像素的亚像素相位和二维 footprint。
  - DB-118 虽做过 `T+δ+c` 联合优化和 GSD-aware 观测，但其主流程先查询 BEV cell center、再回投相机，落盘核心仍是 `grid index + RGB`；GSD 是标量近似，不是从原始像素四角/局部 Jacobian 得到的各向异性成像算子。因此 DB-118 是重要先证，不等于本题已测。
  - AV2 地面在 20–28m 处主要以约 4–6° 掠射角进入相机；一个像素在地面上的足迹沿径向会被拉长，径向/切向长轴比量级约为 `r/h≈9–12×`。把这种椭圆退化近似成一个点或各向同性 mip，会抹掉仍可能由多帧互补采样恢复的切向信息。

**核心假设（必须可证伪）：**
  - 对每个合法原始相机像素，用像素四角射线与固定/强约束 2.5D 地面求交，得到地面椭圆 footprint（或等价 Jacobian/EWA 核），构造 `y = H(T; pose, gain) + noise`。
  - 在多帧 footprint 确实互补、且观测条件良好的区域，用 robust data term 联合求解潜在纹理 `T`，应比中值融合更清晰；所有观测共享的 null space 不可恢复，必须由可观测性图判为 unknown/abstain，不能靠正则项“锐化”出来。
  - 湿地镜面反射不是同一个静态 Lambertian 纹理；首轮只把它当 falsification / rejection 分支，不把复杂 BRDF 塞进主解法。

**非目标 / 红线：**
  - 不用 FLUX、DiT、ESRGAN、ProPainter 或其他生成先验；不追求“看起来完整”，只问能否增加可验证的真实信息。
  - 不跑完整 1+92，不改 `scripts/phase3/db89_ghost_recovery.py`、`agent/db115_drivers/db144_v15.py`、v15 mask 契约或已交付 555 个样本。
  - 不 wholesale 移植 RoMe/RoGS，不自由优化几何到足以吸收纹理误差，不允许手画 ROI、逐场景阈值或只对 BMW 调参。

**一次实验的冻结设计：**
  - **场景**：3 个 log，各代表 dry-straight / dry-turn / wet-or-specular；ID 在看结果图前按现有几何/质量账本自动选定并写入 manifest。BMW `02a00399…` 最多只是三者之一，绝不是调参目标。
  - **patch**：每 log 自动选 1 个高可观测、1 个低可观测的 `2m×2m` 地面 patch，共 6 个；选择只看 footprint 数、面积/长轴比、视角多样性、亚像素相位覆盖和动态遮挡，不看重建好坏。
  - **A 基线**：复现 v10/v15 `2.5cm + ≤6 slot RGB median`。**B 主案**：固定/强约束地面 + sensor-native 椭圆 footprint + bounded pose/gain + Huber/L1 data term。**C 诊断**：B 加跨视角反光/离群拒绝，只回答 wet 失败是否来自观测模型破坏。
  - **严格 held-out**：每个 patch 预先冻结整台相机或连续时间块，完全不进求解；最终把 A/B/C 重新渲染到这些 held-out 原始相机，与真实 raw view 比较。latent texture 自己更锐不算证据。
  - **可观测性账本**：输出每 texel 的 footprint count、有效覆盖、长轴比、角度/相位多样性、局部条件数 proxy、provenance 和 uncertainty；它必须能提前解释“哪里可恢复、哪里只能黑”。

**Pass criteria（全部满足才升级）：**
  1. B 在三类场景中至少两个 dry 高可观测 patch 上，相对 A 同时改善 held-out robust photometric error 与全分辨率眼核；线纹/路缘/颗粒更清楚且无双边、振铃、拼布或漂移。
  2. 改善来自 data term：held-out raw view 同步变好，而不只是 BEV/ERP 图更锐；mask 外/已观测真值不得被重绘。
  3. 一套冻结参数跨 3 logs；高/低可观测成败与事前 observability prediction 一致。
  4. 产物能逐像素追溯到真实观测，并给出 uncertainty；不可观测区保持 abstain。

**Kill / 降级判据：**
  - dry 高可观测 patch 的 B 对 held-out 不优于 A → **立即 kill sensor-native MFSR 主张**，接受当前中值融合已接近信息上限。
  - latent/ERP 更锐但 held-out 不改善，或出现假线、双边、ringing → 判为正则幻觉，kill。
  - 只有手选 ROI、逐场景参数、自由几何或 BMW 特调才成立 → 违反 General，kill。
  - dry 通过、wet 失败 → 不扩成大型 BRDF 工程；wet 走自动 rejection/abstain，主案只声明适用于 photometrically stable 区域。
  - 只有转弯高视差窗口通过 → 不宣称 universal fill；最多把 observability 用于条件启用/选窗。
  - patch 级证据未过就要求整 log、完整 1+92 或改量产内核 → scope violation，停止。

**Max scope / GPU budget：**
  - 代码只进 `agent/db145_ground_operator/`，结果只进 `deliverables/db145_ground_operator/`；3 logs × 2 patches，单卡串行。
  - **1×L4 24GB 足够且是首轮指定卡**；chunk 处理 source observations，目标峰值 `<16GB VRAM`，硬上限 **4 L4 GPU-hours**。不下载任何大模型权重。
  - 预计纯优化几十分钟到约 1 小时，数据定位/几何预处理可能占主要时间；Colab 会话建议留 3–4 小时、RAM ≥24GB、临时盘预留约 40–60GB。
  - 只有本 brief 全部 pass 后，才另立新 brief 讨论 full-log operator、world-map 增量更新或 A100/Blackwell 扩量；本轮不提前优化吞吐。

**Required vision check / outputs：**
  - 每 patch 必出：raw source + footprint overlay、observability map、A/B/C latent crop、uncertainty、held-out render↔raw 对照、最终 ERP crop；眼睛负责判伪影，held-out 负责防“假锐”。
  - 固化 `manifest.json`、完整 config/seed/env、每 patch metrics、失败原因和一页 verdict board；结果回写 `progress.md`。未看全 6 组原图不得下结论。

**执行结果（r3 为唯一有效判决 run）：**
  - 场景/patch 在 P0 自动冻结：straight=`8749f79f…`、turn=`02a00399…`、wet=`05fa5048…`；每 log 的 high/low patch、整台相机或连续时间 held-out 均在优化前写入 `manifest_r3.json`。r1 因漏 ego-body mask 作废；r2 因两块 held-out 实际证据 `<10%` 在 P0 作废；r3 六块 held-out 几何证据均为 `10–35%` 且与训练组不交叉。
  - robust MAE 的 `B vs A`：dry-straight high **+4.36%**、dry-straight low **−113.93%**、dry-turn high **+52.85%**、dry-turn low **+1.73%**、wet high **−7.83%**、wet low **+10.72%**。两个 dry-high 的 median RGB L2 也都改善。
  - **眼核否决全局升级**：turn-high 的白色道路标线边界改善是真实的；但 straight-low 是“latent 更锐、held-out 翻倍变差”，turn-low 出现棋盘/彩边，wet-low 出现严重斜向棋盘和 moiré。wet-low 即使标量 MAE 改善，仍按“眼睛胜过指标”判失败。C 的整 source 拒绝不能稳定消除局部反光/别名伪影。
  - **最终判决**：未经条件数/null-space 门控的 B **KILL**，不得进入量产、不得把其 support 全标白；sensor-native footprint 的局部价值被 dry-high 证实，故研究假设不全死，最多进入下一轮“训练内 held-out + 局部条件数 + 截断奇异模态”的 evidence gate。wet 只允许 rejection/abstain，不做大型 BRDF。
  - L4 实测完全足够：r3 六块 P1 墙钟 **394.4s**，峰值 **387.5MB CUDA**，训练/held-out raw pixels 合计 **1,768,341 / 395,378**，远低于 4 GPU-hour 与 16GB 门。
  - 产物：`deliverables/db145_ground_operator/{manifest_r3.json,verdict_r3.json,verdict_board_r3.png,r3/}`；Drive `results/db145_ground_operator/db145_r3_20260717/`；实现 `agent/db145_ground_operator/`。完整数字与下一实验约束见 `verdict_r3.json`。

---

# DB-136: v15 数据契约定版(用户 + koi 三方对齐)
Status: **DONE (2026-07-17,DB-144 全量收官)。** 五项契约已全部落地；AV2 850 个可用 log 已 100% 判定，最终交付 555 个 A/B 双版样本(val 101 + train 454)，零残留 FAIL。以下保留定版时的完整契约与预测，实际终盘数字以 `progress.md` DB-144 为准。
Question: v15 数据集喂 Cosmos 微调,第三级填充用什么 / 浅脏瑕疵帧放不放行 / mask "白=真实" 契约怎么补齐 / A vs B(去车头填充 vs 车头区全黑)两版本要不要一次导出 / 多窗与旧场景重制开不开——五个契约级问题一次拍死。

**五项拍板(全部用户亲自定):**
  - **① 第三级填充 = Telea 保留**(用户原话"就我们的 telea 吧)。tier3 三方实测:Telea 大洞灰白糊 < Wan(洞内 composite 可用但 640→2048 上采样偏软、337s 慢 4 倍)< ProPainter(85s 最优)。用户选保守 Telea,PP/Wan 判决留档备用(下游 Cosmos 会重生成外观,tier3 只需几何占位)。
  - **② Telea 翻黑 = 采纳**(用户"telea 的 mask 我觉得做的很好,可以保留")。Telea 插值像素(占比 2-9%)在 mask 从白改黑,画面像素零变化,补齐"白 = 100% 严格真实"契约。证据 `19350c96_v15_preview/telea_maskflip_demo.jpg`(帧逐像素相同、mask 红圈区白→黑)。
  - **③ γ 浅脏放行 = 条带版定档**。演进链:帧级(用户质疑"全黑帧 Cosmos 没锚")→ 条带版(接缝 ±90px 竖条,标定 = 7 相机外参一次算出 yaw:front 0° / front_l_r ±45° / side ±99° / rear ±153°,`x=(0.5-yaw/360)*2048`,瑕疵帧的 manifest 自带超差 cam_pair 查表)→ 用户嫌条带太多 → 像素级实验(条带∩纹理行)saved=0%(该帧条带全程有结构)→ 数字定谳:条带真实代价 = 21,337px = 全帧 1% = band 内容 3.9%,且仅发生在 92 帧里 1-3 帧,总监督损失 <0.1% → 用户拍板"就这样吧"。功效:val 被整场枪毙的 44 个瑕疵场景复活 ~30 个。
  - **④ A/B 双版本 = koi 要求,一次渲染同时导出**。A = 车头去掉 + 时序真实填充 + 无源区黑;B = 车头区直接全黑(= A 流程的 band 中间产物,增量成本 ~1min/窗)。同窗口同帧位严格对齐,供 A/B 训练对比。**旧 73 个场景随 v15 全量重制自动补齐 A/B(用户特别叮嘱)。**
  - **⑤ 多窗 + train 全池 + 旧 73 重制 = 全开**(用户"可以就这样吧")。

**核心论点 — 为什么去车头必然出现黑区(开会核心,用户指定记录;92b900b1 红 F-350 铁证):** 原 AV2 白色车头占据的画面位置,去掉车头后其背后内容(贴身车下半身)从未被任何相机拍到(原始 ring_side_right 里皮卡近到溢出视野底缘)。黑区 = 掀开车头遮挡后的真实数据边界,非处理缺陷;"以前没黑"只因那里显示的是白车头图像本身。mask 全标黑 = 生成责任归模型。所有贴身大车场景(Hertz / Gordon 货车)同族,是数据固有属性。证据 `_gamma_static_demo/92b900b1_hoodproof.jpg`(上 = 原图白车头挡皮卡下半身,下 = 去车头后诚实黑)。

**数字包(PPT 直接可用):**
  - 150 val 去向:73 产出 / 44 瑕疵整场拒(旧一票否决规则) / 10 静止(物理不可救) / 4 无干净窗 / 其余早期实验。
  - 冤案史:run100 通过率 17% → v14.4 修 manifest 覆盖 bug 后重审 45%(83 个被拒重审放行 45 个,fd_36 / fd_44 实测 0-3 帧真脏)。
  - 每成品成本(双机):17% 时代 ~16min → 修 bug 后 ~6min → v15(γ + 多窗,~60% 通过)预期 **4-5min/成品**。
  - 瑕疵实态:8-12.3px 贴身车接缝错位、1-3 帧/场景;最重 = 92b900b1 a154 红皮卡裂口(12.3px);典型 = 9-11px 轻微重影,缩略图不可见。
  - 产能预期:train 700 + val 150 全池 × ~60% 通过 × ~1.5 窗 ≈ **550-700 个数据**,双机 2-3 天。
  - 当前库存:73 个 v14 成品(`production_v14` 66 + `cascade_v1` 7),v15 重制后统一语义。

**v15 数据集布局(已定):** `datasets/av2_1plus92_v15/<log8>_w<n>/{A,B}/{frames,masks,clip}.{png,mp4}` + `sample_sheet` / `ledger` / `worldmap` + 顶层 `manifest` / `run_summary` + `_docs/`;根目录单点预创建防 Drive 分身(07-13 曾双机 makedirs 竞争产生两个同名目录)。

**PPT 素材索引(全部已存在):**
  - **mask 语义三图**:`meeting/mask_demo/adf9a841_f0001/f0046_frame_vs_mask.jpg`(白/黑语义)、`gamma_frame_level_demo.jpg`(γ 帧级)、Drive `_gamma_static_demo/92b900b1_seamstrip_demo.jpg`(条带版三行)+ `92b900b1_finestrip_demo.jpg`(像素级实验)。
  - **黑区成因**:`meeting/mask_demo/92b900b1_hoodproof.jpg` + `92b900b1_blackproof.jpg`(egozone + seam 超放大);`meeting/新建 Markdown.md` 已有成文论点段。
  - **Telea 翻黑**:Drive `19350c96_v15_preview/telea_maskflip_demo.jpg` + `v14_vs_v15_cmp.jpg`。
  - **tier3 三方**:Drive `_tier3_abc_19350c96/tier3_cmp_f020.jpg` + `clip_telea/propainter/wan.mp4`。
  - **A/B 版本样例**:Drive `db123/cosmos_abl_A_black*`(纯黑)vs `cosmos_abl_C_tempofill*`(填充)。
  - **瑕疵实态**:Drive `_dirty_examples/92b900b1_a154_zoom.jpg`(最重裂口)+ 两张 dirtydemo。

**执行结果(2026-07-17,DB-144 v15 全量量产收官定谳):** AV2 **全 850 可用 log(val 150 + train 700)判定 100% 完成**。**最终库存 = 555 个 A/B 双版 1+92 样本 = val 101(150 判,67%)+ train 454(700 判,64.9%)**,超本条数字包「~500 / 550-700 预期」命中偏上、超 BOSCH 口径「~500」11%。train 700 拒因:`SKIP_resid` **133**(真实填充覆盖不足=最大拒因) / `SKIP_static` **53** / `SKIP_fine_dirty` **47** / `SKIP_no_clean_window` **13** / `FAIL` **2**(map merge 瞬时故障,均断点重跑回收 verdict=OK)→ **零 FAIL 残留**。质量:成品中位端到端 704s(11.7min/台)、resid 中位 6.3% / p90 12.2%(15% 门内富余)。收官夜双 G4 跑 `0of6`/`3of6` 半分片、哨兵 4.6h 守护零事故;train 700 全程 ~6 次 Colab 回收 + 2 次本地断电重启,Drive ledger 断点机制零判定丢失。γ 接缝条带规则(dirty≤3 帧、坏接缝竖条 ±90px 在 A-mask 标黑)val 救回 34 个 log。A/B 双版产物 Drive `datasets/av2_1plus92_v15/{val,train}/<log8>_w1/`。下一步 = koi 用 A/B 对照做 Cosmos 微调 conditioning 实验(A/B 双版 = 本条 ④ 契约设计初衷)。详见 progress.md DB-144 07-17 终章条目。

---

# DB-123: scene-band ego 车身去除 — band 帧下缘车身(hood 反光凸块 + 两端车顶总成镜面反光弧)去除
Status: **DONE / absorbed into DB-136 + DB-144 v15 production (2026-07-17)。** v15 A/B、ego removal、真实填充与严格 mask 已全量落地；以下保留历史问题与判决链。
Question: band 帧下缘的 ego body(hood 反光凸块 + 两端车顶总成镜面反光弧)与 f000(无车头)不一致,喂 Cosmos 会产生前后帧矛盾;如何在不发明假地面、不损伤真实非车身像素的前提下把它干净去除,并让去除后的洞区对 Cosmos 生成友好?

背景:band 帧下缘有 ego body(hood 反光凸块 + 两端车顶总成镜面反光弧),而 f000 无车头、band 有,喂 Cosmos 不一致。koi 明确要求去除。

已定稿 v6/v7 基线:解析双盒(AV2 ego 系原点=后轴轴心)——body `[-1.25,-1.05,-0.45]→[3.95,1.05,0.60]` + roof `[-1.00,-1.00,0.60]→[1.30,1.00,1.30]`,per-camera 图像域 mask,composite 主投影在源头拒除→置黑;生成器 `agent/db115_drivers/db123_egomask_analytic.py`(纯标定解析、毫秒级 per-log);内核 hook=db89 DB-123 v2(`EGO_IMG_MASK` npz 接口,空 mask byte-identical);driver v7 端到端验证过(e28c16d0 COMPLETE)。宁过勿漏原则。

用户 07-11 晨判决,开三条线:
  - **线1(黑 mask Cosmos 机理论证)**:写机理论证文档给 koi 决策——纯黑洞区 vs mask + 生成基础地面,哪种对 Cosmos 生成更友好、失真更小。
  - **线2(时间反投影填充实验)**:band 帧 A 的车头区用邻帧 A+2..A+10 的地面观测反投影填充(= STAGE-4 fill 机制的定向应用),对比黑化版做眼核 + 时序闪烁评估;并行深度调研 video-inpainting / 时序一致填充方案。
  - **线3(v6 mask 过拦精修)**:用户圈出 cd22abca a125 三处过拦(mask 吃掉车头以外的真实地面);修法=rear 盒 y 半宽收窄 + 车顶盒 z 上限微调 + dilate 9→5,目标=mask 边界贴真实车顶上缘 ±10px。

留存:证据链 12+ 图 `deliverables/db115_pro/db123_ego_removal/`;Drive `results/db115pro/db123/`;memory `db123-ego-removal.md`。

**2026-07-11 Cosmos 代理 ablation 判决**:公开 Cosmos-Transfer2.5-2B(diffusers 0.38)跑 A=黑化 vs C=时间反投影填充,同段 31 帧、两控制强度(cs=1.0/0.5),四变体结论——cs1.0 黑区被 base 当"内容"原样保留(实锤"黑=生成域"是微调语义非模型先验)、C 填充两强度均被尊重且下缘连续零伪影、cs0.5 时 A 黑角仅部分脑补留残迹。**建议:门控填充(时间反投影)为主案推给 koi,黑化为保守回退**;终裁需 koi 的 360 微调版跑同一对输入。数据已备:`db123/{u8}_fine`(93 帧黑化)+ `{u8}_gfill`(31 帧填充);产物 `cosmos_abl_*` mp4/stills + 对比图(Drive `db123/`,deliverables 19 文件)。

---

# DB115-PRO2: 冻结 `av2_1plus92_dataset` 好像素的分层收口——地面污染、ego/真盲区、sky seam、mask 与吞吐
Status: **SUPERSEDED by DB-136 + DB-144 v15 production (2026-07-17)；历史产物保留。**
Question: 能否不重建已经较好的 scene band/真实地面、不让 world-BEV RGB 再次接管前景，只在有明确 ownership/provenance 的缺陷区修复 ①孤立黑斑 ②ego body 残留/真盲区 ③sky↔scene-band 接缝，并同时产出严格的 1+92 known-mask 与 fresh、可复现的速度账本？

Frozen baseline / non-negotiables:
  - 三个 f000 分别为 BMW a014、2c652f9e a145、8749 a160；后续 92 帧 scene band 是硬资产。基线目录只读，任何候选均另写 PRO2。
  - 非目标像素 byte-exact；BMW 的近场弧线/curb 与 8749 的连续白弧是回归哨兵。任何膜、生成、MTF 或重采样若把它们磨糊，组件直接判负。
  - BEV/map 可作 source ledger、拓扑 QA、洞区低频 init；不得作为普通可见地面的 RGB 上屏载体。生成仅进入“全 log 无合法证据”的真洞；车辆下方非常规 ERP 楔形不允许生成整车/假路面。

Plan / staged gates:
  - **P0 ownership freeze + defect ledger (CPU)**：从 DB115/116 原始渲染 sidecar/valid mask 还原 `band/sky/observed_ground/ego/fg_occ/unknown`；先写测试证明 mask 外 byte-exact、ERP wrap 正确、真实黑像素不会因 RGB 阈值被误判 unknown。若旧 sidecar 不足，回到渲染器导出 ownership，而不是从成品颜色猜。
  - **P1 observed-ground / ego (old-base small A/B)**：`av2_1plus92_dataset` 成品是唯一像素底板；孤立黑斑只在 provenance/形态/前景接触门共同确认的小连通域内修补，mask 外 byte-exact。`fg_occ`/皮卡下大黑区与孤立黑斑分流，采用“选帧惩罚 + 深短接触阴影 + abstain”，禁止画路面或让整片 BEV/REST/MTF 接管。
  - **P2 sky seam (simple first)**：scene band 与结构物锁死；第一臂只在双方均为天空的 seam 窄带做 Gaussian feather，以最小半径消除硬边。宽 cyan camera-sky 块与窄 seam 分开记账；Gaussian 不负责消除前者。只有窄带方案视觉失败，才允许升级到更复杂的 photometric/multiband/outpaint；禁止改楼体/线杆/文字。
  - **P3 mask contract (CPU)**：f000=全 255；f001-f092=`known/condition=255`、其余 0，2048×1024 uint8 单通道，逐张同名。mask 来自最终 compositor ownership，不用 `RGB>threshold` 反推。
  - **P4 speed (L4 first, A100 generation)**：fresh 1/16/93 帧、全新输出目录、无缓存；保存每帧 QA sidecar，skip 时无 sidecar 必须重渲；hash 比对 1/16/93 输出。先 profile/缓存/模型复用/7-camera batch/worker sweep，再由证据决定是否 CUDA `grid_sample`，不继承历史 32s 或 188s 口径。
  - **P5 generality**：三现有场景同一代码与阈值，随后一个随机 held-out log；未过 held-out 前只称“三场景有效”，不称 general。

Kill criteria: 任一成立即停该组件——①改动非目标 band/建筑/车辆/文字或回归哨兵；②靠 per-scene ROI/阈值/手绘 mask 才过；③用黑色阈值生成 known-mask；④生成出现文字、车体、重复标线、swirl 或大平板；⑤缓存/缺失 QA 被误报为 clean/提速；⑥同参数在第二场景明显退化；⑦ PRO2 需要读取或覆盖 `db115_pro` 才能工作。
Max scope: 只新增 `agent/db115_pro2/` 的聚焦模块/测试/runner，必要时给既有 DB116 渲染路径加一个默认关闭的 ownership/QA 导出 hook；不改基线 PNG，不改 DB118/DB121 默认算法，不 commit（当前 repo/用户纪律）。GPU 先小样本，视觉 PASS 后才扩全量。
Required vision check: 每个候选必须看原始 2048×1024 全图 + 同位 crop + diff/ownership overlay；主 agent 逐张眼核，数值只做护栏。第一生死图=`2c652f9e a145`，同时以 BMW a014/8749 a160 好线条做负回归。
Compute: L4 负责 fresh band/ground/速度 A/B；A100 负责 sky overlap 与仅真洞生成；凭据只放进进程环境，不写 repo/log。

Checkpoint (2026-07-10): `av2_1plus92_dataset/.../f000_c0_a145_perfect.png` 与 `db116_frame1/V7_c0_a145.png` SHA256 均为 `BD26C1FB72AAC2CD7363E897599DE46C7E39CAA595BF7DAC1EC4408E78B1E77D`，确认基线谱系。simple Gaussian 四臂中 `radius=24,sigma=4` 是当前视觉/保真折中：seam 邻行 Lab 跳变 median `2.236→1.000`，0 个 building-column 像素改变；36/48px 虽指标更低但开始过度软化。ground 选择器和 byte-exact compositor 已单测通过，但 a145 两个黑斑的实图修补尚未完成眼核，不得写成已交付；皮卡大黑区明确不进入该修补器。当前聚焦测试 `78 passed`。

---

# DB-121: AV2 HD-map 条件的分层 world-BEV 路面编译 — 标线拓扑与沥青材质解耦
Status: **PAUSED while DB115-PRO2 ACTIVE (2026-07-10；现有结果与产物原样保留)。原状态：ACTIVE (2026-07-09,user 明确授权按红队建议立项并实验)。P0 BMW map-alignment 视觉判决 PASS：AV2 原生 city 坐标的双黄线/白边/crosswalk 与 T 实景结构同拓扑，ERP 回投形成与当前破碎大弧线同形的完整连续曲线，叠 R71 未形成远离实线的第二条假线；第一版像素证据配准候选打到 (+0.5m,+0.5m) 搜索边界且距离仅改善 3.2%，全分辨率眼核明显更差，v2 已加通用 abstain 门并自动回零偏移。P1 已完成窄横截面门控 + 同 feature sideband residual 编译器 + material/paint 双输出，25 项测试通过（含 2.5cm 真尺度 0.125m 窄线 vs 0.525m swirl、宽带不得劫持 offset、窄白芯+宽彩 halo 不得作 donor、mask 外 byte-exact）；本地全分辨率 R70→R71 眼核又确认 REST 会把清楚标线磨成紫绿 halo，因此正式架构改为 material-only 走 REST、真实 paint residual 绕过 REST 后贴。尚未跑远端同源 T_gsr/Tgen2 真图，不宣称外观修复。DB-119 暂停、DB-120 排队。**
Question: 三场景共同头号缺陷“大弧线/曲线标线糊碎”，根因是否是把拓扑和材质都塞进 RGB mosaic/扩散模型？若让 AV2 vector map 只负责 lane/crosswalk 的世界坐标拓扑，让现有 DB-118 T/Tgen 负责沥青材质、真实同类 paint donor 负责油漆外观，能否在不改 scene band、不发明文字/车辆、不做 per-scene 手调的前提下，使 BMW a014 目标弧线在 T 域和 ERP 域都连续自然？

Alternatives attacked:
  1. **A: 现计划“像素弧拟合 + 沿弧 donor”**：最便宜，但只能从已经破碎的图猜拓扑，容易拟错弧/相位；保留为无地图 fallback，不作为首选。
  2. **B: AV2 HD-map 拓扑 + 真实 donor 材质（本 brief）**：地图只给曲线/类型，真实证据决定 offset/宽度/颜色/磨损；不让生成器画细线，风险最可控。
  3. **C: map-conditioned diffusion/LoRA**：可能最通用，但在 B 尚未证明地图与目标缺陷对应前就训练属于烧算力；本轮禁止。

Plan / staged gates:
  - **P0 map-alignment（生死关，BMW `02a00399...` a014，CPU）**：读 `map/log_map_archive_*.json`，把有实际 mark type 的左右 lane boundary、crosswalk polygon、drivable area投到 DB-118 2.5cm city-XY T 网格；输出 typed vector mask + 半透明 T overlay + 经现有 `geo2.npz` 回投的 ERP overlay。只允许从全图可见标线自动估一个鲁棒全局小偏移，不允许手点控制点或按 ROI 调参。
  - **P1 topology/material split（P0 通过才做）**：只允许单峰 `SOLID_WHITE`；在 map Frenet corridor 内按 0.25m 分 bin，用窄横截面宽度（≤0.22m）+ 两侧 asphalt contrast 判 REAL donor，宽亮 swirl/同色模糊带不得投票或当 donor。只修两侧均有真实 flank、≤3m、且存在等长不复用 donor 的 gap；先用 ±0.45m sideband 恢复最多 ±0.30m 的 material，再搬运 donor 相对其本地 asphalt 的 signed paint residual。真实 anchor 与 corridor 外 byte 保留；DASHED/crosswalk/双峰本轮 abstain。
  - **P2 ERP two-layer eye gate**：复用 `db117_resample.py --cw 0.025` 分别回投 material-only T 与 compiled T。material-only ERP 走 REST；两者之差构成有 provenance 的真实 paint layer，在 REST 后以固定 MTF 回贴，生成模型不得接触细线。输出 baseline / material-REST / post-paint 三臂全图与同位 crop board；必须在原始亮度、原始 2048×1024 眼核。
  - **P3 generality（BMW 通过才做）**：完全同一参数跑 8749 a165；不得换宽度、offset、阈值。highway 暂不做，避免 fg_occ/车底混入本问题。

P0 pass criteria: (a) map 中至少一条有标记类型的曲线与 BMW 目标弧线是同一拓扑；(b) 在未损坏可见段，自动对齐后视觉偏差不超过约一个实测 paint 宽度，且无明显重复/平行假线；(c) 对齐来自全局可见证据，不来自目标 ROI 手工拟合。
Kill criteria: 任一成立即停——① map 没有对应目标弧线/标线类型；② 自动对齐后仍偏差 > 一个 paint 宽度或拓扑方向错误；③ 需要手工控制点/per-scene 参数；④ 找不到等长连续真实 donor、只能放宽 profile 门/重复铺贴；⑤ P1 新增重复线、纯白塑料感、REST 后 halo/双线仍在、或损伤真实像素；⑥ BMW 过而同参数 8749 明显错位。若高置信写区覆盖不到视觉坏弧约 30%，保留为选择性工具但停止把它当头号缺陷主解。
Max scope: 一个独立 `agent/db117_worldbev/db121_vector_ground.py`；不改 `db89_ghost_recovery.py`、DB-118 optimizer、FLUX 或 shipped 默认；P0/P1 全 CPU，A100 仅作为已挂载 AV2/Drive 执行环境；最多 BMW+8749 两场景。输出只写 `deliverables/db121_vector_ground/`，不新增说明类 Markdown。
Required vision check: P0 typed overlay 的全 T + ERP 全图；P1/P2 的 T 与 ERP 三臂、目标弧线 1×/2× crop；检查连续性、宽度、磨损质感、重复线、假文字/假物体、mask 外 byte-diff。coverage/offset 只作诊断，生死看全分辨率图。
Inputs: BMW map 三件套已在 Drive `data/argoverse2/val/02a00399.../map/` 实证存在；最终归因 A/B 必须用 R71 同源远端 `results/db118/bmw118x/T_gsr_bmw.png` + 对应 Tgen2（先在 geo footprint 机械核验是否同值），本地 `ex_bmw118w_a014_T.png` 仅作链路 smoke；geo=`results/db118/geo/bmw_a014_geo2.npz`；resample base=`FINAL_bmw_a014_R69b.png`。A100 `/status` 实测在线、80GB、active_jobs=0；外部调用额度 19:45 恢复后继续。

---

# DB-119: 跨 log 借用主线(v65)— 从同城异日 log 借真实地面像素替换毯区生成填充 + per-sample occlusion 通用门
Status: **PAUSED while DB-121 ACTIVE (2026-07-09；现有结果与产物原样保留，待 DB-121 判决后再恢复 BMW 跨-log generality)。** **原状态:立项(2026-07-08,承 DB-118 GSR 架构零改动)。触发 = v63/v64 跨 log 战报:DTW 双场景 <4m 命中(hw `9239d493` min=3.6m / 894 pose in 30m / az 166°;BMW `c062ba0f` min=3.3m / n30≈2694),拉帧眼核证实异日"毯"下真实地面裸露、裂缝拓扑可配准 → generality 成立。取代"毯区只能生成"的认知:毯 = 本 log 遮挡体伪装(truck 腹部 / 树影)或 fg_occ,异日同址真实像素存在且可借。**
**更新(2026-07-09,BEST67 落地)**:v66→v69 工程闭环——v68 我方 log 静态占据图判决 hw144 双毯 = 瞬态 moving-box(非停放,DB-109 复现);全城 116 同城 log 按矩形重排名仅 `9239d493` 真过矩形(min 4.6m;`d1695c5e` visfrac=0.00 = 最近 15.3m 被 45m 内街景 occluder 物理挡死)→ **单证人 + abstain = 此场景物理上限(非算法不足)**。融合端定版(`best63.py` step 1.5 v6:disagreement 门 + 中频移植 + 连续能量顶补 + mid 去偏置);pano 链破案 = step6 统一 MTF 低通磨掉 BEV 细纹理 → **step6.5 pano 域移植**(plate 正下方真实 cap 条带中频平铺 + MTF 匹配)+ plate 双线性去糊去平移。交付 `deliverables/db118_surfel/FINAL_hw144_BEST67.png`(皮卡毯→带标线磨损沥青,无平板)。**剩余** = v67 可用帧仅 40(当年子采样,补抓提质)+ BMW `c062ba0f` 3.3m 跨 log generality 复验。用户眼核判 67 车底糊状=反向优化 → BEST68=补丁回归+跨log打底+车底AO v2,交付 FINAL_hw144_BEST68.png;跨 log 角色修正=BEV 基底非 plate 前台。BEST69=S1 整根 bar 外推穿 plate(周期实测 1.00m)+plate 落点验尸(皮卡不在斑马线上,糊状物=色调跳变)+plate↔pano 低频膜,交付 FINAL_hw144_BEST69.png。**
Question: 把 BEST62/64 圈出的两块"毯"(truck 灰绿矩形 = 遮挡体伪装地面;pickup = fg_occ 永久障碍洞),用**同城异日 log 的真实地面像素**跨 log 借用替换 GSR 生成填充,能否让毯区真实纹理与周边裂缝拓扑连续(眼核判)、且不引入 per-scene 调参(generality)?
Plan(v65):
  1. **提取** `9239d493`(备选 `d1695c5e`;BMW 用 `c062ba0f`)相机样本到我们的 BEV 网格(复用 DB-118 extract/GSR,架构零改动)。
  2. **配准验证**:两 log 同城 frame 一致性——裂缝 / 井盖拓扑对齐;city-frame pose 有残差,系统偏移 Δxy 用 BEV 相关性显式估出。
  3. **并入 GSR 样本池重渲染毯区**:跨 log 真实样本喂 DB-118 逆问题优化 T,毯区真实像素替换生成毯。
  4. **per-sample occlusion 门(通用判据)**:gsr_blurfield IoU 0.09 已证 blur-only 路由对"遮挡体伪装"盲(毯区 blur 0.026 = 干净区),须补每样本遮挡门筛掉伪装地面样本,与跨 log 一起实装进 extract/GSR。
  5. **修正验尸投影**:定案 truck 毯真身(卡车腹部 vs 树影路面),校验借用替换命中正确物面。
判决标准: 毯区真实纹理与周边裂缝 / 斑马线拓扑连续(眼核);跨 log 借用不得引入接缝或 per-scene 参数。BMW 带 `c062ba0f` 同法复验 = generality 第二场景。
Max scope: 只动 DB-118 的 `GROUND_MODE="extract"` dump + 本地 GSR 优化(跨 log 样本池 + occlusion 门),不动 shipped fill;先 hw `9239d493` 判决,BMW `c062ba0f` 复验;不训练网络(仅 BA 式优化 T/δ/c + 跨 log 样本注入)。
Required vision check: 每张毯区 crop 全分辨率逐张眼核 vs BEST65 生成版(裂缝拓扑连续?借用像素与本 log 无接缝?);配准 Δxy / IoU 看数值但生死看眼。
Output: `deliverables/db118_surfel/`(承 DB-118);跨 log 索引 `xlog_index.csv` / `bmw_xlog_index.csv`、`gsr_blurfield.npz` 已在 agent/。
Handoff: 承 DB-118/117 纪律(非沙箱 Bash 直连 Colab、读回远端结果验证、结果写 deliverables、中文回复、`.git` 已被 BaiduSync 损坏先不动 git;dr_run launch 前必删旧 DONE marker;FLUX/GSR pipeline VM RAM cache 存活可秒级复用)。[[waymo2pano-general-goal]] [[waymo2pano-ground-fill-physics]] [[db118-inverse-ground]]

---

# DB-118: 地面范式跃迁 — 逆问题联合优化(RoGS 式 surfel/纹理 BA)取代 mosaic 选择
Status: **✅ 原型判决 POS(2026-07-06,BMW 眼核:斑马线零重影+弧线平滑=历版最佳;δ p95=2.5cm 配准被解出)。方向=user 连续眼核批评+"别被现有方案拘束"驱动的从头思考;文献=RoMe/RoGS/MagicRoad。下一步=hw/8749 复验→工程化(障碍阈值/洞接 genfill/仿射升级)→固化替换 V2 构建端。**
**loop 迭代进展(2026-07-06→07,/loop 自主探索)**:
  - **E-ego REFUTED**(第 1 迭代):ego-body 直接污染 ≠ 糊根因(hw118g/h A/B:掩膜拒 76 万样本,T 仅动 272 px,δ p95 0.1164→0.1166;robust reweight 早已投出车体少数派)。通用 ego 掩膜 v3(位移感知+触底连通域)已写入 db89 `EGO_IMG_MASK`,留作保险。
  - **E-shadow POS 大胜**(第 2 迭代):亮模式偏置 opt(丢 lum<muu−0.10 且 std≥0.04)——斑马线复活、污斑收缩,**δ p95 0.117→0.073**(影子多数派污染配准);产物 `ex_hw118i_a144_T.png` → 全链 `FINAL_hw144_BEST4.png`。
  - **E-res/E-sr 判决完成 POS**(第 3 迭代,移出在飞):三 T 对决(hw118i/j2/m,单变量链)——**mip 去卷积**(`db118_sr.py`,Phase2 冻结 δ/c、T 作自由变量 + 每样本 GSD 匹配 mip 层)大胜,近场斑马线/井盖/停止线解出,δ p95 累计 2.5× 至 0.047;2.5cm-plain(朴素 splat)仅边际改善。**判决:近场糊最后一层 = 前向模型(splat 点采样平均)+ 粗网格,不是物理;近场 1-8m 无需生成。** 待修 = 高对比边缘处方格状块(mip 硬取整+层间不连续)。
  - **E-sr v3 判决 NEG(移出在飞)**:coarse-tie(细层零证据格绑粗层)没治 BMW 弧线台阶;重新归因=quilt 类伪影(相邻格不同源主导,源内局部曝光/渐晕差印出交替色块,mosaic 逆问题残影,细层证据掩膜罩不住)。bmwv3cmp.png 为证。
  - **E-sr v4/v5 判决 NEG、v6 部分 POS 已入配方(第 5 迭代,移出在飞)**:v4 每源线性空间增益场 + v5 每源仿射配准均不对症(色调/配准类修不了细层稀薄区的逐源拼花台阶);**v6 高斯金字塔降采样**(5-tap 高斯替 P2 box `avg_pool`,消池化边界印块)部分 POS——块幅减弱、黄线边缘更顺但未根除,零代价入标准配方(hw 铁证:splat 无块 / mip 有块 = P2 池化实锤)。`bmwv3/v4/v5/v6cmp.png` 为证。
  - **E-sr 残余块斑在飞(BMW 弧线台阶未根除)**:三候选 = ① 高阶(二次)增益场 ② 渲染端按细层覆盖自适应低通 ③ per-cell 源一致性选择。
  - **BEST52→65 loop 已归档 progress(2026-07-08/09,fable5)**:BEST60 三级深度思考主链落地;BEST63/64/65 修 user 圈的 cap↔band truck 毯(生成 A/B 判 LaMa)+ pickup fg_occ 毯(FLUX s0)+ 天空双缝(cyan 校正 + 交通灯黑团重画)+ 皮卡车底鬼影(接触阴影重塑)。原待办 ①(mip-cap 接 REST + BEST 合成)已由 BEST52/53/54/60 实现。**跨 log 借用真实像素替换生成毯 = v65 主线,已分拆 DB-119(顶部)**;gsr_blurfield IoU 0.09 证 blur-only 路由对遮挡体伪装盲 → per-sample occlusion 门并入 v65。
  - **待办**:① 修好的 mip-cap 接 REST + BEST 合成 = BEST5;② 多场景验证(BMW/8749);③ ego 足迹洞 + 障碍格并入 G1 genfill 掩膜;④ 提速三项(off 死代码/跨 anchor 缓存/懒解码,~2-3×,与 DB-115 4× 正交)等地面质量收敛后实施;⑤ 引擎升级(HYPIR/DiffBIR,等 user 授权外部仓库)。首张完整 360 交付候选 `deliverables/db118_surfel/FINAL_hw144_FULL.png`。
Question: 把地面从"逐点选择拼接"(mosaic,边界 artifact 与源数成正比)换成"全观测联合优化 T+δᵢ+cᵢ"(边界结构性不存在),能否根治波浪/重影/缝/补丁全家族?
判决要点:① mosaic 精度地板(DB-90 定理)= 配准残差 + 光度阶差,地面以百倍源数(200+ 掠射源)重演;② 逆问题 = 未知 纹理 T + 每源 2D 平移 δᵢ + 每源 RGB gain cᵢ,Huber loss 联合优化,边界结构性不存在;③ 原型 v2 收敛(loss 0.0346→0.0329,δ p95=2.5cm,cgain≈1),BMW 眼核斑马线零重影 + ERP 弧线平滑无碎片 = 历版最佳。修复关键:零均值 gauge(δ 均值 + cgain 全局锚)+ δ L2 正则 + lr 降 10×(v1 发散教训)。
Max scope: 原型只动 `GROUND_MODE="extract"` dump + 本地 torch 优化,不动 shipped fill;先 BMW 判决,hw/8749 复验;不训练网络(仅 BA 式优化 T/δ/c)。
Required vision check: 每张 T 图、每张 resample ERP 全分辨率逐张眼核 vs mosaic 版(斑马线/弧线重影与碎片);δ/loss 收敛看数值但生死看眼。
Output: `deliverables/db118_surfel/`;driver `agent/db117_worldbev/db118_run.py`。
Handoff: 承 DB-117 纪律(非沙箱 Bash 直连 Colab、读回远端结果验证、结果写 deliverables、中文回复、`.git` 已被 BaiduSync 损坏先不动 git)。[[waymo2pano-ground-fill-physics]] [[waymo2pano-general-goal]]

**user 眼核 FINAL_hw144_FULL 四问题清单(2026-07-07,已归因待修 = BEST5 计划):**
  - **①左侧车机盖隐约可见 + ②右侧黑块 → classic fill 层的 ego 泄漏 + fg_occ"诚实阴影板"政策遗产。** 双根因:(a) `EGO_IMG_MASK`(位移感知 ego 掩膜)只插进了 extract 分支,**classic fill 源循环还是旧两盒门**,rear 相机后备箱像素混入 → 左侧车机盖隐约可见;(b) fg_occ 走 db89 L2026 的"诚实阴影板"政策(`comp[fg_occ]=plate*0.55`)——持续障碍(persistent)的 footprint 本应走 A 类洞生成,却被压成黑板 → 右侧黑块。**修 F1**=classic fill 源循环插同款 `EGO_IMG_MASK` 门(与 extract 分支对齐);**修 F2**=fg_occ 改从 Tgen(生成层)采样,不再用 `plate*0.55` 黑板。
  - **③band-cap 过渡不平滑 → 三子因叠加。** (i) 直线 ramp(`rows-720` 固定行)vs 真实波浪 band 边界(修=改用 nadirmask 的真实边界曲线做过渡带,不用固定行);(ii) E1.5 缝带低频色彩协调在 BEST 合成中缺失(修=缝带 ±40px 的 cap 侧低频向 band 渐变匹配,重启 E1.5 原理);(iii) 锐度台阶(mip-T 上场后自然缩小,非独立修)。**修 F5**=合成 v2(把 (i)(ii) 装进 BEST 合成端)。
  - **④cap 像素与 band 不在一个 level → 新认领根因 = ISP/锐化统计指纹不匹配。** band = 相机 JPEG,含 in-camera 锐化 halo + 特定亮度噪声谱;cap = 平均/优化产物,无此指纹 → 两者"质感 level"对不上(非亮度/色彩阶差,是统计指纹差)。**修 F4**=终步相机指纹匹配(unsharp 强度 + 亮度噪声谱按 band 路面条带实测统计拟合,**确定性非生成**)。另有 ERP 低仰角投影拉伸的感知成分(物理,非缺陷,不修)。
  - **F3(已在队列)**=mip 方格 fractional-level 修复(承 E-sr v2:相邻两 mip 层插值 + 边缘感知正则,去高对比边缘方格块)。
  - **BEST5 定义** = mip-cap(F3 修好) + F1/F2 修复的 classic 基底 + F5 无缝合成 + F4 指纹对齐 + 天空;预计一个 A100 下午产出;**待 user 拍板**再跑。
  - **上一条 user 指示完成度自评(诚实):** ego body **60%**(extract 路径已修净 / classic fill 层仍漏,= F1 未做);band 过渡 **40%**(颗粒/锐度维修了 / 低频协调与波浪边界未修,= F5 未做);模糊 **概念 70% + 落地 30%**(mip 去卷积原理已解锁验证,但尚未装进交付图 = F3 未收口)。

---

# DB-117: 地面下一代 = world-BEV 公平版(证据门全继承 + 单源渲染 + per-source 色彩 solve + 证据分级)—— fable 5 回归第一性判决与 P0 spike
Status: **✅ P0 三场景判决通过(2026-07-02,hw309/bmw14/u165 眼核)— world-BEV 公平版结构性优于经典;3 轮迭代(anchor 中心帧窗 / BEV-Telea 洞填充 / U6 眼核 NEG 已撤销(条带化+过曝);BMW 紫重新定性=源 ISP 色调非缺陷);残余=curb 源切换碎片(P1 大块 DP-seam)+ 大洞诚实灰(P1 BEV 域生成)。**P1 亦 POS(2026-07-02,hw309f 眼核:BEV 域 FLUX 填洞零 melt 零造车,residual 475k→0)——DB-117 全链收敛;下一步=固化(共享图域+GPU 化)>P2>P3。** **P1.5 配方定型(2026-07-03,hw309h 眼核 POS):genfill→refine(0.30)→layered(tier2 保真)→fgocc 车辆延伸;user 圈三缺陷全过。下一步=BMW/8749 同配方复验→固化。** (前情:fable 5〔项目原架构师〕2026-07-01 回归接手为核心大脑,通读全部 memory/文档/代码/交付视频后的第一性判决;user 全权授权"按你觉得不错的走";P0 spike A100 active_url_5。) **三场景终版收官(2026-07-03):LL_hw309_layered/LL_bmw_genfill/LL_u166_genfill;组合门修近车被盖(user 抓);轻迭代回路(dumpgeo+resample,秒级);配方 v2=场景自适应(亮场景 skip refine);下一技术点=BEV 域源间配准碎片;然后固化。**

判决(第一性,通读后落锤):
  1. **我们没有用错工具,是把对的工具放进了错的求解域。** 经典反投影(fable5 STAGE-4)至今最优,因为它是唯一在三个物理降质轴上都诚实的方法;它的四大可见缺陷(视频 swirl、BMW 紫、NS-inpaint 白条纹彗尾、黑斑死角)**全部是 per-anchor ERP 极点域的实现产物,不是反投影本身的错**。
  2. **nadir 补不完美的第一性 = 三轴降质**:① 分辨率失配——内圈只能 20-28m / 4-6° 掠射看到,源 GSD ~20cm vs ERP 正下方像素 footprint ~4mm,差 ~50 倍;"锐利的正下方地面"物理上不存在,忠实上限 = 20cm 级低通(v3i 的分辨率匹配低通是对的);② 视角依赖外观(Fresnel 天色,只能采样后 view-dependent 补偿);③ 配准放大(掠射 dz→dx 放大 11-19×,spread abstain 是症状不是墙)。遮挡分三类:moving(跨时刻可恢复)/ persistent + ego 轨迹死角(必须生成)/ fg_occ(**本质是"物体下半身未观测",不是地面洞,跨时刻填地面反而错**)。
  3. **world-BEV 从未被公平测试**:2026-06-24 "DROPPED" 否定的是无门 strawman(naive median 堆叠);2026-06-25 的 84-88% 是 selfocc OFF 的 hood 反光假覆盖。代码级实证(工作版 db89 worldbev 分支 L1498-1587)缺 5 件套:(a) `_gzw` 常数平面 Z、无 LiDAR 高度图(掠射放大 → 系统错位);(b) tight-cluster 平均渲染(违反"median 只验证、单源渲染"铁律 DB-88/v6);(c) luminance-only per-cell 归一(不解 WB 色度 → BMW 紫);(d) abstain 二值洞(轨迹带必然 disagree → 大面积洞);(e) 固定 (0,92) 窗口。
  4. **ERP 域生成 = 错域**:F 环"小洞行大洞糊"的本质是 FLUX 不懂 ERP 极点几何;大洞生成应在 BEV 平面域做(模型训练分布内),一次生成全帧共享。3DGS / EPI / 学习法对地面是错工具(地面不缺几何),不追。
  5. **"可恢复 vs 必须生成"的可靠边界 = log 级世界域可见性审计**(gate-funnel 的世界域推广):对每 BEV cell 遍历全 log (frame,cam) 过三门(FOV / two-box selfocc / moving-box),输出 N_vis、best grazing、GSD、方位多样性、moving/persistent 归因。确定性几何,零启发式。
  6. **scene band 近车头断裂 = 一半可修**:route2_middle_v1 / highway t7s(≈a309 白车)断裂案例,DB-105 已证 side_left 单相机看到整车(1610px vs front_left 149)+ LiDAR 密集(1742 点)→ 可修类走 Tier-2 LiDAR-coverage-gated 单相机路由(DB-104 designed-not-built);无单相机全貌的跨缝近物 = 共观测物理地板,只能避(band-gate 兜底)。修 Tier-2 直接提升 1+92 产出率。
  7. **速度与地面重设计是同一件事**:STAGE-4 全部是数据并行几何运算(cKDTree→BEV 高度图栅格化 + bilinear、slab / 投影 → 张量、融合 → scatter),world-BEV 用 torch 写 = GPU-native 的自然形态;一次构建摊薄 93 帧。band-gate 的 STAGE1-3 是另一块存量(后续独立张量化)。
  8. **天空接缝(user 圈图)+ 树冠色斑 = 工程活非物理**:成因 = mask 沿 petal 波浪边界 + 真实带上缘 vignette(p33)+ 色调阶差;修法三件套 = mask 下扩吃掉 vignette + 边界带低频色彩协调(E1.5 原理)+ 树冠 mask 限纯天空 + anti-object prompt。
Question: 把 fable5 经典证据演算从 per-anchor ERP 极点域搬进 log 级 world-BEV 域(公平版:全门继承 + 单源渲染 + per-source 色彩 solve + 证据分级 + LiDAR 高度图),能否一次性根治 紫 / 彗尾 / swirl / 黑斑 / 空旷糊 五类缺陷,同时把 CPU 瓶颈搬上 GPU?
Plan(P0→P3):
  - **P0(判决 spike,进行中)**:改工作版 db89 worldbev 分支为公平版——U1 LiDAR 世界高度图替代常数平面;U2 per-source (frame,cam) 3 通道全局 gain;U3 单源渲染、median 只验证;U4 证据分级 conf / low / hole(low = best-grazing 单源);U5 窗口全 log 分桶采样。三场景判决:highway 2c652f9e a309(彗尾 + swirl + moving 最狠)、BMW 02a00399 a014(紫)、8749f79f a165(空旷糊),渲 worldmap + nadir A/B vs 经典 v7,逐张全分辨率眼核。
  - **P1**:BEV 域生成填洞(先 NS / Telea 平面域延伸,再 FLUX 平面 inpaint),对比 F 环 ERP 域版。
  - **P2**:Tier-2 单相机路由(band 产出率),5-scene 回归防 p10 式拉伸回归(Tier-2 是 gated 路由非重投影,风险低但必须回归)。
  - **P3**:天空接缝三件套。
  - 全程 GPU-native(torch)实现为固化目标;spike 阶段允许 numpy(复用门代码,改动最小)。
必须继承的守门(历史 NEG,一条不能丢):**selfocc 永远 ON**(06-25 hood 反光假覆盖);**Fresnel 色调留在采样后 per-anchor truth-ring gain**(烤进图 = 重蹈 v3b/c);**轨迹带只用 best-grazing 单源 + 验证不 median 堆叠**;**渲染永不平均几何**(单源);**v3i 分辨率匹配低通保留**;**fg_occ 在采样端保持近车保护**(DB-106)。
Kill criteria(预注册):P0 若带全门后、轨迹带以外区域的真实覆盖 / 干净度仍不如 per-anchor 经典(眼核判)→ 世界域假设死,回经典 + 局部修(色彩对齐 per-source gain 单独救紫)。轨迹带本身预期只有 20cm 级低频 + 低信心层,不以它锐利与否判生死。
Max scope: P0 只动 worldbev 分支(gated,`GROUND_MODE` 默认不变),不动 shipped fill;三场景各渲 ≤3 帧;不训练。
Required vision check: 每张 worldmap、每张 nadir A/B 全分辨率逐张眼核(眼睛胜过指标;coverage% 不是质量指标——06-25 教训)。
Output: `deliverables/db117_worldbev/`。driver `agent/db117_worldbev/`(本地驱动),url/token 绝不进仓库(读 `~/.waymo2panorama/runtime/active_url_5.json`)。
Handoff(照抄 DB-115/116 纪律):非沙箱 Bash 直连 Colab、读回远端结果验证、结果写 `deliverables/`、中文回复、难思考自己做只派简单杂活给 subagent、pristine 在 `scripts/phase3/_baseline_fable5/`、`.git` 已被 BaiduSync 损坏先不动 git。[[waymo2pano-ground-fill-physics]] [[waymo2pano-general-goal]] [[db116-frame1-perfect360]] [[waymo2pano-seam-direction]]

---

# DB-116: frame-1 完美 360 panorama — 干净 band 上"地面保真 + 无车头 + 天空 outpaint"的 general robust 方法(自主 loop)
Status: **✅ 方法 PROVEN + pipeline 固化 + batch 端到端打通(更新 2026-07-01 下午)。** 进展链:①general `nadir_imperfect_px` 选帧定型(a145/8749 眼核,fg_occ∪resid 最小);②F 环打通(imperfect 压小盲区 + `db116_ground.py` FLUX 只填 faithfill 局部,无 melt);③整条 1+92 pipeline 固化权威文档 `agent/PIPELINE_1plus92.md`;④batch orchestrator `agent/db115_drivers/db116_batch.py` + 数据本地化(`localize()` s5cmd S3→本地 SSD)→4 机 CPU 拉满(load 12-16);⑤**2026-07-01 下午**:A100 隧道 530 断→换新 endpoint(active_url_5),修 3 个稳定性 bug 后端到端做完 `2c652f9e` 完整 1+92(f000=c8_a168,imp=30434;存 Drive `results/db116/clips/2c652f9e_1plus92/`,93帧/0缺失)。**修的 3 个 bug:** (a) `run_driver` `subprocess(text=True)` 缺 encoding→Windows cp1252 解 FLUX tqdm 非ASCII 崩→`p.stdout=None`→`None+str`,改 `encoding="utf-8",errors="replace"`+None兜底;(b) `process()` 跑完 sky 不检查返回→sky 真失败被 ground `cv2.cvtColor(imread(v7)=None)` OpenCV 断言掩盖(v7 其实 sky 产出,A100 断线那刻误判);(c) **Google Drive 并发写同名目录分裂成 `2c652f9e (1)(2)(3)`**→band-gate 4 机各写一份,主目录只剩 60/319 帧→package 缺 72 帧,即时合并分身修复。**眼核 v8:车头去除✓ 地面保真✓ 天空 outpaint✓,唯右侧 REMAX 楼上方有 FLUX 把树冠延伸进天空的暖褐色斑(待修)。** **★待根治(全量前必修)★:① Drive 分身写(band-gate 每机写独立子目录 or package glob 全分身,别再靠事后合并);② `localize` 遍历 fleet 单机失败即抛→A100 断则全 batch 停,要容错跳过;③ 天空 outpaint 树冠色斑(sky mask 限制在纯天空区 or prompt/构图约束)。** 单场景饱和耗时估 ~22-25min(非一次干净自动跑,含手动补)。user 授权自主迭代、给 A100(active_url_5)+ L4/T4。承接 DB-115(选帧定型)。
关键修正(对原 Plan):① **选 frame-1 用 `residual_inpaint_px` 升序,不是 `coverage_pct`**(后者恒 100% 没区分度;前者=非真实 plate 像素,越少=真实 LiDAR 地面越多)。② **地面盲区用 FLUX-Fill(faithfill_mask)即可**,DiT360 非必须(盲区极小:a160 仅 1791px)。③ **frame-1 须满足 contiguity:p+92≤run.end**(a265 越界=只是手法证明,a160=真 deliverable)。④ **FLUX offline:HF_HOME→Drive cache + HF_HUB_OFFLINE=1,无 token 无下载**。⑤ gate=`max(view_morph.max_reg_px)≤8`(非 ECC max|du|)。⑥ 渲染冷读 Drive 数据慢(~6min/帧),A100 比 L4 可靠。
Question: 在 DB-115 选出的干净 band 区间(新场景 8749f79f = a160–292,133 帧)里,怎么找/造一个 frame-1 完美 360 —— ① 车头去掉 ② 地面保真 ③ 天空 outpaint —— 且 general+robust、能扩到全 AV2 随机场景?
核心点子(user):用 LiDAR 衡量"有多少真实地面像素"→ db89 `GROUND_MODE="fill"` 的 `ground_fill.coverage_pct` = nadir cap 真实重投影(LiDAR 支撑)像素占比 → 选 coverage 最高的帧当 frame-1(地面最保真,生成最少)。
Plan: ① 渲干净区候选帧 `fill+FAITH_MASK+HOOD_TO_MASK`(A100 并行)→ coverage_pct 排名 + carhead=0 → 选 frame-1 base(`db116_frame1_cand.py`);② frame-1 盲区(faithfill_mask,含车头)走 `DiT360` 生成(守 **no-NS-inpaint** 红线);③ 天空 `sky_fill_flux`;④ 逐图全分辨率核(无车头?地面真?天空自然?接缝?);⑤ 该场景拿到完美 360 后,固化成"全 AV2 随机场景可跑"的整套 pipeline。
Kill/降级: coverage 普遍低→该场景地面本难,记 graceful degrade;DiT360 出 swirl/车形→调 tau/init 或换 `run_dit360_trimap_clamp.py`;车头去不掉→查 HOOD_TO_MASK 的 Zsupport 门。
Output: `deliverables/db116_frame1/`。driver `agent/db115_drivers/db116_*`。GPU A100+L4/T4。守红线无 NS-inpaint;眼睛胜过指标。[[waymo2pano-ground-fill-physics]] [[waymo2pano-dit360-findings]] [[db115-selection-pipeline]]

---

# DB-115: AV2→Cosmos 1+92 数据集构建 — 两阶段"搜干净窗"筛选机制(几何预筛 + 渲染后质检门 + 定向人眼)
Status: **DONE / superseded by DB-144 v15 full production (2026-07-17)；以下保留历史计划。**
Plan: `agent/plans/2026-06-30-db115-1plus92-dataset.md`(writing-plans 产出:bite-sized;纯几何函数本地单测,渲染/生成用"读回+人眼"验收;GPU 全 gated;git 暂停)。
Question: 不修 fable5 算法的缺陷,而是利用"一个 log 几百帧"的海量,SEARCH 出算法"恰好不出问题"的连续 93 帧 → 1+92 数据集(frame-1=完整 360 全景;后 92 帧=纯净 scene band,天地黑不填)→ 喂下游 Cosmos。能否用"几何预筛 + 渲染后自动门 + 定向人眼"两阶段、高效且保证正确性地做到?
核心范式(user 锐化,字典序目标):**先**找 band 100% 干净的连续 93 窗口(硬过滤,二元)→ **再**在其中挑 frame-1 全景质量最高者(排序)。不强求算法处处完美,只找它已 work 的那一段;某 log 找不到 → 诚实跳过(graceful degradation,北极星通用性)。frame-1 标准=务实"现有有缺陷算法下能得到的最完美 360",非绝对完美。
两个要狙击的具体缺陷(user 4 场景实测指明):
  - 缺陷1 — band 近 ego 车头处 seam 断裂(highway 一辆白车跨前向接缝;4 场景仅此处坏)。检测=① 几何预测(AV2 3D 框:近 egod + 跨前缝 + 前下方 → 高断裂风险,Stage-1 预先避)② 渲染后 db89 `morph_report.max_reg_px`(db89:1048)数字门兜底。
  - 缺陷2 — frame-1 地面车头/车形 artifact(fable5 ground video 前段干净、中段冒车头=帧相关,非永久)。检测=① 几何预测(该帧正前下方 nadir 有无近车 → 有则不选作起点;无前车运动帧最干净)② 渲染后 nadir 跑 YOLO 检不该有的车形 + 人眼核(每 clip 仅 1 张 frame-1,高效)。
  两缺陷都"既可几何预测(便宜避开)、又可渲染后检测(数字/YOLO/眼 兜底)"——这是"完美挑选法"的骨架。
Plan(两阶段):
  - Stage-1 几何预筛(零 GPU,本地新写小模块):每 anchor 算 ① band 断裂风险(近物跨缝惩罚,=记忆 S0-v2 思路但仓库无、需重建)② 运动充分度(复用 video_gen_av2 --diag 的 path-length+反静止,L96)③ 起点可补分(LiDAR 地面真实占比 + 上半球天空空洞 + S 处 band 风险)。→ 每 log 排 Top-K 候选窗口。
  - Stage-2 渲染+门+眼(GPU,复用 db89 / video_gen_av2 / sky_fill_flux / DiT360):92 帧 GROUND_MODE="off"(天地黑);frame-1=GROUND_MODE="fill"+FAITH_MASK → DiT360 地面生成(守红线,不 NS-inpaint)→ sky_fill_flux 天空。收 db89 QA(max_reg_px / coverage_pct / low_coverage_warning)→ 自动门(band 帧 max_reg_px≤τ_seam 起始 8px;窗口=93 帧全过;frame-1=band 干净 ∧ coverage≥τ_cov ∧ 天空无破绽;阈值按第一批渲图用眼校准)→ 人眼核每 clip 的 frame-1 + band 抽样/被 flag 帧。
  - 失败=换窗不修帧(连续性优先);某 log 无通过窗 → 标"无干净 clip"。
frame-1 盲区填法(守 user 第一条红线):生成式 DiT360/FLUX,**绝不 NS-inpaint**;fable5 ground video 仅作"干净帧确实存在"的证据,不回到 inpaint。
Why now: koi 明确要 1+92 喂 Cosmos;放弃地面 outpaint 死磕,转"选择+质检"工程。范围=通用流水线,先 highway(最难)+ 1-2 易场景端到端验证机制,再放量全 AV2 val(每 log 1 clip)。
Kill criteria: 若 Stage-1 几何预测与渲染后实际缺陷(max_reg_px/YOLO)不相关 → 几何预筛失效,退回全渲门(贵);若字典序在多个 log 都找不到一个通过窗 → 该数据集形态对当前算法不可达,回报用户重定向;任何步骤需 per-scene 调参 → 记通用性失败。
Max scope: 先 highway + 1-2 易场景端到端;Stage-1 纯几何本地;Stage-2 只渲少数候选。不改 db89 算法本身(只用现有 flag),不训练。一次一个 ACTIVE。
Required vision check: 每 clip 的 frame-1 全分辨率逐图核(无车头 + 天空/地面/band 三者完美)+ band 抽样 + 所有被 flag 帧;阈值用眼校准(眼睛胜过指标)。
Output: deliverables/db115_1plus92_dataset/(选窗判据 + 渲出 clip + 门日志 + 人眼对照板)。driver 在 scratchpad(非仓库)。
Handoff: 非沙箱 Bash + 脚本顶禁代理(U.install_opener ProxyHandler({}))直连 Colab(L4 渲 band、A100 跑 FLUX/DiT360);url/token 绝不进仓库(读非仓库 ~/.waymo2panorama/runtime/active_url.json);ALWAYS 读回远端结果文件核实(本仓库有伪造工具输出史,VERIFY 别信);结果写 deliverables 非 agent;中文回复;难思考自己做,只派简单 subagent 干杂活;Pristine 预改核心在 scripts/phase3/_baseline_fable5/。

---

# DB-112: "可救 vs 真盲" 判据图 — 范式重审(DB-111)后的第一步最小实验,决定算力投向
Status: **SUPERSEDED by DB-115(2026-06-30,user redirect:不再死磕地面 outpaint 优化,转 1+92 数据集"搜干净窗"筛选)。(原)ACTIVE(主线)— 取代 DB-110 成为当前 ACTIVE;DB-110 分区门卫降级为"本判据图确认中场可救后的候选修法之一";DB-111 范式重审已完成(结论见 progress.md 2026-06-26 条目)。GPU 待用户充钱(L4/CPU 几分钟、零训练)。**
Question: nadir 每个像素到底属于 (A)已观测可几何借的真实 / (B)算法 abstain 但物理可恢复(被 moving/spread 门误杀) / (C)真 geometry-blind + self-occ 零源? 三类面积占比 → 直接决定:中场该"修门"(B大)还是动底座(B小);深中心是否必须"诚实生成"(C 非零=物理无解,停止用门卫幻想救它)。
Why now: 用户担忧"自嗨/一直走局部最优";范式重审确认"中场修门 vs 深中心生成"该分层,但需数据厘清边界,避免盲目烧 A100 在已判负的 world-grid 重建上。最便宜、最高判据价值的一步。
Hypotheses: H1 中场环带大量像素是 B(被 moving-gate 1.3x / spread>30 误杀的可恢复真实);H2 深中心 disc 大量是 C(self-occ 零源,只能生成);H3 A(已观测真实)集中在好场景(bmw)。
Plan: 复用 db89 GROUND_MODE="funnel"(L1671),两处最小改:(a)放开 flat_g 的 t_g<30m 裁剪(L1112)使诊断覆盖 ego 正下方中心 disc → gate0/n_blind 才真覆盖正下方;(b)给"过了 self-occ 两箱"的像素加一道 hood-grazing 真实性判定(复用 two-box + evidenceAA 的 self-occ ON 逻辑),把 gate5(REAL)拆成"真路面"vs"实为 hood 反光"。bmw_a044 / highway_a309 / clean_a046 / crowd 各一次几何前向(L4/CPU 几分钟)。输出三类面积占比 + 彩色判据图。
Kill criteria: n/a(诊断)。产出=判据图 + 三类面积表 + "算力该投修门 vs 生成"的结论。
Max scope: 一次诊断注入(改 funnel 裁剪 + 加一道 self-occ 判定),不动 shipped fill 默认,不跑任何模型。
Required check: 全分辨率逐图核(三类着色是否落对位置:B 在中场环带、C 在正下方 disc)。
Output: deliverables/db112_recoverable_vs_blind/。
Handoff: 非沙箱 Bash + 脚本顶禁代理直连 L4(代理 7890 未运行,真直连通);funnel 是诊断模式、不改 fill 输出;产物写 deliverables(D盘非沙箱前台 Read 可见);url/token 绝不进仓库。

---

# DB-110: 分区 self-occ 门(中场放真路面 + 深中心拒→生成) + nvalid≥2 + 蓝椭圆并入生成 mask
Status: **降级→候选(2026-06-26 DB-111 范式重审后;主线见 DB-112) — (原)user co-decided 2026-06-26(试"分区门卫"代替 self-occ 二元 gate);GPU 待用户充钱后跑一帧验证。**
Question: SELFOCC 二元 gate 是局部最优陷阱(ON 删中场车道线/OFF 放车头+痤疮+白光);"分区门卫"(SELFOCC_DEEP_R=深中心拒 hood、中场放真掠射源)+ nvalid≥2 守卫 + 把 ERP 极点 nadir-floor 蓝椭圆并入生成 mask,能否一帧同时做到 去车头 + 留车道线 + 去痤疮?
Why now: 用户视频实测三病(车头/痤疮黑点/白光)代码根因已查清=lever-1 关 self-occ 放进"掠过源车自身车体、采到 hood 反光而非路面"的坏源(深中心=车头/中场=单源掠射痤疮/grazing=白光);occ ON 又因两箱过严误拒中场掠射真源→删车道线。二元 gate 两头不对→该换"按区域精确判断"。DEEP_R 已验证留车道线+去深 hood,只剩两尾巴。
Hypotheses: H1 SELFOCC_DEEP_R 留中场车道线 + 去深中心 hood 车头;H2 nvalid≥2(给 db89 L1751 的 _gm 加 haveg.sum(0)>=2,目前仅 COHERENT 有此守卫)去痤疮(拒单源掠射脏块);H3 深中心蓝椭圆=db89 L1756 DB-99 nadir-floor plate(非 occ 能管),并入 faithfill_mask 交 FLUX 重画可去。
Plan: 一帧 bmw(白天,痤疮+车头最明显):SELFOCC=True + SELFOCC_DEEP_R=6 + 给 _gm 加 nvalid≥2 守卫(gated 新 flag)+ 把 capg 深中心极点区并入 faithfill_mask → A100 FLUX。全分辨率核图:车头?车道线?痤疮?白光?
Kill criteria: 若 DEEP_R+nvalid 后痤疮/车头仍在、或中场车道线被删 → "分区门卫"这种局部修补不成立 → 转 DB-111 范式重审结论(3D 重建渲染 / 工业 AVM / 生成范式)。
Max scope: 一帧 bmw 诊断;db89 加 nvalid 守卫 flag(gated,默认不变)+ mask 扩展;不动 shipped 默认。一次一个 ACTIVE。
Required check: 全分辨率深中心不提亮逐图核(车头/车道线/痤疮/白光),对比 lever-1 同帧。
Output: deliverables/db110_zoneselfocc/。
Handoff: 非沙箱 Bash + 脚本顶禁代理直连 Colab(代理 7890 未运行);FLUX 用 A100、render 用 L4;产物写 deliverables(D盘非沙箱前台 Read 可见);视频本地 ffmpeg(imageio_ffmpeg)转 h264;url/token 绝不进仓库(读非仓库 active_url*.json)。

---

# DB-111: 战略范式重审 — 跳出"单中心 ERP mosaic + self-occ + 生成补洞"局部最优(via ultracode Workflow)
Status: **DONE(2026-06-26)— 8-agent Workflow 已完成,结论见 progress.md DB-111 条目,产出第一步=DB-112。(原)user 2026-06-26 担忧"自嗨/一直走局部最优",开 ultracode 要从第一性原理 + 工业级 SOTA 探索根本更好的范式。Workflow 调研进行中。**
Question: 多相机透视→360 ERP(尤其 nadir 地面)的当前范式(per-anchor reprojection 单中心 mosaic + self-occ 几何门 + spread 一致性 abstain + plate/inpaint/FLUX/DiT360 生成补洞)是否是局部最优?工业级(环视 AVM/surround-view、自动驾驶 BEV/occupancy)+ 学术 SOTA(街景 3DGS/NeRF 重建渲染、可控街景生成、360 扩散、几何条件补洞)有无根本更好、能跳出"mosaic+self-occ"破局的解法?
Plan: ultracode Workflow 6 路并行(①内部诊断当前范式假设/是否解错问题 ②工业环视拼接 AVM ③BEV/occupancy ④街景 3DGS 重建渲染 ⑤可控街景+360 生成 ⑥几何条件补洞)→ 综合排序 → 对抗审查(约束:AV2 多相机+LiDAR+pose+多帧、要 360 ERP、ego 正下方物理盲区、真实+连贯+通用)。
Kill criteria: n/a(调研);产出=战略选项排序 + "当前路线是否局部最优"的判断。
Output: Workflow 结果 → progress.md(DB-111 条目)+ 据结论决定是否开新方向 brief。
Handoff: 调研在云端(Workflow agent),不占本地 GPU;GPU 验证仍走 DB-110 / 后续 brief。

---

# DB-109: Nadir ground ROOT-CAUSE fix — per-anchor independent rebuild (temporal incoherence) → world-frame BEV ground mosaic
Status: **DONE / superseded by DB-117–129 and DB-144 production (2026-07-17)；以下保留根因与判决史。**
**DIRECTION UPDATE (2026-06-24, user co-decided A after Evidence-A/B/C — see `progress.md`):** the 格子/tiling that replaced the swirl is NOT selection-fixable (Evidence-B: argmin-to-median pick changes pixels by only 2.66/255) and the nadir ground is only **~6–22% faithfully-recoverable per traffic frame** (Evidence-C SPREAD_MAX 8/14/30 sweep → real 6.6/22/92%, but ≤30 = quilt). The 格子 = grazing-stretch + genuine multi-view disagreement (spread 15–30); no single source is clean there. **NEW ARCHITECTURE (chosen): faithful spread-gated base (clean, deterministic-coherent, ~spread≤14) + GENERATIVE fill (DiT/Cosmos = DB-14) for the honest holes.** The "fill the cap to 100% via reprojection" goal is RETIRED (fights physics). Generative fill MUST be temporally coherent (video model) or the hole re-swirls. Evidence: `deliverables/db109_ground_rootcause/evidence{A,B,C}_*`. NEW gated flag `COHERENT_PICK` (default "sweet"=unchanged) + the per-cap winning-source LABEL dump (db89 ~:1567) added this session; shipped default untouched.
**Stage-1 DONE (2026-06-23, eye+code+data, see `progress.md`):** the "physical wall" is DISPROVED — gate0 geometry-blind = 0.0% on all 4 frames (bmw/clean/highway a309/a260). a309's 94% "no-source" = `gate3` MOVING-BOX occlusion (NOT geometry-blind; Finding 5 corrected); bmw's = `gate4` spread>30; a260 = 99% real.
**Stage-1b DONE (2026-06-23):** the moving-box gate is OVER-AGGRESSIVE — a309 real **5.6% → 81.9%** with `MOVING_GATE=False` (new diagnostic flag, default True). The 94.4% splits into 76.3% mis-blocked REAL ground (becomes spread≤30) + 18.1% genuine car (held by the spread gate). Eye-verified: nomove nadir = real asphalt+lanes, no car-hallucination. Root: `gseg_blocked` drops a whole grazing ray that merely passes near a 1.3x car box. ⇒ a309's low real-share = a FIXABLE single-frame gate bug, NOT a wall/world-BEV necessity. CAUTION: don't ship MOVING_GATE=False (leans on spread to catch cars); ship = a PRECISE moving gate.
**Two independent problems now named:** (1) swirl = per-anchor independent rebuild (needs world-BEV); (2) low real-coverage on traffic frames = over-aggressive moving gate (per-frame fix). They STACK. **NEXT (user co-decide):** (A) precise moving gate; (B) world-BEV mosaic.

**Root cause (eye+code verified this session — see `progress.md` 2026-06-23 entry):** the `ground_video_v1` nadir "boils"/swirls because STAGE-4 rebuilds the ground INDEPENDENTLY per output anchor — `run_case` runs per-anchor; the code self-admits `No cross-anchor fusion` (db89:1471) — on each frame's ERP south-pole disc: per-pixel `argmin` source pick (db89:1438) + per-frame global tone gain (db89:1459) + per-frame plate/inpaint (db89:1484). No cross-frame constraint + ERP-pole singularity → radial streaks + temporal jitter. Severity tracks real-share INVERSELY (clean 91% real = stable; bmw/highway fabrication-dominant = worst swirl). **Root = WRONG REPRESENTATION DOMAIN: the ground is a world-static surface but is solved as a per-anchor ERP disc.**

**Two distances (NOT a contradiction — clarified this session):** `disp` 5–58 m (db89:1158, frame↔frame ego displacement; picks WHICH candidate frames) vs `egod` 5–28 m (db89:1356, ground-point↔source-car; picks whether a source is usable per pixel). inner-cap sweet spot egod≈20–28 m (hood/body self-occ floor + far-graze ceiling; 58 = 28 + 30 m reach, db89:1154). The code itself admits egod is "the wrong geometry (point distance, not ray clearance)" (db89:1372) → the 28 m cut may reject usable sources. ⇒ "94% no-source" CONFLATES geometry-blind vs rule-rejected; it must be SPLIT by diagnostic, not asserted as a physical wall.

**Stage-1 — gate-funnel diagnostic (bmw frame A = a044 + a clean city control).** For each blind nadir pixel build a funnel: N0 candidates → N1 in-FOV (ray hits) → N2 not ego-self-occluded → N3 egod∈[5,28] → N4 not moving-box-occluded → N5 spread≤30. Splits "94% no-source" into **geometry-blind (N1=0 → TRUE wall, generation-only)** vs **rule-rejected (N1>0, N5=0 → recoverable)**. Deliver: (a) per-pixel "which gate killed it" colour map; (b) 3–4 representative blind points back-projected onto the candidate source images (eyeball: was it actually seen? how grazing?); (c) the list of selected candidate frames (disp / time).

**Stage-2 = B (ACTIVE 2026-06-23, user co-decided "do B directly, A folds in") — no-network world-frame BEV ground mosaic.** Accumulate the window's frames × 7 cams' ground pixels (LiDAR ground-height reproject + EMC pose + photometric gain) into ONE world-coordinate BEV raster (~5-6 cm/px) + a coverage map (real vs hole). Each anchor SAMPLES the shared map → temporal coherence by construction; holes filled ONCE on the map (inpaint/generative later, not per-frame). Upgrades DB-102's per-anchor local BEV (db89:1244-1345) to a cross-frame accumulated WORLD map. Pure geometry, L4, no network / no A100.
**Concrete B1 plan (reuse the DB-102 bev branch's cell-projection/fusion verbatim, change 3 things):** (1) grid = WORLD-FIXED over an anchor WINDOW bbox (ego path ± ~18 m lateral), built ONCE — not the per-anchor ego-local HALF=12 tile; (2) accumulate ALL frames in the window (not just the current anchor's cand_fis); (3) DROP the moving box-gate (`MOVING_GATE`-off equivalent) + use SPREAD-dominant fusion with `nvalid≥2` guard (= A folded in: spread separates ground vs car-body, traffic disagrees and abstains). Render: each target anchor's cap rays → world ground point → bilinear-sample the shared map → ERP nadir. Ground Z in world from accumulated LiDAR (or the AV2-flat plane as a first cut, memory says ±0.1 m/60 m). New `GROUND_MODE="worldbev"` in db89 (additive; setup from run_case's load_all gives loader+all_ts+cte+cals+gains, enough to loop the window).
**B1 verify (kill criteria):** render a309 + its ±2 neighbours FROM THE SAME world map → (1) the nadir must be visibly MORE temporally coherent than per-anchor fill (slit-scan / montage); if NOT → representation hypothesis wrong, STOP. (2) a309 real coverage should jump above the single-frame 5.6% (other frames have the cars elsewhere). (3) eyeball: real asphalt, no car-body smear. Output: `deliverables/db109_ground_rootcause/worldbev_*`.

**B1 RESULT + REDESIGN (2026-06-24): the world-grid (`GROUND_MODE="worldbev"`) is the WRONG vehicle.** It builds a 74% road map but a309's cap = the ego TRAJECTORY BAND lands on the map's holes; naive all-frame spread-median pollutes it (cars + grazing diversity; onok 3.6-6.2% vs single-frame nomove's 81.9% on the SAME cap; wider window made it WORSE). **User chose B-coherence over B-grid.**
**B-coherence spec (the chosen path):** DROP the grid; KEEP single-frame per-pixel cap reprojection (the 81.9% nomove path: MOVING off / Stage-1c, spread gate) but make the SOURCE SELECTION WORLD-DETERMINISTIC so neighbouring anchors agree → temporal coherence with NO discretisation/accumulation loss. (1) cap pixel → Xg_city (world ground point, LiDAR-Z); (2) candidate frames = a FIXED window (not anchor-relative) so neighbours share them; (3) replace the per-anchor argmin-spread pick with a pick that is a DETERMINISTIC function of the WORLD point (e.g. egod-closest-to-the-~24 m sweet spot among in-[5,28] sources, tie-break time-nearest) → the same world point picks the same source from any anchor. Spread still gates real-vs-abstain; nvalid≥2 guard against single-source car-body. **Verify:** a309/310/311 from the world-deterministic selection → slit-scan coherent (swirl gone) + coverage ~81.9% + eyeball no car-hallucination. Then 5-scene + the full 93-frame video. Implementation = modify STAGE-4's chosen_g selection in db89 (gated new `GROUND_MODE="coherent"` or a flag); per-pixel path already exists (the MOVING_GATE=False run), only the selection determinism + fixed window are new.

Hypotheses: (H1) much of "no-source" is rule-rejected not geometry-blind (esp. egod-28 + spread) → recoverable real ground > current 5.6%. (H2) world-map sampling removes swirl for BOTH real and fabricated regions. (H3) the straight-highway inner-trajectory band is genuinely geometry-blind (N1=0) regardless → honest hole, generation-only — to be QUANTIFIED, not assumed.

Why now: user's #1 complaint is the boiling/swirl ground; this session eye+code root-caused it to per-anchor independence + wrong representation domain; the fix is FAITHFUL, contract-independent, L4-runnable, and GENERAL. North-star: general perspective→ERP + graceful degradation; bmw `02a00399` is only the stress lens, NOT the target — validate on clean + highway too.

Expected evidence: Stage-1 — per-pixel gate-funnel counts on a044 + clean control; the "which gate killed it" map; back-projection overlays; geometry-blind% vs rule-rejected% (the honest decomposition of the "wall"). Stage-2 — a world BEV mosaic + coverage map for one log; a 2–3-frame sampled-nadir sequence showing swirl gone vs current; real-coverage% vs the 5.6% baseline.

Kill criteria: Stage-1 — if N1=0 DOMINATES on BOTH a044 and clean (≈ no rule-rejection) → the wall is genuinely geometric; world-map won't add real coverage → still pursue it for COHERENCE (swirl) but DROP the "recover more real" claim and the generation question (DB-94) returns. Stage-2 — if the world-mosaic sampled nadir is NOT visibly more temporally coherent than the current per-anchor fill → the representation hypothesis is wrong; STOP and re-investigate (do NOT pile on fixes). If mosaic accumulation needs per-scene params → record as a generality failure.

Max scope: Stage-1 = one diagnostic injection (enhanced diag funnel), bmw a044 + one clean anchor, DIAGNOSTIC ONLY (no shipped-pipeline change). Stage-2 = one world-BEV-mosaic prototype on ONE log window, render ≤3 sampled nadir frames; NO retrain, NO network, NO A100, do NOT touch the shipped fill default. One ACTIVE brief at a time.

Required vision check: EVERY image eyeballed. Stage-1: the gate-funnel map + back-projection overlays (is the blind point actually visible in a source?). Stage-2: sampled-nadir temporal sequence (swirl gone?) on bmw AND clean; coverage-map sanity (holes where geometry-blind, real where seen).

Output: `deliverables/db109_ground_rootcause/` (diagnostics + mosaic prototypes). Driver scripts in `tmp/` (non-repo). NEVER commit url/token; read the endpoint from `~/.waymo2panorama/runtime/active_url.json` (non-repo).

**HANDOFF essentials (disciplines):** GPU = Colab L4 via `~/.waymo2panorama/runtime/active_url.json` (NON-repo; PUBLIC GitHub → NEVER write url/token into any committed file/log/board); inject db89's `code = r'''...'''` remote_py by string-replace → base64 → `/exec`, driver in `tmp/` (non-repo); ALWAYS read the remote result file to verify (this repo has a history of fabricated tool output); vision-check EVERY image; results → `deliverables/` not `agent/`; reply in Chinese; do the hard thinking yourself, only simple subagents for clerical work. Pristine pre-edit core in `scripts/phase3/_baseline_fable5/`.

---

# DB-108: Near-field GROUND — restore plausible ground-feel (un-do the DB-99 gray regression) + pick inpaint vs generative
Status: **SUPERSEDED by DB-109 (2026-06-23): user co-decided the root-cause route (per-anchor independence → world BEV mosaic) over the inpaint/generative/gray filler trichotomy; the filler choice is DEFERRED until the root-cause fix + Stage-1 diagnostic land.** (was: ACTIVE / top priority — user audit 2026-06-22 decided the current gray nadir is a REGRESSION.) Full root-cause = `progress.md` 2026-06-22 AUDIT entry.

**Context (for a fresh agent, no prior session memory):** The faithful mosaic + the determinable scene band are DONE (see DB-105 below). The one open problem the user cares about NOW is the **NADIR GROUND**. Current `db89_ghost_recovery.py` default (`GROUND_MODE="fill"`) fills the camera-blind nadir cap with a flat GRAY plate (DB-99). The user wants the ground to "look real / plausible" — downstream **Cosmos will DiT-regenerate appearance, so plausible-not-faithful is explicitly OK** (user's own framing). North-star reminder: the method must stay GENERAL (multi-scene, graceful degradation); `02a00399` BMW is only a stress case, never the target.

**What the audit established (all eye+code+data verified, evidence in `deliverables/db105_nearfield_geometry/`):**
- `ground_video_v1` (the video the user calls "real ground") = db89 `fill` + **NS-inpaint**, NOT a lost better algorithm. Its ground = small real-reproj skeleton + NS-inpaint extending real edges (plausible-looking; highway = smear/白团).
- **DB-99 swapped NS-inpaint → gray plate** (db89 STAGE-4 ~L1482-1484) = the regression. Its only justification (video swirl-flicker) is moot under Cosmos per-frame regen.
- Real-reproj share is **texture-gated**: clean (city) high; a309 (bare asphalt+grazing) only **5.6%** → ~94% inpaint.
- **COMBO = DB-106 boundary (keep car) + plate→NS-inpaint** recovers the ground-feel AND keeps the near car. Verified on a309; runs via injection (`tmp/_combo_a309.py`), NOT yet固化.

Question: which ground filler ships?
- **(A)固化 COMBO** — DB-106 boundary + NS-inpaint. The verified 1-line restore; brings back the video's ground-feel + keeps the car. Fastest. Cost: inpaint smear/白团 on bare-asphalt frames.
- **(B) upgrade inpaint → GENERATIVE** (FLUX-inpaint / DiT into the `{lower-half ∧ comp-black}` mask) — makes the un-real part look CLEAN-plausible (no smear). Best appearance; needs GPU + model choice; see [[waymo2pano-dit360-findings]].
- **(C) first chase the real-skeleton loss** — 5.6% (current) vs 16.9% (baseline_fable5) on the same frame: roll back DB-98 (LiDAR-ground marching) and/or DB-106 (egoproj-drop) ONE AT A TIME to recover REAL ground before any filler. Maximizes truth (real > inpaint > generated = the ownership law).

Why / recommended order: the user's #1 complaint is "all gray, no real ground". Likely **C → A → B**: first recover as much REAL ground as possible (C), then restore the inpaint feel for what's left (A), then optionally upgrade the fabricated part to generative (B). But the user decides — bring it as a co-decision (do not autonomously commit a filler default).
Plan: all edits are in db89 STAGE-4. A = make the `tmp/_combo_a309.py` plate→inpaint swap permanent + gate it. C = isolate DB-98 / DB-106 effect on `coverage_pct` via one-variable rollback runs. B = a new generative pass on the nadir mask (gated on DB-94).
Kill criteria: A — if inpaint白团 is unacceptable as the standalone deliverable → go B. C — if rollback does NOT raise coverage → the loss is physical (grazing/blind-spot), accept A/B.
Gating: the ground deliverable (real-fill vs generative vs middle-only mask) is ALSO gated on **DB-94** (Cosmos contract: does it ingest masks / regenerate appearance). If Cosmos regenerates appearance, even middle-only+mask suffices.
Required vision check: eyeball nadir on clean (city/high-real) + a309 (bare/low-real) + bmw + crowd; verify near car intact (DB-106) and ground-feel present; compare against `gv1_a309_original.png` + `a309_GV1_vs_NOW_vs_COMBO.png`.
Output: `deliverables/db105_nearfield_geometry/` (audit evidence already there).
**HANDOFF essentials (disciplines):** GPU = Colab L4 via `~/.waymo2panorama/runtime/active_url.json` (NON-repo; the repo pushes to PUBLIC GitHub → NEVER write url/token into any committed file/log/board); drivers live in `tmp/` (non-repo) and inject db89's `code = r'''...'''` remote_py by string-replace then base64→`/exec`; ALWAYS read the remote result file back to verify (the repo has a history of fabricated tool output — VERIFY don't trust); vision-check EVERY image (eyes beat metrics); results → `deliverables/` not `agent/`; reply to the user in Chinese (code/paths/metrics in English); do the hard thinking yourself, only dispatch SIMPLE subagents for clerical work (no big multi-agent workflows unless the user asks). Pristine pre-edit core kept in `scripts/phase3/_baseline_fable5/`.

---

# DB-105: Route-2 middle-only current-core video set for Xinhan fallback
Status: **HISTORICAL / superseded by the 1+92 v15 route；not current ACTIVE.**
Question: with the current Fable-5 core plus shipped `SEAM_FLOWMORPH=True`, are the 4 scenes × 93 exact Route-2 frames clean enough in the middle scene band when sky and ground are black (`GROUND_MODE='off'`) to serve Xinhan's fallback setting: first frame can carry full/ground context, frames 2-93 carry only clean middle-band perspective-to-pano content with no sky/ground loss?
Why: existing `deliverables/route2_middle_v1/` already has the correct frame windows and black sky/ground, but most v1 clips were rendered before DB-103/104; only `highway_seamfixed.mp4` was patched after the near-car flow fix. Xinhan's fallback requires the middle band itself to be robust, so this should be rerendered as an isolated current-core v2 rather than relying on pre-fix v1 frames.
Plan: render the same windows as `route2_middle_v1` / `ground_video_v1` into `datasets/route2_middle_v2`: bmw anchors 0-92, crowd 0-92, clean 0-92, highway 225-317. Use `scripts/phase3/db89_ghost_recovery.py` through the route2 wrapper with only `GROUND_MODE='off'`; no sky fill, no ground fill, no generation, no algorithm tuning, no frame-window changes. Assemble 4 mp4s, fetch to `deliverables/route2_middle_v2/`, then vision-check the middle band.
Kill criteria: stop if the live runtime cannot render with current repo/core, if any scene has missing frames after one forward and one reverse resume-safe pass, if a rendered clip has nonblack sky/ground caused by a wrong `GROUND_MODE`, or if vision review finds recurring middle-band object/seam breaks that DB-103 flow does not address.
Max scope: at most 4 scenes × 93 frames, one isolated output dataset (`route2_middle_v2`), assemble/fetch/review only. No BEV/fill ground, no FLUX/DiT/3DGS, no Waymo, no contract-note expansion, no algorithm edits beyond a reproducible v2 driver.
Required vision check: inspect all 4 mp4s and representative crops/frames for middle-band seam cuts, close vehicles, pedestrians/crowd, lane/curb continuity, and verify sky+ground are black. Compare at least highway v2 against `highway_seamfixed.mp4`; compare crowd against the known mild DB-103 case.
Output: Drive `koi_waymo2pano_colab/datasets/route2_middle_v2/`; local `deliverables/route2_middle_v2/`; result recorded in `progress.md` when complete.

---

## Archived (full record in progress.md)

- **DB-80..DB-93 + V2.1/V2.2** — the Fable-5 recentre breakthrough + the v8 complete-panorama stack (recentred depth-aware mosaic + photometric harmonization + ground fill v8 + FLUX.1-Fill sky). Endorsed core = `scripts/phase3/db89_ghost_recovery.py` + `sky_fill_flux.py`. Deliverable `deliverables/complete_pano_v8/`.
- **DB-97** (ground-fill temporal videos) — DONE: 4 scenes × 93 frames → `deliverables/ground_video_v1/`. The first temporal stress test; it EXPOSED the ground + seam problems below.
- **DB-98** (nadir speckle / black streaks / softness) — root-caused to the near-pole-behind PHYSICAL BLIND SPOT (<4° grazing, ego self-occluded; steeper-view backfire + no-gate-streak-return both confirm). SUPERSEDED by the DB-102 BEV reframe.
- **DB-99 / DB-99a** (nadir 白团 truth-ring plate / whole-log BEV fusion) — the plate idea + the shelved heavy BEV; SUPERSEDED/realized cleanly by DB-102.
- **DB-101** (visibility-consistent ground / middle-only mask) — middle-only (`GROUND_MODE='off'`, BLACK top+bottom) is CLEAN+defect-free across 4 scenes; the 3-way fill/bev/mask choice is folded into DB-102 and gated on the DB-94 Cosmos contract. SUPERSEDED by DB-102.
- **DB-102** (METRIC / BEV ground reconstruction) — DONE/validated. First-principles: the ground defects (speckle/smear/白团/lavender) are per-pixel-ERP-pole-domain artifacts; reconstruct the ground in the METRIC (BEV) domain (depth-reproject to the virtual centre) → defect-free by construction, single-frame + temporally, ZERO params. Near-nadir 0-3 m = sensor-agnostic blind spot (cameras AND roof-LiDAR both can't see under the car) → honest soft + mask → downstream Cosmos. Code `GROUND_MODE='bev'` (additive, gated); NOT flipped to default (ground deliverable = a Cosmos-contract policy, see DB-94). Evidence: `agent/_db102/crops/AB_*`.
- **DB-103** (near-ego scene-band SEAM shear) — SHIPPED (commit aa18629, `SEAM_FLOWMORPH` default ON). The STAGE-3.5 view-morph's ECC-AFFINE registration shears a close object straddling a seam (a309 car `max_reg_px=32`). Fix = dense Farneback optical flow inside the object body, gated on `max_reg_px>8` (surgical: clean seams byte-identical). Validated 4 ways (severe a309 32→8.6 shear-gone, mild crowd, clean unchanged, 6-frame temporal). In the scene band → improves fill/middle-only/bev alike. Deliverable `deliverables/route2_middle_v1/highway_seamfixed.mp4`.
- **DB-104** (ROBUST stitching) — the close-car residual is EXHAUSTIVELY isolated to the PERSPECTIVE physical limit: the two seam cameras see the car's FRONT vs REAR (raw frames), so the overlap has huge perspective disparity; object-box-depth (32→32), YOLO→SAM, and mask-hole-fill (8.64→8.64) are ALL NEG — only the dense flow helps (32→8.6 = the 2-D physical floor). Robustness = the affine→flow ESCALATION (shipped). `SEAM_MASK_FILL` added as a gated general tool (default OFF, fill-holes not dilation → no v7 giant-instance risk). **DEFERRED open sub-item: Tier-2 graceful single-source degrade** (route the seam to the object edge / single-source the more-complete camera) for the un-registrable case — designed (see git), not built (no failing case yet to validate against).

---

# DB-105: Near-field unified solve — step 1: can dense-LiDAR geometric reproject beat "fall back to plane"?
Status: STEP-1+2 DONE (L4 a309, decisive, eyes-verified) — the near-car seam is NOT disocclusion and needs NO generation: LiDAR densely covers the car (1742 pts) and ONE camera (side_left 1610 vs front_left 149) sees it whole; the view-morph's FUSION of the two IS the seam. Faithful fix = LiDAR-coverage-gated SINGLE-SOURCE (= DB-104's deferred Tier-2, now objectively triggered). STEP-3 (implement in core) proposed — needs go-ahead + 5-scene regression. Facts: progress.md 2026-06-21; evidence: `deliverables/db105_nearfield_geometry/`. First-principles framing below.

**First-principles framing (the unification).** "The closer to the AV2 cameras, the worse" = small Z simultaneously inflates FIVE physical quantities: (1) parallax `d_px≈(W/2π)arctan(b/Z)`, (2) occlusion severity (adjacent cameras see DIFFERENT surfaces of a near object — front vs rear), (3) grazing angle (near-ground imaged only at low grazing → stretched source slivers), (4) ERP-pole coordinate stretch, (5) sparse-depth error amplified by b_perp. Split: {1,4,5} are REPRESENTATION problems (already half-cured by DB-102 BEV metric domain); {2,3} are PHYSICAL-OBSERVATION problems (occlusion boundary + grazing/blind-spot) that NO representation can fix. After stripping representation, the residual near-field defect on BOTH routes is pure observation insufficiency: stitching=occlusion boundary (8.6 px floor), ground=grazing low-res + 0–3 m true blind spot. For observation insufficiency there are EXACTLY three responses: (A) TIME (whole-log: a surface unseen now was seen another moment — already used), (B) GEOMETRY (reconstruct 3D, reproject to virtual centre, z-buffer visibility, leave disocclusion holes), (C) PRIOR/GENERATION (fill holes with a learned prior — plausible, not truth). A fails on the true blind spot; B fails on disocclusion holes; C is the ONLY filler of true-blind/disocclusion but is plausible-not-faithful. Literature confirms the modern large-parallax answer is B+C: PIS3R (2508.04236) = VGGT 3D-reconstruct → reproject → point-conditioned diffusion fills holes; MagicRoad (2507.23340) = surfel road + segmentation-guided video inpaint. We are ASSET-COMPLEMENTARY to PIS3R: we HAVE LiDAR (metric, no VGGT needed), calibration, ego-poses, time — it has none. The unified near-field solve = A(whole-log) + B(LiDAR/learned geometry reproject to virtual centre + z-buffer) + C(geometry-conditioned generation for holes, object-veto + temporal-consistent). ONE framework for both routes; it unifies "faithful geometry" and "plausible generation" (geometry owns the observed, generation only the holes, conditioned so it cannot fabricate conflicting salient structure).

Question: on the a309 highway near-car seam (residual 8.6 px after flow, 32 px affine) AND one near-ground fill-speckle patch, does DENSE per-point LiDAR depth + z-buffer visibility, reprojected from the virtual centre, beat the shipped `depth_field` (which Rule-8-degrades to a plane in hard regions) and beat DB-104's box-single-depth (which was NEG 32→32)?
Hypothesis: DB-104 only tested ONE box depth (NEG). Dense per-point LiDAR + z-buffer (the mature surround-view / PIS3R visibility op) may recover the correct visible surface at the occlusion boundary and the curb/near-ground, dropping residual. If it does NOT, the residual is confirmed pure DISOCCLUSION (no source pixel exists) → only C (generation) can fill it → justifies pivoting to the contract+generation experiment.
Why now: user named both near-field routes; PIS3R/OmniStitch say "3D-reconstruct-then-align" is the modern large-parallax answer; this is the faithful, L4-runnable, contract-independent first step that ISOLATES "how much geometry can recover vs how much MUST be generated" (honours isolate-the-variable).
Expected evidence: a309 seam residual px (vs 8.6 flow / 32 affine); near-ground reproject incoherence (vs fill speckle); z-buffer visibility map; disocclusion-hole area (= the mass that still MUST be generated).
Kill criteria: if dense-LiDAR+z-buffer is NOT better than 8.6 px at the seam AND not cleaner than fill at the ground → geometry (B) is spent for near-field → residual is pure disocclusion/blind-spot → STOP B, pivot to C (generation) + the DB-94 contract experiment (two first-frames into Cosmos). Max 2 scenes, DIAGNOSTIC ONLY (no full-pano render).
Max scope: one diagnostic script (dense LiDAR densify + z-buffer visibility + reproject) on a309 + one near-ground patch, L4. Do NOT modify the shipped pipeline; no retrain.
Required vision check: eyeball the reprojected seam (is the car nose un-sheared?), the reprojected near-ground (cleaner than fill?), and the disocclusion-hole map.
Output: `deliverables/db105_nearfield_geometry/`.

---

# DB-94: Xinhan centre-contract confirmation
Status: queued - needs a meeting/message with Xinhan. **This now GATES the ground deliverable choice (fill vs bev vs middle-only mask).**
Question: confirm the downstream Cosmos consumer uses point-cloud first frames whose centre = our ring-camera centroid at camera height (the DB-80 virtual centre), so panorama and point cloud are concentric; and whether it wants a soft-confidence / binary masked hole for the unseen nadir.
Why: if the consumer assumes ego-origin instead, every panorama is ~0.5-1.5 m off-centre relative to the point cloud. If it honors masks, the near-nadir blind spot DISSOLVES (ship bev+mask, let Cosmos outpaint).
Plan: prepare a one-page contract note (centre definition, ERP convention, resolution, axes, mask semantics) from the existing deliverables; review with Xinhan.
Kill criteria: n/a (coordination task).

---

# DB-95: Waymo dataset migration - the next generality gate
Status: queued - the big one (north-star generality).
Question: does the full stack (evidence calculus + ECC-OMC + view-morph + content seam + depth gating + BEV ground) run on Waymo Open Dataset with ONLY loader-level changes (camera count/layout, shutter timing, annotation format)?
Why: the north star is a GENERAL perspective-to-ERP method; AV2 5-scene + no-LiDAR are passed; a second dataset with different ring geometry (5 cameras, different stagger) is the real test that nothing is AV2-specific.
Plan: write a Waymo loader exposing the same frame interface (images, K, T_ego_cam, per-camera timestamps, ego poses, LiDAR, tracks); run the unchanged pipeline on 2-3 Waymo segments; vision-check.
Kill criteria: any fix that requires touching the ALGORITHM (not the loader) must be evidence-principled and scene-agnostic, else record as a dataset-specific limitation.
Required vision check: moving vehicles single-and-intact; seams clean; graceful degradation where evidence is missing.

---

# DB-96: Contact-shadow evidence modelling (icebox)
Status: icebox - known principled gap, low priority.
Question: can the cast shadow be treated as evidence-bound object appendage (dark region adjacent to the object mask, luminance-ratio detected) and moved/kept with the body during compositing?
Why: a remaining visible artefact class on the BMW scene (fill bands show unshadowed background); currently mitigated by harmonic fill.
Plan: only if the downstream consumer flags it; otherwise leave to the generative layer.

---

# DB-120: 量化选帧 score + 1+92 工程化(夜航提案,待 user 审)
Status: **QUEUED while DB-121 ACTIVE (2026-07-09)。band-off 提速与量化选帧范围原样保留；S1 曲线族像素拟合子题由 DB-121 的 HD-map 拓扑 kill-test 取代。** **原提案(2026-07-09 夜航启动,待 user 审)。BMW 升代五件套在跑:L4 = `02a00399` 2.5cm extract(tag bmw118x / a014)+ A100 = gen2 FLUX 填 bmw118w T 洞。承 DB-116/115 的 1+92 orchestrator + DB-118 逆问题地面。** **2026-07-10 夜航实测**:默认 188s/帧×2 复测一致(92帧≈4.8h);EMC 开关无效;band-off 重建=第一优先;三场景(hw/BMW/8749)泛化首验完成,弧线=共性缺陷→S1 曲线族推广立项。**
Question: 交付帧如何量化选择以"避弱"(近距遮挡车 anchor 自动降权),1+92 全链如何压到 <1h/数据集?
提案要点(score 量化选帧): score = w1·`nadir_imperfect_px`(已有,resid∪fg_occ)+ w2·`plate_px`(fg_occ 语义板面积,近车代理)+ w3·(1−`cov2_frac`)(地面数据覆盖)+ w4·`band_clean`(动目标占比);权重用 hw / BMW 两场景标定;阈值分级 GOOD(全组件不触发)/ DEGRADED(触发生成组件)/ REJECT。
提速抓手(1+92 工程化): band-off 渲染已验证 32s/帧(346→32s,byte-identical);extract 一次/log 摊薄 93 帧;rep 8 并发串;FLUX 步集中 batch。目标预算:extract ~40min + 92 帧渲染 ~50min → 并行后 <1h/数据集。
Max scope: 先 hw / BMW 两场景标定权重 + 阈值;不改 db89 算法本身(只用现有 flag);量化门先落地为选帧排序器,不自动 REJECT(人眼复核)。
Required vision check: score 排名 Top-K 帧全分辨率逐张眼核(近车 anchor 是否被正确降权);分级阈值用眼校准(眼睛胜过指标)。
Output: `deliverables/db116_frame1/` + `deliverables/db118_surfel/`(承 DB-116/118)。
Handoff: 承 DB-118/117/116 纪律(非沙箱 Bash 直连 Colab、读回远端结果验证、结果写 deliverables、中文回复、dr_run launch 前必删旧 DONE marker、FLUX/GSR pipeline VM RAM cache 可秒级复用)。[[db116-frame1-perfect360]] [[db115-selection-pipeline]] [[db118-inverse-ground]]
