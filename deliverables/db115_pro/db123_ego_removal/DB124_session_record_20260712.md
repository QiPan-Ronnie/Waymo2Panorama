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

## 13. ★终局:ego 轨迹带物理判决与 v8 PP-full-zone 定稿(DB-128,2026-07-14 用户否决 v7b 后第一性重分析)

> **一句话:** §12.10 的 v7b(clean-blur)交付后被用户否决——放大看**左右两侧模糊感甚至超过 v6 = 净退化**;用户要求"先底层分析、别急着糊"。本役做**第一性物理重分析**,坐实一条比 §12.3 湿路面更根本的边界:**左中右三个车头区 = ego 自遮挡"轨迹带",AV2 front-pod rig 物理上永远无法近距垂直观测自己的足迹**,fill/wbev 在这条带上拿到的**全是掠射拉伸的低分辨率像素**——v6 的杂纹与 v7b 的糊块只是**同一物理事实的两种掩饰**。据此立三选项实验,**A(轨迹带全交 ProPainter)实验胜出 → v8 PP-full-zone 定稿**(全帧眼核几乎看不出填充痕迹 + 时序三帧条穿带标线连续)。全部实测 + 眼核,事实不软化。

### 13.1 用户否决 v7b(净退化,要求先底层分析)

v7b(§12.10 clean-blur,摄影级近场虚化)本以为是中央车头纹理断层的解药,但用户放大 `05fa5048` 修复版判决:**左右两侧(①③ 区)模糊感甚至超过 v6**——虚化把本已勉强的低质真实像素抹得更没细节,**净退化**。用户明确要求:**先做底层物理分析,不要再在 composite 层堆掩饰手段**。这一否决直接推翻"composite 层还能救"的隐含假设,逼出本役的第一性重分析。

### 13.2 ★第一性物理判决:左中右三车头区 = ego 自遮挡轨迹带(本轮核心发现)

三个车头区不是普通洞区,而是 **ego 自遮挡的"轨迹带"(ego trajectory footprint)**:

- **物理成因:** AV2 是 **front-pod 相机装配**(7 目全在车顶前舱),相机**永远无法近距垂直俯视自己的车身足迹**。这条带唯一被采到的时机 = **车开远之后、以 4–6° 掠射角回望**(掠射角度已由 DB-109 ground-fill physics 坐实)。
- **掠射的致命放大:** 掠射下**垂直方向 1px 误差 = 水平方向米级拉伸**。所以 fill(Tier1 时间反投影)和 wbev(Tier2 world-BEV)在这条带上**拿到的全是掠射拉伸的低分辨率像素**——不是"填错了",是**这条带的真实观测本身就只有掠射低分辨率这一种**。
- **实测证据(两条硬数据):**
  - 带内 **wbev HF std = 3.58** vs 两侧正常路面 **5.80**——轨迹带的高频『量』本身就系统性偏低(掠射欠采样的直接读数)。
  - 中央区 **fill 真实像素仅 2%**(§12.2 已录 faithfill 几乎全拒)——中央带几乎没有可信时间反投影真值,全靠 T2 map 兜。
- **湿路面镜面再叠加:** §12.3 的湿路面镜面反射(视角相关、朗伯失效)**叠加在**掠射拉伸之上,雪上加霜。
- **★判决:** **v6 的杂纹和 v7b 的糊块 = 同一物理事实(轨迹带只有掠射低质真实)的两种掩饰**。锐着显示 = 杂纹(v6);糊着显示 = 糊块(v7b)。**"用低质真实假装高质真实",眼睛必然识破**——**composite 门控在这条带上已经到顶**,再怎么调门/gain/虚化都是在低质真实里打转。

### 13.3 三选项与实验判决

据 13.2 的物理判决,跳出 composite 层,立三选项:

| 选项 | 做法 | 判决 |
|---|---|---|
| **A. 轨迹带全交 ProPainter** | `zone2` 全域设为 mask、band 帧为 video,**PP 从带外锐利真实像素做时序传播**补整条轨迹带 | **★实验胜出**(见 13.4) |
| **B. honest-black(轨迹带=Cosmos 生成域)** | 轨迹带整块留黑 = 交下游 Cosmos 生成 | **语义最诚实,留作备选,待 koi**(与 §12.10 honest-black 同源) |
| **C. 掠射超分 / 去反射** | 学术级镜面/漫反射分解 + 掠射超分辨率重建 | **不立项**(学术级) |

选 A 的第一性理由:轨迹带的低质来自**带内真实像素本身掠射欠采样**;**带外**的路面是正常入射的锐利真实像素。PP 的 flow 时序传播能**从带外锐利像素把纹理"传"进带内**,而不是在带内低质像素里打转——这正是 composite 门控做不到的。

### 13.4 A 选项实验胜出(v8 PP-full-zone,全眼核)

- **实现:** `zone2`(§12.5 管辖域扩展,含 band 内容区黑洞)**全域为 mask**,`EGO_BLACK` band 帧为 video,ProPainter 时序传播补整条轨迹带,composite **只贴 mask 内**像素(带外真值一字不动)。
- **全帧眼核判决:** **几乎看不出填充痕迹**——色调统一、**无杂纹无糊块**(v6/v7b 两病同消)、黄线连续、与带外真值**无缝融合**。
- **★时序验证(三帧条 f010/f050/f090 × 左/中/右三车头区):双黄线 / 停止线 / 斑马线 / 自行车道标记全部穿带连续、跨帧无撕裂。** 这是 PP 从带外锐利真实像素时序传播的直接证据——结构性标线能穿过整条轨迹带保持几何连续。
- **性能:** PP 92 帧 **171s**(40GB 卡定式 = §12.7 `--fp16 --subvideo_length 15 --neighbor_length 5` + `expandable_segments`)。

### 13.5 v8 定稿配方(PP-full-zone)

**v8 = PP-full-zone**(轨迹带整条交 ProPainter 时序传播,替代 v6/v7b 的 composite 层填充):

- **video:** `EGO_BLACK` band 帧(轨迹带已黑)。
- **mask:** `zone2`(§12.5 管辖域扩展,含 band 洞管辖——轨迹带 + band 无源黑洞全在内)。
- **ProPainter:** `--fp16 --subvideo_length 15 --neighbor_length 5` + env `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`(40GB 卡定式)。
- **composite:** **只贴 mask 内**像素,带外真值不动。

### 13.6 ★语义账(provenance 变化,必须告知 koi)

v8 相对 v6 有一条**必须上报 koi 的 provenance 变化**:

- **v6 / tier-cascade:** 轨迹带内容 ≈ **~40% 掠射低质真实(fill/wbev)+ 编造(resid PP)**。
- **v8:** 轨迹带内容 = **100% ProPainter 时序编造**(带外真实像素传播而来,但带内每个像素都是模型合成、非直接观测)。

**这不是"变好"或"变坏",是 provenance 换了性质**,而 koi 的下游 Cosmos 模型对条件帧 provenance 敏感,只有他能拍板哪种对模型好:

1. **干净一致的编造**(v8:看起来完美但全是合成);
2. **杂讯低质真实**(v6:难看但每个像素都是真实观测);
3. **黑洞**(honest-black:诚实标注"这里没观测、你来生成")。

**必须把这三选项摆给 koi**,不能替他决定"完美的假"一定优于"难看的真"。

### 13.7 干湿场景分工(v8 不是全局替代 v6)

v8 **只 ship 湿/复杂场景**,不是全局默认:

- **干燥简单场景(`02678d04` 型):** 仍用 **tier-cascade `compose_frame`**——掠射真实像素在干路面质量尚可(HF 差距小、无镜面叠加),保留"带内多为真实观测"的 provenance 优势。
- **湿 / 复杂场景(`05fa5048` 型):** **ship v8**——湿路面镜面 + 掠射双重劣化下,composite 层已到顶,PP-full-zone 是唯一能出干净成品的路。

这条分工本身也是给 koi 的信息:**同一管线按场景难度输出不同 provenance 的条件帧**。

### 13.8 ★Drive 版本管理新纪律(mv 毁链接坑 → cp 定式)

本役踩到一条**实际毁掉用户链接**的坑,固化为新纪律:

- **坑:** 在 Drive FUSE 上 **`mv` = 复制 + 删除源**,会**给新文件分配新 `fileId`**——**用户之前分享的旧链接直接 404**(本役实际发生,用户旧链接失效)。
- **★新定式 = 永不 `mv`,改"同名 `cp` 覆盖 + 版本命名副本并存":**
  - 主产物(`frames/` `masks/` `clip.mp4`)用**同名 `cp` 覆盖**——`fileId` 不变、**用户链接永不断**。
  - 每个版本另存**命名副本** `clip_v*.mp4` 并存,永不删旧。
- **`05fa5048` 现状(落定):**
  - 主 `frames/` `masks/` `clip` = **v8**(同名覆盖);
  - 四版本副本并存:`clip_v1_original.mp4` / `clip_v6_fourfix.mp4` / `clip_v7b_cleanblur.mp4` / `clip_v8_ppzone.mp4`;
  - 备份目录齐:`v1_backup/` / `v6_backup/` / `v7b_backup/`。

### 13.9 git 记录

- **`55fb4e5`**(v8 verdict 模块更新)已推 main。
- **累计 commit 链:** `0f7ebd4`(DB-123..128 全部 driver + v9 内核 + 备份 + 文档)→ `82dbfbe`(v7b `clean_blur` 模块)→ `0561a53` → `55fb4e5`(v8 verdict)。

### 13.10 遗留(诚实)

1. **C 选项(掠射超分 / 镜面-漫反射分解)未做** = 轨迹带物理天花板的**唯一真正根治路**,学术级待办(§12.3 湿路面 + §13.2 掠射双物理边界共同指向)。
2. **B(honest-black)与 v8(完美编造)的最终取舍待 koi** = §13.6 provenance 决策,阻塞项。
3. **v8 干湿分工阈值未参数化** = 目前按场景人工判"干/湿",量产 driver v10 需自动化湿路面检测路由。
4. **v8 未回写 `db127_general.py` 量产 orchestrator** = 与 §12.9 遗留合并,driver v10 时统一。

---

## 13.11 ★v8 黑渗透新症状 → edge-pad 修复(v8b 失败 → v8c 定稿,2026-07-14)

> **一句话:** §13.4 的 v8(PP-full-zone)交付后,用户发现一条 **v8 自身引入的新症状**——**左右车头区黑色 mask 随播放越来越多、地面越来越糊**(`f080` 附近明显,播得越久越黑)。本轮用 per-frame 统计**排除法**先证伪"管辖遗漏",锁定真凶 = **ProPainter 把 ERP 格式黑边当内容、从黑边吸色时序累积恶化**;v8b 一轮自纠失败(colmax 用带洞 band 算错反把 PP 填好的 zone 抹黑),**v8c 定稿**(edge-pad 内容包络 + colmax 全包络 + PP 只贴 mask 内)眼核尾帧黑吞噬消失。全部实测 + 眼核,事实不软化。

### 13.11.1 用户发现(v8 新症状)

中间地面已由 v8 修好,但**左右车头区黑色 mask 随播放越来越多、地面越来越糊**,`f080` 附近最明显——**播得越久越黑**。这是 v8 PP-full-zone 自身引入的新症状,不是 v6/v7b 的旧病。

### 13.11.2 诊断 — 排除法(先证伪"管辖遗漏")

- **逐帧统计推翻第一假设:** `unmanaged_black`(未被 `zone2` 管辖的黑像素)全程 **≈ 0**、`zone2`/mask 面积全程稳定在 **47–53k px** —— **管辖域没漏、mask 没漂**,直接推翻"轨迹带管辖遗漏"的第一假设。
- **★眼核尾帧定真凶 = PP 把 ERP 格式黑边当内容:** 左右端 `zone2` 直接**贴着 band 下边缘的格式黑边**(ERP 底部无源黑);PP 的空间邻域**一半落在黑边上**,flow 匹配不上时**从黑边吸色** → 填出越来越暗的假黑,**时序累积恶化**(`f061` 尚可 → `f092` 近全黑,伴紫晕)。
- **为什么中间不发病:** front 区 `zone2` 下方垫着**真实路面**,PP 邻域有真内容可时序传播;左右车头区 `zone2` 下方**就是格式黑边**,没有真内容可传。

### 13.11.3 v8b(失败一轮,自查自纠)

edge-pad 思路正确(在内容底边以下补一段假路面,给 PP 一个**非黑**的邻域),但 `colmax`(每列内容底边)是用**带洞的 band**(含 `zone2` 黑)计算的 → **`zone2` 贴边列的底边被算到了 `zone2` 上方** → composite 后处理"内容底边以下置黑"这一步**反把 PP 刚填好的 `zone2` 整块又抹黑**。

> **★教训:内容包络(colmax)必须用 `band-real ∪ zone2` 计算,不能用带洞 band。**

### 13.11.4 v8c 定稿(三件套)

1. **edge-pad:** 每列**内容底边以下**用该列**最低 40 行路面均值色**向下延伸 + **σ=3 颗粒**(加噪**防 PP 把平面锁死**成镜面涂抹)—— 给 PP 一个非黑、有纹理的邻域。
2. **colmax 用完整包络:** 内容底边改用 `band-real ∪ zone2` 计算(修 v8b 根因),贴边列底边不再被误算到 `zone2` 上方。
3. **PP 只贴 mask 内 → 恢复格式黑:** ProPainter(§13.5 的 40G 卡定式 `--fp16 --subvideo_length 15 --neighbor_length 5` + `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`)传播后,composite **只贴 mask 内**像素,edge-pad 出来的假路面按格式恢复成黑边(不进成品)。

**眼核判决(尾帧 `f061`/`f081`/`f092`):** **黑吞噬消失** —— 原来越播越黑的左右车头区现在填充为**平滑路面延伸**、跨帧稳定。

**已知微痕(诚实):** L 端黄线**轻微下拖**(PP 色彩扩散特性,edge-pad 假路面被 PP 顺着黄线往下带一点)、底边**微暗渐变**(edge-pad 均值色比真实路面略暗)。均属可接受级,未再堆掩饰手段。

### 13.11.5 产物与 git

- **Drive `datasets/av2_1plus92_cascade_v1/05fa5048/`:** 主版本(`frames/` `masks/` `clip`)= **v8c**(同名 `cp` 覆盖保 `fileId`,§13.8 纪律),新增命名副本 `clip_v8c_ppzone_edgepad.mp4` 并存。
- **git `d9ef65c`**(v8c:edge-pad the ERP format black before ProPainter — black-bleed fix;`db128_composite.py` 模块 Recipe 更新为 **v8c FINAL**)已推 main。累计链 `55fb4e5`(v8)→ `d4cc9d0`(§13 v8 physics docs)→ `d9ef65c`(v8c)。

---

## 13.12 ★★用户价值判决:v8c 否决 → v6 最终定档(DB-128 FINAL STANDING,2026-07-13)

> **一句话:** §13.11 的 v8c(PP-full-zone,黑渗透已修)技术上干净、眼核挑不出填充痕迹,但用户放大 `05fa5048` 后做出**价值判决**:**"完全丢失了真实的路面像素,现在整个就是糊的"**——v8c 把轨迹带 ~40% 掠射真实像素**全部换成 100% 编造**,违背项目第一性(evidence-gated 真实优先)。这一判决**终结了整条 DB-128 "在轨迹带上追清晰"的路线**:v6 之下不存在"更清晰的真实",只有三种哲学。**v6 = 最大真实合成器最终定档**(恢复为 `05fa5048` 主版本,同名覆盖保 `fileId`)。事实不软化。

### 13.12.1 用户价值判决(v8c 判负)

- **用户原话:** **"完全丢失了真实的路面像素,现在整个就是糊的"**。
- **判决实质:** v8c 在**工程上是成功的**(黑渗透修掉、色调统一、标线穿带连续、眼核几乎看不出痕迹),但它是**用 100% ProPainter 编造替换掉轨迹带内 ~40% 掠射真实观测像素**换来的干净。用户判定这**违背项目第一性 = evidence-gated 真实优先**:**宁可要难看的真,不要好看的假**。
- **★与 DB-122 B-coherence 判决同脉:** 这与 DB-122 "线虚化只在 T 管辖区、载体直采锐度不可牺牲"的判决**是同一条用户立场轴** —— **真实性 > 观感**,用户在两役中立场完全一贯。DB-128 的 v8c 否决不是孤立事件,而是这条价值轴的又一次落点。

### 13.12.2 关键认知定档:v6 的"模糊"不是 bug,是掠射物理本相

- **v6 的"模糊纹理" = 4–6° 掠射真实数据的本来面目**(§13.2 物理判决),**不是 bug、不是 composite 没调好**。
- **★v6 之下不存在"更清晰的真实"** —— 轨迹带的真实观测**本身就只有掠射低分辨率这一种**。任何"更清晰"都必然是**编造**(v8c)或**留白交生成**(honest-black),而不是"挖出了更好的真实"。
- 这条认知**终结了从 §12(composite 四门)→ §13(v7b 虚化 → v8/v8c PP-full-zone)的整条"在轨迹带上追清晰"的迭代**:清晰与真实在这条带上**物理互斥**,不是调参能同时拿到的。

### 13.12.3 ★三哲学 / 三版本并存(交 koi 三选)

轨迹带的填充**只有三种哲学**,各自诚实、无一"更对",最终由 koi 按下游 Cosmos 需求拍板:

| 哲学 | 版本 | provenance | Drive 命名副本 |
|---|---|---|---|
| **① 最大真实**(定档主版本) | **v6** | ~40% 掠射低质真实 + resid 编造,**难看但每像素多为真实观测** | `clip_v6_fourfix.mp4` |
| **② 平滑编造** | **v8c** | 100% ProPainter 时序编造,**干净但全合成** | `clip_v8c_ppzone_edgepad.mp4` |
| **③ 生成域** | **honest-black** | 轨迹带留黑 = 诚实标注"此处无观测、交 Cosmos 生成" | 可随时生成 |

- **Drive 现状:** `datasets/av2_1plus92_cascade_v1/05fa5048/` 主版本(`frames/` `masks/` `clip`)**恢复为 v6**(同名 `cp` 覆盖保 `fileId`,§13.8 纪律,用户旧链接不断);三哲学命名副本并存供 koi 三选。

### 13.12.4 给 koi 的问题定稿(数据语义决策,非工程决策)

- **定稿问题:** band 条件帧里 **"低质真实(v6)/ 干净编造(v8c)/ 留黑(honest-black)"**,**哪个对他的 Cosmos 微调最好?**
- **★这是数据语义决策,不是工程决策** —— 三版本工程上都已就绪、都诚实;唯一未知是**下游 Cosmos 模型对条件帧 provenance 的敏感性**,只有 koi 掌握,不能替他决定"完美的假"一定优于"难看的真"。DB-128 到此**把技术做到头、把决策权干净交还**。

### 13.12.5 git 记录(FINAL STANDING)

- **`8b364c7`**(FINAL STANDING 写进 `db128_composite.py` 模块:v6 定档为最大真实合成器、v8c 判负记录、三哲学三版本)**已推 main**。
- **累计 commit 链(DB-128 终稿):** `0f7ebd4` → `82dbfbe` → `0561a53` → `55fb4e5` → `d4cc9d0` → `d9ef65c` → **`8b364c7`**。

---

## 14. DB-129 v6 之上的 restoration 改进版图 + ESRGAN 超分首实验(2026-07-13)

> **一句话:** §13.12 把 v6 定档为"最大真实合成器"、并坐实"v6 之下不存在更清晰的真实(只能编造/留白)"后,用户追问"**v6 还能改进吗 / 有什么没调研的**"。本节做**未调研方法版图盘点(四路线,全部围绕'保真实提质'而非'换成编造')** + **一次 restoration 超分首实验(Real-ESRGAN)**。结论:ESRGAN = **温和净改善的可叠加层、非质变**;真正的**治本路线 = map 端多帧超分(MFSR),可直接复用 DB-118 GSR 逆问题的成功范式**,建议列为下一专项、待用户立项。全部实测 + 眼核,事实不软化。

### 14.1 为什么还有改进空间(与 §13.12 定档不矛盾)

§13.12 的判决是:**在轨迹带上"追清晰"用 composite 门控已到顶** —— 因为 composite 只能在 fill/wbev 已经拿到的掠射低质像素之间做取舍,无法"造"出更高分辨率的真实。**但这不等于"真实提质"的所有路线都封死**:composite 层之外,还有**在像素生成阶段就提高有效分辨率**的路线未被调研。DB-129 的版图正是回答"哪些路线能在**不背叛 evidence-gated 真实优先**的前提下,把 v6 的掠射低质真实变得更清晰"。

### 14.2 未调研方法版图(四路线,按潜力排序)

| # | 路线 | 真实性语义 | 潜力 | 工程量 | 判定 |
|---|---|---|---|---|---|
| **1** | **map 端多帧超分(MFSR)** | **纯真实**(多帧真实观测联合反解) | **最大(预期质变)** | 约一个专项 | **★下一专项主攻,待用户立项** |
| 2 | restoration 单帧/时序超分(Real-ESRGAN / SwinIR / RealBasicVSR) | 半真实(真实为底 + 先验补细节) | 中(可叠加层) | 小(本节已首测) | 温和净改善,非质变 |
| 3 | map 2.5cm 参数刀 + 近观测利用率诊断 | 纯真实 | 中(可能免费) | 小-中 | 待诊断(见下) |
| 4 | 低强度扩散增强(img2img denoise 0.2) | 偏编造 | 低 + 有风险 | 中 | **排最后**(判负史警告) |

**路线 1 — map 端多帧超分(MFSR)= 最大潜力治本:**
- **观察:** world-BEV map 现在每个 cell **只由单帧上色**(DB-117 U3 single-source 机制),但**同一个 cell 在整个 log 里被几十帧以亚像素级偏移反复观测过** —— 这正是经典 **MFSR(multi-frame super-resolution)** 的理想输入结构(多帧亚像素位移 + 已知运动 = 可联合反解出超分辨率真值)。
- **★可直接复用的成功先例 = DB-118 GSR 逆问题:** DB-118 在**毯区(ground carpet)** 已经把"多帧真实观测 → 联合优化 `T+δ+c` → 反解真值"这套逆问题范式**跑通并眼核胜出**。同一哲学**几乎原样可移植到轨迹带**:轨迹带的 cell 也被多帧掠射观测覆盖,把 map 的"单帧上色"换成"多帧亚像素联合反解"即可 —— GSR 是 MFSR 在毯区的已证实例。
- **性质:** **纯真实**(输出的每个像素仍来自真实观测的联合反解、非生成先验)、**预期质变**(不是叠加打磨、是把有效分辨率从"单帧掠射"提到"多帧联合")、工程量约**一个专项**。
- **判定:建议列为 v6 之上的下一改进专项主攻方向,待用户立项。**

**路线 2 — restoration 超分(本节首测,见 §14.3):** 半真实(真实为底、ESRGAN/SwinIR 先验补细节),语义**恰落在 v6(全真实低质)与 v8(全编造)之间**。

**路线 3 — map 分辨率参数刀 + 近观测利用率诊断:**
- map 现为 5cm cell(§13 已述近 nadir ERP 放大欠采样);**参数刀 = map cell 降到 2.5cm**,直接提升 map 采样密度(参数级、零内核逻辑改动)。
- **★"免费提质"诊断点:** rear 相机在车开远后**8-15° 回看**轨迹带时,理论上比 4-6° 掠射能看到**更垂直、更高分辨率**的足迹 —— 但这些近观测**是否被 moving-box 门或 self-occlusion 门错杀**了?若是,**修门 = 不加任何新算法就能拿回更清晰的真实观测**(免费提质)。需一次利用率诊断确认。

**路线 4 — 低强度扩散增强(img2img denoise 0.2):排最后。**
- 全图 latent 重编码有**判决史铁证的劣化风险**(DiffuEraser / Wan-VACE 同构判负,见 §3/§13)+ 逐帧独立必**时序闪烁**。**即便低 denoise 也踩在这条判负线上**,故排在版图最末,非优先项。

### 14.3 ESRGAN 超分首实验(Real-ESRGAN x4,只贴轨迹带,f050)

**动机:** 路线 2 是四路线里**工程量最小、可当天验证**的一条,先用它探明"restoration 先验对掠射低质真实到底能改善多少"。

**设置:**
- **环境:** `pip install realesrgan basicsr`;模型 `RealESRGAN_x4plus`(权重 `wget`),`tile=512`,`half=True`。
- **流程:** 轨迹带区域 **x4 超分 → 再下采样回原分辨率**(净效果 = 用 ESRGAN 先验重整细节,不改尺寸),**只贴轨迹带**(带外真实像素原封不动,遵 §13 保真铁律),测试帧 `f050`。
- **推理:** **3.5s/帧**。

**★basicsr 兼容坑(入踩雷清单):**
- `pip install basicsr` 后在新版 torchvision 上**直接 import 崩** —— 新版 torchvision **移除了 `torchvision.transforms.functional_tensor`** 模块,而 basicsr 仍 `from torchvision.transforms.functional_tensor import rgb_to_grayscale`。
- **修法 = 打 shim:** 用 `from torchvision.transforms.functional import rgb_to_grayscale` 把符号补回 `functional_tensor` 命名空间(monkey-patch 兜住 basicsr 的旧 import 路径),即可跑通。

**眼核判决(温和净改善,非质变):**
- **改善:** 杂斑收敛、边缘更干净、噪点平滑 —— **是净改善**(比 v6 原帧好看)。
- **未改善:** **结构性糊照旧** —— ESRGAN 没能把掠射拉伸丢掉的结构还原回来。
- **★根因:** ESRGAN 的**退化假设**(训练用的是"下采样 + 合成噪声"这类各向同性退化)与轨迹带的**真实退化**(**各向异性掠射拉伸 + 多源拼接**)**不匹配** —— 模型没见过这种退化,只能做通用锐化/去噪,补不出被物理拉伸抹掉的结构。

**定位:** restoration 超分 = **可叠加的润色层,不是质变**。**收益上限受退化模型不匹配限制;若对真实退化(掠射拉伸 + 拼接)做定制退化建模 + 微调,收益可提升**(但那本身已接近一个小专项)。相对而言,路线 1(MFSR)从"多帧真实观测联合反解"入手治本,潜力远高于 restoration 打磨。

**产物:** `/content/db129_sr.jpg`(VM,超分前后对比)+ 本地 scratchpad `db129_sr.jpg`。

### 14.4 小结(交用户)

- **v6 之上确有改进空间,但都是"提质真实"路线,不是回到"换成编造"** —— 与 §13.12"真实 > 观感"的用户价值轴一致。
- **首推 = 路线 1 map 端 MFSR(复用 DB-118 GSR 逆问题范式),纯真实、预期质变,待用户立项开专项。**
- 路线 2 ESRGAN 已首测 = 温和净改善的可叠加层(退化不匹配是收益天花板);路线 3 待一次"近观测利用率"诊断(可能免费提质);路线 4(低 denoise 扩散)排最后(判负史警告)。

---

## 15. DB-129 map 端 MFSR 实验与 v10 定稿(2026-07-13,用户授权"直接进行改进"后)

> **一句话:** §14 把 **map 端多帧超分(MFSR)** 列为 v6 之上"提质真实"的首推治本路线(复用 DB-118 GSR 逆问题范式)。用户授权"**直接进行改进**"后,本节做 MFSR 专项:三 map 变体 A/B 对拍 v6 基线 + WBDIAG 插桩诊断 + 12 宫格眼核 → **M2(细网格 + 6 槽中值融合)全 9 位置碾压或持平 v6、零编造**,定稿为 **v10 = v6 全门 stack + M2 map**,已打包为 `05fa5048` 主版本。全部实测 + 眼核,事实不软化。

### 15.1 实验设计(三 map 变体 A/B vs v6 基线)

v6 基线的 world-BEV map = **46m 半径 / 5cm cell、单源上色**(DB-117 U3 "single renders")。MFSR 的两把刀 = **提采样密度(细网格)** + **多帧联合(中值融合)**,拆成三变体做正交 A/B:

| 变体 | 网格 | 上色 | 隔离的变量 |
|---|---|---|---|
| **v6 基线** | 46m / 5cm | 单源(best-source) | — |
| **M1** | **23m / 2.5cm 细网格** | 单源 | 只测"细网格"单刀 |
| **M2** | 23m / 2.5cm 细网格 | **6 槽中值融合** | 两刀叠加(MFSR 完整版) |
| **M3** | 46m / 5cm(粗网格) | 6 槽中值融合 | 只测"融合"单刀 |

- **★细网格"免费"的关键洞察:** 轨迹带物理上 **< 10m**(ego 足迹紧贴自车),把 map 半径从 46m **减半到 23m** → 在**同样的 cell 总数 / 构建成本**下,分辨率**翻倍**(5cm→2.5cm)。半径减半换分辨率翻倍是**零成本参数变换**,不是靠堆算力。
- **构建:** 三变体**并行构建**(~40min,3 进程抢 12 核,互相抢核是实验期的临时代价、非量产代价)。
- **探针:** 3 帧(`f051` / `f061` / `f081`)跑 fast wbev(§10 CAP-fast v9) + compose,拼 **12 宫格**(3 帧 × 4 变体)眼核对比。

### 15.2 诊断数据(WBDIAG 插桩)

给 wbev 渲染插 `WBDIAG` 探针,逐 cell 导出 best-source egovehicle 距离(egod)与 6 槽融合的实际使用槽数:

- **M2 细网格 best-source egod:** p10 / p50 / p90 = **8.2m / 12.5m / 22.3m**。
- **M2 融合槽数:** `slots_used` 中值 = **6.0**(**每 cell 满 6 观测,融合材料充足**)。
- **M3 粗网格融合槽数:** `slots_used` 中值 = **1.0**(粗网格下每 cell 大多只落到 1 帧,融合无米下锅 → 解释 M3 为何只"平滑不提质")。

**★"近观测被门错杀"假设排除(§14.2 路线 3 的诊断点当场落地):** §14 曾疑"rear 相机 8-15° 回看的近观测是否被 moving-box / self-occ 门错杀"。诊断给出否定答案 —— best-source egod **p10 = 8.2m 就是物理供给本身**:2.5–8m 时地面在**车底 / 刚露出视野下缘**(self-occlusion 门**正确拦截**,不是误杀),**8–22m 掠射就是这块带的全部真实材料**。门没有错杀近观测,轨迹带的低分辨率是**掠射物理供给的硬边界**(与 §13 物理判决一致)。这条把路线 3 的"免费提质"疑点**当场证否**:没有被错杀的近观测可回收。

### 15.3 12 宫格眼核判决(M2 胜出,两刀叠加 = 赢家)

12 宫格(3 帧 × v6/M1/M2/M3)眼核:

- **★M2(fuse + fine)在全部 9 个位置(3 帧 × 3 探针区)碾压或持平 v6** —— 停止线更完整、双黄线穿带连贯、水渍杂斑收敛、**无横向断层**。
- **M1(纯细网格)** = 小改善(有提采样密度但单帧噪声仍在)。
- **M3(纯融合)** = 平滑但糊(粗网格 `slots_used=1` 材料不足,融合退化成低通)。
- **判决:两刀叠加(细网格 + 6 帧中值)才是赢家,且零编造** —— 每个像素仍来自 **6 帧真实观测的中值**,不是生成先验,完全落在 §13.12"真实 > 观感"轴上(与 v8c 的"全编造"路线相反)。

### 15.4 ★U3 判决适用域细化(科学记录,非推翻)

DB-88 / U3 的原判是 **"cluster validates, single renders"**(混合错位掠射源做融合 = smear,故只用单源渲染)。MFSR 的中值融合表面上与 U3 冲突,实则是**适用域细化**:

- **U3 在原域继续有效:** 在 **5cm 全 nadir 域**,不同掠射源**配准松**、错位大 → 中值融合确实会 smear(U3 的判决在这个域成立,**不推翻**)。
- **MFSR 在新域安全反转:** 在 **2.5cm 近场网格**(轨迹带 <10m、配准**紧**)中,6 帧中值 = **多帧降噪 + 亚像素保留**,不产生 smear。
- **一句话:U3 讲的是"错位掠射源不能融",MFSR 用的是"紧配准近场源可以融";两者不矛盾,是同一原理在不同配准质量域下的两个正确结论。** U3 的原域判决保持有效。

### 15.5 v10 定稿与工程

- **v10 = v6 全门 stack(compose)+ M2 map。** 即 §13.12 定档的 v6 最大真实合成器,**只升级底层 map**(单源 5cm → 6 帧中值 2.5cm),门控/compose 层完全不动。**v10 与 v6 同哲学(最大真实),v10 = v6 的 map 升级版。**
- **全量:** 92 帧 wbev(m2 map,**355s**)+ compose **79s**(残洞由 compose 内 **Telea** 处理)→ 打包主版本(**同名覆盖保 fileId**,遵 §13 Drive 纪律)。
- **全帧眼核 v6 vs v10(`f050`):** 轨迹带整体**干净连贯** —— 左端水渍杂纹 → 连贯路面、中央平整、右端暗涂抹**大幅收敛**;**右下角残留少量暗斑(诚实记录,未消尽)**。
- **产物:**
  - `05fa5048` **主版本 = v10**(同名 cp 覆盖,`fileId` / 用户链接不变)。
  - `clip_v10_mfsr.mp4` 版本副本(并存,永不删旧,遵 §13.12 三哲学并存纪律)。
  - `worldmap_v10_m2.png` map 存档(Drive 同目录)。
- **★踩坑(再验证一条老纪律):** v10 首次发射用 **heredoc 超长命令**,bash **截断**(`exit 2 unexpected EOF`)。**这是 §2.1 / §6.3"超长命令必须文件式发射"纪律的又一次实证** —— 改**文件式重发一次通过**。
- **git:** `3914e9b`(`db129_mfsr.py` + `db129_v10_job.py` 入库 + `db128_composite.py` docstring 更新 `FINAL STANDING: v10`)已推 main。

### 15.6 配方 rep(量产用,可直接移植)

在现有 §10 CAP-fast fast 内核之上,MFSR 只需两处 worker `rep`:

1. **map 构建加细网格:** `["_MHALF, _CW = 46.0, 0.05", "_MHALF, _CW = 23.0, 0.025"]`(半径减半 + cell 减半)。
2. **上色改 6 槽中值:** `_wmap` 行 rep 成 **6 槽中值**(替代单源 best-source)。
3. **wbev 渲染:** 加 **GRID rep** + `FILL = m2 map`。

> 与 §4.2 的 `WORLDBEV_CENTER`(map 共享)、§10 的三开关(`CAP_ONLY` 等)一样,MFSR 也是**纯 rep / 参数级改动、零内核逻辑重写**,可直接并进量产 driver v10。

### 15.7 版本链义(三哲学三选项更新为 v10)

§13.12 交 koi 的三哲学三版本,**"最大真实"档从 v6 升级为 v10**(map 更清晰但仍纯真实):

| 哲学 | 版本 | 副本 | 语义 |
|---|---|---|---|
| **最大真实** | **v10**(← 原 v6) | `clip_v10_mfsr.mp4` | 掠射真实 + **6 帧中值联合反解**,更清晰、仍**零编造** |
| 平滑编造 | v8c | `clip_v8c_ppzone_edgepad.mp4` | 干净但全 PP 合成 |
| 生成域 | honest-black | 留黑交 Cosmos | 随时可生成 |

**koi 的数据语义决策不变**("低质真实 / 干净编造 / 留黑哪个对 Cosmos 微调最好"),只是"最大真实"这一档的质量底盘被 MFSR 抬高了。

### 15.8 遗留(诚实)

1. **完整 MFSR 仍是 upside:** v10 用的是"6 帧中值融合",**尚未做亚像素配准 + 反卷积的 GSR 式联合优化**(DB-118 逆问题 `T+δ+c` 完整范式)。完整 MFSR(联合反解而非中值)是**下一档 upside**,潜力更高、工程量更大。
2. **v10 配方未回补 `02678d04`:** 该干燥简单场景 v6 已足够好,回补**可选**(非阻塞)。
3. **构建成本:** m2 map 构建时间**与 v6 同量级**(细网格但半径减半 → cell 总数持平),只是**三变体并行实验时互相抢核**;**单变体量产估 ~10min**(不抢核)。

---

## 16. DB-130 双 log 量产 + anchor 空间系统性根因发现(2026-07-13/14)

> **一句话:** 用户给 **48 核 RTX PRO 6000 Blackwell("G4")** 要求产 **2 个真实 1+92 级联数据集 + 效率记录**。本役在 48 核机上把并行拉到 **K=24 worker**(band **0.8s/帧**),产出 **2 个合格 v10 数据集**;量产暴露出**四层根因**逐层解剖修复,其中最深一层 = **anchor 空间系统性 bug(全管线级发现)——所有 orchestrator 半年来只处理了每个 log 的前半段(~8s)**;并确立**运动感知选窗**新判据。全部实测 + 眼核,事实不软化。

### 16.1 硬件与并行

- **机器:** **RTX PRO 6000 Blackwell 96GB / 48 CPU 核**(对比:此前 A100 只有 **12 核**),`torch 2.11 + cu128` 兼容性已验证。
- **并行:** **K=24 worker**(48 核给足并行度,CPU/Drive I/O 才是瓶颈的老结论下,核数翻 4× 直接吃满)。

### 16.2 产出(Drive `datasets/av2_1plus92_cascade_v1/`)

| log | 版本 | 说明 |
|---|---|---|
| **`0aa4e8f5`** | **v10.5-motionwin** | 最终版,**P=225 运动窗口**(前半静止 → 补渲后半 + 全空间 clean-run + 运动优先选窗) |
| **`0b86f508`** | v10.2 | 曝光归一化修复后 resid 85→17-19% |
| `0b5142c1` | — | 候选,**被 gate 正确拒绝**(clean run len=29 < 93) |

### 16.3 效率账本(模型常驻口径,全实测,ledger 在 Drive `db130_ledger.json`)

| 阶段 | 耗时 | 说明 |
|---|---|---|
| band(K=24) | **0.8s/帧** | vs A100 8-worker **3s/帧 = 3.8×**(核数翻 4× 直接兑现) |
| `0aa4` 顺利链 | **≈9min** | 全链墙钟 |
| `0b86` 全链 | **≈21min** | map 构建 **889s** 是场景方差(见 §16.7 遗留) |
| FLUX | 冷读 **668s**(一次性)/ 常驻后 page cache **74s** / 生成 **~60s/log** | 与 §11 FLUX cache 顾虑消解一致 |
| compose | **3s**(Pool16) | — |
| **稳态估算** | **12-18min/log** | 模型常驻口径 |

**"G4 2-3min" 对账(诚实澄清):** 用户记忆中的"G4 2-3min"是 **① 档纯黑化版**(band-only、不做级联/FLUX);本机今日实测**黑化版约 4-5min/log(含下载)**。级联全链(band + cand + fill + map + wbev + compose + FLUX + 打包)是 12-18min 量级,两者不是同一口径,不能混谈。

### 16.4 ★★四层根因链(量产暴露,逐层解剖修复,全部眼核验证)

量产比单场景更能逼出系统性缺陷。本役连续四轮"修复—证伪—再挖",逐层深入:

**第 1 层 — spec 门晴天过杀(resid 59-85%、Telea 大面积):**
- **症状:** 晴天干燥场景 resid 高到 59-85%,大片走 Telea 兜底。
- **根因:** 镜面门用**绝对阈值 `V>150`** → 晴天干燥亮路面(**天然高 V、低 S**)被整片误判成反光、被拒源。
- **修:** 改**相对阈值 `max(150, ring_lum×1.35)`**(以本帧 band ring 亮度为基准,只有显著高于环境才算反光)。

**第 2 层 — 曝光差冒充反光:**
- **根因:** map / fill 源携带**其他帧的曝光**,整体亮度偏移**是 gain 的职责、不是镜面特征**;镜面门却把这种全局亮度偏移当成高光。
- **修:** 镜面判定前先把 **V 归一到 band ring 亮度**(剥离曝光偏移,只留真实高光)。→ `0b86` **resid 85% → 17-19%**。

**第 3 层 — map 半径不自适应:**
- **修:** `R = clip(窗口 dmax + 14, 23, 46)`,**网格恒 1840²**(半径随窗口运动幅度自适应、网格数不变 → 分辨率自适应,时间成本不变)。

**第 4 层 — ★★anchor 空间系统性 bug(全管线级发现,本役最深一刀):**
- **实锤:** `inspect.getsource` 读 `AV2RingLoader.anchor_timestamps_ns()` 源码 → anchor 时间戳来自 **`ring_front_center` 相机的 20Hz 时间戳(每 log ~319 个)**;而**所有 orchestrator 一直用 `len(lidar)=156` 当 anchor 总数**(LiDAR 是 ~10Hz)。
- **后果:** 每个 log **只处理了前半段(~8s)** —— 索引空间被 LiDAR 帧数腰斩,窗口永远落在 log 前半。
- **影响面(诚实分级):**
  1. **历史产物仍自洽有效** —— 全链(band/cand/fill/wbev/compose)用的是**同一个** index 空间,内部一致、成品没错;
  2. **产出率被系统性低估** —— 被 gate 拒绝的 log,其**后半段可能有合格窗口**,却从未被扫描过;
  3. **帧率语义澄清(告知 koi):** anchor 是 **20Hz**,故 **93 帧 @ 20Hz = 4.65 秒**(此前若按 10Hz 理解会把时长算错一倍)。
- **`0aa4e8f5` 实战修复(眼核质变):** 该 log **前半静止**(motion profile 揭穿:`dmax=1.8m`,轨迹带是**纯物理盲区**、frame-1 是灰浆)→ **补渲后半段(106s)+ 全空间 clean-run + 运动优先选窗**(合格窗口中取 `dmax` 最大者,选中 **P=225、dmax=17.7m**)→ **resid 46% → 11.7%、frame-1 灰浆 → 完整路面**,眼核质变通过。

### 16.5 ★运动感知选窗(新判据入配方)

- **新判据:** **选窗必须运动感知(`dmax ≳ 10m`),纯 `imperfect` 升序不够**。
- **物理依据:** 静止段(`dmax` 极小)= 轨迹带**物理纯盲区** —— 车没动,ego 足迹从没被别的时刻从掠射角观测过,fill/wbev 拿不到任何真实材料,只能出灰浆。这与 §13 的"front-pod rig 掠射物理硬边界"同脉:**没有位移就没有掠射观测,没有掠射观测就没有真实像素**。
- **落地:** 选窗打分从"`imperfect` 最小"升级为"合格窗口中 **运动幅度 `dmax` 最大**",`0aa4` 的 P=225 运动窗口即此判据产物。

### 16.6 诊断方法论亮点(记录,这套方法是本役能挖到第 4 层的关键)

1. **motion profile(pose 端点位移画像):** 对 pose 序列算端点位移,**当场揭穿"静止假象"** —— `0aa4` 前半 `dmax=1.8m` 一眼看出是静止段,而非"选帧没选好"。
2. **lidar 文件名直算位置 vs `ta_of` 对比:** 用 LiDAR 文件名(时间戳)直接反算车辆位置,与 anchor 时间戳插值出的位置对比,**锁定"时间戳源不一致"**这个方向。
3. **`inspect.getsource` 实锤 anchor 定义:** 不靠猜、直接读 `anchor_timestamps_ns()` 源码,坐实 anchor = **ring_front_center 20Hz** 而非 LiDAR 帧。
- **元教训:** 连续四轮**没有止步于表面修复**(spec 门一修 resid 就降了,很容易收手),而是每修一层就问"还有没有更深的",才挖到 anchor 空间这个全管线级 bug。

### 16.7 工程踩坑(本役新增)

1. **`fix4` 的 sed 替换漏改 `find1` 长串路径** → `map3` / `m4` 阶段路径错位(sed 只改了部分出现处)。
2. **`fix6` 的 glob `**` 需 `recursive=True`** —— Python `glob` 的 `**` 不加 `recursive=True` 不递归,静默漏文件。
3. **`fix7` compose 的 glob 未适配 `band2` / `bh` tag** —— 新 tag 命名没进 glob 模板,漏匹配。
4. **heredoc 超长命令截断再犯一次** —— §2.1 / §6.3 / §15.5 的"超长命令必须文件式发射"纪律,本役是**第 N+1 次验证**(文件式重发即过)。

### 16.8 git

- **`405eb94`**(spec v10.2 进 `db128_composite.py` + `db130_job.py` / `db130_fix6_build.py` 入库)已推 main。

### 16.9 遗留(诚实)

1. **★orchestrator `n=len(lidar)` bug 修复要回写量产版:** 本役是在 `0aa4` 上手工"补渲后半 + 全空间"救回来的;**根治 = 把 `db130_job.py` 的 anchor 总数从 `n=len(lidar)` 改成 `n=len(cam jpg)`**(用 20Hz 相机帧数),否则下一个 log 又只跑前半。这是全管线级修复,优先级最高。
2. **`0b86` map 构建 889s 场景方差待查:** 同配方下 `0aa4` map 快、`0b86` 慢到 889s,场景相关因子未定位。
3. **被拒 log 全空间重试(产出率回收):** `04994d08` / `070bbf42`(DB-127 被拒)/ `0b5142c1`(本役被拒)**都是在腰斩的前半空间被拒的** —— 用修复后的全空间(20Hz 全 anchor)重试,后半段可能有合格窗口,**产出率有系统性回收空间**。

---

*记录完成:2026-07-12(§1–§7)/ 2026-07-13 补(§8 DB-125 级联版 1+92 数据集全链量产 + §9 车头痕迹修复 v3 定稿 + wbev sampler 判负 + 加速诚实蓝图 + §10 CAP-fast 内核 v9 手术与实测大捷 + §11 DB-127 三场景盲测泛化验证)/ 2026-07-14 补(§12 DB-128 `05fa5048` 复杂场景四问题解剖 + composite v6 四新门定稿 + 湿路面朗伯失效物理边界 + 40GB 卡 PP 定式)。两个实验(§3/§4)均已眼核 + 归档;六方法版图闭合;④ 档三级级联证据链完整,并在新场景 `02678d04` 端到端量产实证(残洞 0.00%);composite v3 定稿(gain 对齐 / 色调门 45 / 自模糊 feather)已推为 Drive 主产物;wbev sampler 旁路加速三轮判负(与 DiffuEraser/Wan 同构),加速正道 = 内核内 `CAP_ONLY`;§10 内核 v9 三开关实测 fill 2.4s/帧(42×)、wbev 3.8s/帧(260×)、成品级 zone diff med=4.0(vs sampler med=47),CAP-fast v9 定稿;**§11 盲测泛化验证 1/3 过 93 门(重遮挡 `05fa5048` 全链产出、Tier3 首次实战 2.9%)、级联分工随难度合理滑动、产出率受内容限制被证实、最重要工程发现 = band 无源黑洞纳入级联管辖(zone → zone ∪ band 黑洞)**／ 2026-07-14 补(**§13 ★终局**:用户否决 v7b(clean-blur 左右模糊超 v6 = 净退化)→ 第一性重分析坐实**左中右三车头区 = ego 自遮挡轨迹带**(front-pod rig 永不能近距垂直观测自己足迹、唯一观测 = 4–6° 掠射 → 1px 垂直误差 = 米级水平拉伸、带内 wbev HF std 3.58 vs 路面 5.80、中央 fill 真实像素仅 2%);v6 杂纹与 v7b 糊块 = 同一物理事实两种掩饰、composite 门控到顶;三选项实验 A(轨迹带全交 ProPainter、zone2 全域 mask、PP 从带外锐利真实像素时序传播)胜出 = **v8 PP-full-zone 定稿**(全帧眼核几乎无填充痕迹、时序三帧条 f010/f050/f090 穿带标线连续、PP 92 帧 171s);provenance 换性质(~40% 掠射低质真实+编造 → 100% PP 编造)必告知 koi(完美编造 vs 杂讯真实 vs 黑洞由他拍板);干湿分工(干 02678d04 用 tier-cascade compose_frame、湿 05fa5048 ship v8);Drive `mv` 毁链接坑(FUSE `mv` = 复制+删除换 `fileId` 毁用户旧链接)→ 同名 `cp` 覆盖 + 版本副本 clip_v*.mp4 并存新纪律;commit `55fb4e5` 已推 main)／ 2026-07-14 再补(**§13.11 v8 黑渗透修复**:v8 交付后用户发现左右车头黑 mask 随播放渐吞、地面渐糊(`f080`)→ 排除法证伪"管辖遗漏"(`unmanaged_black≈0`、mask 面积稳定 47–53k)→ 真凶 = **PP 把 ERP 格式黑边当内容、从黑边吸色时序累积恶化**(`f061`→`f092` 近全黑);**v8b** colmax 用带洞 band 算错致抹黑 zone → **v8c 定稿** = edge-pad 每列底边下 40 行均值色 + σ3 颗粒延伸 + colmax 用 `band-real∪zone2` 全包络 + PP 只贴 mask 内恢复格式黑,尾帧眼核黑吞噬消失、微痕 L 黄线轻微下拖;commit `d9ef65c` 已推 main)／ 2026-07-13 定档(**§13.12 ★★用户价值判决 = DB-128 FINAL STANDING**:v8c 技术上干净但用户判负 **"完全丢失了真实的路面像素,现在整个就是糊的"**——PP-full-zone 把轨迹带 ~40% 掠射真实像素全换 100% 编造、违背 evidence-gated 真实优先第一性;**与 DB-122 B-coherence 同脉 = 真实性 > 观感、用户立场一贯**;关键认知定档 = **v6 的"模糊" = 4–6° 掠射真实数据本来面目非 bug、v6 之下不存在"更清晰的真实"只有编造/留白**,终结整条"在轨迹带追清晰"路线;**v6 = 最大真实合成器最终定档**(恢复为 `05fa5048` 主版本、同名 `cp` 覆盖保 `fileId`);**三哲学三版本并存交 koi 三选**(v6 最大真实 `clip_v6_fourfix` / v8c 平滑编造 `clip_v8c_ppzone_edgepad` / honest-black 生成域可随时生成);koi 问题定稿 = band 条件帧"低质真实/干净编造/留黑"哪个对 Cosmos 微调最好 = **数据语义决策非工程决策**;commit `8b364c7` FINAL STANDING 已推 main,DB-128 终稿链 `0f7ebd4→82dbfbe→0561a53→55fb4e5→d4cc9d0→d9ef65c→8b364c7`)／ 2026-07-13 再补(**§14 DB-129 v6 之上 restoration 改进版图 + ESRGAN 超分首实验**:用户问"v6 还能改进吗/有什么没调研的"后的方法盘点+首实验;未调研版图四路线按潜力排序=①**map 端多帧超分 MFSR**(每 cell 现单帧上色但被几十帧亚像素观测=经典 MFSR 理想输入,**复用 DB-118 GSR 逆问题 T+δ+c 联合优化范式**、纯真实预期质变=下一专项主攻待用户立项)②restoration 单帧/时序超分(半真实、语义在 v6 与 v8 之间)③map 2.5cm 参数刀+近观测利用率诊断(rear 8-15° 回看是否被 moving-box/self-occ 门错杀=可能免费提质)④低强度扩散增强(排最后、判负史警告);**ESRGAN 首实验**(Real-ESRGAN x4→下采样回原分辨率、只贴轨迹带 f050、tile512 half True 3.5s/帧,**basicsr shim 坑=torchvision 移除 functional_tensor 需 `from torchvision.transforms.functional import rgb_to_grayscale` 补回**)眼核=**温和净改善**(杂斑收敛/边缘干净/噪点平滑,结构性糊未变=ESRGAN 退化假设与真实掠射拉伸+多源拼接退化不匹配)=可叠加层非质变;产物 `db129_sr.jpg`)／ 2026-07-13 再补(**§15 DB-129 map 端 MFSR 实验与 v10 定稿**:用户授权"直接进行改进"后的 MFSR 专项;三 map 变体 A/B 对拍 v6 基线(M1 细网格单源/M2 细网格+6 槽中值/M3 粗网格+融合),**轨迹带<10m 让半径减半换分辨率翻倍=构建成本不变的免费提采样**;WBDIAG 诊断=M2 slots_used 中值 6.0 满观测、best-source egod **p10=8.2m** 就是物理供给(近观测未被门错杀、掠射 8-22m 是全部材料);**12 宫格眼核=M2(fuse+fine)全 9 位置碾压或持平 v6、零编造**(M1 小改善/M3 平滑但糊);**★U3 判决适用域细化(非推翻)**=U3"错位掠射源不能融"在 5cm 全 nadir 域成立、MFSR"紧配准近场源可融"在 2.5cm 近场网格安全反转,同原理两域两正确结论;**v10=v6 全门 stack+M2 map**(同哲学,v6 的 map 升级版),全量 92 帧 wbev 355s+compose 79s(Telea 收残洞),全帧眼核 f050 轨迹带干净连贯、右下角残留少量暗斑(诚实);**踩坑=首发 heredoc 超长命令 bash 截断 exit 2,文件式重发一次通过=再验"超长命令必文件式发射"纪律**;配方 rep=map 构建加 `_MHALF,_CW 46/0.05→23/0.025`+`_wmap` 6 槽中值+wbev 加 GRID rep+FILL=m2 map(纯参数零内核重写);三哲学"最大真实"档 v6→**v10**;产物 `05fa5048` 主版本=v10(同名 cp 覆盖保 fileId)+`clip_v10_mfsr.mp4`+`worldmap_v10_m2.png`;git `3914e9b`(db129_mfsr.py+db129_v10_job.py 入库+db128_composite.py docstring FINAL STANDING: v10)已推 main;遗留=完整 MFSR 亚像素配准+反卷积 GSR 式联合优化仍是 upside / v10 未回补 02678d04(可选) / 单变体量产估 ~10min)／ 2026-07-13/14 补(**§16 DB-130 双 log 量产 + anchor 空间系统性根因发现**:用户给 **48 核 RTX PRO 6000 Blackwell(G4)** 要 2 个真实 1+92 级联数据集 + 效率记录;**K=24 worker、band 0.8s/帧(vs A100 8w 3s/帧 3.8×)**;产 2 合格 v10 数据集(`0aa4e8f5` v10.5-motionwin P=225 运动窗口 + `0b86f508` v10.2,候选 `0b5142c1` clean 29<93 被 gate 正确拒);账本模型常驻口径 `0aa4`≈9min / `0b86`≈21min(map 889s 场景方差)/ FLUX 冷读 668s→page cache 74s / compose 3s Pool16 / 稳态 12-18min/log,"G4 2-3min"是 ①档黑化版口径(本机黑化 4-5min 含下载)非级联;**★★四层根因链**=① spec 门晴天过杀(V>150 绝对阈值把晴天干燥亮路面高V低S 全判反光→相对阈值 max(150,ring_lum×1.35))② 曝光差冒充反光(map/fill 源带其他帧曝光→spec 前 V 归一到 band ring→`0b86` resid 85→17-19%)③ map 半径自适应 R=clip(dmax+14,23,46) 网格恒 1840²④ **★★anchor 空间系统性 bug 全管线级发现**=`inspect.getsource` 实锤 `anchor_timestamps_ns()`=ring_front_center **20Hz** 时间戳(~319/log)而所有 orchestrator 用 `len(lidar)=156` 当总数→**每 log 只处理前半段~8s**(历史产物同 index 空间自洽有效、但产出率被系统性低估=被拒 log 后半可能有合格窗、**93 帧@20Hz=4.65 秒**帧率语义告知 koi);`0aa4` 前半静止(motion profile dmax=1.8m 轨迹带纯盲区/frame-1 灰浆)→补渲后半 106s+全空间 clean-run+**运动优先选窗**(合格窗 dmax 最大 P=225 dmax=17.7m)→resid 46→11.7%、灰浆→完整路面眼核质变;**★运动选窗新判据入配方=选窗必须运动感知 dmax≳10m、纯 imperfect 不够、静止段=轨迹带物理纯盲区**;诊断方法论=motion profile 揭穿静止假象/lidar 文件名直算位置 vs ta_of 锁时间戳源差/inspect.getsource 实锤,连续四轮"修复-证伪-再挖"不止表面;踩坑=fix4 sed 漏改 find1 长路径(map3/m4 错位)/fix6 glob `**` 需 recursive=True/fix7 compose glob 未适配 band2/bh tag/heredoc 超长截断再犯;git `405eb94`(spec v10.2 进 db128_composite.py+db130_job.py/db130_fix6_build.py)已推 main;遗留=**orchestrator n=len(lidar) bug 修复要回写 db130_job.py 量产版(n=len(cam jpg))**、`0b86` map 889s 场景方差待查、被拒 log(04994d08/070bbf42/0b5142c1)可用全空间重试回收产出率)。凭据零泄露。*
