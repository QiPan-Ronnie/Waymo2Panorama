# Waymo2Panorama Stage 2 — 3 工作流并行 Plan

**创建**: 2026-05-25 · **作者**: Qi Pan (panq@usc.edu) + 队友 (Waymo) + 顾问 Koi Chen + Bosch 产学研

---

## 中文快速 scan (1 分钟看完)

### 我们在做什么
2026-05-22 跟队友 + Bosch 开完会, 项目从"单人 AV2 学术 paper"变成"产学研合作 + 队友并行 Waymo, 每周交付"。 7 个分支问题拆成 **3 个可并行 workstream**, 互不冲突, 一起干.

### 3 个 workstream 一句话总结

| WS  | 干啥                                                                                              | 时间   | 你这周看到啥                                                |
| --- | ------------------------------------------------------------------------------------------------- | ------ | ----------------------------------------------------------- |
| WS1 | 清洗 + 给队友 share. 修柱面图里突兀长方形 + 白色痕迹; 给队友 HDR script 和 Waymo loader 起始代码 | 2-3 天 | 给队友的 2 个 PR + 我们柱面图变干净                         |
| WS2 | L1 + ORB hybrid (队友 brainstorm 推荐方向). 给 cam 之间 overlap 区做特征匹配 + 预对齐 → 消除鬼影  | 5-7 天 | 新路线 #9, 10 anchor cycle-PSNR 数字, 预计 **+0.2~+0.5 dB** |
| WS3 | Option B reweight. 把已有的 新-D stereo 输出接到 L1 blend weight 上 (从未集成过)                  | 3-4 天 | 新路线 #10, 4 anchor 数字, 预计 **+0.05~+0.3 dB**           |

### 4 个诊断问题已答完 (Explore agent 已 verify, 不再当 workstream)

| 问题                            | 结论                                                                                                | 处理                |
| ------------------------------- | --------------------------------------------------------------------------------------------------- | ------------------- |
| L1 ERP 一辆车 2 个轮子          | **正常 design** — 文档化的 F5 限制. L1 球面假设深度=∞, 近物视差→鬼影                                | 告诉队友, WS2 会修  |
| 新-A 柱面有突兀长方形           | **原始 AV2 数据有** — 车顶硬件 (mounting plate) 在柱面更大 vertical FOV 下被暴露; sphere 当时剪掉了 | WS1 加 ego mask 修  |
| 新-A 柱面接缝处有白色痕迹       | **几何不同** — 柱面的 cos² 衰减比球面快, weight 在 vertical edge 不连续                             | WS1 调 feather 修   |
| AV2 vs Waymo 色差               | AV2 也有但轻 (-18%), 我们的 HDR 算法 dataset-agnostic, 改 5-cam adapter 直接给队友                  | WS1 share HDR adapter |

### 跟之前的关键差异

1. **不再担心 framing 问题** — Bosch 实测说 panorama 输入对他们 world model work, 我们做的东西本身就是有用的产物, 之前"AV ring → ERP distribution mismatch"焦虑收掉
2. **要并行做** — 队友推 Waymo, 我们推 AV2 改进 + 探索新路线, 但 share 通用模块 (HDR / loader 骨架)
3. **每周交付** — 这周 ship WS1 + WS3 出第一批数字, 下周 ship WS2

### Week 1 交付 (~6/1 之前)
- ✅ WS1 全部
- ✅ WS3 prototype + 4-anchor 数字

### Week 2 交付 (~6/8 之前)
- ✅ WS2 prototype + 10-anchor 数字
- 决定 WS4 选哪个 (Temporal coherence / Distance-to-boundary / 别的)

### 后续候选 (3 个 WS ship 后选)
1. Temporal coherence loss (G) — 5-7 天, +0.5~+1.2 dB, **novel**
2. Distance-to-boundary blending (F) — 2-3 天, stacks with everything
3. 4D-Gaussian splatting raycaster — 7-10 天, novel 但风险
4. Rolling-shutter compensation — 6-8 天, Waymo jelly effect 需要
5. PanFlow diffusion seam refinement — 等 PanFlow 开源
6. L1 跑 Waymo (Cluster D) — 队友 Waymo loader ship 之后 1 周

---

## Framework 集成 + 修复 contingency (新加)

### 工作流跟 Colab 的 boundary

**重要原则** (revised 2026-05-25): 凡是涉及"视觉输出 / 真实 AV2 数据 / 性能数字"的任务, **代码 done 之后必须立刻 Colab 验证**, 不要 batch 到后面. 单元测试只验证算法本地 self-consistent, 不能替代 real-data 测试. 唯一例外: 给队友用的代码 (我们没他数据).

| WS              | 本地 (Windows) 做啥                                                | Colab (A100 / CPU) 做啥                                                                    | 必须 Colab? |
| --------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ | ----------- |
| **WS1.1 HDR**   | 写 hdr_waymo_adapter.py + 单测                                     | (不需要 — 给队友本地用 Waymo, 我们没数据)                                                  | ❌ 单测够   |
| **WS1.2 mask**  | 写 ego_mask.py + wire 进 cylinder driver                           | **必须**: 代码 done 后立刻跑 anchor 60 cylinder re-render, 看 mask 是否盖对位置 (~2 分钟)  | ✅ 必须     |
| **WS1.3 feather** | 改 cylinder.py cos² → cos⁴                                         | **必须**: 跟 WS1.2 同一 Colab session, 看白色拼接痕迹是否消失 + cycle-PSNR 不退化 > 0.1 dB | ✅ 必须     |
| **WS1.4 loader**  | 写 waymo_loader.py skeleton (大量 TODO 留给队友)                   | (不需要 — 队友自己跑 Waymo 数据填上)                                                       | ❌ 给队友  |
| **WS2 L1+ORB**  | 写 alignment/pair_homography.py + 单测 + driver + eval             | **必须**: 10-anchor sweep cycle-PSNR vs L1, +0.2~+0.5 dB 目标                              | ✅ 必须     |
| **WS3 Option B** | 写 pipeline/option_b_reweight.py + 单测 + driver + eval            | **必须**: 4-anchor (0,60,90,150) cycle-PSNR, +0.05~+0.3 dB 目标                            | ✅ 必须     |

### Per-task Colab verify protocol

每个 ✅ 任务的标准流程:

1. **代码 done** (implementer subagent 完成本地单测 + commit + push)
2. **Spec + code review 通过**
3. **告诉用户开 Colab** — 我提供具体步骤 (e.g., "在 notebooks/runtime.ipynb Run All, 等 ✓ READY, 告诉我")
4. **跑 Colab verify** — controller 用 `mcp__colab-direct__exec` 跑 git pull + 验证脚本
5. **看结果** — 视觉 diff (从 Drive 下回本地查看) + 数字 (cycle-PSNR / lum gap)
6. **判定**: 
   - ✅ 通过 → task 标记 complete, 进下一个
   - ❌ 失败 → 退回 implementer subagent 修, 再 review, 再 verify
7. **commit verify 数字** — 把 Colab 输出 (json / png) 拉回本地 commit 到 outputs/phase3/, 进 progress.md
8. **任务结束** — 告诉用户可以 disconnect Colab (除非下一 task 也 Colab)

### Colab 怎么用 (agent-colab-direct v0.1.0)

每次 Colab task 标准流程:

1. **用户**: 在 Colab 打开 `notebooks/runtime.ipynb` → Run All
2. **用户**: 等 30-60s 看到 `✓ READY` + tunnel URL printed
3. **用户**: 告诉我 "ready"
4. **我**: 拉取 active URL — `cat <Drive>/runtime/active_url.json` 或调 MCP tool 自动读
5. **我**: 同步代码 — `mcp__colab-direct__exec(cmd=["bash", "-c", "cd /content/Waymo2Panorama && git pull"])`
6. **我**: 跑任务 — `mcp__colab-direct__exec(cmd=["python", "scripts/phase3/eval_l1_orb_hybrid_cycle.py", "--anchors", "0,30,60,...", ...])` (短的 sync 返回, 长的 async + `wait_for_job(id)`)
7. **我**: 看 SSE 实时 stdout, 结果写 Drive
8. **我**: 跑完报告给用户; 任务结束后用户手动 disconnect runtime (省钱)

### 长 batch 任务 (10-anchor sweep) 用 `@checkpointed`

WS2 + WS3 evaluation 脚本 wrap 一层:

```python
import colab_direct as cd

@cd.checkpointed(
    unit_id_fn=lambda anchor_idx: f"anchor_{anchor_idx:03d}",
    storage_dir="/content/drive/MyDrive/koi_waymo2pano_colab/progress/ws2_l1_orb_hybrid/",
)
def eval_one_anchor(anchor_idx, ...):
    # 跑单个 anchor 的 L1+ORB + cycle-PSNR 评估
    return result_dict
```

Colab 中途断了, 重启后 run-all 会自动 skip 已完成的 anchor (基于 `.done` marker 文件).

### Drive 路径速查

- Workspace root: `MyDrive/koi_waymo2pano_colab/` (fileId `1o0Ewp6tTXjH_C0g8wv2mJPh2MHt7mpJ1`)
- `runtime/active_url.json` (fileId `1cGUTCJYXmPeJYWwEP-3TX6wwNbVk-h7V`) — agent-colab-direct heartbeat
- 新-D stereo 缓存输出 (WS3 input): `outputs/phase3/p3.6_stereo/anchor_{000,060,090,150}/stereo_*.npz`
- WS2 输出位置: `outputs/phase3/p3.7_l1_orb_hybrid/anchor_*/`
- WS3 输出位置: `outputs/phase3/p3.8_option_b_reweight/anchor_*/`
- 进度 checkpoint: `progress/ws2_l1_orb_hybrid/` + `progress/ws3_option_b_reweight/`

### Framework bug contingency (重要)

agent-colab-direct v0.1.0 **smoke test 通过, 但日常 use validation pending** (per handoff lesson #16). 这套 plan 是**第一次真用**, 会暴露 rough edges.

遇到 framework bug 的处理流程:

1. **遇 bug 立刻停止 task work, 切到 framework repo**: `D:/BaiduSyncdisk/2024 to future/agent-colab-direct/`
2. **复现 bug**: 写最小 repro case, 跑 `pytest tests/test_<related_module>.py` 看是否能 trigger
3. **修 bug + 测试**: 改源码, 跑 `pytest tests/` 全套 (80 测试), 全 pass 才行
4. **发布 v0.1.x**: bump version in `pyproject.toml`, commit 单行 message `"v0.1.x: fix <thing>"`, push 到 `git@github.com:QiPan-Ronnie/agent-colab-direct.git`
5. **Colab 装新版**: 在 Colab cell 跑 `!pip install --force-reinstall git+https://github.com/QiPan-Ronnie/agent-colab-direct.git`
6. **resume task work**: 继续原 WS

可能遇到的 bug 类型 + 预案:

| Bug 类型                                    | 检查                                                                            | 修法                                                                                |
| ------------------------------------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `exec` SSE stdout 不返回 / 卡住             | 短任务用 sync, 长任务必须用 async + wait_for_job. 看 `tests/test_exec_async.py` | 检查 server 的 SSE flush 逻辑, 修 `agent_colab_direct/server/executor.py`           |
| Drive `active_url.json` 找不到 (lesson #15) | FUSE 写即时, Drive 后端可能延迟分钟级                                           | 直接 SSH-style 让用户从 Colab 左侧 file panel cat 给我; 或等 60-90s                  |
| Tunnel URL 改了 (Colab 重连后)              | active_url.json 自动每 5s 更新                                                  | 调 `mcp__colab-direct__refresh_url` 强制重读, 不要 cache 老 URL                     |
| `write_file` 超 10MB 限制                   | check `tests/test_write_file.py` 边界                                           | 拆成多个 chunk 写; 或改 server 限制 (但要小心 token bandwidth)                      |
| `shell` 持久 session 突然失效               | pexpect session 可能因 Colab 重启或长 idle 断                                   | 调 `mcp__colab-direct__shell_reset`; 之后 cd / source venv 重做                     |
| `@checkpointed` 跨进程不 skip 已完成 unit   | marker 文件可能没写到 Drive (FUSE delay)                                        | 在 marker 写完后强制 `os.fsync`; 或改用 SQLite 而非文件标记                         |
| MCP server 启动失败                         | `colab-direct mcp` 没在 ~/.claude.json 注册                                     | 看 `agent-colab-direct/docs/migration_from_acq.md` 配置例子, 重启 Claude session    |

bug 修复完, 把这条经验加进 Waymo2Panorama `agent/progress.md` 当条 entry 的 "lessons learned" 段, 同时 update `agent/handoff.md` defensive lessons (会从 #16 → #17, #18...).

### Framework 之外的 fallback

如果 framework 修不动 (block > 半天), 临时 fallback:

1. **诊断/小 verify** (WS1.2, WS1.3 verification): 本地跑就够, 不需 Colab
2. **L1+ORB / Option B 数字** (WS2/3 eval): 临时退回 老 agent-colab-queue 的 git-push-job-spec 流程, 但**只用于 unblock paper deadline, 别贪用** — 一旦 framework 修好立刻切回

### Local 工作的 file 编辑约定

写 / 改 / 删都用 Edit / Write tool 直接动 Windows 本地 repo, **不要**走 Colab 改文件 (除非临时调试). 用户已授权 direct push to main (per `[[feedback-direct-push-main-waymo2pano]]`), commit message 一行短描述, 不要 noise.

### Git discipline (全程保持, 不能 batch 到最后)

**规则**:

1. **每个逻辑单元都 commit** — 不要攒一堆改动再一次性 commit. 例: WS1.2 ego_mask.py 写完即 commit; cylinder.py 改完即 commit; 不要等 WS1.2 + WS1.3 一起.
2. **每天至少 push 一次** — 即使工作没完成, 把当天的进度 push 到 main, 保护工作 (Colab 端 worker 是 pull main 的, 也需要看到最新代码).
3. **Commit message 格式**: 一行 50 字以内, 模式 `WS<N>.<sub>: <一句话>`. 例:
   - `WS1.1: add hdr_waymo_adapter.py for 5-cam arc`
   - `WS2: implement compute_overlap_homography with DISK+LightGlue`
   - `WS3: build_stereo_confidence_mask draft`
   - `fix: cylinder vertical feather cos² → cos⁴`
   - `progress: WS1 + WS3 day 1 done`
4. **progress.md 同步更新, 不攒** — 每个 WS 完成时就在 `agent/progress.md` 顶部加 entry (怎么做 / 结果 / Deliverables / Status / Next), 不要等全部 ship 才补.
5. **测试通过才 commit** — local pytest pass / single-anchor run 没 crash 才 commit. broken 状态不进 main.
6. **大里程碑打 tag** — WS2 prototype ship 打 `v0.3-l1-orb-hybrid`; WS3 ship 打 `v0.3-option-b-reweight`; 3 个 WS 全 ship 打 `v0.4-stage2-complete`.
7. **framework bug 修复同步 commit** — 如果中途修了 agent-colab-direct, 那边也要 commit + push + bump version, 不要 local-only 改.
8. **不用 `git add .` / `git add -A`** — 显式 `git add <file>` 避免误推 secrets / 大文件 / Colab cache.
9. **遵守已有约定**: 用户已授权 direct push main, 不需要 PR; 但 commit 之前 `git status` 看一眼, 别带 noise (如 `__pycache__/`, `.ipynb_checkpoints/`).

**典型一天的 git 节奏 (举例 WS2 day 2)**:

```
[morning]
  edit pair_homography.py (add fallback logic)
  pytest tests/test_pair_homography.py → pass
  git add code/waymo2panorama/alignment/pair_homography.py
  git commit -m "WS2: pair_homography fallback for low-inlier pairs"
  git push

[afternoon]
  edit stitch_frame.py (add pre-warp hook)
  single-anchor run on Colab → done.json appears
  git add code/waymo2panorama/pipeline/stitch_frame.py
  git commit -m "WS2: stitch_frame pre-warp hook"
  git push

[end of day]
  edit agent/progress.md (add WS2 day 2 entry)
  git add agent/progress.md
  git commit -m "progress: WS2 day 2 — pre-warp hook integrated, single-anchor OK"
  git push
```

3 个 commit / 天, 每个都 atomic, main 全程 green.

---

## 🌅 明天 Verify Checklist (Stage 2 Day 1 code 全 ship, 等用户回来做 Colab verify)

**状态 2026-05-25 ~late evening**:
- T1 (WS1.1 HDR Waymo adapter): code DONE, **不需要 Colab verify** (给队友用 Waymo, 我们没数据). 当 teammate 跑后反馈再 tune.
- T2 (WS1.2 ego mask + WS1.3 cos⁴ feather): code DONE, **Colab verify 已通过** (anchor 60, 0 dB regression, +24.9pp coverage 验证). 完结.
- T3 (WS1.4 Waymo loader skeleton): code DONE, **不需要 Colab verify** (给队友填 calib stub).
- T4 (WS3 Option B reweight): code DONE + 单测 12/12, **Colab verify 待你回来跑** (本节 §A).
- T5 (WS2 L1+ORB hybrid chain warp): code DONE + 单测 22/22, **Colab verify 待你回来跑** (本节 §B).

**整体节奏**: 你睡, 回来后我陪你按 §A → §B 顺序跑. 一个 task verify 完才进下个. 大致需要 **~1.5 小时**:
- §A 设置 Colab + T4 4-anchor eval ≈ 20-30 分钟
- §B T5 单 anchor smoke + 10-anchor eval ≈ 50-70 分钟
- §C 收尾 (handoff.md 更新 + 最终 review + 数字写入 progress) ≈ 10-15 分钟

---

### §A — T4 Colab Verify (WS3 Option B reweight, 4 anchors)

**前提**: 你打开 Colab + 用 GPU runtime + 跑 `notebooks/runtime.ipynb` Run All + 告诉我 "ready". 我从 `runtime/active_url.json` 拿 URL+token 自动接管.

**步骤** (我自动 dispatch, 你只需提供 Colab):

1. **环境同步** (我跑): `git pull` on Colab → 拉最新 WS2/WS3 + cleanup commits (b5af3c6 head).

2. **Step A — smoke test** (~30 秒, 1 anchor): 
   ```bash
   python scripts/phase3/run_option_b_reweight.py \
     --pi3-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor/anchor_060 \
     --stereo-cache-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.6_stereo/anchor_060 \
     --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/T4_verify/anchor_060 \
     --alpha 1.0
   ```
   - **看**: `option_b_l1.png` (1024×2048 ERP), `confidence_mask.png` (turbo-colored 稀疏点状), `summary.json` 里 `mask_stats.frac_above_0.05` 在 0.01-0.10 范围
   - **同时跑 A/B baseline**: 加 `--no-reweight` 输出到 `anchor_060_noreweight/` 比较

3. **Step B — 4-anchor cycle eval** (~5-10 分钟):
   ```bash
   python scripts/phase3/eval_option_b_cycle.py \
     --pi3-cache-root /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor \
     --stereo-cache-root /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.6_stereo \
     --anchors 0 60 90 150 \
     --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/T4_verify/eval_cycle \
     --alpha 1.0
   ```
   - 跑完读 `eval_option_b_cycle.json` aggregate

4. **Step C — pair with held-out cycle protocol** (~10 分钟):
   - 跑 `scripts/phase2/eval_cycle_consistency.py` on `option_b_l1.png` 拿绝对 PSNR vs 12.34 dB baseline

**Pass/fail thresholds (T4 Option B)**:

| Mean ΔPSNR (4 anchors, vs L1 baseline 12.34 dB) | Verdict | Action |
|---|---|---|
| ≥ +0.05 dB | 🟢 Pass — ship + 写进 progress | 进 §B |
| -0.03 to +0.05 dB | 🟡 Neutral — 数字微弱 | 进 §B (这条 paper 价值小但 stack 安全) |
| < -0.03 dB | 🔴 Negative — 退回检查 | 用 alpha sweep [0.5, 2.0] + sigma sweep [6, 24] 或 drop |

**理论上限** (per T4 implementer 自述): multiband 内部会 per-pixel 重新 normalize, 所以 reweight 只影响 ~15% 重叠区. 上限 ~+0.3 dB. 多了说明 mask 假阳性, 少了说明 mask 太稀疏.

---

### §B — T5 Colab Verify (WS2 L1+ORB hybrid, 10 anchors)

**前提**: Colab 还在 (从 §A 续上), 或者 §A 完跑完再开. GPU 必需 (DISK+LightGlue 在 CPU 慢 4-6 倍).

**步骤**:

1. **环境同步**: 已在 §A 做完, skip.

2. **Step A — smoke test 单 anchor** (~2-4 分钟):
   ```bash
   python scripts/phase3/run_l1_orb_hybrid.py \
     --input-mode pi3-cache \
     --pi3-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor/anchor_060 \
     --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/T5_verify/anchor_060 \
     --reference-cam ring_front_center
   ```
   - **看 stdout**: `[l1-orb] pair homographies: N/7 ok` with N ≥ 4
     - 每对 `inliers=` 100-500, `residual=` < 2px
     - Chain log `n_hops` 应该是 (0, 1, 2, 3, 3, 2, 1) — 对称
     - **NO** `WARNING: 0/N pairs ok`
   - **看 文件**:
     - `l1_orb_hybrid.png` — 跟 baseline `sphere_l1.png` (T2 verify 那张) 对比, 看重叠区 2-轮子 ghost 是否减少
     - `pair_homography_summary.json` — 至少 4 对 status="ok"

3. **Step B — A/B baseline 单 anchor**:
   ```bash
   python scripts/phase3/run_l1_orb_hybrid.py \
     --input-mode pi3-cache \
     --pi3-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor/anchor_060 \
     --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/T5_verify/anchor_060_noprewarp \
     --no-prewarp
   ```
   - 视觉 diff `anchor_060/l1_orb_hybrid.png` vs `anchor_060_noprewarp/l1_orb_hybrid.png` — 期望 prewarp 版本接缝更清晰, 特别后部 ring

4. **Step C — 10-anchor cycle eval** (~25-40 分钟 GPU, `@cd.checkpointed` 可断点续跑):
   ```bash
   python scripts/phase3/eval_l1_orb_hybrid_cycle.py \
     --pi3-cache-root /content/drive/MyDrive/koi_waymo2pano_colab/outputs/phase3/p3.1_multi_anchor \
     --anchors 0 30 60 90 120 150 180 210 240 270 \
     --output-dir /content/drive/MyDrive/koi_waymo2pano_colab/outputs/T5_verify/eval_cycle \
     --reference-cam ring_front_center
   ```
   - **看每 anchor stdout**:
     - `PSNR (L1+prewarp vs L1) = NN.NN dB` (inter-method) — overlap region 更低 (说明 warp 集中在 overlap)
     - `pair homographies ok: N/7 (mean inliers=K, mean residual=Lpx)` with N ≥ 5
     - `seam_gradient_energy_delta = -X` (负 = seam smoother)
   - **看 aggregate JSON**:
     - `fraction_pair_homographies_ok` ≥ 0.6
     - `mean_seam_gradient_energy_delta` < 0

5. **Step D — held-out cycle protocol for headline ΔPSNR** (~20 分钟):
   - 对每 anchor 跑 `eval_cycle_consistency.py` against `l1_orb_hybrid.png` vs baseline
   - 计算 mean ΔPSNR over 10 anchors

**Pass/fail thresholds (T5 L1+ORB)**:

| Mean ΔPSNR (10 anchors, vs L1 baseline 12.34 dB) | Verdict | Action |
|---|---|---|
| ≥ +0.20 dB | 🟢 STRONG pass | Ship + 写 progress + 进 §C |
| +0.05 to +0.20 dB | 🟡 WEAK pass | Ship, flag v2 (global bundle adjustment) for later milestone |
| -0.05 to +0.05 dB | ⚪ NEUTRAL | 检查 `fraction_pair_homographies_ok` (<0.5 说明 fallback 过多 → 放宽 thresholds 重试) OR drop |
| < -0.05 dB | 🔴 NEG | 退回. 可能原因: chain drift 后部 cam 过大 / non-overlap 区被 warp 破坏 / homography 拟合不稳 |

**理论 ceiling** (per T5 implementer 自述): chain warp 后部 cam (rear_l, rear_r 3 hops from front_center) 期望 2-5 px 额外 registration error. 若数字差, 可考虑 v2 (global BA 或 clamp warp magnitude `||H - I||_F > T → fallback identity`).

---

### §C — 收尾 (verify 后)

**根据 T4 + T5 verify 结果决定**:

1. **若 T4 + T5 都 pass / weak pass**:
   - 更新 `agent/handoff.md`: 8 routes → 10 routes 表 (加 Option B + L1+ORB 行 + verdict + dB 数字)
   - 更新 `deliverables/handoff_to_koi_w2_*.{md,pdf}` 加新两 route 一节 (这个我可以草拟; user 决定要不要给 Koi 看)
   - 更新 progress.md: 把 verify 数字 + 视觉判断 写进 stage-2 entry
   - 跑 final code reviewer (T9, opus) on 整个 stage-2 diff
   - Tag `v0.4-stage2-complete`

2. **若 T4 negative / T5 negative**:
   - 写 NEG report 到 `notes/option_b_reweight_neg.md` 或 `notes/l1_orb_hybrid_neg.md`
   - 不删除代码 (留 baseline 给 future iterations)
   - progress.md 标 NEG + 学到的
   - 不进 handoff route 表

3. **若 mixed (一个 pass 一个 NEG)**:
   - 单独处理. handoff 加 pass 的那条.

**Tag**: `v0.4-stage2-complete` 在所有 verify + progress 写完后打.

---

### 框架 bug 修复 (低优先, 后续)

提醒: **agent-colab-direct `colab-direct generate-notebook` 在 Windows Git-Bash 跑时 mangle 路径** (实际暴露 bug). 已通过 hotfix `a4fc0e6` 临时解决项目内 notebook. 永久修需:
1. 在 `D:/BaiduSyncdisk/2024 to future/agent-colab-direct/` 找 generate-notebook 源代码
2. Drive 路径用 `//content/drive/...` 双斜杠或 raw string 绕 MSYS
3. 加单测 (跨平台 mock 测试)
4. bump v0.1.1 + push
5. Waymo2Panorama notebook 不需重生成 (hotfix 已覆盖)

不阻塞 T4/T5 verify, 等明天 verify 完再说.

---

## 详细 plan (英文 reference, 工程层面)

### Approach: 3 parallel workstreams

| WS      | Theme                                       | Cost     | Touches                                                                | Deliverable                                                                                              |
| ------- | ------------------------------------------- | -------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| **WS1** | Share + cleanup                             | 2-3 days | `color/`, `data_io/`, `projection/cylinder.py`                         | HDR script handoff for teammate, ego-mask + cylinder feather fix, Waymo loader skeleton                  |
| **WS2** | L1+ORB hybrid (per-cam pre-warp homography) | 5-7 days | new `alignment/pair_homography.py`, hook in `pipeline/stitch_frame.py` | New stitching route, 10-anchor cycle-PSNR vs L1, expected +0.2–0.5 dB                                    |
| **WS3** | Option B reweight (新-D stereo integration) | 3-4 days | new `pipeline/option_b_reweight.py` or `blending/` hook                | New stitching route using sparse stereo 3D as confidence mask, expected +0.05–0.3 dB                     |

All 3 WS run in parallel — no file overlap, no dependency.

---

### Workstream 1 — Sharing + cleanup (2-3 days)

#### WS1.1 — HDR adapter for Waymo (give to teammate)

**Why**: Teammate sees dramatic shadow-side darkening on Waymo. AV2 has the same issue (lum gap 16.62 → 13.61 = -18% per 新-E HDR). Our `hdr_gain_estimate.py` algorithm is dataset-agnostic; needs only a small adapter for Waymo's lack of ring closure (5 front cams, no rear).

**Files**:
- Reuse: `code/waymo2panorama/color/hdr_gain_estimate.py:50-165` (`extract_overlap_pixels()`, `global_color_correction()`)
- New: `code/waymo2panorama/color/hdr_waymo_adapter.py` (~50 lines)
- New: `scripts/run_hdr_compensation_waymo.py` driver

**Verification**: Apply to teammate's anchor 60 area on Waymo, expect -15 to -25% lum gap reduction.

#### WS1.2 — Ego mask for 突兀长方形 fix

**Why**: Cylinder's wider vertical FOV (`v_max=±45°`) exposes AV2 ego hardware. Sphere's `asin()` clipped it.

**Files**:
- New: `code/waymo2panorama/data_io/ego_mask.py` (~80 lines) — per-cam binary mask
- Modify: `code/waymo2panorama/projection/cylinder.py:135-144` — apply mask before `cv2.remap`

**Verification**: Re-render anchor 60 cylinder ERP, confirm rectangle gone.

#### WS1.3 — Cylinder vertical feather fix for 白色痕迹

**Why**: Cylinder's `cos²(angle) = z_cam / √(1+h²)` decays faster than sphere's at large |v|.

**Files**:
- Modify: `code/waymo2panorama/projection/cylinder.py:151-154` — replace `cos²` with softer schedule (`cos⁴` or explicit vertical soft-cutoff at `|h| > 0.8·h_max`)

**Verification**: Re-render, confirm white seam traces gone, cycle-PSNR not regressed > 0.1 dB.

#### WS1.4 — Waymo loader skeleton (give to teammate)

**Why**: No existing Waymo loader in sibling dirs. Save teammate 2-3 days.

**Files**:
- New: `code/waymo2panorama/data_io/waymo_loader.py` (~200 lines, modeled on `av2_loader.py:64-191`):
  - `RING_CAMS_5` tuple
  - `WaymoRingLoader` class with same public API as `AV2RingLoader`
  - `_load_calibrations()` stub with TODO for teammate's Waymo format
  - `_index_images()` for Waymo file naming

**Verification**: Send to teammate, they fill `_load_calibrations()` body.

---

### Workstream 2 — L1 + ORB hybrid (Architecture B: per-cam pre-warp)

**Goal**: Fix overlap parallax ghost by pre-warping each cam image to align with neighbor before sphere projection.

**Pipeline**:
```
Original cam images       Match adjacent pairs        Pre-warped cams       L1 sphere
(7 cams)         ──>      (DISK + LightGlue)     ──>  (homography applied)  ──>  ERP
                          per-pair homography
```

**Files**:
- **New**: `code/waymo2panorama/alignment/pair_homography.py` (~150 lines)
  - `compute_overlap_homography(img_a, img_b, K_a, K_b, T_ego_a, T_ego_b) -> (H_a_to_b, inlier_count, residual_px)`
  - Reuses `wide_baseline_stereo.py:125-212` for DISK + LightGlue
  - `cv2.findHomography(..., cv2.RANSAC, 3.0)`
  - Soft-fallback: if `inlier_count < 30` or `residual_px > 2.0`, return identity + log warning
- **Modify**: `code/waymo2panorama/pipeline/stitch_frame.py:15-38` — pre-call hook
- **New**: `scripts/phase3/run_l1_orb_hybrid.py` driver
- **New**: `scripts/phase3/eval_l1_orb_hybrid_cycle.py` — wrap with `@cd.checkpointed`

**Reuse list**:

| Function                    | From                                                          | Why                                                |
| --------------------------- | ------------------------------------------------------------- | -------------------------------------------------- |
| `extract_pair_features(...)`| `stereo/wide_baseline_stereo.py:125-162`                      | DISK keypoint detection                            |
| `match_with_lightglue(...)` | `stereo/wide_baseline_stereo.py:165-212`                      | LightGlue matcher (CPU 100-300 ms/pair)            |
| `render_camera_to_erp(...)` | `projection/sphere_projection.py:29-142`                      | L1 sphere base, unchanged                          |
| `multiband_blend(...)`      | `blending/multiband.py:57-129`                                | Existing blend, unchanged                          |

**Stacking compatibility**: ✅ 新-E HDR (warp before HDR) · ✅ 新-B graphcut (seams on aligned output) · ✅ orthogonal to 新-C IPM

**Verification**:
- Run on 10 anchors (0, 30, 60, ..., 270) via Colab
- Target: cycle-PSNR mean **+0.2 to +0.5 dB** vs L1 12.34 dB
- Inspect anchor 60 visual: 2-wheel ghost reduced or gone
- Failure: if mean ΔPSNR < 0 dB, document as NEG, switch to Architecture A (post-warp) fallback

**Day-by-day**:

| Day | Local                                                | Colab                                                          |
| --- | ---------------------------------------------------- | -------------------------------------------------------------- |
| D1  | Implement `pair_homography.py` + unit test          | -                                                              |
| D2  | Integrate into `stitch_frame.py`                    | Single-anchor end-to-end run                                   |
| D3  | -                                                    | 5-anchor sweep, measure inlier counts + residuals              |
| D4  | -                                                    | 10-anchor cycle-PSNR + anchor 60 visual diff                   |
| D5  | Write `notes/l1_orb_hybrid_report.md` + commit + add to progress.md | -                                                              |

---

### Workstream 3 — Option B reweight (新-D stereo confidence integration)

**Goal**: Use 新-D's sparse stereo 3D points (already extracted, never integrated) as confidence mask to reweight L1's blend.

**Pipeline**:
```
Sparse 3D points       Project to ERP        Build confidence       L1 blend with
from 新-D       ──>    (per-cam-pair         mask C(u,v) on ERP ──>  modified weights:
(ego frame)            triangulated)         (gaussian smooth)       w'(u,v) = w(u,v) * (1 + α·C)
```

**Files**:
- **New**: `code/waymo2panorama/pipeline/option_b_reweight.py` (~120 lines)
- **Modify**: `code/waymo2panorama/pipeline/stitch_frame.py` (or new wrapper)
- **New**: `scripts/phase3/run_option_b_reweight.py` driver
- **New**: `scripts/phase3/eval_option_b_cycle.py` — wrap with `@cd.checkpointed`

**Reuse list**:

| Function                       | From                                            | Why                                                 |
| ------------------------------ | ----------------------------------------------- | --------------------------------------------------- |
| `process_anchor_all_pairs(...)`| `stereo/wide_baseline_stereo.py` (existing)    | Returns ego-frame 3D points per cam pair            |
| `ego_points_to_erp_uv(...)`    | `pipeline/lift_and_project.py:30-68`           | Convert ego 3D to ERP pixel coord                   |
| `render_camera_to_erp(...)`    | `projection/sphere_projection.py`              | L1 base, slightly modified for weight multiplier    |

**Stacking compatibility**: ✅ 新-E HDR · ✅ 新-B graphcut · ✅ WS2 L1+ORB hybrid

**Verification**:
- Run on 4 anchors (0, 60, 90, 150) via Colab
- Target: cycle-PSNR mean **+0.05 to +0.3 dB** vs L1
- Per-anchor breakdown: anchors with more stereo inliers (60/90/150 = 307/390/390 pts) should gain more than sparse anchor 0 (142 pts)

**Day-by-day**:

| Day | Local                                         | Colab                                                       |
| --- | --------------------------------------------- | ----------------------------------------------------------- |
| D1  | Implement `build_stereo_confidence_mask()`    | Unit test on anchor 60                                      |
| D2  | Integrate into stitch_frame                   | Single-anchor end-to-end run                                |
| D3  | -                                             | 4-anchor cycle-PSNR + stereo-density correlation            |
| D4  | Write `notes/option_b_reweight_report.md` + commit | -                                                       |

---

## Diagnostic Findings (closed 2026-05-25 by Explore agent)

| Issue                       | Finding                                                                                                                  | Action                                          |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------- |
| 2-轮子 overlap ghost        | **Expected** — documented F5 failure (`notes/baseline_diagnosis.md:51`). L1 sphere infinity-depth assumption.            | Tell teammate by-design. WS2 + WS3 address it. |
| 突兀长方形 on 新-A cylinder | **Original data** — AV2 ego hardware exposed at cylinder's wider vertical FOV. Not a code bug.                           | WS1.2 ego mask fix                              |
| 白色拼接痕迹 on cylinder     | **Geometry-induced** — cylinder's `cos²(angle) = z_cam/√(1+h²)` decays faster than sphere's at large \|v\|.              | WS1.3 vertical feather fix                      |
| AV2 vs Waymo color shift    | **AV2 has it (-18% per 新-E); Waymo likely worse**. HDR algorithm dataset-agnostic, needs ring-closure adapter for Waymo. | WS1.1 HDR adapter for teammate                  |

---

## Asset inventory (paths user provided 2026-05-25)

### Images
Root: `D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama/deliverables/images/`

18 PNGs include: `l1_erp.png`, `route_cylinder_vs_sphere.png` (突兀长方形 verify), `route_graphcut_seam_compare.png`, `route_hdr_before_after.png`, `route_ipm_multi_region_compare.png`, `route_wide_baseline_depth.png`, `ipm_hybrid_10anchor_honest.png`, etc.

### Videos
Local: `D:/BaiduSyncdisk/2024 to future/koi chen/experiments/Waymo2Panorama/deliverables/video/` (~125 MB, 7 files)
- baseline_video.mp4 / l3_video.mp4 / ipm_hybrid_video.mp4 / cylindrical_video.mp4 / graphcut_video.mp4 / ipm_multi_region_video.mp4 / hdr_video.mp4

Drive direct view URLs: see `agent/handoff.md` "Video deliverables" table.

### Drive workspace
`MyDrive/koi_waymo2pano_colab/` (root fileId `1o0Ewp6tTXjH_C0g8wv2mJPh2MHt7mpJ1`)
- `runtime/active_url.json` (fileId `1cGUTCJYXmPeJYWwEP-3TX6wwNbVk-h7V`) — agent-colab-direct heartbeat
- `outputs/<route>_video/<log_id>/*.mp4` — actual videos
- `data/argoverse2/` — 4 logs, ~32 GB
- `outputs/phase3/p3.6_stereo/anchor_{000,060,090,150}/stereo_*.npz` — WS3 input (新-D cache)

---

## End-to-end verification

After WS1 + WS2 + WS3 ship (target: 2026-06-08, ~2 weeks):

1. **Cycle-PSNR table** updated with 2 new routes (L1+ORB hybrid, Option B reweight) over 10 anchors, σ reported
2. **Visual diff panel** at anchor 60 showing: L1 → L1+ORB → L1+ORB+OptionB stacked, with 2-wheel ghost region zoomed-in
3. **Cleanup confirmation**: re-render `route_cylinder_vs_sphere.png` shows no 突兀长方形 and no 白色拼接痕迹
4. **Teammate handoff**: confirm teammate received HDR adapter + Waymo loader skeleton, can run on their Waymo data
5. **Progress log**: 3 entries in `agent/progress.md` (one per WS)
6. **Updated handoff**: 8 stitching routes → 10 routes, ranking table refreshed
7. **Framework feedback**: if any framework rough edges hit during WS work, agent-colab-direct bumped to v0.1.1 with fixes; lessons added to handoff `agent/handoff.md`

---

## Deferred / next workstream candidates (after 3 WS ship)

Ranked by Agent 3's brainstorm:

1. **Temporal coherence loss** (G) — 5-7 days, +0.5-1.2 dB, novel — top pick for WS4 if WS2/WS3 show momentum
2. **Distance-to-boundary blending** (F) — 2-3 days, stacks with everything — quick add
3. **4D-Gaussian splatting raycaster** — 7-10 days, novel but riskier
4. **Rolling-shutter compensation** — 6-8 days, important for Waymo (teammate's jelly effect concern)
5. **PanFlow diffusion seam refinement** — 4-5 days, blocked on PanFlow code release
6. **L1 cross-dataset run on Waymo** — 1 week after teammate's loader ships
