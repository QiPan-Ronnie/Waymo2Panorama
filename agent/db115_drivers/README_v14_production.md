# v14.1 双机量产操作手册(DB-134)

> 目标读者:一个**只读这一份**就要能双机发射 + 监控 + 收产物的新 agent。
> 权威原理见 `agent/PIPELINE_1plus92.md` §9;这里只讲**怎么操作**。
> **发射实例:`agent/db115_drivers/db135_run100.py`**(实际在双机运行的量产脚本,`LOG100` 100 候选清单内嵌;`db134_production.py` 是母版,+ `db134_fluxpack.py`)。内核 `scripts/phase3/db89_ghost_recovery.py`(md5 `cca4f0c586a2d91abe14d4b121824cf4`)。git 存档 commit `de5bcd3`。

---

## 0. 一分钟速览

- 两台 48 核 Blackwell 96GB G4,各跑一半 log(`MACHINE_SHARD "0,2"` 和 `"1,2"`),**零通信**。
- 每台从 `USED` 排除后的 100 个 AV2 val log 里,按 `cands[i::k]` 各取自己那一半,顺序跑。
- 单机每个合格 log 热机 ~7-8min;双机渲染吞吐 ~3-3.5min/合格 log;含被拒摊销(产出率 ~37%)~6-7min/合格成品。
- **冷启动每台一次性 FLUX 载入 ~11min 税**(page cache 后 ~71s),不计入稳态。
- 产物落 Drive `koi_waymo2pano_colab/datasets/av2_1plus92_production_v14/<u8>/`。

---

## 1. 两个 runtime 句柄(dr2)

两台机器 = 两个 Colab runtime,各有独立凭据文件。用 `agent/db115_drivers/dr2.py` 取句柄:

```python
import sys; sys.path.insert(0, r"D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama/agent/db115_drivers")
import dr2
a = dr2.get('a100')   # 机器 A;凭据 ~/.waymo2panorama/runtime/active_url_5.json
g = dr2.get('g2')     # 机器 B;凭据 ~/.waymo2panorama/runtime/active_url_g2.json
```

- `dr2.get(gpu)` 返回一个模块,`_exec / dr_launch / dr_wait / dr_pull` 已绑定到该 runtime。
- **凭据文件**(url/token 本体):
  - 机器 A:`~/.waymo2panorama/runtime/active_url_5.json`
  - 机器 B:`~/.waymo2panorama/runtime/active_url_g2.json`
- **🔒 绝不把 url/token 写进任何提交文件、日志、文档、命令回显**。只更新这两个本地 json。隧道断(HTTP 530/502)= 等 user 重贴新 endpoint 写回对应 json。

---

## 2. 发射(dr_launch,文件式)

### 2.1 每台机器的 driver 只差一行 `MACHINE_SHARD`

`db135_run100.py` 顶部占位符 `MACHINE_SHARD = "__MS__"`,发射前替换成本机分片:
- 机器 A:`"0,2"`
- 机器 B:`"1,2"`

### 2.2 发射模式(dr_launch)

```python
src = open(r"D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama/agent/db115_drivers/db135_run100.py", encoding="utf-8").read()

a.dr_launch("prod_m0", src.replace('"__MS__"', '"0,2"'))   # 机器 A
g.dr_launch("prod_m1", src.replace('"__MS__"', '"1,2"'))   # 机器 B
```

- `dr_launch(name, code)` = base64 分块 push(30000 字/块)→ `base64 -d` 还原 → `nohup setsid python -u ... &` 后台跑 → 打印 `LAUNCHED <pid>`。
- **📌 超长命令一律文件式发射**:driver 源码几百行,**绝不**塞进一条 `bash -lc` 内联(会被截断 exit 2)。`dr_launch` 内部已经是"push 成文件再跑"的正道;自己另写 bash 时也必须先 `printf > file.py` 再 `python file.py`,别 heredoc 塞长串。
- 前置:内核 + worker + merge 脚本要先在 runtime 上(`db131_setup.py` 推 `db89_ghost_recovery.py` / `db125_worker.py` / `db131_merge.py` 到 `/content/`)。若是干净 runtime,先跑一遍 setup。
- 内核开关由 driver 自动注入,**发射者不用手改**:map 分片走 `WORLDBEV_SHARD="i,8"`(8 shard)+ `WORLDBEV_DUMP` 落盘 → `db131_merge.py` 归并 → final 走 `WORLDBEV_LOAD` 加载 merged.npz。默认全关时内核与 v9 byte-identical(见 `PIPELINE_1plus92.md` §9.1)。

### 2.3 前置检查(发射前)

1. 两台 runtime 隧道都活(`_exec("echo ok")` 返回 exit 0)。
2. FLUX cache 在 Drive(`HF_HOME` 指 Drive,`HF_HUB_OFFLINE=1`,别找 token;见 §5 冷启动税)。
3. `localav2` SSD 有空间(渲染冷读 Drive 会拖死 CPU,driver 内部走 localize→本地 SSD)。

---

## 3. 监控(grep 模式)

driver 全程往 `/content/_dj_<name>.log` 打结构化行。远端 grep:

```python
def tail(h, name, pat, n=40):
    r = h._exec("grep -E '%s' /content/_dj_%s.log | tail -%d" % (pat, name, n), 60)
    print(r.get("log_tail") or "")

tail(a, "prod_m0", "LED[|verdict=|QUEUED_FLUX|specmap|Traceback")
```

关键信号:

| grep 关键词 | 含义 |
|---|---|
| `LED[` | 每个分段耗时/指标写账(`probe_s` `fine_s` `map_s` `wbev_s` `compose_s` `dmax_m` `specmap_hit` …) |
| `specmap` / `specmap_hit` | 投机建图是否命中(命中 → map 藏进 probe 轴) |
| `QUEUED_FLUX <n> (<u8>)` | 该 log 过了级联,已交后台 FLUX+打包线程,主线程去下一个 log |
| `verdict=` | 判定:`SKIP_static` / `SKIP_fine_dirty_N` / `SKIP_no_clean_window` / `FAIL: <msg>` |
| `DB134_QUEUE_EXHAUSTED ok=<n>` | 该机队列跑完,`ok` = 成品数 |
| `Traceback` | 异常;配合 log 尾部定位 |

- **进度账本(ledger)**:每台把 ledger 复制到 Drive `datasets/av2_1plus92_production_v14/db135_run100_ledger_m0of2.json` / `db135_run100_ledger_m1of2.json`,可直接读判进度。
- **候选清单(manifest)**:发射时每台写 `datasets/av2_1plus92_production_v14/manifest_m0of2.json` / `manifest_m1of2.json`(含 100 候选全清单 `log100` + 本机归属 `my_cands` + `machine_shard`)。
- 产出率参考:~37%(每 ~3 个判定 log 出 1 个成品),被拒多数是 `SKIP_static` / `SKIP_fine_dirty` / `SKIP_no_clean_window`。

---

## 4. 停止 / 重启(pkill 纪律)

- **⚠ pkill 必须用字符类**转义点号,只杀目标、别自杀:

```python
a._exec("pkill -f 'db135_run100' ; pkill -f 'db125_worker[.]py' ; pkill -f 'db131_merge[.]py'", 30)
```

  用 `'name[.]py'` 而不是 `'name.py'`——`.` 是正则通配,会误伤;更别写宽到能匹配到发射器自身的模式(历史 pkill 自杀坑)。
- 重启前清残留:kill 全部 worker → 清该 log 的 `root`(driver 内部判负时会自己 `rmtree`,手动中断需自己清)。
- 单机重跑不影响另一台(`MACHINE_SHARD` 隔离,各跑各的 log)。

---

## 5. 冷启动税 & OOM(v14.1 已修但要知道)

- **FLUX 首次载入 ~11min**(冷读 Drive 权重),之后靠 page cache ~71s。每台开机吃一次,别误判成卡死。
- **OOM 防护(v14.1)**:
  - worker Popen env 已带 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`(FLUX 线程 ~33.6GB + 24 probe worker 会撑爆 96GB)。
  - FLUX 生成走 `_pipe_retry`,OOM 退避重试 90s×3。
  - 若仍偶发 OOM:确认 env 传进去了(grep worker log)、别手动再起额外重进程抢显存。

---

## 6. 产物结构(收货核对)

Drive `koi_waymo2pano_colab/datasets/av2_1plus92_production_v14/<u8>/`:

```
<u8>/
  frames/          93 帧 ERP PNG(frame-1 完美 + 92 band)
  masks/           孪生 mask = (RGB和≥12)*255,近黑=无效(unknown)
  clip.mp4         H.264 预览
  sample_sheet.jpg 眼核缩略图
  ledger.json      分段耗时 + verdict + cascade 占比(t1pct/t2pct/residpct)
  worldmap_m2.png  该 log 的 world-BEV 地图(M2 fine+fuse)
```

- **黑斑是设计而非 bug**:贴身高大车辆 ∩ ego 拒源区 → 级联三层无供给 → 诚实黑,mask 自动标无效。**不**加 PP/FLUX 涂黑(把幻觉标真值比留黑更糟)。
- **眼核抽检**:先看 `sample_sheet.jpg`;`resid%`(ledger `residpct`)高的 log(如 19350c96 的 19.13%)是过曝/Telea 灰白块的边缘品,按用户裁定可保留。

---

## 7. 一次完整双机跑的动作清单

1. 确认两 runtime 隧道活 + FLUX cache 就位(§2.3)。
2. 干净 runtime 先跑 `db131_setup.py` 推内核/worker/merge 到 `/content/`。
3. `dr2.get('a100')` / `dr2.get('g2')` 取双句柄。
4. `dr_launch` 发射两份 `db135_run100.py`,分别替换 `MACHINE_SHARD` 为 `"0,2"` / `"1,2"`(§2.2)。
5. 前 ~11min 是 FLUX 冷载,别慌;之后 grep `QUEUED_FLUX` / `verdict=` 跟进度(§3)。
6. 每机 `DB134_QUEUE_EXHAUSTED ok=<n>` = 跑完。
7. 到 Drive 产物目录核对 `<u8>/` 结构 + 眼核 `sample_sheet.jpg`(§6)。
8. 隧道断 → 等 user 贴新 endpoint 写回 `active_url_5.json` / `active_url_g2.json`,重发该机 driver(另一台不动)。
