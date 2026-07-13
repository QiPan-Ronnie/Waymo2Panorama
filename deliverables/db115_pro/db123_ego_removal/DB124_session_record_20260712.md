# DB-124 Session 实验记录(独立复现级)

> **本文用途:** 一份**自足、复现级**的单 session 实验档案。任何后续接手者(人或另一个模型)**不需要访问原始对话**,只读本文即可完全理解并复现本次 session 的两个实验。
>
> **日期:** 2026-07-12 ｜ **硬件:** A100(经 `dr2` 远程执行框架)｜ **实测集:** frame `cd22abca`,anchor `a110–a140` 共 31 帧,2048×1024 ｜ **原则:** 事实不软化、瑕疵诚实记录、每张结果图眼核。
>
> **姊妹/上游文档:** 同目录 `DB123_generation_route_final.md`(七节终极评估文档,本文是它 §2.6 与 §3.4 的实验级底稿)、`DB123_ego_mask_decision_for_koi.md`(给 koi 的语义决策文档)。
>
> **凭据纪律:** 本文**不含任何 url/token/凭据**。凭据的**存放位置指针**见 §2.3,内容一律不落纸。

---

## 0. Session 概览(一段话)

本 session 在 `cd22abca` 的 31 帧测试集上完成**两个实验,均已眼核 + 归档**:

1. **实验一 — Wan2.1-VACE(DiT 视频补洞)→ 判负。** 补齐了 B 类(学习先验)版图里唯一缺席的 **DiT 时序生成**代表。此前 B 类只测过 flow 类(ProPainter 胜)与 SD 重编码类(DiffuEraser 负);Wan-VACE 与 DiffuEraser **同构判负**——"整段视频进 latent 空间重生成"架构与"95% 真实像素 + 5% 小洞"的任务结构不匹配。**至此 DiT 时序生成路线实测排除,六方法版图闭合。**
2. **实验二 — 三级级联端到端实证(DB-124)→ 通过。** 把此前只是**纸面配方**的三级级联(Tier1 门控反投影 A → Tier2 world-BEV C → Tier3 ProPainter B)在 31 帧上**端到端一次跑通**,同时用关键工程创新 `WORLDBEV_CENTER`(map 共享)把 world-BEV 从 990s/帧摊销到 **30.9s/帧(降 32×)**,解除了 ④ 档量产的最大成本顾虑。给 koi 的四档决策中,**④ 档(三级级联)证据链至此完整**。

两个实验的产物、脚本、内核改动均已落盘并安检(无凭据泄露);全部相关文档索引见 §7 接手者清单。

---

## 1. 背景与前置知识(让接手者零对话上手)

### 1.1 问题定义

Waymo2Panorama 把多路透视相机拼成 360° ERP 全景(panorama / "band")。自车车身(前 hood 反光 + 车顶 roof 总成镜面)会侵入 scene band。DB-123 的定版结论:把车身在图像域 mask 掉(解析双盒 body+roof mask + 无 LiDAR 支撑判据),留下一块**洞区**。本 session 承接的核心问题是:**这块洞区要不要生成内容、用什么生成、生成的像素能不能标进可信 band。**

### 1.2 补全信息的四个来源(第一性)

在 T 时刻被车身挡住的方向,辐射**根本没被任何相机采到**——是不可观测的。补全信息**只可能**来自四源,没有第五个:

| 来源 | 名称 | 信息本质 | 真实性 | 级联定位 |
|---|---|---|---|---|
| **A** | 跨时观测(时间反投影) | 同一 log 别的时刻曾拍到这块地面 → 反投影回 T | **最高(真值级)** | **Tier-1 主力** |
| **B** | 学习先验(video inpainting) | 模型从海量视频学到"这类洞该长什么样" | 中(似真非真) | **Tier-3 兜底残余** |
| **C** | 全局重建(world-BEV 地面图) | 整个 log 的地面观测拼成 BEV 大图再投回 | 高(真实像素,受重建约束) | **Tier-2 真值补覆盖** |
| **D** | 下游一体化(留黑交 Cosmos) | 洞留黑 + mask=0,交下游 Cosmos 视频模型补 | 由 Cosmos 决定 | 兜底(不在本管线生成) |

**终极配方 = 三级级联:门控反投影(A,最真)→ world-BEV(C,真值补覆盖)→ ProPainter(B,兜底极小残余)**,做到零黑洞且 95%+ 像素为真实观测。与项目"宁过勿漏"原则同构:能拿真值就拿真值(A→C),拿不到宁可交生成(B/D),绝不把不可信像素冒充真值。

### 1.3 本 session 前的版图状态(为什么做这两个实验)

- **A(门控时间反投影)** 已定版:四件套质量门(`egozone ∩ fill非黑 ∩ ~faithfill ∩ sharpness>40`),~90% 洞填真值,盲区诚实留黑。
- **B 类**已测:ProPainter **胜出**(全分辨率 31 帧 67s、零黑洞、标线续接、时序稳,**架构上只改洞内像素**);DiffuEraser **判负**(全图重编码劣化洞外真值);FLUX 单帧**弃测**(时序闪烁 + 环境冲突)。**缺口:B 类的 DiT 时序生成代表未测** → 实验一补上。
- **C(world-BEV)** 单帧已证是黑马(覆盖率反超 A、时序一致免费),但 **990s/帧成本 + 三级级联从未串联跑通** → 实验二一次解决两者。

### 1.4 六方法最终版图(本 session 后闭合)

| 方法 | 来源 | 判决 | 关键原因 |
|---|---|---|---|
| 门控时间反投影 | A | **定版·主力** | 真值级地基,~90% 洞填真值 |
| ProPainter | B | **胜出·补盲层** | 只改洞内像素,全分辨率 67s/31 帧 |
| DiffuEraser | B | 判负 | 全图 SD 重编码劣化洞外真值 |
| **Wan2.1-VACE** | B / DiT | **判负(本 session)** | 全图 VAE 重编码劣化洞外,同构 DiffuEraser |
| FLUX 单帧 | B | 弃测·判负 | 无时序(逐帧闪烁)+ 环境冲突 |
| **world-BEV** | C | **黑马·真值补覆盖层** | 覆盖率反超 A、时序免费;本 session 解决成本 |

---

## 2. 实验环境与远程框架

### 2.1 dr2 远程执行框架

本 session 所有 A100 作业经 `agent/db115_drivers/dr2.py` 发射。它是双 runtime 的 `dr_run` 封装:

- `dr2.get("a100")` 返回一个绑定了 A100 runtime 的模块,暴露 `_exec(cmd, timeout)`(同步执行 shell)、`dr_launch(name, job)`(后台发射 Python job,日志写 `/content/_dj_{name}.log`)、`dr_wait`、`dr_pull`、`read_file`。
- 底层复用旧 scratchpad 的 `dr_run.py`(路径硬编码在 `dr2.py` 的 `OLD_SP`),每次调用前把 `W2P_RUNTIME_SECRET_FILE` 环境变量重新指回对应 runtime 的凭据文件(a100 用 `active_url_5.json`),避免双 runtime 共享进程环境时串号。
- **超长命令必须文件式/base64 发射**(见 `db124_patch_wbevcenter.py` 用 `base64.b64encode(JOB)` 包裹后 `python -c`),否则 bash 会截断长字符串。

### 2.2 VM 关键路径(A100 `/content`)

| 路径 | 内容 |
|---|---|
| `/content/w2p_ego` | patched 内核树(`cp -r` 自 `/content/waymo2panorama`) |
| `/content/localav2/val` | AV2 数据(`cd22abca-9150-3279-87a4-cb00ba517372` log) |
| `/content/vi_in/%05d.png` | 31 帧门控反投影 band(A 的输出,2048×1024),补洞实验的输入 |
| `/content/vi_mask/%05d.png` | 31 帧残洞 mask |
| `/content/vi_out/vi_in/inpaint_out.mp4` | ProPainter 基线全序列输出(对比用) |
| `/content/ProPainter` | ProPainter 仓库(`inference_propainter.py`) |
| `/content/wb0_worldmap.png` | staged 的 world-BEV 地面图(自 Drive 复制,渲染时跳过 Drive I/O) |
| `/content/egomask_cur.npz` | 逐相机图像域 ego-body mask(quarter-res) |
| `/content/hf_token` | HF token 文件(**指针,内容不落纸**) |

### 2.3 凭据存放位置(只写指针,严禁写内容)

- **A100 凭据:** `~/.waymo2panorama/runtime/active_url_5.json`(过期后由用户提供新的)。
- **HF token:** 本地 `~/.waymo2panorama/runtime/hf_token.txt`;VM 上 `/content/hf_token`。
- 所有 driver 脚本**从这些文件读取**,不内嵌任何 url/token;本 session 已对 6 个新脚本安检确认无凭据。

---

## 3. 实验一:Wan2.1-VACE(DiT 视频补洞)→ 判负

### 3.1 动机

用户直接问"DiT 用上了吗"。B 类此前只测过 flow 类(ProPainter 胜)和 SD 重编码类(DiffuEraser 负),**缺 DiT 时序代表**。补上它才能宣称"B 类版图闭合、DiT 路线已实测排除",而不是纸面推断。

### 3.2 设置

- **模型:** `Wan-AI/Wan2.1-VACE-1.3B-diffusers`,diffusers `WanVACEPipeline`,bf16,cuda。
- **输入:** `cd22abca` 的 31 帧门控反投影 band(`/content/vi_in/%05d.png`,2048×1024)+ 残洞 mask(`/content/vi_mask/%05d.png`)。
- **resize:** 1280×640(模型 mod-16 约束 + 1.3B 模型原生 480p 量级;2:1 比例保持)。
- **prompt:** `"street-level driving scene, asphalt road surface with lane markings and sidewalks, photorealistic, consistent geometry"`。
- **采样:** 30 步,`guidance_scale=5.0`,`seed=0`,`num_frames=31`。
- **发射脚本:** `agent/db115_drivers/db124_launch_wanvace.py`。

### 3.3 过程 — 五次发射的依赖链踩坑史(最贵的教训,逐条记录)

DiffuEraser 的 `requirements.txt` **无版本上限**,此前把 VM 的 torch/transformers/accelerate/peft/diffusers **五层全部降级破坏**。跑 Wan 时逐层修复,发射了五次才成功:

1. **发射① 崩:** accelerate 缺 `clear_device_cache` → `pip install -q -U accelerate`。
2. **发射② 崩:** transformers 缺 `EncoderDecoderCache` → 固定 `transformers==4.56.2`(项目 pin)。
3. **发射③ 崩:** torch 2.3.1 缺 `torch.nn.RMSNorm`(DiffuEraser 的 requirements 曾把全 VM torch 降级)→ `pip install -U torch torchvision --index-url https://download.pytorch.org/whl/cu126` 恢复 **2.13.0+cu126**(耗时 129s)。
4. **发射④ 半崩:** `ftfy` 未安装(Wan 的 prompt 清洗依赖)→ `pip install ftfy`;同次推理成功(`WV_GEN` 115s),但**视频写出崩**——diffusers 输出 float [0,1] 帧,`cv2.VideoWriter` 要 uint8(`cv2.error: image.depth() == CV_8U`)。
5. **发射⑤ 成:** 加 `to8()` 转换(`np.clip(ar,0,1)*255 → uint8`)→ 全链成功 `WV_DONE`。

> **纪律再确认:重依赖项目(Wan / DiffuEraser / FLUX)必须各自独立 venv,不要同 venv 互相踩踏依赖。** 这是本 session 最贵的一条教训,`ftfy`、`RMSNorm`、`EncoderDecoderCache` 三个缺失都是同 venv 依赖污染的直接后果。

### 3.4 性能

- 模型加载:7–8s(缓存后)。
- 31 帧推理:**115s**(约 3.7s/帧-batch step)。
- 对比 ProPainter 同输入:**67s**。

### 3.5 结果与眼核判决(判负,三条)

对比图 `wanvace_cmp_cd22abca.jpg`(全图行 + 三洞区 crop,Wan vs ProPainter,f15):

1. **洞外真值被系统性劣化。** 全图过 VAE latent 重编码 + 2048→1280 降采样,洞外 ~95% 真实像素系统性劣化。铁证:"FOGO DE CHAO" 招牌文字 Wan 版糊成不可读,ProPainter 版完整保留。
2. **三个洞区 crop 全输 ProPainter。** `x0-361` 黄色车道标线糊成色块;`x766-1278` 斑马线/行人/远车全糊;`x1689-2048` 墙面窗格糊 + 地面棕色涂抹伪影。三区 PP 版均清晰。
3. **补救无效。** 即使只 composite 洞内像素回原图,洞内内容仍是 1280×640 生成再上采样,锐度天花板低于 PP 的 flow 传播真实像素;且与洞外真值有色差接缝风险。

### 3.6 结论

Wan-VACE(DiT 时序生成)与 DiffuEraser(SD 重编码)**同判负,共同败因 = "整段视频进 latent 空间重生成"架构与"95% 真实像素 + 5% 小洞"的任务结构不匹配**。ProPainter 的 "flow 传播 + 仅洞内 composite" 仍是 B 类唯一胜者。**DiT 时序生成路线至此实测排除,六方法版图闭合,三级级联配方经完整版图检验后维持不变。**

### 3.7 产物路径

- **Drive:** `results/db115pro/db123/wanvace_out_cd22abca.mp4`(381KB)、`results/db115pro/db123/wanvace_cmp_cd22abca.jpg`(767KB)。
- **本地:** `experiments/Waymo2Panorama/deliverables/db115_pro/db123_ego_removal/wanvace_cmp_cd22abca.jpg`。
- **发射脚本(已持久化):** `agent/db115_drivers/db124_launch_wanvace.py`。

### 3.8 复现命令

```bash
# 前置:VM 已装补洞树,vi_in/vi_mask 已就位,依赖链已按 §3.3 修复到位
cd "D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama"
python agent/db115_drivers/db124_launch_wanvace.py
# 脚本内部:pip -U accelerate → 读 /content/hf_token → WanVACEPipeline bf16 →
#           31 帧 1280×640 推理 30 步 → to8() 写 mp4 → 生成对比图 → 写回 Drive/本地
# 关注日志标记:WV_PIP_ACC / WV_LOADED / WV_GEN / WV_DONE
```

---

## 4. 实验二:三级级联端到端实证(DB-124)→ 通过

### 4.1 动机

三级级联(Tier1 门控反投影 → Tier2 world-BEV → Tier3 ProPainter)此前**只是纸面配方**——A/C/B 三路各自独立测过,从未串联跑通;且 **world-BEV 990s/帧的成本是 ④ 档量产的最大顾虑**。一个实验同时解决"跑通"与"成本"两件事。

### 4.2 关键工程创新 — `WORLDBEV_CENTER`(map 共享)

**核心思想:** world-BEV 990s/帧的成本大头是**地面图构建**。若一张 map 能在整个 log 的 92 帧间共享,边际成本就接近普通渲染。此前 map 网格原点 **per-anchor 移动**(`_mcx,_mcy = float(ta[0]), float(ta[1])`),导致 map 无法跨帧复用——这正是 990s/帧的根因。

**改动:** 内核 `scripts/phase3/db89_ghost_recovery.py` 新增常量:

- **line 66:** `WORLDBEV_CENTER = ""`("x,y" city 米制坐标;**默认空 = 行为完全不变**)。它**固定 world-BEV map 网格原点**,使一张在某 anchor 建的 map(经 `WORLDBEV_FILL` 加载)在邻近 anchor 采样时保持世界坐标配准。
- **line 1771–1772:** worldbev 渲染分支加 `if WORLDBEV_CENTER: _mcx,_mcy = (float(_v) for _v in WORLDBEV_CENTER.split(","))` 覆盖 per-anchor 中心。

与现成的 `WORLDBEV_FILL`(DB-117 P1 "generate once + sample" 机制)组合,即实现"**map 建一次、全 log 共享**"。

> **⚠️ 内核踩坑:内核有两处 `_MHALF, _CW = 46.0, 0.05`** —— **line 1638** 是 DB-118 GSR 证据导出分支,**line 1769** 是 worldbev 渲染分支。**只改 worldbev 分支(1769)那处**,GSR 分支(1638)保持不动。改错分支不会报错但会静默污染 GSR 导出。

**map 中心复算(零漂移的关键):** map 中心 = a125 的 cte 插值 `ta = (1249.621676, -107.802527)`,用与内核**逐行相同**的 loader 复算:`AV2RingLoader.anchor_timestamps_ns()[125]` 取 timestamp → 读 `city_SE3_egovehicle.feather`(sort + drop_duplicates)→ `np.interp` 插值 tx/ty。复用 wb0 已建的 `wb0_a125_worldmap.png`(1840×1840,92m / 5cm),Drive `results/db115pro/db123/cd22abca_wbev/`。

**纪律执行链:** 本地备份 `scripts/phase3/_backup_db115pro/db89_ghost_recovery_20260712_v8_pre_wbevcenter.py` → 本地 edit(2 处)→ A100 `w2p_ego` 树同步 patch → **断言 `WORLDBEV_CENTER` 恰 3 处**(见 `db124_patch_wbevcenter.py` 的 `assert chk.count("WORLDBEV_CENTER") == 3`)。当前本地内核已含该 patch(已验证 3 处)。

### 4.3 渲染(Tier2 — world-BEV 共享 map)

- **发射脚本:** `agent/db115_drivers/db124_launch_wbcascade.py`。
- **做法:** 31 anchor(`a110–a140`),`video_gen_av2.batch_py` 同款 worker,在其 `py` 文本上做 8 处 `rep`(replace):输出目录、本地数据路径、`BAND_TORCH=True`、`EGO_IMG_MASK`、`GROUND_MODE="worldbev"`、`WORLDBEV_WIN=(79,172)`、`WORLDBEV_FILL="/content/wb0_worldmap.png"`、`WORLDBEV_CENTER="1249.621676,-107.802527"`。
- **`FILLED-OVERRIDE` 生效**(map 已存在,跳过构建循环),`ingrid 100%`。
- **性能:** **958s / 31 帧 = 30.9s/帧**(vs per-anchor 重建 990s/帧,**降 32×**)。这就是 ④ 档量产成本顾虑解除的核心数据。

### 4.4 过程 — 踩坑二条(入踩雷清单)

1. **batch_py 输出路径陷阱:** batch_py 输出路径模板是 **Drive 根下相对路径** `datasets/av2_ground_video_v1`。若把它 rep 成**绝对路径** `/content/wbc_out`,会被**拼进 Drive 根**(实际输出跑到 `koi_waymo2pano_colab/content/wbc_out/`)。正确做法 = 传 **Drive 相对路径**,或事后 `cp -r` 搬回本地盘。
2. **输出文件命名:** 命名格式是 `{tag}_a{anchor}_segcomposite.png`(**tag 整体作前缀,无 case 序号**;本实验 tag=`c`,故文件是 `c_a125_segcomposite.png`。注意 `wb0_a125` 的 "0" 是 tag `wb0` 的一部分,不是序号)。取文件时按此模板 glob 匹配。

### 4.5 级联 composite(Tier2 → Tier3)

- **发射脚本:** `agent/db115_drivers/db124_launch_cascade_finish.py`。
- **合成逻辑(逐帧):** `hole = vi_mask>127`;`valid = wbev.astype(int32).sum(2) >= 12`;`fill = hole & valid` 贴 wbev 像素到 base;`resid = hole & ~valid` 交 ProPainter。
- **级联填充统计(分工比预期更极端):** 31 帧总洞 **693,310 px** → Tier-2 world-BEV 填掉 **692,819 px(99.9%)** → 残洞仅 **491 px(0.1%)** 交 Tier-3。**world-BEV 几乎独吞整块洞区,ProPainter 是真正的兜底保险。**
- **Tier-3 ProPainter:** `inference_propainter.py --video /content/casc_in --mask /content/casc_mask --fp16 --width 2048 --height 1024`(目录输入),**57s** 扫尾。

### 4.6 结果与眼核判决(通过,四点 + 瑕疵)

对比图 `cascade_cmp_cd22abca.jpg`(三洞区四方对比 hole / Tier2 wbev / final / PP-only + f05/f15/f25 时序条):

1. **左下洞 `x0-361`:** Tier-2 恢复 "BUSES ONLY" 路面字标 + 黄线完整几何;**PP-only 字标完全消失、只剩编造的平滑路面**。**真实信息恢复 vs 平滑幻觉 = 决定性差异,是给 koi 的最有力一格。**
2. **中下洞 `x766-1278`:** 斑马线向下延续完整 vs PP-only 斑马线拦腰抹断。
3. **右侧洞 `x1689-2048`:** cobblestone 纹理 + curb 白线保留 vs PP-only 糊成均匀灰。
4. **时序条 f05/f15/f25:** 字标随视角合理移动、跨帧一致(map 共享 = **构造性时序一致**)。
5. **诚实瑕疵:** wbev 填充区与周边直采区**轻微色调差**(接缝可感)+ 字标边缘碎化(map 5cm 分辨率 + Telea 低置信区);量产可加 **seam feather(接缝羽化)**。

### 4.7 量产账本(④ 档核心)

| 阶段 | 耗时 | 说明 |
|---|---|---|
| map 构建 | **990s** | 每 log 一次,可摊销(后续还可 GPU 化) |
| 共享 map 渲染 | **958s / 31 帧 = 30.9s/帧** | vs per-anchor 重建 990s/帧,**降 32×** |
| Tier-3 ProPainter 扫尾 | **57s / 31 帧** | 补最后 0.1% 残余 |
| **92 帧 log 全量估算** | **≈ 67 分钟 / 单卡 A100** | `990 + 92×30.9 + PP`;此前架构 ~25 小时 |

### 4.8 产物路径

- **Drive:** `results/db115pro/db123/cascade_final_cd22abca.mp4`(31 帧 2048×1024 级联终稿)、`results/db115pro/db123/cascade_cmp_cd22abca.jpg`。
- **本地:** `experiments/Waymo2Panorama/deliverables/db115_pro/db123_ego_removal/cascade_final_cd22abca.mp4`、`cascade_cmp_cd22abca.jpg`。
- **渲染中间产物:** Drive `koi_waymo2pano_colab/content/wbc_out/`(31 帧 segcomposite,因 §4.4 路径坑落此)。
- **内核改动:** `scripts/phase3/db89_ghost_recovery.py`(含 `WORLDBEV_CENTER`,line 66 + 1771–1772);备份 `_backup_db115pro/db89_ghost_recovery_20260712_v8_pre_wbevcenter.py`。

### 4.9 复现命令

```bash
cd "D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama"

# (0) 若 fresh VM:先装 patched 渲染树(见 §5 / §6.2)
python agent/db115_drivers/db124_install_tree_wbev.py

# (1) patch WORLDBEV_CENTER 到 A100 树 + 复算 a125 中心 + stage map png
python agent/db115_drivers/db124_patch_wbevcenter.py
#     关注标记:PATCH_ASSERT_OK 3 refs / A125_TA 1249.621676,-107.802527 / MAP_STAGED

# (2) Tier-2:31 帧共享 map world-BEV 渲染
python agent/db115_drivers/db124_launch_wbcascade.py
#     关注标记:WBC_PATCHED_8REPS / WBC_ALL_DONE {t}s（958s）

# (3) Tier-2→Tier-3:composite + ProPainter + 对比图 + 账本
python agent/db115_drivers/db124_launch_cascade_finish.py
#     关注标记:CF_COMPOSITE holes=693310 wbev_filled=692819(99.9%) residual=491(0.1%)
#             CF_PP rc=0 57s / CF_LEDGER render=30.9s/frame total_92f_log~67min / CF_DONE
```

---

## 5. 脚本持久化清单(本 session 已落盘)

`agent/db115_drivers/` 新增 6 件(均已安检无凭据,均从凭据文件读取):

| 脚本 | 作用 |
|---|---|
| `dr2.py` | A100 远程执行框架(读 runtime 凭据文件,暴露 `_exec` / `dr_launch` / `dr_wait` / `dr_pull` / `read_file`) |
| `db124_launch_wanvace.py` | 实验一:Wan-VACE 发射(含 `to8` uint8 修复) |
| `db124_patch_wbevcenter.py` | 实验二:内核 `WORLDBEV_CENTER` patch(断言 3 refs)+ a125 中心复算 + map staging |
| `db124_launch_wbcascade.py` | 实验二:31 帧共享 map world-BEV 渲染发射(8 处 rep) |
| `db124_launch_cascade_finish.py` | 实验二:composite + ProPainter + 对比图 + 量产账本 |
| `db124_install_tree_wbev.py` | fresh VM 装渲染树定式(Drive relay zip 解压 + 6-edit base64 patch + md5 校验) |

---

## 6. 复现要点(关键约束与定式)

### 6.1 凭据(只写指针,内容见 §2.3)

- A100 凭据:`~/.waymo2panorama/runtime/active_url_5.json`(过期后用户会给新的)。
- HF token:`~/.waymo2panorama/runtime/hf_token.txt`(VM 上 `/content/hf_token`)。

### 6.2 fresh VM 装树顺序

1. `db124_install_tree_wbev.py` 从 Drive relay zip(`bundles/w2p_bundle8_relay.zip`)解压装树 → 断言 base 树 md5 `cebab759a30a1af56f88d694ea6bd182` → 施加 6 处 DB-123 ego-removal edit(base64 patch,校验 patched md5)→ 发射 wb0 建 map。
2. **注意:** 该脚本装的是 DB-123 ego-removal 6-edit 版本,**不含 `WORLDBEV_CENTER`**。若从 relay zip 重装,**必须重跑 `db124_patch_wbevcenter.py`** 才能拿到 map 共享能力(它会先检查 `PATCH_ALREADY` 幂等)。
3. 本地权威内核 `scripts/phase3/db89_ghost_recovery.py` **已含 `WORLDBEV_CENTER`**(3 处已验证),是最终参照版;备份链在 `_backup_db115pro/`。

### 6.3 工程定式与坑

- **超长命令必须文件式 / base64 发射**(bash 会截断长字符串)。
- **Drive 写后勿 tight-loop 查询**(FUSE 后端延迟数分钟;写是本地即时、后端异步)。
- **batch_py 输出走 Drive 相对路径**,不要传绝对路径(§4.4 坑 1)。
- **重依赖项目(Wan / DiffuEraser / FLUX)必须独立 venv**(§3.3 教训)。
- **内核两处 `_MHALF, _CW`,只改 worldbev 分支(line 1769)**(§4.2 坑)。
- **凭据文件遇 BaiduSync EPERM 文件锁时重试**(BaiduSyncdisk 目录偶发写锁)。

---

## 7. 接手者清单

### 7.1 等待的外部决策(阻塞项)

- **koi 对四档的拍板:** ① 纯黑化 / ② 门控 + 留洞 / ③ 门控 + ProPainter 零洞 / ④ 三级级联。**④ 档证据链已完整**(本 session §4)。
- **黑区 / mask 语义约定:** 洞区留黑的"黑 = 待生成"语义是否与 Cosmos 训练约定对齐。
- **生成像素标注方式:** ③④ 档里 ProPainter 补出的**生成像素**在孪生 mask 里怎么标(标白 = 可信 vs 单独标注 = 可 refine)。这是上线前必须与 Cosmos 训练方对齐的关键点。

### 7.2 拍板后待办

- **seam feather:** wbev 填充区与周边直采区的色调差羽化(§4.6 瑕疵)。
- **map 构建 GPU 化:** 990s 可再降(当前是 CPU 大头)。
- **定向 fill GPU 化:** gfill 约 9min → 目标 ~1.5min(两级量产版 A100 从 ~15min 压到 ~8min 的主刀)。
- **量产 driver v10:** 把三级级联固化成一键 per-log driver。
- **存量 54 log 重产:** 用定版配方全量重跑。

### 7.3 相关文档索引

| 文档 | 位置 | 内容 |
|---|---|---|
| `DB123_generation_route_final.md` | 同目录 | 七节终极评估文档(§2.6 Wan-VACE、§3.4 级联实证 = 本文的上层结论页) |
| `DB123_ego_mask_decision_for_koi.md` | 同目录 | 给 koi 的语义决策文档(留黑 vs 填地面的语义契约) |
| `agent/progress.md` | `experiments/Waymo2Panorama/` | 2026-07-12 条目(如已归档) |
| `agent/PIPELINE_1plus92.md` | `experiments/Waymo2Panorama/` | 管线权威文档(§7 踩雷清单必读) |
| memory `db123-ego-removal.md` | `~/.claude/projects/.../memory/` | 项目长期记忆索引(六轮判决史 + 级联实证) |
| 6 个 driver 脚本 | `agent/db115_drivers/` | 见 §5 清单 |
| 对比图 | 本目录 | `wanvace_cmp_cd22abca.jpg`(实验一,Wan vs PP)、`cascade_cmp_cd22abca.jpg`(实验二,四方对比 + "BUSES ONLY" 字标恢复铁证) |

---

## 8. DB-125 级联版 1+92 数据集全链量产实证(2026-07-12/13)

> **一句话:** 把 §4 的三级级联配方,在一个**从未用过的新 AV2 场景**上**端到端产出完整 1+92 数据集**,所有加速做到极致,产物落 Drive 专门文件夹。**结果:残洞 0.00%(0 px)、frame-1 完美 360、92 帧 band 零黑洞,纯净重跑 ≈1h/log 单卡 A100。** 全部实测,硬件 A100 单卡 80GB / 12 核 / 8-worker 并行。

### 8.1 场景与排除集

- **场景:** `02678d04-cc9f-3148-9f95-1ba66347dff9`(AV2 val 列表**第一个未用**场景)。
- **排除集:** batch_band 已用的 `02a00399 / 0bae3b5e / 2c652f9e / 8749f79f` + memory 已知实验 log,确保 `02678d04` 是真·未见过的泛化考。

### 8.2 全链账本(8-worker 并行,A100 80GB / 12 核)

| 阶段 | 耗时 | 关键数据 / 说明 |
|---|---|---|
| localize + egomask 生成 | 一体 job | s5cmd S3→本地 SSD + 解析 egomask,合并成单 job |
| band-gate 157 帧 | **472s = 3.0s/帧** | off 模式 + `EGO_IMG_MASK` + `EGO_BLACK=True`;**全 log 干净**(clean run a0–a156 全 157 帧 `max_reg_px≤8`) |
| cand 选帧(10 候选) | **780s** | fill+FAITH+`capg\|=egoproj`+`GROUND_RESID` inpaint+imperfect 输出;**frame-1 = a042**(`imperfect=36756`,fg_occ 主导场景),窗口 `[42,134]` |
| fill 92 帧(GPU) | **459s = 10.4s/帧** | `GROUND_TORCH=True` 补渲 44 帧,**11× 加速**(详见 §8.3 事故) |
| worldbev map 构建 | 与 fill **并行** | `mp_a088_worldmap.png`,MID=42+46=88,中心 `ta=(6748.148568,1633.894933)`(同内核 interp 复算) |
| wbev 93 帧共享 map 渲染 | **~16s/帧(8w)** | `WORLDBEV_FILL` + `WORLDBEV_CENTER` map 只读共享 |
| 级联 composite(四件套) | 见 §8.4 | **残洞 0.00% → ProPainter 跳过** |
| frame-1 FLUX(offline) | sky 41s + ground 40s | 加载 613s 冷读(Drive cache);sky auto-prompt 晴天蓝天;ground faithfill `36756px` → **v8 完美 360** |
| 打包 | ~5min | 93 帧 PNG + mask 孪生 93 张 + H.264 mp4 + ledger.json + sample_sheet.jpg |

**关键加速合并①(省一遍渲染):** band-gate 渲染**直接带** `EGO_IMG_MASK` + `EGO_BLACK=True`,gate 通过的帧**就是 fine band 成品**,省掉此前"gate 一遍 + fine band 再渲一遍 93 帧"的重复渲染。

### 8.3 ★GROUND_TORCH 事故与教训(量产必读)

fill 92 帧**首次发射只开了 `BAND_TORCH`、漏开 `GROUND_TORCH`**(内核默认 `False`)→ 800k 点候选扫描**全走 CPU**,~100s/帧,12 核 load 飙到 40,预计 **2.5 小时**。**用户一句"fill 不能 torch GPU 加速吗"直接命中根因** → 杀进程后 `GROUND_TORCH=True` 补渲剩余 44 帧:**459s = 10.4s/帧,11× 加速**。GPU 路径 = DB115-PRO fix#3,与 CPU 路径**数学位级同构、可混用**(前 48 帧 CPU + 后 44 帧 GPU 拼成完整 92 帧无缝)。

> **★铁律:量产 driver 必须默认 `GROUND_TORCH=True`。`BAND_TORCH` 与 `GROUND_TORCH` 是两个独立开关,开一个不等于开另一个。** 这是本 session 最贵的一条工程税。

### 8.4 级联 composite — 四件套门控(残洞 0.00%)

- **总 egozone:** **457.3 万 px**。
- **Tier1 门控反投影 fill:** **37.5%(171.5 万 px)**。
- **Tier2 world-BEV:** **62.5%(285.8 万 px)**。
- **残洞:** **0.00%(0 px)→ ProPainter 直接跳过,零黑洞。**
- **四件套 = `egozone ∩ fill 非黑 ∩ ~faithfill`(Gate1)∩ `Laplacian 局部方差>40, win15, open/close7`(Gate2)+ 孤岛 <400px 净化。**

> **与 §4(cd22abca)的口径差(不矛盾):** cd22abca 报的"**99.9% wbev**"是 **Tier1 之后残洞里** wbev 的占比;DB-125 的 **37.5 / 62.5** 是**整个 egozone 的三层分工**(Tier1 完整四件套**先吃** 37.5%,剩下交 Tier2)。两个数字统计的分母不同,互不冲突。

### 8.5 产物与眼核判决

- **产物 Drive:** `datasets/av2_1plus92_cascade_v1/02678d04/`(93 帧 PNG:`fr_0000`=frame1 v8 + 92 band;mask 孪生 93 张:frame1 全白 / band=非黑;H.264 mp4;`ledger.json`;`sample_sheet.jpg`)。
- **眼核判决:** frame-1 **完美**(蓝天自然 / 地面无洞 / 无车头);band 92 帧**零黑洞、格式标准、时序连续**。**已知瑕疵 = 填充区轻微色调差(seam feather 待办)**。

### 8.6 端到端墙钟与纯净重跑估算

- **本次端到端墙钟(含试错,主要是 §8.3 的 GROUND_TORCH 事故):** ~3 小时。
- **纯净重跑估算:** **≈ 1 小时 / log 单卡 A100** = band 5min + cand 13min + fill 16min(+ map **并行**不额外计)+ wbev 25min + FLUX 12min + 打包 5min。

### 8.7 新踩坑(入踩雷清单)

1. **pkill 自匹配自杀:** `pkill` 的模式字符串**出现在它自身的 bash 命令行里** → pkill 杀死了自己所在的进程,后续 pkill 不执行、目标进程反而存活。**修法 = 用字符类打断自匹配:** `pkill -f 'name[.]py'`(把 `name.py` 写成 `name[.]py`,自身命令行含 `[.]` 不含 `.py` 字面就不自杀)。
2. **`GROUND_TORCH` 默认 `False`,worker rep 必须显式开:** `BAND_TORCH ≠ GROUND_TORCH`,两个独立开关(见 §8.3)。
3. **PowerShell 5.1 `-Encoding utf8` 写 JSON 带 BOM:** 会让 python `json.loads` 崩(BOM 前缀非法)。**凭据 / JSON 文件改用 bash `printf` 写**(不带 BOM)。

### 8.8 本 session 脚本清单(已拷入 `agent/db115_drivers/`)

| 脚本 | 作用 |
|---|---|
| `db125_setup.py` | fresh VM 装树(8-edit + ProPainter + s5cmd + 场景清单) |
| `db125_bandgate.py` | worker 推送 + band-gate(off + EGO_IMG_MASK + EGO_BLACK) |
| `db125_cand.py` | 10 候选选帧 + imperfect 输出 + frame-1 选定 |
| `db125_orchestrator.py` | 全链(fill / map / wbev / composite / FLUX / 打包) |
| `db125_resume.py` | GPU fill 补渲(GROUND_TORCH=True) |
| `db125_resume_orch2.py` | 续跑版 orchestrator |

> 注:scratchpad 另有一件 `db125_resume_orch.py`(被 orch2 取代的中间版),未纳入本清单。

---

## 9. 车头痕迹修复(composite v3 定稿)+ wbev sampler 加速判负 + 加速诚实蓝图(2026-07-13)

> **一句话:** DB-125 数据集(§8)产出后,用户圈出 frame-1 车头区**三处红框痕迹**,本轮把 composite 从 v2/v3 迭代到定稿(眼核过);同时探索"wbev sampler 旁路加速"路线,**三轮质量判负**(与 DiffuEraser/Wan"全图重生成"判负**同构**);最后给出**修正版加速诚实蓝图**——单卡底线 30–36min/log,wbev 下限由**内核内部 `CAP_ONLY` 开关**决定,而非旁路 sampler。全部实测 + 眼核,事实不软化。

### 9.1 车头痕迹修复 — 分层解剖诊断(三个红框 = 三个不同的病)

用户红框三处并非同一病因。逐区解剖(诊断图 `db125_diag.jpg`):

| 红框 | 病灶 | 根因 | 处置 |
|---|---|---|---|
| **填充区(通用)** | 三层色调不齐 + fill 源紫灰 smear | band 直采**亮** / Tier1 fill **暖** / Tier2 wbev **灰** 三层未对齐;fill 源紫灰 smear **有纹理、清晰门(Gate2)拦不住**(缺色调门) | v3 三件套修复(见 9.2) |
| **M 框(中央)** | 暗色大片 | band 直采层的**真实车影**(egozone **外**,诚实内容) | **不动**(shadow removal 另议) |
| **R 框左上** | 蓝色块 | 多相机**白平衡竖色块**(band 直采层老遗留) | **独立项**(不在本轮 composite 范围) |

> **关键认知:** 只有"填充区"这一处是本轮 composite 能修的;M 框车影是**真实内容**、R 框蓝块是**白平衡遗留**,两者都**不是 composite 的病**,强行动它们会伤真值。

### 9.2 v2 失败 → v3 定稿(composite 三件套)

**v2 失败(两条根因):**
1. **feather 用 `blur(band)` 混合边界** → band 的 `EGO_BLACK` 黑区被晕染进边界 → 填充区周围**一圈暗带**。
2. **色调门 `dcol>75` 太松** → 仅移 5,298 px,紫灰 smear 基本没被换掉。

**v3 定稿(三件套,眼核过):**
1. **per-frame 3 通道 gain 对齐** — 用 zone 外 **21px 环带** band 中值 / 填充源中值,`clip 0.8–1.3` → 把 Tier1/Tier2 填充层的整体色调拉到 band 直采层。
2. **色调门收紧 `dcol>45`** — 判据 = fill 与 wbev 的**低频(`blur31`)色差** → 移 **93,962 px** 给 wbev(把紫灰 smear 换成连续 wbev)。
3. **feather 改 tempo 自模糊** — `GaussianBlur 9×9` **只作用边界带**、源是填充结果自身(**无黑源**),根除 v2 的暗带。

**v3 分工(微调后):** Tier1 **35.4%** + Tier2 **64.6%** + 残洞 **0%**。

**眼核判决(`db125_v3cmp.jpg`):** M 框色调拉齐、R 框不一致的 fill 亮带换成连续 wbev、L 框无暗边。**v3 碾压 v2/v5。**

**v3 产物(已推为 Drive 主产物):**
- **Drive:** `datasets/av2_1plus92_cascade_v1/02678d04/`(`frames/` + `masks/` + clip mp4 **= v3**;旧 v1 移入同目录 `v1_backup/`;对比图 `db125_v3cmp.jpg` 同目录)。
- **遗留(独立项,不在本轮):** 蓝色块(白平衡)、车影(真实内容,shadow removal 另议)。

### 9.3 wbev sampler 加速路线 — 34s 速度上限实证 + 质量三轮判负(重要负结果)

**动机:** wbev 93 帧渲染 25min 是全链**最大项**;级联只用 **egozone 内**的 map 像素 → 能不能写一个**独立采样器**(sampler)只采 egozone、跳过整套渲染?

**实现(内核逐行同构):** 独立 sampler 复刻内核几何 —— `erp_dirs` 公式 / C=7 相机中心均值 `[1.3613,-0.0007,1.3958]` / LiDAR KDTree 3-iter march(单 sweep 近似)/ `fg_occ` footprint 门 / map 双线性;与内核 `line 1243-1265 + 1943-1952` 逐行对照。脚本 scratchpad `db125_wbs.py`。

**速度(上限存在):** **0.37s/帧,92 帧 34s**(vs 渲染 16s/帧,**43×**)。

**质量三轮判负:**

1. **raw 对拍(a052):** coverage 双 100% 但 `diff med=45 / p95=171`。**根因① = 内核 cap 有 GLOBAL cast correction**(`db89 line 2224-2246`:map 像素全局 gain 拉到 anchor band 底部 truth-ring 中值,去掠射天空反射偏色),sampler **没有** → 偏暗紫。
2. **成品级对拍 v4:** `resid 126 万 px(27.7%)` 暴露 —— 渲染版 `resid=0` 的真相 = 内核对 cap **几何门(`dz<-0.08 / t<30`)外**的像素做了 **NS-inpaint / plate 兜底**,并非 map 真实像素;渲染帧 zone 内还混有**主投影直采**。眼核:sampler 版**网状 speckle 斑驳**(缺内核的 resolution-matched low-pass)。
3. **v5(补 cast gain + GaussianBlur7 低通 + Telea 兜底):更糟** —— 126 万 px 的 Telea 大区成**毛玻璃**,低通**杀纹理**。眼核 **v3 碾压 v5**。

**判决:** 旁路重写内核 cap 管线 = **维护一个永远追不平的、劣化的第二实现**。这与 **DiffuEraser / Wan "全图重生成" 判负同构** —— 绕过打磨过的管线必付质量债。渲染版 `resid=0` 不是"map 全覆盖",而是内核 cap 管线(cast correction + 几何门外 NS-inpaint/plate 兜底 + resolution-matched low-pass)在兜底,sampler 复刻不出这套打磨。**加速必须在内核内部做,不能旁路。**

**sampler 中间产物:** `/content/db125_wbs/`、`db125_v4cmp.jpg`、`db125_v5cmp.jpg`(**VM 本地,未上 Drive**)。

### 9.4 加速诚实蓝图(修正版,单卡 A100 12 核)

| 阶段 | 现状 | 近期刀(已证 / 待做) | 预期 |
|---|---|---|---|
| band-gate 157 帧 | 8min | 两段式粗探针 + fine 93(待做) | 6min |
| cand 10 帧 | 13min | `GROUND_TORCH`(**已证 11×**) | 2min |
| fill 92 帧 | 16min(GPU 已开) | 定向 fill 只扫 egozone(待做,**90% 算力浪费实证**) | 8min |
| map 构建 | 与 fill 并行 | 保持 | 隐藏 |
| wbev 93 帧 | 25min | **`CAP_ONLY` 内核开关**(跳过主投影 / OMC / seg 段,cap 管线**原样保留**——正道,待做,内核手术需谨慎) | 12min |
| FLUX | 12min(Drive 冷读 613s) | cache 拷本地 SSD / 批量常驻 | 3min |
| composite + 打包 | 5min | 保持 | 5min |

- **单卡诚实底线:~30–36min/log**(部分并行)。
- **2–10min 需 DB-115 跨机 fleet**(4 机流水吞吐 ~8–10min/log)。
- **sampler 34s 路线判负后,wbev 下限由内核 `CAP_ONLY` 开关决定** —— 即在内核渲染循环里跳过主投影 / OMC / seg 段、**cap 管线一字不改**,与旁路 sampler 的本质区别是"**手术在内核内部、复用打磨好的 cap 管线**",故不付质量债。`CAP_ONLY` 内核手术需谨慎(与 `WORLDBEV_CENTER` 同级的内核改动纪律:备份 → edit → 断言引用数 → 空开关 byte-identical 验证)。

### 9.5 本轮产物与脚本索引

| 产物 | 位置 | 说明 |
|---|---|---|
| v3 数据集(主产物) | Drive `datasets/av2_1plus92_cascade_v1/02678d04/` | `frames/` + `masks/` + clip mp4 = **v3**;`v1_backup/` = 旧 v1 |
| `db125_diag.jpg` | Drive 同目录 | 三红框分层解剖诊断图 |
| `db125_v3cmp.jpg` | Drive 同目录 | v3 vs v1 对比(眼核判决图) |
| `db125_wbs.py` | scratchpad | wbev 独立 sampler(判负,内核逐行同构复刻) |
| `db125_v4cmp.jpg` / `db125_v5cmp.jpg` | VM `/content/db125_wbs/`(未上 Drive) | sampler v4/v5 判负证据 |

---

## 10. CAP-fast 内核 v9 手术与实测大捷(DB-126,2026-07-13)

> **一句话:** §9.4 加速诚实蓝图预告"wbev 下限由内核 `CAP_ONLY` 开关决定、加速必须在内核内部做",本役(加速专项第二役)**就是这刀的落地**。给内核加**三个默认关闭的开关**,让 fill / wbev 渲染只算 nadir cap 那一段、复用打磨好的 cap 管线,**全部实测 + 眼核**:**fill 2.4s/帧(42×)、wbev 3.8s/帧(260×)、成品级 zone diff med=4.0(数值噪声级)、眼核三 crop 肉眼无差 → CAP-fast v9 定稿。** fill+wbev 从 41min 压到 9.4min,map 构建 990s 成为单卡新瓶颈。**本役干净:5 锚点一次全中、A/B 一次通过、无新踩坑。**

### 10.1 为什么在内核内部做(sampler 判负的直接教训落地)

§9.3 的 wbev **旁路 sampler** 虽有 43× 速度上限,但三轮质量判负——旁路重写内核 cap 管线 = 维护一个永远追不平的、劣化的第二实现(与 DiffuEraser/Wan"全图重生成"判负同构)。§9.4 由此定论:**加速正道 = 在内核渲染循环里跳过主投影 / OMC / seg 段,而 cap 管线(cast 校正 + 几何门外 NS-inpaint/plate 兜底 + resolution-matched low-pass)一字不改**。本 §10 就是把这句话变成三处开关。**本质区别与旁路 sampler:手术在内核内部、复用同一套打磨过的 cap 像素路径,故不付质量债。**

### 10.2 内核 v9 三开关(默认全关 = 已发布路径零影响)

内核 `scripts/phase3/db89_ghost_recovery.py`,三个新常量**默认 `False` / 空字符串 = 已发布量产路径 byte-identical、零影响**。手术纪律与 `WORLDBEV_CENTER`(§4.2)同级:本地备份 `scripts/phase3/_backup_db115pro/db89_ghost_recovery_20260713_v9_pre_caponly.py` → 本地 5 处文本 edit → A100 `w2p_ego` 树同步 patch → **md5 断言 `fcbc077ead935e487ff940419ba7c964`**。

| 开关 | 默认 | 作用 | 机制 |
|---|---|---|---|
| **`CAP_ONLY`** | `False` | fill/wbev 渲染**只算 nadir cap**,跳过主投影侧 | 条件化 import + 循环内 continue 跳过 YOLO seg;OMC 对象匹配 `for uid in (sorted(moving) if not CAP_ONLY else [])` 空跑;morph/view-morph 因 `morph_jobs` 空**自然归零**;**cap 管线(cast 校正 / 低通 / resid 兜底)原样保留** |
| **`CAP_LIMIT_TMPL`** | `""` | 把 cap 候选扫描**限到 egozone 条带**,省 75–85% 扫描点 | printf 模板(anchor `%03d`)glob 外部 mask,AND 进 nadir cap;band 帧只需 egozone 条带,无谓的全 nadir 点不再扫 |
| **`CAP_REF_TMPL`** | `""` | `CAP_ONLY` 下重建 cast 校正的 truth-ring 参照 | `CAP_ONLY` 时 comp 全黑,cast 校正的 truth-ring **自我参照失效** → 从外部 band segcomposite 重建 `ring_px`,cast 校正照常生效 |

**手术方式:** 5 处锚点唯一的文本 edit(常量声明 / seg 两处 / OMC 一处 / CAP_LIMIT 一处 / CAP_REF 一处),**避免大段缩进重构**——这是"内核手术需谨慎"纪律的具体执行:锚点唯一、改动最小、可逐处断言。

> **⚠️ 三开关联动语义:** `CAP_ONLY=True` 单开会让 comp 全黑 → cast 校正 truth-ring 失效,**必须同时给 `CAP_REF_TMPL`**(否则 cast 偏色);`CAP_LIMIT_TMPL` 是纯性能优化(限扫描域),不给也正确、只是慢。三者是"正确性(CAP_ONLY+CAP_REF)+ 性能(CAP_LIMIT)"的组合。

### 10.3 量产 worker 用法(级联 fast 定式)

- **fill worker:** `CAP_ONLY=True` + `CAP_LIMIT_TMPL`=egozone 模板 + `CAP_REF_TMPL`=band segcomposite 模板 + `FAITH_MASK=True` + `capg|=egoproj` + `GROUND_TORCH=True`。**`GROUND_RESID` 用默认 plate 即可**——faithfill mask 语义不变、合成四件套的 Gate1(`~faithfill`)照样把 plate/inpaint 拒掉,不影响成品。
- **wbev worker:** 同 common 三开关(`CAP_ONLY` / `CAP_LIMIT_TMPL` / `CAP_REF_TMPL`)+ worldbev 四件(`GROUND_MODE="worldbev"` / `WORLDBEV_WIN` / `WORLDBEV_FILL` / `WORLDBEV_CENTER`)。

### 10.4 三级验证(全部实测 + 眼核)

**① 单帧 A/B(`a052`):**

| 路 | 耗时(含 ~10s 启动开销) | 覆盖 | zone diff |
|---|---|---|---|
| fill-fast | **18.2s** | 100% | med=16 / p95=65(zone 边缘主投影直采归属变化) |
| wbev-fast | **26.8s** | 100% | med=21 / p95=29 |

wbev 的 **p95≈med = 纯全局色调偏移**(非局部结构差),来源 = cast 参照从 uint8 band 重建的细微差,**预判会被 composite 的 gain 对齐(§9.2 v3 三件套)吸收**。

**② 全量(91 帧 fill + 92 帧 wbev,8-worker):**

| 阶段 | fast v9 实测 | 对比基线 | 加速比 |
|---|---|---|---|
| **fill 91 帧** | **216s = 2.4s/帧** | CPU 事故版 100s/帧(§8.3) | **42×** |
| | | GPU 全 nadir 10.4s/帧(§8.2) | **4.3×** |
| **wbev 92 帧** | **349s = 3.8s/帧** | 整帧渲染 16s/帧(§8.2) | **4.2×** |
| | | per-anchor 建图 990s/帧(§4.3) | **260×** |
| **fill+wbev 合计** | **9.4min** | 此前 41min | **4.4×** |

**③ 成品级对拍(`v6` = fast 层过 §9.2 v3 composite vs `out3`):** **zone diff med=4.0 / p95=31 / mean=8.5 = 数值噪声级**(§9.3 sampler 判负版是 med=47,相差一个量级);**眼核三 crop 肉眼无差**。**CAP-fast v9 定稿。**

> **与 sampler 判负的对照铁证:** 同样是"只算 egozone",旁路 sampler 成品级 med=47(§9.3 v4)、眼核网状 speckle;内核内 CAP-fast med=4.0、眼核无差。差距的全部来源 = CAP-fast **复用了同一套 cap 管线**(cast 校正 + 兜底 + low-pass),sampler 复刻不出。这就是"手术在内核内部"与"旁路重写"的量化代价差。

### 10.5 新账本与新瓶颈

fill+wbev 从 41min 压到 **9.4min** 后,**map 构建 990s(16.5min)成为单卡关键路径的新瓶颈**(此前被 fill+wbev 掩盖)。

- **下一刀(正在验证):** map 帧 budget **110→60**(参数级,预计 ~540s,可被前段 fill/band 隐藏)。
- **更远期:** map 构建循环 GPU 化(同 §7.2 待办)。
- **单卡全链估算:** **~34min**(map 优化 + FLUX cache 本地化后);**多 log 流水吞吐 ~20min**;**2–10min 区间仍需 DB-115 跨机 fleet**(4 机 ~8min)。较 §9.4 的 30–36min 底线进一步收紧,且 wbev 下限已由 `CAP_ONLY` 实测坐实(不再是蓝图预测)。

### 10.6 产物与脚本

- **VM 中间产物:** `/content/db126_fill`、`/content/db126_wbev`、`/content/db126_out`(fast 成品 92 帧)、`db126_v6cmp.jpg`(成品级对拍图,VM 本地)。
- **数据集主产物:** **保持 §9.2 的 v3 不变**——fast 内核是**后续量产的加速引擎**,不改已定稿的 v3 成品语义。
- **内核:** `scripts/phase3/db89_ghost_recovery.py`(含 `CAP_ONLY` / `CAP_LIMIT_TMPL` / `CAP_REF_TMPL` 三开关,5 处 edit);备份 `_backup_db115pro/db89_ghost_recovery_20260713_v9_pre_caponly.py`;md5 `fcbc077ead935e487ff940419ba7c964`。

### 10.7 本役工程记录

- **新踩坑:无。** 本役干净:5 锚点一次全中、A/B 一次通过、成品级对拍一次达标。
- **纪律复盘:** 三开关全默认关 = 已发布路径 byte-identical;备份 → 5 edit → 同步 patch → md5 断言,严格复用 `WORLDBEV_CENTER`(§4.2)的内核手术定式。这是"能在内核内部手术就不旁路重写"原则的第二次成功应用(第一次 = `WORLDBEV_CENTER` map 共享)。

### 10.8 map60 参数刀收尾(新瓶颈落地,量产参数定稿)

§10.5 预告的 map budget 一刀已落地实测。

- **方法:** worker rep `"_aidx))[:110])" → "_aidx))[:60])"`——map 构建时间分桶采样 budget **110→60 帧**,**纯参数、零内核改动**(不动任何 cap 像素路径)。
- **结果:** **map 构建 990s→567s(1.75×)**。用 map60 跑 fast wbev 92 帧(349s 不变)+ §9.2 v3 composite → 与 v3(map110)成品对拍 **zone diff med=4.0 / p95=37 / mean=9.5**;与 map110-fast 的 med=4.0 / mean=8.5 **同级** = 60 帧 budget 的额外质量代价 **≈0**;**眼核三 crop 肉眼无差。map60 定为量产参数。**
- **更新后单卡账本(全实测):**
  - localize ~480s(∥ 后台,全隐藏)。
  - 中段:band-gate 472s + cand ~250s(`GROUND_TORCH`)+ fill 216s ≈ **938s**;**map 567s 与中段并行,全隐藏**(≤938s,不再是关键路径)。
  - 尾段:wbev 349s + composite 66s + FLUX ~200s(cache 本地化后)+ 打包 ~300s。
  - **单 log 串行 ≈ 35–39min;多 log 流水吞吐 ≈ 18–22min/log(单卡);fleet 4 机 ≈ 6–8min/log**(2–10min 区间的达成路径 = fleet)。
- **剩余待办(量产 driver v10):** ① 两段式 band-gate;② cand 开 `GROUND_TORCH` 实测;③ FLUX cache 本地化;④ DB-115 fleet 复活。

**§10 最终定论:** CAP-fast v9(内核内三开关)+ map60(budget 参数刀)= 单卡关键路径从 41min(fill+wbev 段)压到中段 ~938s 全隐藏 map;成品级 zone diff 稳定在 med=4.0(数值噪声级),质量零债。2–10min 区间仍靠 fleet。

---

## 11. 三场景盲测泛化验证(DB-127,2026-07-13)

> **一句话:** 北极星原则 = GENERAL 方法必须多场景实证。本役取 **3 个从未用过的 AV2 val 场景**做盲测,K=12 worker、全 fast 配方(§10 的 CAP-fast v9 + map60),端到端跑级联版 1+92 管线。**结果:1/3 过 93 帧门(全链产出 + 打包),2/3 被 band-gate 正确拒绝(clean run 65–66 帧 < 93);过门的 `05fa5048` 是重遮挡难场景(imperfect 620k,比 `02678d04` 大 17×),Tier3 ProPainter 首次真正扫尾(残洞 2.9%);级联三层分工随场景难度合理滑动 = generality 实证。** 全部实测,硬件同 §8–§10(A100 单卡 80GB / 12 核)。

### 11.1 目的与设置

- **目的:** 验证级联版 1+92 管线的 **generality / robustness**——此前历史场景 5/5 过存在**选择偏差**(都是已知可用的),盲测才是真正的泛化考。
- **盲测集:** 3 个从未在任何实验/量产用过的 AV2 val 场景 `04994d08` / `05fa5048` / `070bbf42`。
- **配方:** K=12 worker、全 fast 配方(§10 CAP-fast v9 三开关 + map60 参数)。

### 11.2 结果总表

| 场景 | band-gate clean run | 判决 | 关键数据 |
|---|---|---|---|
| `04994d08` | `[33,98]` len=**66** < 93 | **SKIP(gate 正确拒绝)** | band 1268s(localize 并发拖慢) |
| `070bbf42` | `[91,155]` len=**65** < 93 | **SKIP(gate 正确拒绝)** | band 1088s |
| `05fa5048` | `[32,138]` len=**107** ✓ | **全链产出 + 打包 ✓** | 见 §11.3 |

> **产出率初步数据(给 koi 的决策变量):** 盲测 **1/3 过 93 帧门**;两个被拒 log 的 clean run 均为 **65–66 帧** → **若下游接受 1+64 窗口,产出率大幅上升**。历史场景 5/5 过有选择偏差;**`PIPELINE_1plus92.md` §6"产出率受内容限制"的预言首次被盲测证实**——`SKIP` 不是失败而是 gate 诚实拒绝(连续干净帧不足 93 时不硬凑)。

### 11.3 `05fa5048` 全链详账(重遮挡难场景)

**难度:** frame-1 = `a041`,**imperfect = 620,364 px**,比 `02678d04` 的 36,756 **大 17 倍** —— fg_occ + 残洞盲区极大的重遮挡场景。

| 阶段 | 耗时 | 关键数据 |
|---|---|---|
| band-gate | **713s** | clean run `[32,138]` len=107 |
| cand(GROUND_TORCH) | **142s** | frame-1 = a041,imperfect=620,364 |
| fill(∥ map) | **294s** | 与 map 构建并行 |
| map 构建 | **841s** | 与 fill 并行 |
| wbev | **328s** | 共享 map fast 渲染 |
| 级联 composite | 见 §11.4 | zone=4,366,485 px |
| **Tier3 ProPainter** | **178s** | **首次真正实战扫尾**(补残洞 2.9%) |
| frame-1 FLUX 二次加载 | **仅 26s** | **page cache;613s 只是首次冷读** |
| 打包 | ✓ | Drive `datasets/av2_1plus92_cascade_v1/05fa5048/` |

> **★FLUX cache 顾虑消解:** §8 报的 FLUX 加载 613s 是**首次冷读**(Drive FUSE);本役二次加载 **26s**(OS page cache)。证明"FLUX 加载慢"是一次性冷启动税,量产批内共享进程后**不再是账本项**。

### 11.4 级联分工跨场景稳定性(generality 实证)

| 场景 | Tier1 门控反投影 | Tier2 world-BEV | 残洞 → Tier3 | zone 总量 |
|---|---|---|---|---|
| `02678d04`(§8/§9,简单) | 37.5% | 62.5% | **0.00%** | 457.3 万 px |
| `05fa5048`(本役,重遮挡) | **27.8%** | **69.3%** | **126,916 px(2.9%)** | 436.6 万 px |

- **分工随场景难度合理滑动:** 场景越难(遮挡越重),Tier1 真值反投影能吃的比例越低(37.5%→27.8%),Tier2 world-BEV 补覆盖顶上(62.5%→69.3%),最后极小残余交 Tier3 ProPainter(0%→2.9%)。**三层各就各位、按难度自动分配 = general 方法的实证**,而非只在单一场景 tuning。
- **★Tier3 兜底首次真正干活:** 此前 `02678d04` 残洞 0%、`cd22abca` 残洞 0.1%(§4),ProPainter 一直是"备而不用"的兜底保险;本役 2.9%(126,916 px)是**首次真正落到 Tier3 扫尾**——178s 补完,零黑洞产出。三级级联"兜底层存在的必要性"至此被难场景坐实。

### 11.5 眼核发现的 3 个改进点(简单场景暴露不了)

眼核帧 f001 / f046 / f092 + frame-1,均为难场景才暴露的问题:

1. **frame-1 天空白色球状伪影**(右上)—— FLUX sky outpaint 把天际线结构延伸成怪异白顶,与踩雷清单"树冠色斑"(`PIPELINE §7` / DB-116)**同类**。**处置:sky mask 需收紧**(已知类待修)。
2. **frame-1 地面 FLUX 补丁痕迹 + 整体偏灰** —— imperfect **620k** 的重遮挡场景**盲区太大**,F 环"盲区必须小"原则被突破。**这是选帧已最优下的场景上限**(诚实记录,非算法 bug)——盲区大到一定程度,生成层无论如何填都留痕。
3. **★band 内非车头黑洞未纳入级联管辖(DB-127 最重要工程发现):** f046 / f092 有菱形小黑块 = **band 自身的无源洞**(多相机拼接盲区),它们在 **egozone 外**,composite 末尾的 `out[~(zone|bnz)]=0` 把它们保持黑。**改进方向:级联管辖域 zone → zone ∪ (band 下半球黑洞),三层(反投影 / world-BEV / ProPainter)直接复用**——现有三级级联天然能吃这类洞,只是管辖域没圈进去。

### 11.6 工程细账

1. **orchestrator import 顺序 bug:** `waymo2panorama` 的 import 写在 `sys.path` 注入**之前** → `05fa5048` 首跑烧掉(渲染层已完成但 centre 计算崩)。**修法 = `db127_resume.py`**(把三处 `sys.path.insert` 提到 import 之前),resume 复用已完成的 band/fill/wbev 渲染层,**零渲染浪费**。
2. **band 渲染与 localize 并发抢 CPU:** band-gate 渲染与后台 s5cmd localize 下载**并发抢 12 核** → band 从 §8 的 472s 拖慢到 **713–1268s**。**量产 driver 要错峰**(localize 完再启 band,或限 localize 并发度)。
3. **12w band 不如 8w?** 存疑:1268s 的样本受 localize 下载干扰,**未定论**——12-worker 理论应快于 8-worker,但本役被 I/O 争用掩盖,需干净复测。

### 11.7 产物与脚本

- **数据集产物:** Drive `datasets/av2_1plus92_cascade_v1/05fa5048/`(93 帧 PNG + mask 孪生 93 张 + H.264 mp4 + `ledger.json` + `sample_sheet.jpg`,同 §8 格式)。
- **脚本(已拷入 `agent/db115_drivers/`,安检无凭据):**

| 脚本 | 作用 |
|---|---|
| `db127_general.py` | 3 场景盲测 orchestrator(per-log:localize+egomask → band-gate K=12 → SKIP if <93 → cand → map∥fill → wbev → composite v3 +PP → 批量 FLUX + 打包) |
| `db127_resume.py` | 修复续跑版(fixed-import centre → wbev → composite → PP → 批量 FLUX → 打包,复用已完成渲染层) |

**§11 最终定论:** 级联版 1+92 管线通过**盲测泛化考**——三层分工随场景难度自动滑动(简单 37.5/62.5/0% → 重遮挡 27.8/69.3/2.9%),Tier3 ProPainter 在难场景首次真正扫尾坐实兜底必要性,generality 得实证。产出率受内容限制被首次盲测证实(1/3 过 93 门,1+64 窗口是产出率放大的决策变量)。最重要工程发现 = **band 自身无源黑洞应纳入级联管辖域(zone → zone ∪ band 黑洞)**。凭据零泄露。

---

## 12. `05fa5048` 复杂场景四问题解剖与 composite v6 修复(DB-128,2026-07-13/14)

> **一句话:** §11 盲测中唯一过 93 门的重遮挡难场景 `05fa5048` 成品(f050)被用户标注**四处问题**;本役做**第一性诊断 → 七层分色解剖 → v4/v5/v6 三轮迭代**,把 composite 升到 **v6 四新门定稿**(**内核零改动、全在 composite 层**),四问题**眼核逐一消除**;并首次坐实一条第一性物理边界——**湿路面镜面反射是视角相关的、朗伯假设失效 = 雨天场景补洞的物理天花板(非 bug)**。硬件 = **新 A100-40GB**(注意**非** §8–§11 的 80GB,ProPainter 参数因此重定式)。全部实测 + 眼核,事实不软化。

### 12.1 用户四问题标注(`05fa5048` 成品 f050)

用户判断复杂场景 `05fa5048` 成品**不如 `02678d04`**,圈出四处问题,要求第一性诊断修复:

| 标注 | 位置 | 现象 |
|---|---|---|
| **① ③** | 左右车头区 | 奇怪的模糊纹理(两处) |
| **②** | 中央车头区 | 雨后反射样暗块 |
| **④ ×2** | **非车头区** | 黑色空洞(两处) |

### 12.2 七层解剖判决(分色图 a091)

把 `05fa5048` 的中间层逐层染色叠图(a091,七层:**band / egozone / fill / faithfill / sharpgate / wbev / T1T2resid**),对四问题逐一定位根因:

- **④ 黑洞 → 纯管辖域缺陷:** `zone=0.0%`——egozone **完全不覆盖**这两个黑洞。**但 fill 层和 wbev 层的内容都有!** 两个 Tier 拿着答案,却因洞落在 egozone 外没处填。这正是 §11.5 DB-127 "band 自身无源黑洞未纳入级联管辖"最重要工程发现在 `05fa5048` 上的**实例**。
- **① ③ 模糊纹理 → 湿路面镜面 + fill 白斑:** T1/T2 红绿**犬牙交错碎斑** = 像素级两源混拼(湿路面让门控 mask 碎片化)+ R3 区有 fill 带入的**白色反光斑**。**第一性根因 = 湿路面镜面反射视角相关,邻帧反投影的高光在本帧视角下位置就是错的(朗伯假设失效)**——雨天场景的物理,非 bug(见 §12.3)。
- **② 暗块 → faithfill 拒 + 全帧单 gain 不足:** fill 层几乎全被 faithfill 拒(T1 仅 **2%**),T2 map 主导;暗色 = 卡车阴影 + 湿路面**真实暗内容**(**部分诚实**)+ 全帧单一 gain 对**湿/干混合区**对齐不足。

### 12.3 ★第一性发现:湿路面镜面反射 = 朗伯假设失效(补洞物理边界)

级联的 Tier1 门控时间反投影,隐含**朗伯假设**——同一地面点从不同视角看颜色一致,故邻帧拍到的像素反投影回本帧成立。**湿路面镜面反射打破这一假设**:高光是**视角相关**的,邻帧那束反射在本帧相机视角下的位置**根本不同**,反投影把"错位的高光"贴进本帧 → 表现为 ①③ 的碎斑与白色反光斑。**这是雨天场景补洞的物理天花板,不是算法 bug**;composite 层只能**检测并拒源**(§12.5 HSV 镜面门),根治需**镜面/漫反射分解**(学术级待办)。

### 12.4 v4 → v5 → v6 迭代史(三轮,全眼核)

| 版本 | 改动 | 判决 |
|---|---|---|
| **v4** | 管辖域扩展首版:`zone2 = zone ∪ band 内容区黑洞` | **误吃 band 右边缘**(黑连通域无过滤,把 band 边缘线当成洞)→ 需过滤 |
| **v5** | ① zone2 加过滤(面积 + 不触底边线);② 镜面门用 **lum-vs-ring**(亮度 vs 环带) | 镜面门**仅移 0.3% 像素 = 判无效**(lum-vs-ring 检测不到镜面高光) |
| **v6** | 镜面门直接换 **HSV 检测**(命中);连贯化 + per-洞 gain 补齐 | **四门定稿,眼核四问题逐一消除** |

### 12.5 composite v6 四新门定稿(内核零改动,全在 composite 层)

1. **管辖域扩展(治 ④):** `zone2 = zone ∪ band 内容区黑洞`——逐列取内容底边,底边**之内**的黑连通域,面积 **200–30000 px** 且**不触底边线**(v4 无过滤误吃 band 右边缘,v5 加过滤修正)。落地 §11.5 DB-127 "zone → zone ∪ band 黑洞"最重要工程发现。
2. **HSV 镜面门(治 ①③ 白斑):** `V>150 & S<70`(dilate 9)**双 Tier 拒源** → 打成 resid → 交 **ProPainter 时序传播兜底**。(v5 的 lum-vs-ring 门仅移 0.3% 判无效,v6 直接 HSV 检测命中。)
3. **mask 连贯化(治 ①③ 碎斑):** ok 形态学 **open7 + close11**、孤岛阈值 **400 → 2000 px**(消犬牙交错)。
4. **per-洞区独立 gain(治 ② 边界):** 每个 zone2 连通域用**自己 21px 邻边 band ring 中值**对齐(`clip 0.75–1.35`),**替代全帧单一 gain**(全帧单 gain 对湿/干混合区对齐不足 = ② 暗块的部分成因)。

### 12.6 全量 92 帧统计与眼核验收

- **zone2 = 459.6 万 px**(管辖域扩展**吃进 band 洞**,比 §11.4 的纯 egozone 4,366,485 px 大);
- **Tier1 17.0% + Tier2 75.0% + resid 8.1%(37.1 万 px,交 ProPainter)**。
- **眼核验收(`db128_final_cmp.jpg`):**
  - **④ 消失**(纹理连续、黄线延续);
  - **③ 白斑消灭**;
  - **① 碎斑连贯化**;
  - **② 边界过渡自然**(中央暗色 = **诚实内容保留**,不强修)。

> **与 §11.4 分工对照(不矛盾):** §11.4 报 `05fa5048` 的 Tier1 27.8 / Tier2 69.3 / resid 2.9% 是**纯 egozone(v3 配方)**的分工;§12.6 的 17.0 / 75.0 / 8.1% 分母是**扩展后的 zone2(含 band 黑洞)**,且 HSV 镜面门把更多像素打进 resid → resid 从 2.9% 升到 8.1%。两者分母与门控不同,互不冲突。

### 12.7 ★40GB 卡 ProPainter 参数定式(新踩坑)

**新 A100 是 40GB(非 §8–§11 的 80GB)**,PP 显存预算减半,踩坑重定式:

- 92 帧 2048×1024 **直跑 OOM**(80G 卡可以);
- `subvideo_length 30` **仍 OOM**;
- **★定式 = `--subvideo_length 15 --neighbor_length 5` + env `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`,172s 跑通。**

### 12.8 产物

- **修复版数据集(主产物):** Drive `datasets/av2_1plus92_cascade_v1/05fa5048/`——**92 band = v6 + PP**,frame-1 **复用原 v8**;原版移入同目录 `v1_backup/`;对比图 `db128_final_cmp.jpg` 同目录。
- **中间层与七层解剖图:** VM `/content/db128*`(**随 runtime 消失**);本地 scratchpad `db128_diag.jpg` / `db128_v4cmp.jpg` / `db128_v5cmp.jpg` / `db128_v6cmp.jpg`。
- **脚本:** `agent/db115_drivers/db128_setup.py`(fresh VM **三段 patch 链**装 v9 树 = 6+2+5 edits、md5 `fcbc077e` + 层重建;本役已拷入)。

### 12.9 遗留(诚实)

1. **①③ 残余轻度湿路纹理不齐** ——**镜面/漫反射分解才能根治**(§12.3 朗伯失效物理边界,学术级待办)。
2. **② 中央暗色 = 真实内容,不修**(shadow removal 另议)。
3. **composite v6 尚未回补 `02678d04`** ——该场景无这些问题(简单场景),配方版本差异已记录。
4. **v6 四门未回写进 `db127_general.py` 量产 orchestrator** ——量产 driver v10 时合并。

### 12.10 v7b 中央车头纹理断层修复 + 频率解剖反直觉判决(2026-07-14,用户二次标注）

v6 交付后用户**放大 `05fa5048` 修复版**发现新问题:**中央车头填充区纹理与前方道路明显不同**——暗色涂抹感 + **字迹状鬼影**(PP 时序传播把邻帧文字/车道线残影拖进来)。

- **★频率解剖反直觉判决(准备好的增强被判错药)**:先入为主的诊断是"填充区糊了、要锐化+加噪回补高频"。但实测填充区 **HF std = 6.28**,**超过** band 参照 **5.80**——**问题根本不是高频『量』不足,而是高频的『质』**:patch 接缝 / 多源杂斑 / PP 涂抹。**根源 = map 5cm cell 在近 nadir ERP 放大下欠采样**(每个世界 cell 被拉伸成一大片 ERP 像素,拼接缝与杂斑被放大)。→ **已写好的 unsharp + grain 增强当场判为错药弃用**;正确的药是**摄影级近场虚化**(edge-preserving 平滑填充区 + 亚阈值反光追杀)。**"眼睛胜过指标"再一次应验**(HF std 更高却更丑)。
- **★三方眼核(current / clean-blur / honest-black)**:①`current` = v6+PP 原样(字迹鬼影 + 涂抹);②`clean-blur` = 本役 v7b(填充区摄影级虚化);③`honest-black` = 车头区**回归黑**(= Cosmos 生成域语义,把这块交给下游生成模型)。**判决 = clean-blur 胜出**(字迹鬼影消失、近场虚化自然、暗涂抹平顺);**honest-black 记为给 koi 的语义选项**(若下游 Cosmos 约定"黑=生成域",车头填充可整块留黑交模型,而非我们补真值)。
- **★v7b 定稿配方(PP 之后的后处理,`clean_blur`)**:① 亚阈值反光追杀 = HSV `V>135 & S<80`(比 v6 主门 `V>150 & S<70` 更严的亚阈值,dilate7)→ Telea inpaint(7)去掉 v6 主门漏网的湿路面反光小斑;② `bilateralFilter(11,45,9)` + `GaussianBlur(5,5)` **只作用于填充区**(`filled` = 下半球 ∩ 非黑 ∩ 非 band 直采区);③ **6px feather**(distanceTransform / 6.0 的 alpha 混合,边界带渐入,绝不 `blur(band)` —— v2 黑区晕染教训)。**92 帧 45s**;Drive 已**重打包**(v6 版移入 `v6_backup/`)。
- **★已集成进正式模块** `agent/db115_drivers/db128_composite.py`(`compose_frame` + `clean_blur` 两函数,module docstring 含 v1→v7b 判决史;`clean_blur` 自带频率解剖 docstring:HF std 6.3 vs 5.8、"sharpening is the WRONG drug (measured)")。
- **★git commit 记录(纪律修正)**:本仓库 `experiments/Waymo2Panorama/.git` **完好可用**(remote = github QiPan-Ronnie/Waymo2Panorama,push main 成功;旧情报"本地 git 被 BaiduSync 弄坏"已过时——**外层 koi chen 目录无 git,Waymo2Panorama 子仓库健康**)。两个 commit 已推 main:`0f7ebd4`(DB-123..128 全部 driver + v9 内核 + 备份 + 文档证据)+ `82dbfbe`(v7b `clean_blur` 模块)。**新纪律 = 每轮代码改进即 commit + push main**(feedback 已有直推授权)。

---

*记录完成:2026-07-12(§1–§7)/ 2026-07-13 补(§8 DB-125 级联版 1+92 数据集全链量产 + §9 车头痕迹修复 v3 定稿 + wbev sampler 判负 + 加速诚实蓝图 + §10 CAP-fast 内核 v9 手术与实测大捷 + §11 DB-127 三场景盲测泛化验证)/ 2026-07-14 补(§12 DB-128 `05fa5048` 复杂场景四问题解剖 + composite v6 四新门定稿 + 湿路面朗伯失效物理边界 + 40GB 卡 PP 定式)。两个实验(§3/§4)均已眼核 + 归档;六方法版图闭合;④ 档三级级联证据链完整,并在新场景 `02678d04` 端到端量产实证(残洞 0.00%);composite v3 定稿(gain 对齐 / 色调门 45 / 自模糊 feather)已推为 Drive 主产物;wbev sampler 旁路加速三轮判负(与 DiffuEraser/Wan 同构),加速正道 = 内核内 `CAP_ONLY`;§10 内核 v9 三开关实测 fill 2.4s/帧(42×)、wbev 3.8s/帧(260×)、成品级 zone diff med=4.0(vs sampler med=47),CAP-fast v9 定稿;**§11 盲测泛化验证 1/3 过 93 门(重遮挡 `05fa5048` 全链产出、Tier3 首次实战 2.9%)、级联分工随难度合理滑动、产出率受内容限制被证实、最重要工程发现 = band 无源黑洞纳入级联管辖(zone → zone ∪ band 黑洞)**。凭据零泄露。*
