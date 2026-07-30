# 1+92 Panorama Pipeline (DB-116) — 权威说明 · 可复现

> ## ⚠️ 状态框(2026-07-17,先读这一条)
> **本文档主体(§0-§9)描述的是 07-01 时代的管线(单版 frame-1 完美 360 + 92 band),概念底盘仍然有效,但已不是当前量产版。**
> **当前量产版 = v15(2026-07-14 定版,DB-136 契约 + DB-144 执行),见文末新增的 [§10 v15 量产管线](#10-v15-量产管线2026-07-14-定版db-144)。** v15 相对旧版的三大改动:① **A/B 双版一次渲染同时导出**(A=去车头+真实填充+诚实黑;B=车头置黑);② **mask "白=严格真实" 契约**(Telea 插值像素在 A-mask 翻黑);③ **γ 接缝条带放行浅脏帧**(旧版整场一票否决)。
> **数据集已收官**:AV2 全 850 可用 log(val 150 + train 700)判定 100% 完成,**最终库存 555 个 A/B 双版 1+92 样本**,产物 Drive `datasets/av2_1plus92_v15/`。读者若只想理解"现在怎么量产",可直接跳到 §10。

> 最后更新 2026-07-01(§0-§9);**§10 v15 追加于 2026-07-17**。任何时候要查"我们怎么从一个 AV2 log 造出 1+92 数据"都看这份。
> 相关记忆:[[db116-frame1-perfect360]] [[db115-selection-pipeline]] [[waymo2pano-ground-fill-physics]] [[av2-cosmos-pipeline-db114]] [[db123-ego-removal]] [[db135-run100-production]]

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

### 🆕 v15 量产阶段新踩坑(2026-07-14~17,DB-144;跑 §10 前必读)
- 🔴 **manifest-clobber = 重试轮 tag 复用 → 假脏**:被拒 log 重跑时复用同一 tag,fine band 的 manifest 被后一轮**覆盖**,把早轮的干净结果写脏 → run100 通过率被系统性低估(17% 假象)。**修**(v14.4):重试轮用独立 tag / 不覆盖 manifest。修后重审:83 个被拒 log 里 **45 个直接放行**(`fd_36`/`fd_44` 实测仅 0-3 帧真脏)。**纪律:凡"被拒率异常高"先怀疑 manifest 覆盖,别信第一轮拒因。**
- 🔴 **FAIL 中断留半写 PNG → 毒化 resume**:map merge 瞬时故障等 FAIL 会在产物目录留下**半写/截断的 PNG**,`RESUME` 断点续跑按目录存在即跳过 → 把坏帧当成品。**修**:resume 前先**清场**(删该 log 目录再重跑),别在半写残骸上续。实测 2 个 FAIL(`c2c0e6bc`/`7ccdda39`)清场重跑后 verdict=OK,**零 FAIL 残留**。
- 🟠 **跨机 Drive FUSE `ls` 互不可见**:一台机 `ls` 看不到另一台机刚写的目录/ledger(**双向延迟**),据此会误判"成品丢失"。**纪律**:巡检**各机读各自 ledger**、合并统计走本地中转,别指望一台机实时看到另一台的 Drive 写。
- 🟠 **`pkill` 必用字符类防自杀**:清残留 driver/worker 用 `pkill -f` 时,匹配串若太宽会把**哨兵自己**和当前 shell 一起杀掉。**修**:模式用字符类打断自匹配(如 `pkill -f '[d]b144'` 而非 `db144`),让 `pkill` 自己的进程行不匹配。
- 📌 **Drive 换版本用同名 `cp` 覆盖,绝不 `mv`**:Google Drive FUSE 下 `mv` 会**换 fileId**,毁掉旧的分享链接;要保住 koi 已拿到的链接就**同名 `cp` 覆盖**(保 fileId),另留版本副本并存。
- 📌 **`db144_v15.py` 运行时是 `/content/_dj_db144tr.py`(train 变体)**:master 在 `agent/db115_drivers/db144_v15.py`,实际双机跑的是手术出的 train/val 变体(`MACHINE_SHARD` 与候选池不同);改逻辑改 master、发射前确认变体已同步。已修 bug:`15ec0778` fluxpack 的 `P` 变量错用(121→应 76、被 specmap 罩住)+ `LED` 全局未定义。

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

---

## 10. v15 量产管线(2026-07-14 定版,DB-144)★当前量产版

> 追加 2026-07-17。§0-§9 讲"怎么造一个 log"和"怎么把它变成能双机批量跑的量产栈";**这一节讲当前实际量产的 v15 契约(DB-136 用户五项拍板)+ 全 850 log 收官账目(DB-144)**。看完这一节就能理解"我们现在到底在造什么、怎么造出来的 555 个样本"。
> 事实来源:`decision_briefs.md` DB-136 + `progress.md` DB-144(07-15/16/17)。相关记忆:[[db135-run100-production]] [[db123-ego-removal]] [[db115-parallel-framework]]。

### 10.1 v15 是什么(一句话)

v15 = 在 §9 的 v14 投机建图量产栈之上,把**数据契约**换成 koi 三方对齐的定版:**每个 log 一次渲染同时导出 A/B 两个版本**,mask 严格"白=真实",浅脏帧靠 γ 接缝条带救活而非整场枪毙。词典目标(§1)与真实优先原则不变,零 per-scene 调参不变。

### 10.2 v15 与旧版(v14)差异总表 ★

| 维度 | 旧版 v14(§9) | v15(DB-136 拍板) |
|---|---|---|
| **版本导出** | 单版 | **A/B 双版一次渲染同时导出**(同窗同帧位严格对齐,供 A/B 训练对比;增量成本 ~1min/窗) |
| **A 版语义** | (即旧版:去车头 + 时序真实填充) | **去车头 + 时序真实填充 + 诚实黑**(无源区留黑=真实数据边界) |
| **B 版语义** | 无 | **`EGO_BLACK` 车头区直接置黑**(= A 流程的 band 中间产物,车头留黑) |
| **mask 契约** | `(RGB和≥12)*255`,近黑标 0 | **白=严格真实**;**Telea 插值像素(占比 2-9%)在 A-mask 翻黑**(画面像素零变化、补齐"白=100%真实") |
| **浅脏瑕疵帧** | **整场一票否决**(见 §6) | **γ 接缝条带放行**:`dirty≤3 帧`则放行,只把坏接缝 **±90px 竖条**在 A-mask 标黑(真实代价=全帧 1%=band 内容 3.9%,总监督损失 <0.1%) |
| **质量门** | 运动窗 dmax≥8m | **运动窗 dmax≥8m / fine seam≤8px / resid≤15%** |
| **建图** | 投机建图 specmap(v14 引入,v15 沿用) | **specmap**:建图藏进 probe 时间轴,pose 表算 dmax 预测窗口、几何检验命中即复用(§9.4) |

**γ 接缝标定(可复现)**:7 接缝 yaw 由 7 相机外参一次算出——`front 0°` / `front_l/r ±45°` / `side ±99°` / `rear ±153°`;接缝像素列 `x = (0.5 − yaw/360) * 2048`。瑕疵帧的 manifest 自带超差 `cam_pair`,查表定位是哪条接缝、在该列 ±90px 竖条标黑。功效:val 被整场枪毙的 44 个瑕疵场景**复活约 34 个**(07-17 实测)。

**核心论点(为什么去车头必然出现黑区,开会核心,`92b900b1` 红 F-350 铁证)**:原 AV2 白车头占据的画面位置,去掉车头后其背后内容(贴身车下半身)**从未被任何相机拍到**(原始 ring_side_right 里皮卡近到溢出视野底缘)。黑区 = 掀开车头遮挡后的真实数据边界,**非处理缺陷**;"以前没黑"只因那里显示的是白车头图像本身。mask 全标黑 = **生成责任归模型**。所有贴身大车场景(Hertz / Gordon 货车)同族,是数据固有属性。

**第三级填充 = Telea 保留**:tier3 三方实测 Telea(大洞灰白糊)< Wan(可用但 640→2048 上采样偏软、慢 4×)< ProPainter(85s 最优);用户选**保守 Telea**(下游 Cosmos 会重生成外观,tier3 只需几何占位),PP/Wan 判决留档备用。

### 10.3 管线流程(v15 端到端)

```
localize + egomask                # 一体 job;pose 表就位后立即算全窗 dmax
  │
probe(每 3 锚点 stride-3 全窗粗扫)  # 1/3 成本定位干净窗口;specmap 在此期间就提前开建 map
  │
运动选窗(dmax 最大的合格 clean 窗)  # dmax<8m → SKIP_static(轨迹带物理盲区)
  │
fine 渲染(只补渲窗口内未探帧)       # fine 复验脏帧 >3 → SKIP_fine_dirty;≤3 走 γ 条带放行
  │
cand(frame-1 基底,imperfect 选帧)  # nadir_imperfect_px 升序取真实最完整帧
  │
fill(门控反投影)+ map(8 分片 merge)+ wbev   # 三路;map 分片与 fill/probe 并行(specmap 命中则近零增量)
  │
compose(v10.2,七门级联)            # Tier1 fill ∩ Tier2 wbev,egozone∪band 黑洞全包络
  │
Telea 收残洞                        # tier3 几何占位;Telea 插值像素在 A-mask 翻黑
  │
FLUX sky + ground(仅 frame-1)      # 天空 outpaint + faithfill 局部补盲(§5,F 环)
  │
A/B 打包上 Drive                    # A(真实填充)+ B(EGO_BLACK)双版 + 逐帧严格 mask + mp4
```

- **driver**:`agent/db115_drivers/db144_v15.py`(master;`K=24`,pipeline tag `v15-db144`,ledger 头嵌 git ref)。实际双机跑的是手术出的 train/val 变体 `/content/_dj_db144tr.py`(候选池 + `MACHINE_SHARD` 不同,逻辑同 master)。
- **内核**:`scripts/phase3/db89_ghost_recovery.py` = **v11,md5 `cca4f0c5`**;三开关(`WORLDBEV_SHARD`/`WORLDBEV_DUMP`/`WORLDBEV_LOAD`)默认全关时与 v9 **byte-identical**(§9.1)。worker = `/content/db125_worker.py`,`Popen` env 带 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`(防 FLUX 线程 + 24 worker 并发 OOM)。
- **B 版几乎白赚**:B = A 流程 band 阶段(`EGO_BLACK=True`)的中间产物,不用第二遍渲染,增量成本 ~1min/窗。

### 10.4 运维定式(双机量产)

- **机器级分片 `MACHINE_SHARD="i,k"`**:本机只跑 `cands[i::k]`,跨机**零通信、线性扩展**;收官夜用**半分片** `0of6`/`3of6`(继承种子账本切分、不重跑已判定 log)。
- **Drive ledger 实时落盘 + 断点续跑**:每 log 判定即写 `datasets/av2_1plus92_v15/db144_v15_ledger_m{i}of{k}.json`(头部嵌 git ref);`RESUME` 靠 `DONE_VERDICTS` **精确跳过已判定 log**、只 `FAIL` 重试。全程经历 **~6 次 Colab 会话回收 + 2 次本地断电重启,零判定丢失**。
- **哨兵巡检自动重拉**:本地 `train_guard.py` 每 5 分钟经隧道巡检各机,driver 死则自动重部署 + 续跑(10min 归队),全分片 `EXHAUSTED` 自动收官。收官夜双 G4 + 哨兵 **4.6h 守护零事故**。
- **机队决策(07-16)**:三机(G4×2 + A100)→ **停 A100 保双 G4**——G4(RTX Pro 6000 Blackwell 48 核 96GB)单机 `~10.5min/判定` ≈ 4 台 A100(`~40min/判定`);A100 反而零回收但吞吐低,双 G4 只丢 10-15% 产能却省一半会话回收风险。双机摊销 `~5.2min/判定`。

### 10.5 终盘数字(2026-07-17 收官,全实测,勿改) ★

- **判定覆盖**:AV2 **全 850 可用 log(val 150 + train 700)判定 100% 完成**。
- **最终库存 = 555 个 A/B 双版 1+92 样本** = **val 101(150 判,67% 通过)+ train 454(700 判,64.9% 通过)**;超 BOSCH 口径「~500 expected」11%,命中 DB-136 数字包「550-700」下沿偏上。
- **train 700 拒因分布**:`SKIP_resid` **133**(**最大拒因**=真实填充覆盖不足、resid 超 15% 门)/ `SKIP_static` **53**(静止=轨迹带物理盲区)/ `SKIP_fine_dirty` **47**(接缝瑕疵 >3 帧、γ 条带救不回)/ `SKIP_no_clean_window` **13** / `FAIL` **2**(map merge 瞬时故障,`c2c0e6bc`/`7ccdda39` 断点重跑 verdict=OK)→ **零 FAIL 残留**。
- **质量账目**:成品中位**端到端 704s(11.7min/台)**;**resid 中位 6.3% / p90 12.2%**(15% 门内富余,分布健康非贴门产出)。
- **产物**:Drive `koi_waymo2pano_colab/datasets/av2_1plus92_v15/{val,train}/<log8>_w1/{A,B}/{frames,masks,clip}` + `sample_sheet`/`ledger`/`worldmap`(布局见 DB-136)。
- **交付语境**:数据集就绪,**下一步 = koi 用 A/B 对照做 Cosmos 微调 conditioning 实验**(A/B 双版正是 DB-136 ④ 契约设计初衷)。

---

## 11. 内核修复(2026-07-28~30,量产之后)★改动 band 输出,存量 555 未含

以下四项都在 `scripts/phase3/db89_ghost_recovery.py` 里,**全部影响 band 帧输出**。存量 555 样本是修复前渲的;是否重导待 user 决。

### 11.1 DB-171 band rule-5 兜底(真 bug 修复)

- **症状**:B 版远处行人脚下 / 车底 / 柱底出现黑块。
- **根因**:`EGO_BLACK` 用 `Zsupport` 判"无支撑"→ 而 `annotations.feather` 存在时,支撑图会剔除标注物体框内的 LiDAR 回波 → **停放物体自己的足迹被判无支撑**,∩ 宽大 ERP `egoproj` → 被当自车涂黑(实测 `ego_black_px=446,700`)。
- **复现钥匙**:db151 band-only 用的是**选择性下载**(不含 annotations),机制空转 → 五次误诊都没复现。**输入数据必须对齐才能复现**。
- **修法**:`EGO_BLACK` 之后,带内内部黑洞按射线方向从「光轴最近 + 视场内 + 采样不落解析 ego 掩膜」的相机取锚时刻像素。truck 区 4,098→1,283 px、feet 12,131→9,576 px。

### 11.2 DB-181 band 合成器门(真 bug 修复,内部编号与另一 agent 的 PandaSet DB-181 撞车,归档时称 DB-182)

- **症状**:不骑缝的行人出现偏移叠影(蓝衣人 fr_0037)。
- **根因**:`ann` 驱动的物体机制(`SEAM_OBJDEPTH` 强制框深度 + `track_pose_at` 按各相机曝光时刻插值框位姿)把行人按**标注框位置**重新定深/合成;而代码自己的注释就写着 AV2 框在移动 track 上位置误差 ~4 m(投影偏 100 px)。
- **修法**(`db89:134` 一行):`GROUND_MODE == "off"`(band 模式)时**根本不加载 annotations** → 物体机制全空转 → 纯图像证据 → 单人。fill/worldbev 模式保留。

### 11.3 DB-184 `DEPTH_SEAMRAMP = 60.0`(画质,用户第一性质疑触发)

- **用户原话**:「文字就只是在那个相机的正中间的位置,只是拿原图简单的拼接啊,为什么要反投影?」——**成立**。
- **第一性**:逐像素反投影 `X = C + Zd·DIRS` 的唯一合法目的是消**缝处**视差;全 ERP 被 ≥2 相机看到的只有 **4.15%**。其余 95.85% 里,**深度场的空间梯度 = 图像位移的空间梯度**,而字母笔画只有 2-4 px 宽 → 撕裂。
- **数字**(a100 招牌,θ=−104° 单相机,side_right 垂直基线 0.353 m):位移 13.4 px / 梯度 p99 **1.08** max **1.74** → ramp 后位移 12.8 px(**不变**)/ 梯度 p99 **0.44** max **0.75**。素墙控制组 0.93→0.19。
- **修法**:在**逆深度域**(视差 ∝ 1/Z)按「到最近双相机重叠区的 EDT 距离」把 `Zd` 混向 8× 下采样中值的大尺度无梯度场。缝处 `w=1` 保精细深度 → 不回归。
- **反证**:`FARFIELD`(全程不用深度)接缝色阶 **34.33 vs 14.64**,皮卡/建筑/黄线肉眼断裂 → **深度必要,但只在缝附近必要**。相机基线实测仅 **0.21-0.38 m**。
- `DEPTH_SEAMRAMP = 0` → byte-identical 回退。

### 11.4 DB-184b `GAIN_STRENGTH`(试过 0.5,**已被 DB-198 回退到 1.0**)

> ⚠️ **这一节记录一次被泛化验证推翻的落地,保留是因为陷阱本身有价值。结论见 §11.6。**


- **用户原话**:「这个色差感觉也不应该存在啊,只是拼接为啥会有?」
- **事实**:色差**不是拼接产生的**——7 相机独立 AE/AWB,同一表面 front_center 记录值仅 side_right 的 ~1/2(增益比 1.97×)。`solve_gains_for` 就是在补偿它。
- **但它过冲 ~2×**(共视点 log 中值差不是纯曝光比:视角相关 BRDF + 渐晕 + 眩光都漏进去)。3 帧 × 4 条领地缝双轴实测:

  | 方案 | 接缝 \|dRGB\| | 色调偏离实际记录 |
  |---|---|---|
  | **×0.5** | **13.81** | **6.1%** |
  | ×1.0(原) | 14.64 | 12.5% |
  | 不做 | 15.29 | 0% |
  | 限幅 ±0.2 | 15.38 | 11.0% |

- side_right 店面:原图 `[76.9,74.2,75.6] R/B 1.017` → ×1.0 `[59.6,58.4,62.6] R/B 0.953`(用户报「发闷偏紫」)→ **×0.5 `[73.2,70.3,73.0] R/B 1.003`**。
- **★限幅不等价且更差**:截断部分相机会破坏相机间的**相对**关系,只有等比缩放保持它。
- `GAIN_STRENGTH = 1.0` → 回退。**验证仅在 band 模式**(`GROUND_MODE="off"`);fill/worldbev 继承同一 gains 未单独眼核。

### 11.5 踩坑增补

- **判"最初有没有"必须 git 第一提交 + 纯拼接层输出**:`_baseline_fable5/` 是 6-19 快照不是最初版;带地面填充的终输出会盖住 band 层缺陷,两者都会骗人。
- **色彩类全局量不能用单帧下结论**:我用单帧得出"增益净负收益",三帧统计直接推翻(14.64 < 15.29)。
- **几何归因先算方位角再说话**:招牌/蓝衣人都在 side_right 独占区(覆盖 [−130.1°,−67.7°]),离最近重叠带 9-36°,骑缝解释根本不成立。ERP 约定 `theta = 180 − (u+0.5)/W·360`。

### 11.6 DB-198:泛化验证推翻 §11.4,并修掉一个错误的度量(2026-07-30)★

用户要求「再去一些别的人多的场景试试」。扫 90 个 log 的 `annotations.feather`(只下标注,几 MB/个)按峰值行人数排序,取三个从未用过的 log 端到端重渲:

| tag | log | split | 峰值行人/帧 | 城市 |
|---|---|---|---|---|
| s1 | `1842383a-1577-3b7a-90db-41a9a6668ee2` | train | **108** | 迈阿密海滩 |
| s2 | `e453f164-dd36-3f1a-9471-05c2627cbaa5` | train | **106** | 匹兹堡市中心 |
| s3 | `280269f9-6111-311d-b351-ce9f63f88c81` | val | **76** | — |

**① 度量本身是错的(先修工具再下结论)**
旧 `seamstep` 把每条缝钉在**一个列** `x` 上,然后只取「该列上领地发生变化的行」做窗口——但领地边界在 ERP 上是一条**曲线**,绝大多数行根本没被测到。实证:s2 路面上肉眼可见 **21 luma** 的台阶,旧度量给 **1.0**。DB-198 改为**逐行沿边界走**(每条缝 200+ 行样本,取行中值),这才是可信数字。**结论:所有用旧度量得出的接缝数字作废,包括 DB-184b commit 里引用的 14.64/15.29。**

**② `GAIN_STRENGTH=0.5` 不泛化 → 回退**

| 场景 | g1.0(原) | g0.5 |
|---|---|---|
| s1 迈阿密 | **5.50** | 7.33 |
| s2 匹兹堡 | **9.08** | **14.17** |
| s3 | **6.33** | 7.67 |
| 00a6ffc1 fr_0032 | 8.00 | **6.21** |
| 00a6ffc1 fr_0037 | 8.75 | **6.92** |

只在 00a6ffc1 上更好,三个新 log 全部退化(s2 的 `front_left|front_center` 8.3 → **20.7**,肉眼是一整块欠补偿的暗梯形)。**根因不是「增益普遍过强」,而是 00a6ffc1 这个 log 的增益求解本身坏了**:fr_0037 `front_right|side_right` 缝在全增益下 **70.5**,而**完全不做增益只有 12.8**。正道是**鲁棒化 `solve_gains_for`**(剔除被眩光 / 视角相关 BRDF 污染的共视点对),不是全局缩放。

**③ `DEPTH_SEAMRAMP` 经住了泛化验证**
把 gains 固定在 1.0,只切 ramp:接缝 8.00→7.75 / 9.58→9.46 / 8.75→8.79 = **±0.25 零影响**,而文字撕裂被修掉。**与设计预测一致**(缝处 `w=1`,精细深度原样保留)。三个新场景 A/B 眼核也无退化。

**④ 方法教训(第三条,与 §11.5 并列)**
- **调参类改动必须跨 log 验证,单 log 三帧不够**:00a6ffc1 的"帕累托全赢"在三个新 log 上全线翻车。**修复(几何错误)和调参(权衡)要区别对待**——前者单场景可判,后者必须泛化。
- **眼睛和数字冲突时,先怀疑度量**:这次是度量错了(采样偏差),不是眼睛错了。
- **领地图必须用每个 log 自己的标定**:复用 00a6ffc1 的 `terr_map` 会把 `rear_right|rear_left` 的缝算到 θ=−76°(几何上不可能),整批数字作废。`C = 7 相机在 ego 系位置的质心`,纯标定量,本地可算(`db197_terr.py`)。

### 11.7 DB-203:色差的第一性修复 —— 相机对有效性门(2026-07-30)★

用户:「需要把色差从第一性原理修复清楚」。§11.4/§11.6 试过的全局缩放是**在症状上调参**,这一节是查根因。

**模型与它的四条承重假设**。`solve_gains_for` 把相机 j 相对 i 建模为**每通道一个乘性标量**,用两者共视 LiDAR 点上 `median(log I_j − log I_i)` 估计。成立需要:(a) 表面朗伯(否则 log 差里混进视角相关辐射);(b) 无渐晕(否则边缘点系统性偏);(c) 无加性杂散光(纯乘性模型吸收不了基座);(d) **共视点在两幅图里配准正确**。

**诊断(DB-201/202:把共视点原始证据 dump 出来量,不猜)**。对每个相机对测:log 比的 IQR、与图像半径平方差的相关(渐晕特征)、鲁棒仿射拟合 `I_j = a·I_i + b`、共视点距离。结果:

| 帧 | 对 | n | IQR | 斜率 a | 中位距离 | **corr(log Iᵢ, log Iⱼ)** |
|---|---|---|---|---|---|---|
| 00a6ffc1 a100 | side_right\|front_right | 2690 | **1.615** | **−0.027** | **6.1 m** | **0.029** |
| 00a6ffc1 a100 | 其余 6 对 | — | 0.014–0.393 | 0.53–1.11 | 7–39 m | 0.47–0.95 |
| e453f164 a070 | 全部 7 对 | — | 0.083–0.368 | 0.39–1.37 | 13–41 m | **0.372–0.862** |

**斜率 −0.027 = 两侧像素值几乎不相关**——这不是"曲率估错了",而是**那些点根本没落在同一块表面上**。渐晕(vig_r)在多数对上 ≈0,**不是主因**;假设破的是 (d)。

**为什么是 (d)**:ERP 上相邻相机的重叠带只有 **~8°**,而这对的共视点中位距离只有 **6.1 m**——近距离 + 窄重叠 = 视差极大,任何标定/时间戳误差都把两侧采样走到不同表面上。按距离分层看更直白:该对**在每个距离段 corr 都 ≈0**(0-8m: +0.048,8-15m: −0.041),而且 15 m 以外**根本没有共视点**,所以"距离门"救不了它。

**修复(判据本身就是第一性的)**:曝光比在 log 域是**常数偏移**,常数偏移**不可能破坏相关性**。所以——

> 如果两台相机确实拍到同一表面,它们的 log 强度**必须相关**,无论曝光比是多少。`corr ≈ 0` 就是"不是同一表面"的直接证据,该对不得参与解。

`GAIN_MIN_CORR = 0.30`,低于此值的对整对退出最小二乘。分离度很宽:最差的健康对 0.372,中毒对 0.029(差 12 倍)。剔除后相机图仍**全连通**(side_right 经 rear_right 连回),解不受影响,只是失去污染源。

**离线复算(用 dump 的证据)**:00a6ffc1 a100 的 side_right 增益 `0.653/0.686/0.725` → **`0.954/0.855/1.091`**;健康 log e453f164 七台相机增益**逐位不变**(门一个对都没拒)。

**这与 §11.6 的关系**:DB-198 判定"00a6ffc1 是求解本身坏了的异常个例,正道是鲁棒化 `solve_gains_for` 而不是全局缩放"——DB-203 就是那条正道,而且诊断证实了"坏"的确切含义(不是权重不对,是有一对根本不该参与)。`GAIN_STRENGTH` 保持 1.0。

### 11.8 DB-203b:被拒的边不能直接删,要退回先验

剔除一条边不是中性操作。剩下的图会用**绕行路径**推断那对相机的相对曝光,误差沿路累积——实测 00a6ffc1 a099 的 `front_right|side_right` 缝在直接删边后 **22.0 → 38.0**,比中毒还差。

第一性:**一个量不可测时,诚实的退路是先验,不是长程推断**。所以被拒的对不退出方程,而是以弱权重加一条"无相对曝光差"的约束(`GAIN_PRIOR_W = 0.05`,相对该对自身样本数)。任何**有**证据的对权重都比它大 20 倍,所以它只在无证据处起作用。

三档实渲(00a6ffc1 a095/a099/a100,整帧接缝中位):

| 先验权重 | a095 | a099 | a100 | 均值 | a099 的坏缝 |
|---|---|---|---|---|---|
| 0(直接删边) | 6.92 | 9.62 | **6.25** | 7.60 | **38.0** |
| **0.05** | 7.04 | **9.42** | **6.25** | **7.57** | **18.0** |
| 0.25 | **6.79** | 10.00 | 6.83 | 7.87 | 18.0 |

### 11.9 DB-203 全链最终账目

| 指标 | 修复前 | 修复后 |
|---|---|---|
| a100 `front_right\|side_right` 缝 | **70.5** | **14.3** |
| a100 整帧接缝中位 | 8.79 | **6.25** |
| a095 / a099 整帧中位 | 7.75 / 9.46 | 7.04 / 9.42 |
| side_right 店面亮度(原始传感器 75.6) | 60.2(**−20%**) | **82.5** |
| side_right 店面 R/B(原始 1.017) | 0.953 | 0.953(**未改善**) |
| 健康 log(e453f164)/ 迈阿密 / val3 | — | **逐字节不变** |
| 全带七领地平均 RGB | [101.6 103.0 110.0] | [101.4 103.4 110.8] |

**诚实边界**:①**亮度问题解决了**(店面不再被压暗 20%),这是"发闷"的主因;②**色调偏冷(R/B 0.953 vs 原始 1.017,约 6%)没有解决**——剔除污染边后 side_right 只剩 `rear_right` 一条证据边(rho=0.472),它的三通道解就带着这个色温。要继续追就得回答"七台各自 AWB 的相机里,哪一台的色温是基准",而这没有绝对答案;再往下调等于退回"完全不做归一化"。③全带平均几乎不动,说明修复是**局部的**(只动被污染的相机),没有全局改色。

### 11.10 DB-208:不要假装能估白平衡 —— 只归一化曝光(用户报「发紫」)

DB-203 修好亮度后用户报「颜色发紫」。数字对上了,而且指出了**第二层**问题:

| | R | G | B |
|---|---|---|---|
| 原始传感器 | 76.9 | 74.2 | 75.6 |
| DB-203 后 | 84.2(**+9.5%**) | **74.9(+0.9%)** | 88.3(**+16.8%**) |

**G 几乎不动,R 和 B 都被提高** = 品红。这不是"偏冷",是**通道间比例错了**。

**第一性**:相机之间有两种差异,可估性完全不同——
- **曝光差** = 三通道**共同**偏移。任意表面都能估,只要两台相机看的是同一块表面(DB-203 的门就是保证这一点)。
- **白平衡差** = R/B 相对 G 的偏移。估它需要**中性色参照**。而共视 LiDAR 点落在什么颜色的表面上就带什么颜色——**它们无法充当中性参照**。三通道各解一个增益,拟合的是"共视点恰好落在什么颜色上",不是 AWB。

**证据(决定性)**:健康帧上 per-channel 解出的 R/B 比在七台相机上是 **0.974–1.043**(即它根本没找到任何白平衡差要修,那三个自由度什么也没买到);而在证据不可靠的帧上它摆到 **0.898**(side_right)和 **0.917**(rear_right)——**per-channel 从来没拟合到真实 AWB,只在拟合噪声**。

**修复**:`GAIN_PER_CHANNEL = False`,在亮度上解**一个**曝光增益,三通道同用。等价于:统一亮度,保留每台相机自己的白平衡。

| 00a6ffc1 a100 店面 | R/G | B/G | 亮度 |
|---|---|---|---|
| 原始传感器(目标) | **1.036** | **1.019** | 75.6 |
| 原问题 | 1.021 | 1.072 | 60.2 |
| DB-203 per-channel | 1.123 | **1.179** | 82.5 |
| **DB-203 + DB-208** | **1.064** | **1.005** | 82.8 |

**代价**:接缝中位在五个场景上 7.04→7.08 / 9.42→9.50 / 6.25→6.50 / 5.42→5.67 / 6.33→6.50 = **平均 +2.5%**。用 2.5% 的接缝换回正确色调。

### 11.11 色差全链最终账目(DB-203 + DB-203b + DB-208)

| 指标 | 修复前 | 最终 |
|---|---|---|
| a100 `front_right\|side_right` 缝 | **70.5** | ~14 |
| a100 整帧接缝中位 | 8.79 | **6.50** |
| 店面亮度(原始 75.6) | 60.2(**−20%**) | **82.8** |
| 店面 R/G(原始 1.036) | 1.021 | **1.064** |
| 店面 B/G(原始 1.019) | 1.072 | **1.005** |
| 健康 log / 迈阿密 / val3 接缝 | 9.08 / 5.42 / 6.33 | 9.08 / 5.67 / 6.50 |

**三层各治一病,互相正交**:①`GAIN_MIN_CORR` 剔除配准失败的边(治压暗 20%)②`GAIN_PRIOR_W` 无证据时退回先验而非长程推断(治删边后的路径漂移)③`GAIN_PER_CHANNEL=False` 不假装能估 AWB(治发紫)。

### 11.12 DB-210:色差修复的覆盖性验证(24 个未见 log)★结论有保留

用户问"是不是从根本解决了、而不是对某个场景的局部最优"。跑了 24 个从未用过的 log(随机跨 train/val,每个下载→算增益证据→渲一帧→删数据),168 个相机对。判据在看结果前写死。

**① 双峰性 —— 通过,但边界是任意的**

rho 分布:0.70–1.01 占 111/168,0.45 以上共 145/168(86%)。**灰区 0.20–0.40 只有 8 对 = 4.8%**,低于门的 4 对 = 2.4%。所以不是"横跨阈值抹成一片"。

**但边界确实任意**:`a160c635` 的 side_right|front_right 是 rho **0.330**(保留)而 medLog **−0.375**;`8bc34c99` 是 rho **0.298**(拒绝)medLog −0.317 —— 两者几乎一样却被分到门的两侧。低 rho 是**连续谱,没有天然断点**;00a6ffc1 那个 0.029 是极端案例,0.2–0.4 是灰的。

**缓解(架构性,重要)**:因为有 `GAIN_PRIOR_W` 兜底,**误拒的代价只是"退回无差异先验"**,不是崩掉。所以门可以偏保守,阈值敏感性被结构性地降低了。这是 DB-203b 的额外收益。

**② 拒绝率 —— 通过**:4/168 = 2.4%,且**每个 log 至多拒 1 对**,没有出现某类场景大面积被拒。

**③ 低光 —— 没验证到**:24 个 log 的场景平均亮度是 **102.5–142.3**,全是白天。**样本里没有夜间,也没有雨天。** 非朗伯反射(湿路面镜面)和加性杂散光这两条假设仍然只是"在白天场景里不是主因"。

**④ 通道一致性 —— 通过**:`|rho_channel − rho_luma|` median **0.010** / p90 0.039 / max 0.158。三通道相关性紧跟亮度,**不需要分通道门** → DB-208 用亮度统一判定是对的。

**★⑤ 意外发现:失效是结构性的,不是噪声**

| 相机对 | 24 log 中 rho<0.45 |
|---|---|
| front_center\|front_left | **0** |
| front_center\|front_right | **0** |
| rear_left\|rear_right | **0** |
| side_left\|rear_left | 2 |
| rear_right\|side_right | 2 |
| front_left\|side_left | 4 |
| **side_right\|front_right** | **8(33%)** |

前向三对**从不失效**,侧向四对会。与几何一致:侧向重叠区看到的是近处建筑/路缘,且基线更大(side_right 0.378 / front_right 0.357 vs front_center 0.276 m),视差远超"前向能看到远处道路"的情形。**这证明 corr 门抓的是真实物理现象而非噪声**,也说明未来可以按相机对设定不同期望。

**综合判定**:判据第一性、极端案例可靠、灰区影响被先验兜底限制住、通道处理正确。**但"完全根本解决"还差两块:(a) 夜间/雨天样本(AV2 本身以白天为主,可能要靠别的数据集补);(b) 灰区目前靠一个经验阈值,更根本的形式应该是相对判据或直接用 medLog 的可信区间。**
