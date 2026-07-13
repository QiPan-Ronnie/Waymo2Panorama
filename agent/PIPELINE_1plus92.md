# 1+92 Panorama Pipeline (DB-116) — 权威说明 · 可复现

> 最后更新 2026-07-01。任何时候要查"我们怎么从一个 AV2 log 造出 1+92 数据"都看这份。
> 相关记忆:[[db116-frame1-perfect360]] [[db115-selection-pipeline]] [[waymo2pano-ground-fill-physics]] [[av2-cosmos-pipeline-db114]]

---

## 0. 目标(第一性)

任意 Argoverse-2 log(多相机透视)→ **93 连续帧 ERP "1+92"**:
- **frame-1**: 完美全 360 —— ① scene band 无缝 ② 地面真实完整 ③ 天空 outpaint ④ 无 ego 车头 ⑤ 残留盲区生成补全
- **frame 2..93 (92 band)**: 只有 scene band,天空+地面**黑**(留给下游 Cosmos 视频模型填)
- **通用**: 一套流程跑任意 log,零 per-scene 调参
- **鲁棒**: 不挑场景(繁忙/空旷/textureless 都行)

分辨率 H×W = 1024×2048。

---

## 1. 完整流程(6 阶段)

```
阶段1 BAND-GATE   每帧渲 scene band(db89 GROUND_MODE="off"),判据 max(view_morph.max_reg_px)≤8 → 干净否
阶段2 CLEAN-RUN   最长连续干净段,要求 ≥93;合法 frame-1 位置 = [run.start, run.end-92]
阶段3 选 FRAME-1  合法位置渲 fill+FAITH+车头修复,按 nadir_imperfect_px 升序选"真实数据最完整"的
阶段4 地面重建    db89 STAGE-4:真实反投影 + capg|=egoproj 时序补车头 + GROUND_RESID=inpaint 死角(=fable5 经典)
阶段5 FLUX 生成   ① db116_sky 天空 outpaint → v7  ② db116_ground FLUX 补 faithfill 盲区 → v8(=完美 frame-1)
阶段6 打包        frame-1(v8) + 92 band → 93 帧 PNG 序列 + H.264
```

**词典目标(lexicographic):** ① 硬门 92-band 必须 100% 干净(阶段1-2);② 过门的窗口里选 frame-1 真实数据最完整(阶段3);③ 残留盲区生成补全(阶段5)。

---

## 2. 每阶段详解(文件 / 命令 / 参数)

### 阶段1-2 band-gate + clean-run
- **驱动**: `agent/db115_drivers/db116_pipeline.py`(stage1_band_gate + _longest_clean_run),或 `db115_fullparallel.py`。
- **渲染**: `video_gen_av2.batch_py(uuid,tag,anchors)` → db89 remote,字符串替换 `GROUND_MODE="fill"→"off"`(band-only,黑 nadir)。
- **判据**: 每帧 `max(case.view_morph[*].max_reg_px) ≤ 8.0`。**注意:原始 ECC max|du| 不可做门(过严);几何预测器(band_break/single_cam)被证否**——flowmorph 修正后残差是唯一可靠信号。
- **并行**: 5-GPU fleet(active_url{,_2,_3,_4}=L4/T4 + _5=A100),19-worker 把单场景 ~2h → ~30min。
- **产出**: `{scene}_band` dir 里每帧 segcomposite(band+黑 nadir) + manifest。

### 阶段3 选 frame-1(GENERAL imperfect 度量)★
- **驱动**: `agent/db115_drivers/db116_cand_any.py`(交付实际用) / `db116_pipeline.py` stage3_pick_frame1(orchestrator)。
- **命令**: `python db116_cand_any.py <uuid> <frames逗号分隔> <dir_suffix>`
  - frames = 合法 frame-1 位置采样(如 clean run [100,148] 每 4:`100,104,...,148`)。
- **渲染注入**(db116_cand_any WORKER,对 batch_py 返回的 db89 字符串 py.replace):
  1. `FAITH_MASK=True`(导出 faithfill_mask=残留盲区)
  2. `capg |= egoproj.reshape(H,W)`(车头走时序真实重投影,非灰块)
  3. `GROUND_RESID="plate"→"inpaint"`(死角 NS-inpaint 延伸真实纹理)
  4. `"residual_inpaint_px":...` → 追加 `"fg_occ_px"` + `"nadir_imperfect_px"`(=`(resid_m|fg_occ).sum()`)
- **选帧标准**: `nadir_imperfect_px` **升序**取 argmin = 真实数据最完整。
  - **为什么不是老的 residual_inpaint_px**: db89:1767 `resid_m &= ~fg_occ` 把前景遮挡黑斑**剔除**了 → residual 对 fg_occ 结构性失明。nadir_imperfect_px = resid_m(plate/abstain/死角) ∪ fg_occ(遮挡) = 全部"非完美真实数据",才 general。
  - **验证**: 3 场景 imperfect 随帧 4~59 倍变化,自动适应场景主导缺陷(繁忙=fg_occ 主导 / 都市=resid 极小 / textureless=resid 主导),零调参各选最优。

### 阶段4 地面重建(db89 STAGE-4 = fable5 经典)
- **文件**: `scripts/phase3/db89_ghost_recovery.py`(工作版) · pristine 在 `scripts/phase3/_baseline_fable5/`。
- **remote_py()** 返回内嵌 db89 源码字符串(line 47 `code = r'''...'''`),batch_py 送 Colab exec。改本地此段即改远端行为。
- **nadir 每像素三类处理**:
  | 区域 | 填法 | 真实? |
  |---|---|---|
  | evidence 充分 | 多相机/时序真实反投影 | ✅ 真实 |
  | resid_m(abstain,无 LiDAR 证据) | `cv2.INPAINT_NS` 延伸纹理(GROUND_RESID=inpaint) | ❌ 经典填充 |
  | fg_occ(前景遮挡) | `plate_rgb×0.55` 暗 shadow | ❌ 诚实暗斑 |
- **车头**: `capg|=egoproj` 让 ego 车身投影走时序真实重投影(前几帧看到当前被车头挡的地面)。**弃用的退化 hack**:椭圆/矩形 plate 灰块。

### 阶段5 FLUX 生成(天空 + 补盲区)★F 环
- **FLUX offline(别再找 token)**: FLUX.1-Fill-dev(40G) 在 Drive `cache/huggingface/hub`。指 `HF_HOME=<Drive>/cache/huggingface` + `HF_HUB_OFFLINE=1`(+`TRANSFORMERS_OFFLINE=1`)即离线加载,不下载不用 token。跑 A100(active_url_5)。
- **天空**: `python db116_sky.py <SC> <cand_dir>` → `frame1_complete/{SC}_complete_v7.png`。
  - SC = 选中 tag(如 `c1_a165`);cand_dir = 阶段3 的 dir(如 `imp_hw`)。auto-prompt 按 sky_mean 颜色。guidance 30 steps 40 seed 0。
- **补盲区(F 环)**: `python db116_ground.py <SC> <cand_dir>` → `{SC}_complete_v8.png` = **最终完美 frame-1**。
  - FLUX-Fill 只填 `faithfill_mask` 白区(dilate 7px blend),prompt="smooth grey asphalt road surface, photorealistic, seamless ground continuation, soft natural shadow",guidance 30 steps 40 seed 0,**mask 外 byte-exact 还原**。
  - **★F 环打通的关键(2026-07-01)**: 生成填地面前两次崩(DiT360 seam=melt / 早期 FLUX v8=糊),根因是**盲区太大**(含车头正下方 egoproj 死角=拉伸最严重)。**general imperfect 选帧先把盲区压到最小 + 分散 + 只填局部 mask** → FLUX 有足够真实周边参考,填得合理不 melt。a144 大盲区(右上黑三角)被填成合理路面,a165 小盲区软融合。**选帧(压小盲区)+FLUX = 打通地面生成这条腿。**

### 阶段6 打包
- **驱动**: `db116_package.py` / `db116_hw_package.py <p> <tag>`。band 帧从阶段1 的 `{scene}_band` dir 按 frame→machine 映射取。
- **本地重拼**(隧道断/离线时): copy PNG 成规则编号 `fr_%04d.png` → `imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe` subprocess image2 拼(**imageio 直接调会联网崩,必须用 exe subprocess**)。
- **坑**: band 帧号零填充 3 位(`a015` 非 `a15`),跨机取 band 用 `a%03d`。
- **产物**: `deliverables/db115_1plus92_dataset/clip_{scene}_1plus92.{mp4,/}`,各 93 帧。

---

## 3. 基建

- **框架**: agent-colab-direct。`ColabClient`(from `db64_ltr_v0_phase4b_z_visibility_cause`),读 `~/.waymo2panorama/runtime/active_url_5.json`(A100,**url/token 绝不进仓库**)。`.post("/exec",{cmd:["bash","-lc",bash]})` + `poll_job` + `.read_file`。必须 `U.install_opener(U.build_opener(U.ProxyHandler({})))`。
- **A100 隧道不稳**(cloudflare trycloudflare,uptime 常 ~90s-3min,HTTP 530=隧道断):失效等 user 给新 endpoint,写入 active_url_5.json。渲染冷读 Drive ~6min/帧首批,A100 最可靠。
- **本地 python**: `D:\miniconda3\python.exe`(numpy/PIL/cv2 齐)。中文 print 会 cp1252 崩 → 本地脚本用 ASCII/英文 print 或设 `PYTHONIOENCODING=utf-8`。

---

## 4. 三场景实证(零 per-scene 调参)

| 场景 | 类型 | clean run | 合法 frame-1 | 选中(imperfect 最小) | imperfect | 主导缺陷 |
|---|---|---|---|---|---|---|
| 8749f79f-a30b-3c3f-8a44-dbfa682bbef1 | 都市/晴 | a160-292 | a160-200 | **a165** | 2480 (99.8%) | resid 极小 |
| 2c652f9e-8db8-3572-aa49-fae1344a875b | 交叉口/暮 | a100-240 | a100-148 | **a144** | 26980 (97.4%) | **fg_occ** 遮挡 |
| 02a00399-3857-444e-8db3-a8f58489c394 | BMW textureless(最难) | a0-108 | a0-16 | **a14** | 12823 (98.8%) | resid 无证据 |

完美 frame-1 产物:`deliverables/db116_frame1/PERFECT_8749_a165.png`(近完美) · `PERFECT_a145_a144.png`(难场景,FLUX 填掉右上黑三角)。诊断图 `RESIDUAL_CHECK.png` · F 环对比 `FRING_a144_v7_vs_v8.png`。

---

## 5. 关键决策与教训(为什么这样做)

1. **band 门 = flowmorph 残差 max_reg_px≤8**,几何预测器全否。
2. **选帧 = nadir_imperfect_px 升序**(非老 residual)——residual 对 fg_occ 结构性失明(db89:1767 剔除)。imperfect 自动适应场景。
3. **车头 = capg|=egoproj 时序真实重投影**,非椭圆/plate 灰块 hack(全弃用)。溯源 ground_video_v1(`_baseline_fable5/db89` STAGE-4 `blackg=(comp<12)|egoproj`)。
4. **地面 = 100% fable5 经典**(真实反投影 + NS-inpaint + 暗 plate),**DiT 从未成功介入地面主线**;天空才用 FLUX/DiT360。地面生成试过两次崩(DiT360 seam melt / 早期 FLUX v8 糊)。
5. **F 环打通(2026-07-01)= 选帧压小盲区 + FLUX 填局部**。前两次崩=盲区太大。这是"地面处处完美"的最后一公里。
6. **FLUX offline Drive cache**,不用 token。
7. **本地重拼用 ffmpeg exe subprocess**(imageio 联网崩)。

---

## 6. 已知局限(诚实)

- **产出率受内容限制**: 不是任意 log 都有 ≥93 连续干净 band(断裂密集的 log 产不出)。给卡并行只能快速**筛**合格 log,不能让烂 log 变合格。合格率需实测(三场景都合格,非 100% 保证)。
- **地面色彩一致性未做**: BMW nadir 左半紫(多相机白平衡不一致),imperfect 不抓色彩(正交维度)。根治=world-BEV 统一来源 mosaic 或 per-source 色彩对齐。
- **端到端未全自动**: db116_pipeline.py 是骨架,实际交付手动拼 cand_any+sky+ground+package。要批量一键需补 orchestrator 串通。
- **FLUX 补的是"生成的合理内容"非真实**(persistent 遮挡区真实数据不存在);对喂 Cosmos 的条件帧足够。

---

## 7. ⚠️ 已知 bug / 踩雷清单(改 batch/pipeline 前必读,别再踩)

> 后续 agent:动 `db116_batch.py` / 全量前,先读这节。分级=🔴阻塞 / ✅已修 / 🟠🟡待修 / 📌纪律。(2026-07-01 batch 过夜跑 + 手动收尾 `2c652f9e` 暴露)

### 🔴 全量前必修(阻塞 —— 150 log 每个都会踩)
- **Drive 并发写同名目录分身**:band-gate 4 机同时写 `batch_band/<u8>/`,Google Drive FUSE 不合并→建 `<u8> (1) (2) (3)` 副本,band 帧(worker `subs[i::total_k]` interleaved 分片)散落 4 目录,`run_package` 只 glob 主目录→**缺帧**(实测主目录只剩 60/319→package 93帧缺72)。**修**:band-gate 每机写独立子目录 `batch_band/<u8>/m{i}/`,或 package 前 glob 所有 `<u8>*` 分身合并。今天靠事后 `cp 分身/*_segcomposite.png 主目录/` 临时救。
- **localize 单机失败即全 batch 停**:`localize()` for-loop 遍历 fleet,任一台 530/502 即 `raise`→整条队列后续 log 全 `EXCEPTION`(A100 一断,2 L4+T4 空转)。**修**:每机 try/except 单机跳过,fleet ≥1 台活就继续。

### ✅ 已修
- **`run_driver` cp1252 崩**(`db116_batch.py:98`):`subprocess(..., text=True)` 缺 encoding→Windows 父进程默认 cp1252 解子进程 FLUX tqdm 非ASCII 字节(0x8d)→`_readerthread` `UnicodeDecodeError` 崩→`p.stdout=None`→下游 `TypeError: None+str`(sky 步空转 retry)。已加 `encoding="utf-8", errors="replace"` + `(p.stdout or "")` 兜底。**教训:所有读远端输出的本地 subprocess 都要显式 utf-8**(qclips/PPT 都撞过同一坑)。

### 🟠🟡 待修(逻辑洞 / 质量)
- 🟠 **sky 失败被 ground 掩盖**:`process()` 跑完 `run_driver("db116_sky.py")` **不检查返回**就跑 ground→ground `cv2.cvtColor(cv2.imread(v7)=None)` 报 `OpenCV color.cpp:199 (-215)`,假装是 ground 崩、真因是 sky 没产 `complete_v7.png`(A100 断线那刻 sky 的 poll 被打断)。**修**:sky 后检查 v7 存在/成功标志,失败报 `ERR_sky` 而非顺跑 ground。
- 🟡 **天空 FLUX 树冠色斑**:有树/楼贴天际线的场景,sky outpaint 把树冠/楼顶颜色向上延伸成天空里一块突兀色团(`2c652f9e` 右侧 REMAX 楼上方暖褐斑;v7 已有=sky 步引入,非 ground)。**修**:sky mask 严格限纯天空区 / prompt / 构图约束。

### 📌 历史纪律坑(别重蹈)
- **band 帧号必 3 位零填充 `a%03d`**(`a015` 非 `a15`);跨机取 band 前两场景 band≥100 侥幸没暴露。
- **db89 字符串 `replace` 注入会静默失效**(HOOD_TO_MASK 史):`video_gen_av2.batch_py` 的 remote_py 版本可能没目标行→`replace(...)` 不生效也不报错→车头曾从未处理。改任何 db89 注入前**本地 batch_py 代理验证所有 replace 全命中 + remote compile OK**,别信"replace 成功"。
- **渲染冷读 Drive 32s/帧**(CPU load 卡在 6-7)→必须 `localize()` s5cmd S3→本地 SSD `/content/localav2` + db89 `DATA_ROOT` 替换;SSD ~78G,几 log 后清 localav2。
- **resume skip 假象**:已渲 log band-gate 秒跳过(54s"渲"319帧=skip);验证"4 机拉满"要跑没渲过的新 log 或先清 `batch_band/<u8>`。
- **候选 >10 单机选帧撞 poll timeout**(0bae3b5e no_pick 教训):stage3 候选采样限 ≤10(`valid[::max(1,len//10)][:10]`)+ 用多机 fleet(非单机 A100)。
- **endpoint 隧道频繁 530(过夜死穴)**:Colab tunnel 会掉,断了 URL/token 会变→要 user 重贴、更新 `active_url_5.json`;orchestrator 有 3 次重试但彻底死透会卡等(=localize 容错的必要性)。
- **url/token 绝不进仓库**:只更新本地 `~/.waymo2panorama/runtime/active_url_5..8.json`,绝不写进任何提交文件/日志/文档。
- **地面别乱跑 `db116_ground.py` FLUX**:只在盲区**小**(imperfect 选帧已压过)时填 faithfill 局部(F 环,有效);盲区大会糊/覆盖真实 NS-inpaint(方案 A 教训)。今天 `2c652f9e` 盲区小=有效。

---

## 8. 复现一个场景(命令序列)

```bash
# 前提: active_url_5.json 有活的 A100;FLUX 在 Drive cache;db89 已含 fg_occ_px 输出
# 0) band-gate 得 clean run(或查已知表) -> 合法 frame-1 位置
# 1) 选帧(渲合法位置,按 imperfect 选)
python db116_cand_any.py <uuid> <frames> imp_<scene>      # -> BEST_FRAME1 aNNN tag=cX_aNNN
# 2) 天空
python db116_sky.py     cX_aNNN imp_<scene>               # -> {tag}_complete_v7.png
# 3) 补盲区(F 环) = 完美 frame-1
python db116_ground.py  cX_aNNN imp_<scene>               # -> {tag}_complete_v8.png
# 4) 92 band(阶段1 已渲) + v8 -> 93 帧打包
python db116_package.py ...        # 或本地 ffmpeg exe 重拼
```

---

## 9. DB-131~134:v14.1 量产栈(map 分片 + 投机建图)

> 追加 2026-07-13。第 2-8 节讲"怎么造一个 log";这一节讲"怎么把它变成能双机批量跑 100 个 log 的量产栈"。
> 相关驱动全在 `agent/db115_drivers/`(db131_setup / db132_production / db133_extreme / db134_production / db134_fluxpack)。git 存档 commit `de5bcd3`(v14.1 ARCHIVE)。

### 9.0 主线瓶颈(为什么要做这一节)

DB-130 把单 log 端到端压到 12-18min 后,**唯一独占时间轴是 world-BEV map 构建**(单机 990s→参数刀 567s,仍是串行长杆)。全部提速空间集中在这条杆上:先把 map 变**可分片并行**(DB-131 内核),再把它**藏进本来就要等的 probe 时间轴**(DB-134 投机)。词典目标不变(§1),全程零 per-scene 调参、真实优先。

### 9.1 DB-131:内核 v11 三开关 + 分片归并 ★

- **内核**:`scripts/phase3/db89_ghost_recovery.py`,**md5 `cca4f0c586a2d91abe14d4b121824cf4`**。备份 `scripts/phase3/_backup_db115pro/db89_ghost_recovery_20260713_v10_pre_mapshard.py`。
- **新增三开关(蓝图落地纪律:默认全关时与 v9 byte-identical,零影响)**:
  | 开关 | 值 | 作用 |
  |---|---|---|
  | `WORLDBEV_SHARD` | `"i,k"` | 本 worker 只处理 `_wfis[i::k]` 帧(map 构建循环分片) |
  | `WORLDBEV_DUMP` | `path` | 构建后把 `chosen/score/col` 三数组 `np.savez_compressed` 落盘(不做后处理) |
  | `WORLDBEV_LOAD` | `path` | **跳过**两个构建循环,直接 `np.load` 归并结果 → 走原生 gain/median/tier/Telea 后处理 |
- **`WORLDBEV_LOAD` 的关键设计**:merge 只重建"选源+采样"的 slot 状态,**调好的后处理管线原样复用**——不重写内核第二实现(与历史 DiffuEraser/Wan/sampler 旁路的"劣化第二实现"判负史一致)。
- **分片归并器 `db131_merge.py`**(由 `db131_setup.py` 生成并推 `/content/db131_merge.py`):把 K 个 shard 的 6 槽候选沿 axis-0 拼接 → 全局 `np.argsort` 取 top-6 → `np.take_along_axis` 取回 `chosen/score/col` → `merged.npz`。**数学上与单机全量构建等价**。
- **A/B 实证**(`0b86f508`,MID=94,N=316,R=40.9,fused):

  | 路径 | 耗时 | 像素 diff |
  |---|---|---|
  | 单机全量构建 | 471s | — |
  | 8-shard 并行 139s + merge 16s + final 40s | **195s(2.4×)** | med=0.00,identical=**100.00%** |

### 9.2 DB-132:v12 四刀合体量产 driver

`agent/db115_drivers/db132_production.py` 把四个正交刀合成一条队列级 driver:

1. **两段式 band**:`probe`(stride-3 全窗扫描,1/3 成本定位窗口)→ `fine` 只补渲窗口内未探帧;fine 复验发现脏帧则 `SKIP_fine_dirty_N`(⚠ 改进队列:未来应回退重选窗,当前直接判负)。
2. **运动门**:合格 clean 窗口取 pose `dmax` 最大者;`dmax<8m → SKIP_static`(静止段=轨迹带物理纯盲区,DB-130 根因)。
3. **分片 map**:8 shard + merge + final(DB-131)。
4. **FLUX+打包后台线程**:`flux_and_pack` 单独线程,`join` 序列化(CPU 主线程去跑下一个 log,FLUX/打包藏进后台)。
5. **`MACHINE_SHARD="i,k"` 机器级 log 分片**:`cands[i::k]`,两机各跑各的 log,**零通信、线性扩展**。

- **双机首批实测**(48 核 Blackwell 96GB ×2,K=24):**3 成品 / 8 判定 log**。

  | log | resid% | 判定 |
  |---|---|---|
  | 182ba3f7 | 4.32% | ✓ 成品 |
  | 15ec0778 | 4.18% | ✓ 成品 |
  | 19350c96 | 19.13% | ⚠ 边缘品(见 §9.6) |
  | (其余 5) | — | 2 静止 / 2 fine 脏帧 / 1 无干净窗口 |

  热机合格 log 端到端 **8.9-16min**(map 独占轴仍是主因 → 引出 DB-133/134)。

### 9.3 DB-133:单 log 极致实验 + 判决

`agent/db115_drivers/db133_extreme.py`:重叠编排(map **16 分片** ∥ fine ∥ cand)+ FLUX 预载线程,最难 log `0b86f508` 极致做到 **672s=11.2min**。

- **判决**:48 核 CPU 总算力已饱和,重叠后各段互相拖慢、**总量守恒** → 这是**单机物理极限**。结论:后续加速不能再靠"堆并行",只能靠**投机**——把串行段藏进"本来就必然存在的等待轴"里。这条判决直接定义了 DB-134。

### 9.4 DB-134:v14 → v14.1 投机建图 ★

`agent/db115_drivers/db134_production.py` + `db134_fluxpack.py`。核心思想:**map 不等窗口定下来再建,localize 一完成就先赌一个窗口开建**,赌中就白赚整段 map 时间。

- **投机建图流程**:
  1. localize 后立即用 pose 表算**全窗口 `dmax` 表**(`DM[p]`);
  2. `P_guess = argmax(DM)`,`R_g = clip(dmax+20, 23, 46)`;
  3. **map 8 shards 在 probe 期间就提前启动**(用 guess 的中心 `MID_g`、半径 `R_g`);
  4. probe 选出**真实**窗口 `P` 后做**几何检验**:窗口所有帧到 guess 中心距离 `far ≤ R_g-2` → `specmap_hit=True` 直接复用;未命中 → kill 掉投机 shard + 用真实窗口重建。
- **实测**(`15ec0778`):`specmap_hit=True`,`map_s=100`(map 几乎全部藏进 probe 时间轴)。渲染层挂钟 **~5.9min** vs v12 同 log **~7.1min**。分段耗时(秒):

  | probe | fine | cand | fill | map(重叠) | wbev | compose | flux_load | flux | pack |
  |---|---|---|---|---|---|---|---|---|---|
  | 144 | 91 | 18 | 45 | 100 | 50 | 3 | 71(page cache) | 53 | 9 |

- **v14.1 三修复**:
  1. ⚠ **OOM 修复**:`fan()` 的 worker `Popen` env 加 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`(修 FLUX 线程 33.6GB 与 24 个 probe worker 并发撑爆 96GB 的 OOM)。
  2. ⚠ **FLUX 退避重试**:生成走 `_pipe_retry`,捕获 OOM 后退避重试(90s×3,覆盖两处 pipe 调用)。
  3. `USED` 列表补 8 个已判定 log(避免重复消耗探测预算)。

### 9.5 速度三口径(务必分清,别再混淆)★

用户多次把这三个数字混为一谈,发布/汇报时必须写清是**哪一个口径**:

| 口径 | 数字 | 含义 |
|---|---|---|
| 合格 log 单机端到端(热机) | **~7-8min** | 一台机器从 localize 到成品打包 |
| 双机渲染吞吐 | **~3-3.5min / 合格 log** | 两机并行,只算合格 log 的平均产出间隔 |
| 双机含被拒摊销(产出率 ~37%) | **~6-7min / 合格成品** | 把被拒 log 的探测成本摊进每个真正成品 |

外加:**冷启动一次性 FLUX 载入 ~11min 税**(page cache 之后降到 ~71s),这是每台机器**开机一次**的固定成本,不计入上面三个稳态口径。

### 9.6 质量契约(黑斑语义,用户 2026-07-13 拍板)★

- **黑斑是什么**:贴身高大车辆 ∩ ego 车身拒源区 → 级联三层(fill / world-BEV / ProPainter)全都无供给 → **诚实黑**。
- **mask 孪生定义**:打包 mask = `(RGB和 ≥ 12) * 255`,近黑像素自动标 0=无效。Cosmos 微调把 mask 当 loss / 条件 mask 时,黑斑落进 **unknown 区**——**无毒但信息量低**(不会把幻觉当真值去学)。诊断样例:`19350c96` Hertz 货车,`diag_van_strip.jpg`(在 Drive 产物目录)。
- **用户判决**:**不**加 PP/FLUX 去涂黑斑——"把幻觉标成真值喂模型比留黑更糟";数据侧只给真实,**生成责任归模型**。
- **眼核记录**:

  | log | 判定 | 备注 |
  |---|---|---|
  | 182ba3f7 | ✓ 干净 | — |
  | 15ec0778 | ✓ 干净 | f092 白 SUV fg_occ 小黑 |
  | 19350c96 | ⚠ 边缘品 | 强阳光过曝,frame-1 nadir Telea 灰白大块,resid 19.13% —— **用户裁定保留** |

### 9.7 量产配置 + 产物结构(2026-07-13 启动)

- **候选**:AV2 val 150 log 排除 `USED`(28 个历史已用/已判定)后取 **100 个**。
- **发射实例**:`agent/db115_drivers/db135_run100.py`(从母版 `db134_production.py` 手术而来,`LOG100` 100 候选清单内嵌;实际在双机运行的就是它)。
- **双机**:两台 48 核 Blackwell 96GB G4,`MACHINE_SHARD` 分别 `"0,2"` / `"1,2"`。
- **账本落盘**(均在 `datasets/av2_1plus92_production_v14/` 下):
  - ledger:`db135_run100_ledger_m0of2.json` / `db135_run100_ledger_m1of2.json`(每机进度 + 每 log 分段耗时/verdict)。
  - manifest:`manifest_m0of2.json` / `manifest_m1of2.json`(含 100 候选全清单 + 机器归属)。
- **产物目录**:Drive `koi_waymo2pano_colab/datasets/av2_1plus92_production_v14/`,每 log 一个 **8 位前缀子目录**:
  ```
  <u8>/
    frames/          # 93 帧 ERP PNG(frame-1 完美 + 92 band)
    masks/           # 孪生 mask(RGB和≥12)*255
    clip.mp4         # H.264 预览
    sample_sheet.jpg # 眼核缩略图
    ledger.json      # 该 log 的分段耗时 + verdict + cascade 占比
    worldmap_m2.png  # 该 log 的 world-BEV 地图(M2 fine+fuse)
  ```

### 9.8 ⚠️ 踩坑 / 纪律(动 v11-v14 前必读)

- ⚠ **内核默认必须全关=byte-identical**:任何改 db89 前先本地代理验证三开关默认关时输出与 v9 逐字节一致,再改注入(HOOD_TO_MASK 静默失效史)。
- ⚠ **FLUX 线程 + probe worker 并发 OOM**:FLUX 单独线程占 ~33.6GB,与 24 个 probe worker 抢 96GB → 必须给 worker Popen 传 `expandable_segments:True`(v14.1 已修)。
- ⚠ **fine 脏帧当前直接判负**:两段式 band 的 fine 复验若发现脏帧,现版直接 `SKIP`,没有回退重选窗——列入改进队列。
- ⚠ **投机未命中要 kill 旧 shard**:`specmap_hit=False` 时必须 kill 掉投机建图进程再用真实窗口重建,否则残留进程抢 CPU。
- 📌 **map 是唯一独占长杆**:所有提速判断先看 map 有没有藏进等待轴(`specmap_hit` / `map_s` 是否≈0 增量);别去优化已经并行掉的段。
- 📌 **速度口径必须标注**(§9.5):汇报数字前先确认是单机端到端 / 双机吞吐 / 含摊销 哪个口径。
