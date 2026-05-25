# Waymo2Panorama Progress

> ### 2026-05-25 ~late evening UTC — [Stage 2 Day 1 evening] T4 (WS3 Option B reweight) + T5 (WS2 L1+ORB chain warp) code ship + reviews, Colab verify 待明天
> - **怎么做**: 同 day 接续, opus 4.7 implementer + spec reviewer + code reviewer 三段式 per task. 用户晚上要睡, 接受我"先全推完 code 再批 Colab verify" (老 plan "每 task verify 完才进下一个" 妥协, 但 verify discipline 在明天 §A §B 文档化), 文档化在 plan `agent/plans/adaptive-seeking-turtle.md` 顶部新增 "🌅 明天 Verify Checklist" 段.
>   - **T4 / WS3 Option B reweight** (3 commits + 1 cleanup `1941b23,cab3051,af17c7b,d200275`): 新写 `code/waymo2panorama/pipeline/option_b_reweight.py` (284 LOC) — `build_stereo_confidence_mask(stereo_npz_paths, erp_hw, sigma_px)` 从 新-D 缓存 (key=`pts_3d_ego`) 加载 ego-frame 3D 点 → `ego_points_to_erp_uv()` 投到 ERP 像素 → gaussian splat (max-merge, ERP 横轴 wrap) → 归一化到 [0,1]; `apply_option_b_reweight(weights_dict, mask, alpha)` 公式 `w' = w*(1+α*C)`, alpha=0 identity, 不 mutate input. 新 driver `run_option_b_reweight.py` (235 LOC, `--alpha` `--no-reweight` A/B). 新 eval `eval_option_b_cycle.py` (447 LOC, `@cd.checkpointed` graceful guard, 用 inter-method PSNR pattern 同 `eval_cylindrical_cycle.py`). 12 pytest 全 pass (含 mask range warning + alpha=0 identity + ERP wrap + 空文件 graceful). Code reviewer flagged stale "accumulate" 注释 + mask range sanity (`> 1.0+1e-3` warning) — 都 fix 进 `d200275`. **Colab verify 明天**: 4-anchor cycle eval, target +0.05~+0.3 dB. multiband 内部 per-pixel renormalize → reweight 只影响 ~15% overlap 区, 上限 ~+0.3 dB.
>   - **T5 / WS2 L1+ORB hybrid chain warp** (4 commits + 1 cleanup `33834ec,d1a17af,cc1a8d8,68c3b72,b5af3c6`): 新模块 `code/waymo2panorama/alignment/pair_homography.py` (~250 LOC) — `compute_overlap_homography(img_a, img_b, K_*, T_ego_*, overlap_roi_*, min_matches, min_inliers, max_residual_px, ransac_thresh_px)` 复用 `wide_baseline_stereo.py:125-212` 的 DISK + LightGlue (不重写), `cv2.findHomography` RANSAC 3px, 4 status (ok/low_inliers/high_residual/no_matches), 所有 fallback path 都返回 `np.eye(3)` (caller 无脑 warp). `RING_ORDER` + `ADJACENT_PAIRS` (7 对 含 wrap), `compose_homographies([H1,H2]) = H2 @ H1` 左乘, `ring_path_homography(target, ref, ...)` 走最短 ring path (两方向选短). 改 `pipeline/stitch_frame.py` 加新函数 `stitch_one_frame_with_prewarp(frame_sample, ..., reference_cam="ring_front_center") -> (erp_uint8, summary)` — 每个 non-ref cam 沿最短 ring path 链式 compose 到 ref → `cv2.warpPerspective` 预对齐 → 喂回 `render_camera_to_erp` (无修改) → `multiband_blend` (无修改). 旧 `stitch_one_frame` 100% 保留 (backward compat). 新 driver `run_l1_orb_hybrid.py` (250+ LOC, `--reference-cam` `--no-prewarp` A/B). 新 eval `eval_l1_orb_hybrid_cycle.py` (250+ LOC, `@cd.checkpointed` 同 T4 模式). 22 pytest 全 pass (含 chain compose 顺序 + 最短路 + 反向 hop inverse + missing hop fallback + KeyError + DISK 复现已知 H within 5px). Code reviewer flagged 死代码 (chain warp swap 后 `_prewarp_one_cam` + `ADJACENT_PAIRS_RING` constant 未用) + 1 unused import — 都 fix 进 `b5af3c6` (-44 LOC). **Colab verify 明天**: 10-anchor cycle eval, target +0.20 dB (STRONG), +0.05~+0.20 (WEAK), <0 (NEG). chain drift 后部 cam (rear_*, 3 hops from front_center) 期望 +2-5px registration error.
>   - **明天 verify 文档化** (plan 顶部新增): §A T4 4-anchor (步骤 + thresholds) + §B T5 10-anchor (步骤 + thresholds + ceiling 分析) + §C 收尾 (handoff.md 更新 + final code reviewer + tag v0.4). 估时 ~1.5 小时 Colab 时间. 我自动 dispatch, 用户只需开 Colab + 告诉我 "ready".
> - **结果**: T4 + T5 code 全 ship, code+spec reviews 全过. 单测 累计 70 pytest (T1 6 hdr + T2 36 ego_mask + T3 6 waymo_loader + T4 12 option_b + T5 22 alignment + 其他). 9 stage-2 commits 主线 (cd6081c → b5af3c6, 含 1 hotfix a4fc0e6 + 1 progress 640abce). 8 实 atomic feature commits + 3 cleanup commits + 1 progress + 1 hotfix = 13 main commits 今天. 项目从 8 routes 进 10 routes (加 Option B + L1+ORB), 数字待 Colab verify 后才能 lock.
> - **Deliverables**: stage-2 plan `agent/plans/adaptive-seeking-turtle.md` 新增 "🌅 明天 Verify Checklist" 段 (~150 lines, 详 step-by-step + thresholds 表). 这条 progress entry. 不动 handoff.md (per 用户"等需要交接的时候再更").
> - Status: [DONE T4 + T5 code, **Colab verify 待明天**, T9 final review 待 verify 后]
> - Next: 明天用户回来 → 开 Colab + Run All `notebooks/runtime.ipynb` → 告诉我 "ready" → 我按 plan §A §B 顺序自动跑 → verify 完写 verify 结果到 progress + 决定是否进 §C 收尾.

> ### 2026-05-25 ~12:00 UTC — [Stage 2 Day 1] WS1.1 (HDR-Waymo) + WS1.2 (ego mask) + WS1.3 (cos⁴ feather) + WS1.4 (Waymo loader skel) ship + T2 Colab verify
> - **怎么做**: 跟队友 + Bosch 开完 5.22 会, Bosch 实测说 panorama 给他们 world model 用 work, 项目 reframe 为产学研协作 (跟队友并行: 我做 AV2 改进, 队友推 Waymo). 跟用户 brainstorming 把 7 个分支问题拆成 3 个 parallel workstream (WS1 cleanup+share / WS2 L1+ORB hybrid / WS3 Option B reweight), 详 plan `agent/plans/adaptive-seeking-turtle.md` (8 commits stage-2 总体). 用 subagent-driven-development skill 走 implementer→spec reviewer→code reviewer 三段式, 全程 opus 4.7 model.
>   - **T1 / WS1.1 HDR Waymo adapter** (3 commits + 1 cleanup `cd6081c,eafe856,3fd053b,85f5106`): 新写 `code/waymo2panorama/color/hdr_waymo_adapter.py` (244 LOC) fork AV2 的 6-参 LS solver, 但**两端 pin identity** (cam_0 + cam_last 都固定为 identity) — 解决 Waymo 5-cam arc 无环闭合的 gauge ambiguity. 新 driver `scripts/run_hdr_compensation_waymo.py` (237 LOC) 镜像 AV2 driver. 单测 `__test_hdr_waymo_adapter.py` (248 LOC, 6 tests, 含 perturbation recovery). AV2 path 零修改, 单测全 pass.
>   - **T2 / WS1.2 ego mask + WS1.3 cos⁴ feather** (3 commits + 1 cleanup `83ddda4,e5fe5d8,cfff379,e52389e`): 新写 `code/waymo2panorama/data_io/ego_mask.py` (heuristic ROIs: 全 cam 顶 3%, front_center 底 5%, rear_l/r 底 8%) + `build_ego_masks()` helper. 改 `cylinder.py:154` cos² → cos⁴ (软化 vertical edge weight decay 消白色拼接痕迹). 两 driver `run_cylindrical_baseline.py` + `eval_cylindrical_cycle.py` 都 wire mask, 都加 `--no-ego-mask` A/B flag, 都从 `av2_loader` import RING_CAMS_7 (cleanup follow-up 去重). 36 pytest 全 pass.
>   - **T2 Colab verify** (anchor 60 双跑): A (with_mask) 26s + B (no_mask) 22s, exit 0. Cycle-PSNR `psnr_l1_vs_l2 = 9.372 dB` 两边**完全一致** (0 dB regression). Cylinder coverage 58.55% vs sphere 33.65% (+24.9 pp, 跟历史 新-A 数字 reproducible). Seam gradient cylinder 47.74 vs sphere 49.11 (-1.37, cylinder 更平滑). 视觉看 anchor 60 cylindrical_l2.png A/B 几乎一样, 没看到原 v6 PDF 抱怨的"突兀长方形". 实际原因: Pi3-cache eval 模式下 mask 等于盖在 letterbox padding 区 (504×504 letterboxed, top 3%=15px 多半是 pad) → mask 真实价值需在具体出现 ego-hardware artifact 的 anchor/log 再 empirical-tune. 当前是 "no-harm, ready-when-needed".
>   - **T3 / WS1.4 Waymo loader skeleton** (2 commits `06094cc,136bbdf`): 新写 `code/waymo2panorama/data_io/waymo_loader.py` (211 LOC) — 跟 `AV2RingLoader` 同 public API (`cameras() / load_synced_frame() / iter_synced_frames()` 等), 复用 `CameraCalibration` + `FrameSample` dataclasses (single source of truth). `_load_calibrations()` 和 `_index_images()` 是 `NotImplementedError` 留给队友的详细 TODO docstring (含 waymo_open_dataset proto 提示 + 5-param distortion 兼容性 caveat). 单测 6 tests 含动态 API parity 检查 (`inspect.getmembers` 比对 AV2 loader 同名方法集). 给队友直接 drop-in.
>   - **Framework bug 修复**: notebook 启动失败 — `notebooks/runtime.ipynb` cell 1 的 `drive_workspace` 写成 Windows-mangled `C:/Program Files/Git/content/drive/...` (MSYS path translation bug from `colab-direct generate-notebook` 在 Windows Git-Bash 跑时). Hotfix `a4fc0e6` 改为正确的 Linux path `/content/drive/MyDrive/koi_waymo2pano_colab`. 这是 handoff lesson #16 警告的 "agent-colab-direct daily-use validation pending" 第一个暴露的 rough edge, 之后要在 framework 源代码层修 (`colab-direct generate-notebook` 命令).
> - **结果**: 3 个 workstream 同日 code-ship + T2 Colab verify 通过. 数字: 0 regression (psnr_l1_vs_l2 9.372 dB), coverage 验证 +24.9pp, 单测 48 pytest 全 pass (6 hdr_waymo + 36 ego_mask + 6 waymo_loader). 给队友的两个 drop-in 包 (HDR adapter for Waymo + Waymo loader skeleton) 完成. agent-colab-direct framework 真实 use 暴露 1 个 bug (Windows path mangling) 已 hotfix.
> - **Deliverables**: stage-2 plan `agent/plans/adaptive-seeking-turtle.md` (4 段: 中文 scan / framework + git discipline / 3 WS 详 / verify checklist). 11 commits stage-2 (`cd6081c → e52389e + a4fc0e6 notebook hotfix + 06094cc,136bbdf T3`). Colab verify outputs at Drive `outputs/T2_verify/{anchor60_with_mask,anchor60_no_mask,eval_anchor60_with_mask,eval_anchor60_no_mask}/`. 这条 progress entry.
> - Status: [DONE T1 + T2 + T3, T2 verify 通过] — 余下 T4 (WS3 Option B reweight code) + T5 (WS2 L1+ORB hybrid code) + 之后 Colab eval + 最终 handoff/code review 还在 plan 里.
> - Next: dispatch T4 implementer (opus) 写 Option B reweight (3-4 天预期, +0.05~+0.3 dB). 然后 T5 L1+ORB hybrid (5-7 天预期, +0.2~+0.5 dB). 也要在 agent-colab-direct repo 修 generate-notebook 的 Windows path bug (v0.1.1) + 加 handoff defensive lesson #17.

> ### 2026-05-24 ~early UTC — [handoff prep] agent/handoff.md + progress.md 整理为 clean handoff state
> - **怎么做**: 接续昨晚的 v0.1.0 + migration session, 用户 "明天继续推进项目的时候我们再看看能不能真的用" 之后没睡着, 决定先把所有 progress 整理好交接给下一个 agent. 重写 `agent/handoff.md`: (a) 顶部 metadata 改 2026-05-24; (b) TL;DR "Current state" 改 2026-05-24, 加上 infrastructure migration + "daily-use validation pending" 说明 + "What the next agent should do" 3 个分支 (Koi feedback / Colab task / paper draft); (c) "Currently in-flight" 段彻底重写 (worker 死了, 旧 jobs/*.json 是历史 artifact 不会再被 pull); (d) "Infrastructure (must-know)" 段重组 — agent-colab-direct 写成 active framework, agent-colab-queue 标 FROZEN; (e) 顶部冗余的 "Infrastructure: agent-colab-direct (active)" 段删掉 (与 middle 段重复); (f) Defensive lessons 加 #15 (FUSE write vs Drive backend sync, 来自昨晚 smoke test 的实际坑) + #16 (daily-use validation pending warning); (g) Memory references 加 `agent-colab-direct-framework` + `feedback-drive-colab-sync-delay`, 旧 `agent-colab-queue-framework` 标 FROZEN. 同时这条 progress entry 加在顶部.
> - **结果**: handoff.md + progress.md 现在是 self-contained handoff state — 下一个 agent (今晚或明天) 读这两个文件 + memory 索引就能完全 onboard. **关键 gap 明示出来**: 新框架 smoke test 通过但日常 use 还没真试过; paper work gate 在 Koi feedback. 没有隐藏 todo.
> - **Deliverables**: `agent/handoff.md` 6 处 edit; `agent/progress.md` 这条新 entry. 单 commit + push.
> - Status: [DONE handoff state] — 用户休息; 下一个 session/agent 任何时候捡起来都能直接走.
> - Next: 等 Koi feedback (paper angle 决定) OR 用户拿到 HF VGGT access (新-F 解锁) OR 用户主动想跑 Colab task — 第一种和第二种是高价值; 第三种是 v0.1.1 dogfood 机会 (会暴露 framework 的实际 friction).

> ### 2026-05-23 ~22:00-23:00 UTC — [architecture refactor] agent-colab-direct v0.1.0 实现 + Colab smoke-test 通过 + Waymo2Panorama migration
> - **怎么做**: 在单次对话内推完 plan 6 天的全部 5 个 implementation phase. 新 repo `D:/BaiduSyncdisk/2024 to future/agent-colab-direct/` (git init, 5 commits: Day 1 Flask executor 570 LOC + cloudflared tunnel + zstd-tar Drive cache → Day 2 client 自动 sync↔async via SSE + pexpect 持久 bash → Day 3 FastMCP server 12 tools + shell ANSI 清理 → Day 4 `@checkpointed` decorator + `single_cell.run_setup` + `notebook.generate` → Day 5 `colab-direct` CLI 4 子命令 + named tunnel docs + migration docs). 总 80 cross-platform tests 在 Windows 上通过 (12 Linux-only shell 测试 skip). Push 到 https://github.com/QiPan-Ronnie/agent-colab-direct (public). 用户开 Colab CPU runtime 跑 `pip install git+...` + `colab_direct.launch(...)`, Flask + Cloudflare quick-tunnel + Drive heartbeat 全部启动成功, URL `https://administrators-spatial-twins-applying.trycloudflare.com` printed. Agent 从本地 Windows curl 该 URL — 无 token 401 / 有 token 200, `/status` `/heartbeat` `/exec` `/jobs` 全通, Python subprocess 在 Colab kernel 跑 (hostname=`8b0077842081`, Python 3.12.13, cwd=`/content/`) 0.5s 完成, exit_code=0, stdout 通过 SSE log_tail 返回. **AutoDL-like UX 端到端 work**. Waymo2Panorama migration: `colab-direct generate-notebook` 生成 `notebooks/runtime.ipynb` (1.9 KB), 删 4 个旧 worker 文件 (`cell_acq_worker.py` / `cell_worker_bootstrap.py` / `runtime_filter.py` / `drive_queue.py` 共 ~33 KB), `jobs/*.json` 86 个保留为审计 archive.
> - **结果**: 新框架可用. 之后任何 Colab task — 不管 Waymo2Panorama 还是别的项目 — agent 都直接通过 MCP tool `mcp__colab-direct__exec(...)` 在 Colab 跑代码, 看 SSE 实时 stdout, 不再 commit-push 走 main. Main 干净, 之后 paper 期间 commit 全是真东西.
> - **Deliverables**: (1) `agent-colab-direct/` 仓库 v0.1.0 commits `816958a` → `d48f9a5` (Day 1-5 全套) + push origin/main. (2) Waymo2Panorama `notebooks/runtime.ipynb` 新生成. (3) `agent/handoff.md` 顶部 "Pending architecture refactor" 段落改写为 "Infrastructure: agent-colab-direct (active)" + 老 worker 标 frozen. (4) 4 个 worker 旧文件删除 (jobs/ 保留). (5) Memory: 新增 `agent-colab-direct-framework.md` + `feedback-drive-colab-sync-delay.md`, 旧 `agent-colab-queue-framework.md` 改 status=frozen.
> - Status: [DONE v0.1.0, validated end-to-end on real Colab] — 框架可日常使用; pip 发布到 PyPI 是后续 nice-to-have, 不阻塞 paper work.
> - Next: 任何下一个 Colab task (e.g. 等 Koi feedback 回来跑 T13 self-sup Pi3 finetune, 或 user 拿到 HF VGGT access 跑 新-F) 直接用 `notebooks/runtime.ipynb` Run All + agent 通过 MCP `colab-direct__exec` 提交; 旧的 "commit job spec to main" 模式正式弃用. 学到的 Drive sync 坑 (FUSE write 即时 / Drive web 同步可能几分钟) 写进了 `feedback-drive-colab-sync-delay` memory, 之后调试别再被卡.

> ### 2026-05-23 ~late UTC — [architecture refactor] agent-colab-direct plan 设计完成 + 批准
> - **怎么做**: 用户提出 `agent-colab-queue` 把 main 当 queue → 每个 Colab task push commit, 严重污染 git log (今天一天 15+ noise commits). 用户要求 "直接端到端 像 AutoDL 那样丝滑". 经过 brainstorming workflow (3 个 Explore + 跟用户 4 轮 Q&A: 方向 / scope / URL handoff / Colab tier) 设计 `agent-colab-direct` (new repo, separate from `agent-colab-queue` 老 repo). 核心: Cloudflare quick-tunnel + Flask executor in Colab + Drive-mediated URL handoff + 32-char bearer token. 用户额外要求 6 个 optimizations 全 bake: A 单 cell setup / B 客户端 auto sync↔async / C `pexpect` 持久 bash (SSH-like) / D `@checkpointed` decorator (mid-task resume) / E CF named tunnel (固定 URL) / G `colab-direct init` CLI. 实现量 5-6 天 v0.1.0.
> - **结果**: Plan 文件 `C:\Users\14294\.claude\plans\snug-shimmying-wave.md` ~600 行, 包含 Context / Approach / Repo Layout / 13 HTTP endpoints + 11 MCP tools / 3-pronged disconnect resilience (Drive cache 25s 恢复 + tunnel retry + @checkpointed) / Security (CF hash URL + bearer token) / Migration plan for Waymo2Panorama / 6-day implementation phases / 10-point verification suite. ExitPlanMode 用户已批准.
> - **Deliverables**: `~/.claude/plans/snug-shimmying-wave.md` (approved plan) + `agent/handoff.md` 🆕 段顶部添加 "Pending architecture refactor" 指引 + 这条 progress entry.
> - Status: [DONE design, 等实施] — design 阶段完成, 实施需要新对话/新 agent (~6 day 工作量).
> - Next: 用户决定 timing — refactor 先 (~1 周, paper 期间 git 干净) vs paper draft 先 (~10-11 周 paper, 之后再 refactor). 用户可以切新 agent 给 prompt "implement plan at ~/.claude/plans/snug-shimmying-wave.md" 直接开干. 新 agent 不应往 main push job spec (除非走 agent-colab-queue 兼容模式, 但建议直接用新设计).

> ### 2026-05-23 ~13:00-14:00 UTC — [paper supplementary] 7 route videos 全套生成
> - **怎么做**: 用户重启 Colab worker (cell_acq_worker.py on A100, 13:54 UTC 13:54 失效后用户 12:56 UTC 重启) 后, 在同一对话里 fire 6 个新 video drivers 把 8 路线里 7 个 dense ERP 路线全部视频化 (5sec @ anchor 60 区域, 100 frames @ 20fps, 1024×2048 ERP). 新-D wide-baseline stereo 物理上不可视频化 (sparse 3D points 不是 dense ERP), 跳过. 6 个 driver 全新写: `scripts/run_l3_video.py` (Pi3+Sim3+forward-splat), `run_cylindrical_video.py` (球→柱面), `run_graphcut_video.py` (L1+apply_graphcut_seams), `run_hdr_video.py` (L1+6-param HDR LS, with `--also-baseline` 给 parallel L1 对比), `run_ipm_hybrid_video.py` (Pi3+detect_ground_from_pi3+ipm_project_ground+sphere fallback), `run_ipm_multi_region_video.py` (Pi3+ipm_project_multi_region). 全部 in-memory pipeline, imageio + libx264 编码, done.json marker.
> - **结果**: **7 个 mp4 视频** ready on Drive (`outputs/<route>_video/02a00399-.../<route>_video.mp4`):
>   - L3 (24 MB, 7 min wall, mean Pi3 0.54s + splat 1.22s, file `1PZEvwFoCeQUc0oatymgYL7cw0XyF-AcL`)
>   - 新-A 柱面 (26 MB, 5.7 min wall, mean 3.04s/frame, file `1YvkYTW2dEHrBkH0wKTmxl2s9UoZwIs1z`)
>   - 新-B graphcut (17 MB, 16 min wall, mean 9.47s/frame, file `1aA9iw8RTLFTOXFwGYFYAwBFFIvHbwa2s`)
>   - 新-C multi-region (13 MB, 12 min wall, mean 6.5s/frame, file `1O5dAAq6MASxUtFyebuzrPN3fK6FTbLoX`)
>   - 新-E HDR + L1 baseline (15+17 MB, 16 min wall, mean 9.24s/frame incl. 5.59s Huber LS, files `1Ln-BV6zU_FwQ7yzdY2_e9Y0X3V74-cUA` + `13jNNJCV8FjMGMUbqo03I47ZMTTTJBpro`)
>   - T14 IPM hybrid (13 MB, 7.7 min wall, mean 4.17s/frame, file `1ozuDgzl4g-Anxg1qHJTq8m6liQrSDkn4`)
>   - Total Colab wall: ~70 min A100, cost ~$4-5
> - **3 个 v1 crashes 学到的 lessons** (新增到 handoff.md §Defensive lessons #9-14):
>   1. L3 v1: pi3_repo 默认路径错 (3 级 ../ vs 应该 2 级) → `/01-pi3/...` 不存在; fix v2 pass `--pi3-repo` 显式
>   2. L3 v2: `/content/01-pi3-Pi3` 在新 Colab session 不存在; fix v3 clone 到 `/content/Pi3` 用 3-URL fallback `yyfan2014/Pi3 || yyfz/Pi3 || yyfan2014/Pi3-clean`
>   3. T14 v1: `detect_ground_from_pi3()` 不接受 `conf` kwarg (跟 segment_regions_from_pi3 不同), 第一帧 TypeError crash; v2 用正确 signature `ego_z_thresh_m / min_forward_m / max_radius_m` 修复
>   4. 通用: Python `print` block-buffered when piped via tee — 长 Pi3 model load 期间 `tail -f run.log` 看不到任何输出, 不要误判 worker 卡了
>   5. 通用: Drive API metadata cache 有 30-60s delay — 判断 worker liveness 需要 2-3 次 spaced reads
>   6. 通用: Worker idle ≠ A100 free — 全部 job 跑完后 worker 仍在 polling 但 A100 还在按小时烧钱, 必须用户手动 disconnect runtime
> - **Deliverables**: 6 个新 video driver scripts (`scripts/run_*_video.py`) + 6 个对应 job specs (jobs/phase3-*-video-*.json) + 7 个 mp4 on Drive (~125 MB total) + handoff.md 更新 (新 "Video deliverables" 段 + 6 个新防御教训 #9-14) + progress.md (this entry).
> - Status: [DONE] — paper supplementary 4-grid 或 6-grid 现成材料齐全 (任意 ffmpeg `-filter_complex` 拼合一行命令).
> - Next: (a) 用户 disconnect A100; (b) 切新 agent 继续 paper draft v0 或推 新-F / T13; (c) 后续任何 video / training / eval 任务都走同一 scratchpad 管道 (write driver → job spec → git push → worker pull → Drive result).

> ### 2026-05-21 ~late session — [project handoff polish] 集成最终交付 + 文档清理
> - **怎么做**: 在 T-Koi-4 PDF 5 版迭代 (v1 dense → v2 unified old+new → v3 strip advisor framing → v4 add point cloud figures → v5 + §0 metrics primer + §5 ranking table, final commit `473aa7b`) 之后, 进入项目收尾整理. 失败/学到的: WeChat 措辞 v2 给用户后他挑出 "3 baselines all lose to L1" overclaim — Depth Pro / Temporal Pi3 是 L3 backbone swap NEG 不是真 head-to-head, 修正为 v3 "1 head-to-head (OmniStitch -6.67dB) + 2 internal NEG datapoint". 新-F VGGT 尝试 (commits `c1c3dfe` / `1b86df8` / `ee8d1c5`) — install + smoke + tar-cache 3 jobs with guards, 工作者 alphabetical 拉取, install step 6 `VGGT_IMPORT_OK` 后 ckpt download 撞 HF 403 GatedRepoError (`facebook/VGGT-1B-Commercial` is gated, 需 user 在 HF 点 "Agree and access"); guards 让 eval + tar-cache 自动跳过, 不烧额外 GPU; total 190s instead of 15-30min. Project handoff 大改: agent/handoff.md 全文重写 (从 2026-05-15 scaffold → 当前 8 路线 state + 8 防御教训 + infrastructure pointers), README.md 全文重写 (Week-1 scaffold → 8-route verdict table + nav pointers + open decisions), 写 deliverables/learning_plan.md (7-phase CV roadmap, 3-day quick / 3-4w deep) + deliverables/meeting_cram.md (5min talking points + 数字 cheatsheet + 7 predicted Q&A) + self_learning/ 6 chapters (00_README + 01_project_overview + 02_cv_foundations 31 concepts + 03_methods_walkthrough 8 routes deep + 04_external_baselines 3 NEG + 05_findings_and_paper). Cleanup: 删 8 个历史 Koi handoff snapshots (保留 v6cpu_done.{md,pdf}), 删 15 个 progress_T*_addendum.md (info 已在 progress.md), 删 3 个 stale agent docs (plan.md / parallel-tracks.md / agent-roster.md, 已 superseded by claude plans + handoff.md), force-add 4 个 agg_*.json (新-A/B/C + IPM 数字证据). Commits today: c1c3dfe, 1b86df8, ee8d1c5, 6fb559d, 5dd76d1 + this entry.
> - **结果**: agent/ 从 21 文件压到 4 (handoff.md + progress.md + README.md + 2026-05-15-brainstorm-survey.md). deliverables/ 从 30+ 文件压到 final 1 套 (v6cpu_done.{md,pdf}) + 3 user-facing docs (learning_plan / meeting_cram / images) + tooling scripts. self_learning/ 新建, 6 chapters ~25KB. README.md 现在打开 GitHub 30 秒看懂 project. 项目 GitHub-ready 完成度 100%, 任意新 agent 读 agent/handoff.md (~5min) 能接手, 任意人读 self_learning/ (~3-4h) 能完整理解项目. 新-F VGGT pending HF access, A100 still idle (cannot remote-shutdown). T13 deferred pending paper angle 决定.
> - **Deliverables**: `agent/handoff.md` (rewrite) + `agent/README.md` (rewrite to reflect lean state) + `agent/progress.md` (this entry — single source of truth going forward) + `README.md` (full rewrite) + `deliverables/learning_plan.md` + `deliverables/meeting_cram.md` + `self_learning/{00-05}_*.md` (6 chapters) + 4 force-added `outputs/phase3/.../agg_*.json` + 3 new-f Colab job specs in `jobs/`.
> - Status: [DONE] — project 交付完整, 等 Koi 反馈或用户开始 CV 学习/paper draft.
> - Next: (a) Koi PDF 反馈 → lock paper angle (default A' Method paper); (b) 用户 disconnect A100 (remote 不可); (c) 用户决定 新-F (HF access click → retry) vs abandon; (d) T13 仅在 paper angle 要求时启动 (5-6d high-cost). 用户切新 agent session 时 entry point: 读 agent/handoff.md 5min + 扫 progress.md 顶 5-10 entries.

> ### 2026-05-21 ~very-late+2 UTC — [T-Koi-4] v6.1 mid-CPU-wave snapshot PDF 完成
> - **怎么做**: gp 子代理基于 v6.1 已完成 5 条 CPU 路线 (Wave 1 新-A 柱面 + 新-E HDR / Wave 2 新-B graph-cut seam + 新-C IPM 多区域 + 新-D wide-baseline stereo) 生成 15 页 Koi-targeted snapshot, 重写 `handoff_to_koi_v6.md` 为 Koi-面向叙事 (TL;DR 6 行 + 路线 summary 卡 + 5 节 each-route writeup + v5 9 路线 compressed recap + 方法论审计 + paper 角度三候选 + 4 个 ask + 附录文件路径 + commit history)。 Renderer 复用 `_render_pdf_w2_late_mid.py` 的 pandoc + xelatex + Cambria + YaHei pipeline, 输出 14.5 MB PDF, 7 figures 嵌入 (5 v6.1 路线图 + wave3 NEG summary + Pi3 depth-binned)。
> - **核心 ask**: paper 角度从 T-Koi-3 的 "B-with-C-as-motivation" pivot 到 **A' Method paper** — 3 个 stack-able 正面贡献 (新-C ground IPM +0.20 dB / 新-E HDR +1.0 dB proxy / 新-B graph-cut visual win) + 4-5 NEG (L3 / Depth Pro / temporal Pi3 / OmniStitch / sparse stereo) 当 Section 6。 备选仍是 B-with-C (保守) 或 C-headline (D&B-friendly)。
> - **Deliverables**: `deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.md` (22 KB MD, ~600 行) + `deliverables/_render_pdf_v6cpu.py` (~135 LOC) + `deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.pdf` (14.5 MB, 15 pages, 7 figures)。
> - Status: [DONE]
> - Next: Koi 反馈 -> 决定 (a) paper 角度 A'/B/C, (b) 新-D Option B reweight 跑不跑, (c) T13 self-sup 训不训, (d) target venue main vs D&B。 主线继续 Wave 3 / GPU 路线 (新-F VGGT, T13 finetune) 不阻塞。

> ### 2026-05-21 ~very-late+1 UTC — [Wave 2 新-D / route 13] 邻 cam wide-baseline sparse stereo 完成
> - **怎么做**: 用已知出厂外参 (T_ego_cam, ±5 mm 精度) 在邻 cam 对上做 sparse stereo, 不做 SfM 估计。Pipeline: kornia DISK 抽 ≤2048 keypoints + LightGlue 学习型 matcher; 用 KNOWN T_a_b 直接构造 fundamental matrix `F = K_b^{-T} [t]_x R K_a^{-1}` (而非 cv2.findFundamentalMat 估算); Sampson distance ≤ 3 px 过滤; cv2.triangulatePoints DLT 三角化 (world frame = cam_a, `P_a=K_a[I|0], P_b=K_b[R_b_a|t_b_a]`); 三重几何过滤 cheirality (Z_a>0 ∧ Z_b>0) + depth band [0.5, 120] m + parallax angle ≥ 0.5° (剔除远距离近平行射线退化, 这是实测发现的关键 fix — front_left↔side_left 152 个 epi inlier 全部因近 0° parallax 三角化到 cam 背后, 加 cheirality filter 后正确降到 0 NEG)。CPU only kornia LightGlue ~7-10 s/anchor (7 对)。
> - **结果** (anchor 0/60/90/150 × 7 邻对 = 28 stereo pair): 平均 N_final=44 inlier 3D pts/pair (range 0-127), depth median 9-22 m, depth 跨度 [2.5, 26.5] m。Anchor 60 (主): 307 个 3D 点跨 7 对, 5/7 对成功 (29-115 pts each), 2/7 对 NEG (front_left↔side_left: 152 epi inlier 全部 fail cheirality → 远距离 sky/building 内容近平行射线退化; side_right↔front_right: 仅 11 LightGlue match → side_right 视野被近距离黑墙占据无法配对)。Anchors 90/150 各 ~390 pts/7 对, anchor 0 较稀 142 pts (textured-content 较少)。Median parallax 0.55-1.39° 表示 triangulation 数值稳定区。
> - **Deliverables**: `code/waymo2panorama/stereo/wide_baseline_stereo.py` (~430 LOC: extract_pair_features (DISK) + match_with_lightglue + compute_F_from_known_T + epipolar_ransac_filter (Sampson) + triangulate_sparse (DLT + cheirality + parallax) + process_cam_pair + process_anchor_all_pairs) + `code/waymo2panorama/stereo/__init__.py` + `scripts/phase3/run_wide_baseline_stereo.py` (~390 LOC: CLI, per-pair viz with turbo-depth colormap, mosaic builder, multi-anchor mode) + `outputs/phase3/p3.6_stereo/anchor_{000,060,090,150}/` (per anchor: 7×stereo_*.npz + 7×depth_viz_*.png + depth_viz_mosaic.png + summary.json) + `deliverables/images/route_wide_baseline_depth.png` (anchor 60 mosaic for paper) + handoff route 13 section 完整填充。
> - Status: [DONE — partial success per design intent, 5/7 pairs metric-sane, 2 pairs honest NEG]
> - Next: Module's `process_anchor_all_pairs()` 输出的 ego-frame 3D pts 是 "Option B reweight L1" 的 drop-in 输入, 留给 Wave 3 集成。本路线本身的 paper value 是 figure (5/7 cam-pair 深度 viz) + NEG 论据 (sparse stereo on AV ring cam 单独不足以驱动 dense reweight) — 与 Pi3 / VGGT NEG 收敛 ("AV ring cam 的 3D-aware 重建 brittle")。

> ### 2026-05-21 ~very-late UTC — [Wave 2 新-C / route 12] IPM multi-region prior extension (ground + sky + building) 完成
> - **怎么做**: 把 T14 的「单一地面 IPM」推广为三区域决策树。Normal 从 `local_points_<cam>.npy` finite-diff + box-filter (with valid-mask 卷积避免 NaN 传播 — 这是 step 1 关键 bugfix) 估出, 然后 first-match-wins: ground (|ego_z|<=0.30, |n_z|>=0.85), sky (conf<-2.0 OR (z_cam>30m AND z_ego>5m AND v<0.4H)), building (z_ego>0.5m, |n_z|<=0.30, n_xy>=0.85, radius<=80m), 其余 fall back to L1。Building 每 32×32 tile RANSAC 拟合垂直平面 `n_x*x+n_y*y=d (n_z=0)`, 50 iter, threshold 0.20m, inlier >= 0.40, PCA-refit。Forward composite: sphere base + building override + ground override (优先级), 3px Gaussian feather on weight 边界。
> - **结果** (4 anchors 0/60/90/150 cycle-PSNR mean): L1 10.85 → T14 10.90 (+0.05) → 新-C ground+sky 10.90 (+0.05, **+0.20 dB on ground-only mask**, sky 路由 +0.00 dB neutral) → 新-C with building 10.86 (+0.01, **-0.33 dB on building-only mask** — RANSAC tile fit 视觉合理但 cycle 评测下跨 cam 不通用)。**按设计 hard floor 默认 `--enable-building False` 出货 (即 ground+sky 路由), building 接口保留供 future cross-cam plane consensus 工作**。Building forward composite 每 cam ~67 planes, 88% inlier frac, visual facade alignment OK。
> - **Deliverables**: `code/waymo2panorama/projection/ipm_multi_region.py` (~590 LOC: estimate_normals_from_points + RegionMasks dataclass + segment_regions_from_pi3 + _ransac_vertical_plane + ipm_project_sky + ipm_project_building + ipm_project_multi_region + make_region_overlay) + `scripts/phase3/run_ipm_multi_region.py` (~240 LOC, --enable-building default False) + `scripts/phase3/eval_ipm_multi_region_cycle.py` (~270 LOC, L1/T14/newC 三路 + per-region PSNR breakdown) + `outputs/phase3/p3.3_multi_region/anchor_{000,060,090,150}{,_no_bld}/` + `agg_4anchors.json` + `deliverables/images/route_ipm_multi_region_compare.png` (3-way L1/T14/newC ERP stack) + handoff route 12 section 完整填充。
> - Status: [DONE — partial success, ground branch +0.20 dB on ground mask is the real win; building branch ablated per design fallback]
> - Next: building cross-cam plane consensus (union-find on (n_x, n_y, d) within Δθ<10°, Δd<0.5m) is the next idea — single-cam RANSAC over-segments the same facade across 2-3 cams with different (n_x, n_y) → cycle eval can't reconcile them.

> ### 2026-05-21 ~late UTC — [Wave 2 新-B / route 11] Graph-cut optimal seam selection 完成
> - **怎么做**: 每对 ERP-adjacent cam (front_c↔front_l/r, front_l↔side_l, side_l↔rear_l, rear_l↔rear_r, rear_r↔side_r, side_r↔front_r) 在重叠 bbox (~200×400 px) 上跑 PyMaxflow min-cut, 边权 = 1.0·color + 0.5·grad + 0.1·boundary。Source = only-A region, Sink = only-B region, 输出硬 0/1 mask + σ=3 高斯 feather, 直接喂回 `multiband_blend` (不需要 patch blender — multiband 本就接受任意 weight)。CPU only ~5 s/anchor。
> - **结果** (4 anchors 0/60/90/150): seam-band 平均 |grad| L1 **48.63** → graphcut **42.59** = **-12.4% / +0.58 dB 等价 seam-smoothness gain (4/4 anchor win)**。L1 ERP 与 graphcut ERP 整体 PSNR=32.84 dB → 差异只在 seam 局部。Cycle-PSNR 结构上不动 (reconstruct_l1 不经过 blender)。
> - **Deliverables**: `code/waymo2panorama/blending/graphcut_seam.py` (~430 LOC, PyMaxflow + scipy.csgraph fallback) + `scripts/phase3/run_graphcut_seam.py` (~310 LOC) + `deliverables/images/route_graphcut_seam_compare.png` (anchor 60 L1-vs-graphcut seam overlay 对照) + `outputs/phase3/p3.5_graphcut/anchor_{000,060,090,150}/` + `agg_4anchors.json` + handoff route 11 section 完整填充。
> - Status: [DONE]
> - Next: Drop-in 可叠加任何下游 stitching baseline (L1 / L2 / IPM / Pi3); 视觉 figure 是 paper Section 5 "seam selection: midline vs energy-min cut" 主产出。

> ### 2026-05-21 ~12:00 UTC — [Wave 1 新-E / route 14] HDR cross-cam compensation 完成
> - **怎么做**: 每 cam 6 参数 (3 gain + 3 bias), cam_0 (front_center) 固定为 identity, 剩余 36 参数用 global LS + Huber + box bounds + Tikhonov 先验解。对应关系直接在 ERP 空间提 (无 feature matching), RANSAC-lite 中位数 3× 过滤 parallax outliers。校正在 multiband blend 之前应用。CPU only, scipy.optimize.least_squares, ~5s/anchor。
> - **结果**: 4 anchors (0/60/90/150) 平均重叠区 lum gap 16.62 → 13.61 (Δ +3.01 levels, **18.1% reduction**)。Anchor 60 (rear_right, side_right) 对 45→14 (-68%) — 戏剧性曝光修复。
> - **Deliverables**: `code/waymo2panorama/color/hdr_gain_estimate.py` (~210 LOC) + `scripts/phase3/run_hdr_compensation.py` (~290 LOC) + `deliverables/images/route_hdr_before_after.png` (anchor 60 + 90 before/after stack) + `outputs/phase3/p3.7_hdr/anchor_{000,060,090,150}/` + handoff route 14 section 完整填充。
> - Status: [DONE]
> - Next: (留给主线) route 14 可作 drop-in preprocessing 给 L1/L2/L3/IPM 任何 baseline; 是否做 10-anchor full sweep + downstream cycle-PSNR 重测由主线决定。

> ### 2026-05-21 ~07:30 UTC — [plan v6.1] 战略 pivot 通过 + Wave 0.5 启动
> - **战略**: 主线从 "system integration (Pi3 → Pantheon360 适配层)" pivot 到 "**stitching 方法学**" — 多视角探索 7-cam → 360° ERP 的拼接路线本身
> - **下游 paused**: ViPE / Pantheon360 / GEN3C / Panacea+ 不再追加投资 (现有队列让跑完拿 datapoint 入库)
> - **v6.1 新加 active**: 7 条路线 (新-A 柱面 / 新-B graph-cut seam / 新-C IPM 多区域 / 新-D wide-baseline stereo / 新-E HDR 补偿 / 新-F VGGT 3rd backbone + T13 self-sup Pi3 finetune)
> - **v6.1 关键约束**: 每条路线必出 数字 + ≥1 张拼接图 + 在统一 `deliverables/handoff_to_koi_v6.md` 加一节
> - **v6.1 基础设施**: 新-W worker UX 总改造 (`scripts/cell_worker_bootstrap.py` 单行 Colab cell, 一键换 CPU/GPU runtime 0 干预)
> - **进行中**: Wave 0 (T11 install / inference / T1 multi-log / tar-cache 让跑完, ~2h), Wave 0.5 (Plan agent 设计 worker bootstrap, in-flight)
> - **Plan file**: `C:\Users\14294\.claude\plans\snug-shimmying-wave.md`
> - Status: Plan approved, prep work done (v6 演化 MD + tasks 加好)
> - Next: 等 Wave 0 Colab 队列完成 + 等 新-W Plan agent 返回 → 实现 worker bootstrap → Wave 1 启动 (新-A / 新-E / 新-F)

> ### 2026-05-21 ~05:40 UTC — [T1 Phase B] Submitted AV2 val UUID listing (Colab in-flight)
> - Wrote `scripts/phase3/list_av2_val_uuids.py` (~190 lines): s5cmd-based S3 enumeration of 150 val UUIDs + optional per-log annotations.feather download for ped:veh scoring. Replaces local-data dependency of original `find_av2_val_candidates.py` (which needed all logs downloaded to score).
> - Submitted `phase3-t1prep-list-av2-uuids-v1` (commit `2fd2fe1`). Worker runs UUID listing + per-log scoring, ~15 min wall. Output: Drive `data/av2_val_uuid_index.json`.
> - Status: 🟡 In-flight (Colab job)
> - Next: When index returns, main thread picks 4 diverse UUIDs (e.g., low/mid/high ped:veh + 1 outlier); fire s5cmd downloads (~32 GB); T1 multi-log replication.

> ### 2026-05-21 ~05:35 UTC — [T11 prep] GEN3C 3D-cache spike design subagent dispatched
> - Plan subagent designing T11: Python 3.10 install path on Colab Python 3.12 (conda-in-Colab or pip-anyway), minimum-viable inference target (single_image / multiview / dynamic), 2-job Colab design (install + inference), failure modes + fallbacks, P(success) estimate.
> - Status: 🟡 Subagent in-flight (Plan)
> - Next: When plan returns, main thread submits the 2 Colab jobs (install ~60-90 min, inference ~10-30 min).

> ### 2026-05-21 ~05:25 UTC — [T9b] ViPE + DAP depth on L1 ERP (partial)
> - Result: 138s end-to-end. **Depth/pose/intrinsics/masks all produced**, BUT "Too few valid pixels in pano frame N, skipping scale estimation" warning fired on all 100 frames → **depth is RELATIVE not metric**. Cause: panorama-mode post-processor's valid-pixel threshold tripped (likely sky/dynamic mask over-filtering on virtual views).
> - Deliverable: Drive `outputs/phase3/t9b_vipe_depth/` (depth 48 MB, pose .npz, intrinsics, masks) + `notes/t9b_vipe_depth_report.md`.
> - Status: ⚠️ Partial (artifacts ✓, metric scale ✗)
> - Next: Accept relative depth for Section 6 narrative (sufficient for "downstream consumer" demo); investigate T9c metric-scale fix later OR T9d post-hoc scale fit from AV2 ego ground-truth. Pivot to T11 GEN3C spike.

> ### 2026-05-21 ~05:30 UTC — [T-Koi-3] Wave-3 mid-week-v2 PDF
> - Result: 12-page PDF, 5 figures embedded (IPM hybrid compare anchor 60, T14b 10-anchor honest chart, Wave-3 NEG findings summary, Pi3 depth-binned bias, Pi3 vs LiDAR per-anchor). Wave-3 summary table + 4 NEG (T18/T2/T12 v2/T17) + T9 ViPE downstream demo + paper narrative shift ask (B-with-C → C-with-B-supplement).
> - Deliverable: `deliverables/handoff_to_koi_w2_2026-05-21_late_mid.{md,pdf}` + renderer `deliverables/_render_pdf_w2_late_mid.py` + 2 new figure scripts (`_make_t14b_figure.py`, `_make_neg_summary_figure.py`).
> - Status: [DONE]
> - Next: User hand-deliver to Koi async; pivot to T11 GEN3C spike + T9b depth integration + T13 Pi3 self-sup finetune small spike

> **Latest: 2026-05-21 ~04:30 UTC** — **Phase 3 W2 Wave-1 + Wave-2 全部 CPU autonomous work 完成 (9 tracks / ~5h via 8 parallel subagents)**。
>
> ## Wave-1 (6 tracks):
> - **T-Koi-1** ✅ — 8 页 PDF (Phase 3 W1 + Pi3→Pantheon360 适配层定位)
> - **T5** ✅ — cycle-PSNR metric audit: **L3 negative metric-robust** (LPIPS 1.83× worse, MS-SSIM 0/7, object-band -6.88 dB)
> - **T6** ✅ — parallax ranking: anchor 60 best (rank #3 + 最小 L3 deficit), anchor 180 negative control
> - **T8** ✅ — lit watch: PanFlow + Fin3R + Percep360 (4-6 周 scoop window) + CylinderSplat 升回 Phase 4
> - **T14** ✅ — **IPM ground hybrid: 首个正面 method contribution** (ground-only ΔPSNR +0.20 ± 0.11 dB across 3 anchors, rear cams +1.0~+1.7 dB, full-image drop-in safe)
> - **T16** ✅ — Bayesian depth fusion: **修 .ply 几何 (overlap RMSE 1-5m), 不修 L3 ERP** (~2% ERP overlap, ghost 主因 single-cam mis-splat)
>
> ## Wave-2 (3 tracks):
> - **T7-prelim** ✅ — paper 角度 = **B-with-C-as-motivation**, primary venue **3DV 2026** (~Aug ddl), upgrade CVPR 2027 if T9/T10 lands. Top risk: T14 10-anchor regression
> - **T1-prep** ✅ — AV2 val UUID 选 4 个候选策略 (Miami urban + Pittsburgh highway + Detroit/DC dense + DC night) + 自动 scan script ready
> - **T-Koi-2** ✅ — 9 页 mid-week snapshot PDF for Koi (5 图含 IPM compare + Bayesian depth diff)
>
> ## 🟢 Worker UP (~03:47 UTC user restarted A100) — Wave-3 大丰收
>
> **3 个 NEG findings 综合 → paper B-with-C-as-motivation 论据链非常硬**:
>
> - **T18 ✅ DONE Depth Pro NEG**: 2.84× worse than Pi3 on AV2 (abs_rel 0.580 vs 0.204, δ<1.25 0.064 vs 0.633). **Algorithm is bottleneck, NOT backbone** — Apple SOTA monocular AV outdoor 不行。 angle C 强化, paper hook 拿下。
>
> - **T2 ✅ DONE OmniStitch NEG**: -6.67 dB vs L1 (OmniStitch 17.28 vs L1 23.95 anchor 60), 输 7/7 cams。 **唯一 published AV-360 baseline 也输 L1**, T7-prelim 第 3 大风险 (OmniStitch beats us) 反向 close 为正。 paper "vs prior art" 一栏铁稳。
>
> - **T12 v2 ✅ DONE temporal Pi3 K=3 NEG**: abs_rel 0.213 (vs single 0.204), δ<1.25 0.572 (vs 0.633), 远场 bias -23.92% (vs single 10-anchor mean -23.7%)。 **多帧时间多基线假说 false** — Pi3 远场 bias 是结构性 (not single-frame info gap)。
>
> **T14b v4 ✅ DONE (10-anchor IPM 真实数字)** — T7-prelim 第 1 大风险**部分 materialized**:
> - **Full image ΔPSNR = -0.010 ± 0.082 dB** (10/10 essentially break-even, drop-in safe ✓)
> - **Ground-only ΔPSNR = +0.048 ± 0.181 dB** (7/10 positive, range -0.24 ~ +0.32)
> - vs 3-anchor cherry-picked (T14 60/0/150): +0.20 ± 0.11 — 平均掉到边缘 statistical
> - **Paper 含义**: IPM hybrid 是 "parallax-conditional" (top-3 parallax frames +0.20 dB) + "drop-in safe full-image" (0 ± 0.08 dB regression). B contribution 弱化, C (negative findings) 论据比重上升。 paper 角度 B-with-C-as-motivation 仍 ship-able 但 narrative shift 倾向 C 主导。
> - Bug 修复链: v2/v3 silent fail (bogus arg) → v4 (data 出但 aggregator key 错) → 我主线手动 extract per_anchor.raw_overall。 aggregator 需修 (next session)。
>
> **T9 ViPE ✅ DONE — paper Section 6 demo 成立**: ViPE 端到端跑通 L1 ERP 5s clip (96.7s on A100), 输出 SLAM pose + intrinsics + masks。 **首个 "stitched-RGB → published-downstream system" 数据流**。 ViPE depth 没出 (default config `depth_align_model: null`, T9b 一行 config flip 修)。 commit `a751876` pushed.
>
> **🎯 T17 critical insight** (Panacea+ recon DONE, inference NOT run):
> - Panacea+ 是 **parallel generator** (BEV + 3D bbox + HD-map → 6-cam video), **不消费**我们 RGB ERP
> - 同理 Pantheon360 — 它们是和 L1 平行的另一条生成路径, 不是 L1 的下游
> - **真正的 downstream consumer for L1 ERP = ViPE** (paper #2, 显式支持 360 ERP 输入 → pose + metric depth)
> - paper narrative pivot: "downstream demo" 走 ViPE-on-L1-ERP 而非 Pantheon360/Panacea+
> - Panacea+ 仍可作 paper Section 4 "naive prior-art transfer fails" 第 4 个数据点 (modality gap structural)
>
> T14b v2 silent fail (我 bash 漏传 run_ipm_hybrid.py 必需参数 --erp-h/w/--ego-z-thresh-m 等)。 v3 修正重发 ~10 min。
>
> Wave-1 deliverable confirmed: T-Koi-1 + T-Koi-2 PDFs 给 Koi async
>
> T12 v1 crashed 11s (Pi3 repo not in /content after restart). T14 subagent's Colab job (3-anchor IPM) ran 84s, eval succeeded but bash aggregator heredoc crashed — per-anchor JSON OK on Drive. Anchor 150 ground-only +0.32 dB confirms anchor-60-extension positive direction.
>
> ## 🔴 Still blocked / pending
> - **T12** (multi-frame temporal Pi3 K=3 @ anchor 60) — Colab job queued, auto-pick up 10s 内
> - **T1 Phase B** (run find_av2_val_candidates.py → pick 4 UUIDs → s5cmd 下载 ~40 GB)
> - **T14b** (extend IPM hybrid 3 anchors → 10 anchors, CPU ~30s)
> - **T18** (Depth Pro / Metric3D drop-in on anchor 60)
> - **T2** (OmniStitch baseline)
> - **T9 / T10 / T11 / T17** (ViPE on L1 / Pantheon360 spike / GEN3C 3D cache / Panacea+ baseline)
> - **T13** (self-sup cycle finetune of Pi3, training)
>
> ## Paper 角度 (locked v0)
> **B-with-C-as-motivation**: "Hybrid 2D/3D pipeline for AV → 360 stitching, with analysis of why naive 3D-lift fails". IPM hybrid 是 method contribution (+0.20 dB ground), L3 forward-splat -3.15 dB metric-robust negative 是 motivation, T5 metric audit 是 reviewer defense, T16 Bayesian fusion 是 .ply deliverable upgrade。 Primary venue 3DV 2026, upgrade CVPR 2027。
>
> ## Next actions (用户 W3 D1)
> 1. 重启 Colab worker cell — unblock T12 + 所有 GPU tracks
> 2. 把 `handoff_to_koi_w2_2026-05-21_mid.pdf` 发 Koi (异步)
> 3. (可选) Koi 反馈到了再调 priority — 默认 D 1: T12 finish + T14b 10-anchor; D 2: T17/T18; D 3: T1 multi-log; D 4: T9/T10/T11 system integration
>
> **🎯 T14 IPM ground hybrid: 首个正面 method contribution** (3 anchors)
> - 全 image ΔPSNR = **+0.04 dB** (drop-in safe, IPM hybrid ≈ L1)
> - 仅 ground 区域 ΔPSNR = **+0.20 ± 0.11 dB** (consistent 跨 3 anchors)
> - Rear cams ground-only **+1.0~+1.7 dB** (crosswalk / lane markings 跨 cam 边界对齐, 5-20 cm ghost-shifts 消失)
> - vs L3 forward-splat (-3.15 dB), IPM hybrid 是**结构性改进** — paper 角度 B (method) 现在有 concrete contribution。
> - 失败模式: front cams 动态阴影 -0.5~-0.8 dB; 后续 T20 (Fin3R + cycle combo) 可改进。
> - 下一步: Colab 复活后扩 10 anchor sweep (script 已写好, CPU job ~30s)。

> **Latest: 2026-05-21 ~00:18 UTC** — Phase 3 W2 Wave-1 早期进展。
> 启动 v5 plan (`C:\Users\14294\.claude\plans\snug-shimmying-wave.md`) 下 18 tracks 多 subagent 并行执行。
>
> **T-Koi-1**: 8 页 PDF 给 Koi (Phase 3 W1 + 重新定位为 Pi3→Pantheon360 AV2 适配层 + 5 forward path)。
> **T5 metric audit**: **L3 negative 结论 metric-robust** — LPIPS 1.83× 更差, MS-SSIM 0/7 cams, object-band PSNR -6.88 dB (parallax 本该帮 L3 的地方反而输得最惨), sky -3.78, ground -3.22. paper headline 不变 PSNR, 但 main table 加 (PSNR, MS-SSIM, LPIPS) 三元组防 reviewer 质疑 cherry-pick。
> **T6 parallax ranking**: top-3 anchors {0, 150, 60} (score 0.41-0.40), bottom {180, 210} (~0.32). 推荐 T12/T18 先跑 anchor 60。
>
> in-flight: T-Koi-2 (Wave-1 mid-week Koi PDF) + T1-prep (AV2 val UUID 候选搜索)。
>
> **T16 Bayesian fusion done**: Pi3 conf-as-inverse-variance per-ERP-pixel fusion. **修 .ply 几何 (overlap 区域 RMSE 1-5m, 建筑边界更干净), 但不修 L3 ERP cycle-PSNR** (ERP overlap 只 ~2%, L3 ghost 主因是 single-cam mis-splat, fusion 修不了)。 paper framing: ".ply 更干净 for downstream consumer" 而非 "L3 ERP 修好"。 commit `e1dbaa6`. 
>
> **Wave-1 全 7 个 CPU tracks 完成** ✅ (T-Koi-1 + T5 + T6 + T8 + T14 + T16 + T7-prelim). Wave-2 启动: T-Koi-2 (mid-week snapshot) + T1-prep (UUID 选 4 个候选)。
>
> **📜 T7-prelim Paper-angle 决定 (v0)**: 推荐角度 **B-with-C-as-motivation** = "Hybrid 2D/3D pipeline for AV → 360 stitching, with analysis of why naive 3D-lift fails". IPM hybrid (+0.20 dB ground) 作 method contribution; L3 forward-splat negative (-3.15 dB, metric-robust per T5) 作 motivation。 Primary venue **3DV 2026** (~Aug 2026 ddl, 12 周 runway), upgrade CVPR 2027 if T9/T10 downstream lands。 Top risk: T14 10-anchor extension regress (Colab worker back 后必跑)。 Re-issue T7 v1 at W3 D3 after T12 + T16 + T14b + P3.5 done。
>
> **T8 lit watch 完成**: PanFlow (AAAI 2025, alternative panoramic diffusion) + Fin3R (NeurIPS 2025, LoRA fine-tune Pi3 — 直接对应我们 T13) + CylinderSplat (ICLR 2026, 提升出 Out-of-Scope) + Percep360 (ICRA 2026 closest competitor, code pending June 2026)。 我们 hybrid (3D-aware + diffusion) 角度 4-6 周 scooping 窗口。 plan v6 候选: T19 PanFlow spike / T20 Fin3R+cycle combo / T21 Dur360BEV cross-dataset。
>
> **⚠️ BLOCKED**: T12 (temporal Pi3 K=3) submitted Colab job `phase3-t12-temporal-pi3-k3-anchor90` (commit `a95f75c`), 但 Colab worker 心跳 2026-05-21T01:14 已 ~50min 旧, worker session 断了。 **需用户重启 Colab worker cell** (scripts/cell_acq_worker.py 内容), 起来 10s 内自动 pick up job。 阻塞所有 GPU 链条 (T12/T18/T9/T10/T11/T2/T17/T13)。

> **2026-05-20 ~23:31 UTC** — **Phase 3 W1 (multi-anchor robustness) 完成**。
> 10 anchors × Pi3 + 全 metric stress test 结果: Phase 2 所有 headline 数字都在 Phase 3 1σ 内。 Pi3 vs LiDAR `abs_rel = 0.202 ± 0.042`, `δ<1.25 = 0.697 ± 0.142`。 L1 vs L3 `ΔPSNR = -3.15 ± 0.72 dB` (10/10 anchor L3 全输, range -1.60 ~ -4.22)。 Anchor 180 最佳: `abs_rel = 0.139, δ<1.25 = 0.866` 接近 KITTI SOTA。 Phase 2 conclusions **鲁棒**。 详见 `notes/phase3_multi_anchor_report.md`。 下一步: P3.2 多 log + P3.5 OmniStitch baseline + P3.6 D8 paper angle 决策。

> **2026-05-20 ~22:51 UTC** — **Phase 2 P2.11 Pi3 vs LiDAR 完成 (single anchor)**。
> Phase 1 (L1) ✅ · Phase 2 D1 (Pi3 胜) ✅ · P2.3-P2.5 (Sim3 + .ply) ✅ · P2.6 (L1 vs L3 视觉 negative) · P2.7 (cycle-consistency: L3 PSNR 8.65 vs L1 11.78, -3.13 dB) ✅ · **P2.11 Pi3 vs LiDAR: overall abs_rel 0.215, RMSE 7.70m, δ<1.25 = 65.3% (99,015 matched points)** ✅。 **关键发现: Pi3 系统性低估深度 ~25% (mean 13.96m vs 18.53m), 近场 (<15m) δ<1.25 ~0.9, 远场 (>20m) 跌到 ~0.22-0.58**。 下一步: Phase 3 (多 sequence + paper angle 决策 / OmniStitch baseline)。

---

## Phase 完成度

| Phase | 任务 | 状态 |
|---|---|---|
| 0 | Repo bootstrap, plan v0/v1/v2 | ✅ COMPLETE |
| 0.5 | AV2 API spike, 2×4 mosaic, GO 判定 | ✅ COMPLETE |
| **1** | **L1 baseline (sphere + multi-band, mirror fix)** | ✅ COMPLETE · tag `v0.1-l1-mvp` |
| **2 D1** | **Pi3 vs DVGT head-to-head → Pi3 胜** | ✅ COMPLETE · tag `v0.2-d1-resolved` |
| 2 P2.2 | Backbone 适配 AV2 (504×504 letterbox) | ✅ COMPLETE |
| 2 P2.3 | Sim(3) Pi3-world ↔ AV2 ego alignment | ✅ COMPLETE |
| 2 P2.4 | `code/.../alignment/sim3_align.py` (Umeyama) | ✅ COMPLETE |
| 2 P2.5 | `code/.../pipeline/lift_and_project.py` + `.ply` 导出 | ✅ COMPLETE |
| 2 P2.6 | L1 vs L3 视觉对比 | ⚠️ **结论 negative**: forward-splat ERP 不优于 L1, 详见 §"L3 探索结论" |
| **2 P2.7** | **Cycle-consistency PSNR/SSIM/MAE** | ✅ **DONE 2026-05-20**: L3 mean PSNR 8.65 vs L1 11.78 → **ΔPSNR = -3.13 dB**, L3 输 7/7 cam (除 front_center 微胜 0.26 dB)。 forward-splat 量化也确认输给 L1。 |
| 2 P2.8 | 多帧 temporal smoothing | ⏸️ skipped — 单帧已得出 L3 forward-splat 不优结论, 多帧不会改变 |
| **2 P2.9** | **`notes/l3_evaluation_report.md`** | ✅ **DONE 2026-05-20** |
| **2 P2.10** | **tag `v0.2-l3-mvp`** | ✅ **DONE 2026-05-20** — Phase 2 主线收官 |
| **2 P2.11** | **Pi3 vs AV2 LiDAR depth eval** | ✅ **DONE 2026-05-20**: overall abs_rel 0.215, RMSE 7.70m, δ<1.25=65.3% (n=99015). 近场 δ<1.25≈0.9, 远场跌到 0.22-0.58。 Pi3 系统性低估 ~25%。 详见 `notes/pi3_vs_lidar_report.md` |
| **3 W1 P3.3** | **Depth-binned Pi3 vs LiDAR** | ✅ **DONE 2026-05-20**: bias 单调恶化 -12.8% (<5m) → -33.8% (>40m). 证实 Pi3 是真有 depth-dependent 压缩, 不是 selection bias artifact. |
| **3 W1 P3.1** | **Multi-anchor Pi3 (10 anchors)** | ✅ **DONE 2026-05-20**: 10 anchors on A100, mean fwd 1.23s (warm), 总 74s. 详见 `notes/phase3_multi_anchor_report.md` |
| **3 W1 P3.1b** | **Batch P2.7 + P2.11 over 10 anchors** | ✅ **DONE 2026-05-20**: Phase 2 single-frame 数字 all within 1σ. abs_rel 0.202±0.042, δ<1.25 0.697±0.142, ΔPSNR -3.15±0.72 (L3 输 10/10). Phase 2 conclusions 鲁棒. |
| 3 W2-3 | P3.2 多 log + P3.5 OmniStitch baseline + P3.6 D8 paper angle 决策 | ⏸️ next |
| 3 W4 | P3.7 Pantheon360 集成 spike | ⏸️ later |
| 4 | Pantheon360 集成 + Waymo Track B | ⏸️ 未启动 |
| 5 | Paper / follow-up spec | ⏸️ 未启动 |

**整体: Phase 0-2 主线约 70%, 略超 plan v2 W1-W2 进度。**

---

## L3 探索结论 (关键 negative finding)

试过 3 种参数组合 (raw conf > 0.1 / strict conf > 0.5 dist < 40m / L1+L3 hard-mask hybrid), **视觉上都不及 L1 sphere projection**。

**根因**:
- Pi3 单目深度 ±0.3m variance → 路面在 ERP 出现"鼓包"
- L1 (parallax-naive) 和 L3 (3D-aware) 把同一物体投到 ERP 不同位置 → blend 出双影
- 天空 / 低纹理区 Pi3 conf 低, 砍掉后 ERP 大片黑色

**含义**: forward-splat to ERP **不是 L3 的正确输出形式**。 L3 的真正产物是:
- `fused_pointcloud.ply` (690K colored 3D 点, AV2 ego 米制坐标系, 9.9 MB)
- Per-view depth maps (7 张)
- 供下游 3D-aware 消费 (Pantheon360, 3DGS, depth-conditioned diffusion)

要让 L3 ERP 视觉超 L1, 需要 raycast + z-buffer 或 3D Gaussian Splatting (LiftProj/CylinderSplat-class), **这是 Phase 4 题目**。

详见: `notes/backbone_decision.md`, `deliverables/handoff_to_koi_2026-05-20.md` §6。

---

## 关键数字

| Metric | Value |
|---|---|
| AV2 anchor | log `02a00399-3857-444e-8db3-a8f58489c394` (val) · 7 ring + 2 stereo · 319 frames @ 20Hz |
| Sync delta | 22.49 ms (< 50 ms 阈值) |
| Pi3X forward (A100 bf16, 7 view joint) | **8.35 s**, peak 7.5 GB |
| Pi3 K-recovery 误差 vs AV2 真值 | +0.06% ~ +2.08% (mean ~1%) |
| **Sim(3) 对齐残差** | **mean 0.157 m, max 0.218 m, scale 1.0346** |
| L3 .ply | 690,360 colored 3D 点, 9.9 MB |
| **P2.7 cycle-consistency mean** | **L1 PSNR 11.78 vs L3 PSNR 8.65 → -3.13 dB**, L1 wins 7/7 cam on SSIM/MAE |
| **P2.11 Pi3 vs LiDAR overall** | **abs_rel 0.215, RMSE 7.70m, δ<1.25 65.3%, δ<1.25² 90.2%, δ<1.25³ 93.9%** (n=99015) |
| **P2.11 LiDAR sweep sync** | Δt = 9.8ms vs anchor (10Hz LiDAR ~50ms grid) |
| **P2.11 best cam** | ring_front_right: abs_rel 0.170, δ<1.25=91.7% (scene mean 7.05m) |
| **P2.11 worst cam** | ring_rear_left: abs_rel 0.296, δ<1.25=22.3% (scene mean 29.26m) |
| **P3.1 multi-anchor (10)** | 10 anchors × Pi3 7-cam: model load 167s (cold cache), per-anchor warm 1.23s, total 74s inference on A100 |
| **P3.1b LiDAR 10-anchor mean** | **abs_rel 0.202 ± 0.042, RMSE 5.27 ± 1.02m, δ<1.25 0.697 ± 0.142** (893k matched points total) |
| **P3.1b cycle 10-anchor mean** | **L1 PSNR 12.34 ± 1.31, L3 PSNR 9.19 ± 1.18, ΔPSNR -3.15 ± 0.72** (L3 loses 10/10) |
| **P3.1b best anchor** | 180: abs_rel 0.139, δ<1.25 0.866 (≈KITTI-tuned SOTA) |
| **P3.1b worst anchor** | 270: abs_rel 0.283, δ<1.25 0.412 |
| **P3.3 depth-bin bias** (anchor 0) | -12.8% (<5m) → -33.8% (>40m), 单调恶化 → Pi3 真有 depth-dependent 压缩 |
| **P3.3 depth-bin bias** (10-anchor mean) | -10.2% ± 11.2 (<5m) → -23.7% ± 6.8 (>40m), 单调模式 10/10 anchor 都成立, slope 结构性 |
| DVGT 尝试 | 8 次 (v1-v8), 全失败, 详见 §DVGT 失败原因 |

---

## DVGT 失败原因 (Phase 2 D1)

8 次尝试逐步深入:
- v1-v5: clone DVGT / submodule / deps / 公开 URL gate (cumulative blockers)
- v6: HF token 在 worker env 外 → `GatedRepoError 401`
- v7: HF auth OK (whoami JingShuo66), 但 DVGT 硬编码 `.pth` 文件名 HF repo 没有 (只有 `model.safetensors`) → `RemoteEntryNotFound 404`
- v8: 下 `model.safetensors` + 转 `.pth` → key naming 不兼容 (HF transformers 风格 `embeddings.cls_token` vs Meta 原生风格 `cls_token`, 几十层 ViT-L)

**需要修**: 写一层 HF↔Meta state_dict key remapper, 或 patch DVGT 跳过 dinov3 预加载。 均超出 D1 scope。

详见: `notes/backbone_decision.md`。

---

## Track 状态

| Track | 状态 | Branch | Next |
|---|---|---|---|
| **A — Main (AV2 spine)** | **active, P2.6 done (negative), P2.7 next** | `main` | Cycle-consistency 评估 |
| B — Waymo + diffusion fill | not activated | `parallel/waymo` | activates at Phase 2 完成 |
| C — DVGT vs Pi3 eval | **superseded** | — | 8 次 DVGT 尝试已纳入主线 D1, Track C 不再单独 spawn |
| D — OmniStitch baseline | not activated | `parallel/omnistitch` | activates at Phase 2 完成 |
| E — Lit watch | available anytime | `parallel/lit-watch` | user spawns when desired |
| F — Pantheon integration | not activated | `parallel/pantheon` | activates at Phase 3 end |

---

## 衍生产物 — `agent-colab-queue` v0.1.2

调试 Pi3/DVGT 时发现 `colab-mcp` 长任务不稳, 投入 ~5h 实现自研 **Drive-as-queue agent ↔ Colab 框架**:

- 仓库: https://github.com/QiPan-Ronnie/agent-colab-queue
- 架构: Agent → git push job spec → Colab worker git pull → bash 执行 → 结果写 Drive → Agent 读 Drive
- 关键修复 (v0.1.2): Windows subprocess + git 非交互模式 (`stdin=DEVNULL`, `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=Never`) — submit_job 从 200+s hang → 2-3s
- 验证: 3-shape stress test 7s 全过, 真实 MCP submit 5.07s exit=0
- tag `v0.4-acq-mcp-v012-robust`

**复用价值**: 后续 Pantheon360 / 360° diffusion 训练 / 任何长跑 Colab 任务都用它。

---

## 交付物

### 给 Koi 的 week-1 handoff
- 完整版: `deliverables/handoff_to_koi_2026-05-20.md` (14 sections, 含反思 / 时间线 / commit 索引)
- **精简版**: `deliverables/handoff_to_koi_2026-05-20_concise.md` (7 sections, 同 6 张图)
- PDF: `deliverables/handoff_to_koi_2026-05-20{,_concise}.pdf` (4.2 / 3.9 MB)
- 渲染器: `deliverables/_render_pdf.py` (pandoc + xelatex + Cambria/YaHei)
- 6 张图: `deliverables/images/` (spike_mosaic, l1_erp, l3 pc perspective+topdown, depth overlay, l1_vs_l3 hybrid)
- GitHub render: https://github.com/QiPan-Ronnie/Waymo2Panorama/blob/main/deliverables/handoff_to_koi_2026-05-20_concise.md

### Drive 工作区 (panq@usc.edu owns)
- AV2 原数据: `koi_waymo2pano_colab/data/argoverse2/val/02a00399-.../`
- L1 输出: `koi_waymo2pano_colab/outputs/l1/...` (含 .mp4)
- Pi3 7-view 输出: `koi_waymo2pano_colab/outputs/phase2/pi3_one_frame/`
- L3 .ply + depth: `koi_waymo2pano_colab/outputs/phase2/l3_pointcloud/`
- HF 模型缓存: `koi_waymo2pano_colab/hf_cache/` (Pi3X + DVGT-1 都缓存了)

### 关键 commit / tag
- `v0.1-l1-mvp` — L1 baseline 完成
- `v0.2-d1-resolved` — Pi3 backbone 选型完成
- `v0.4-acq-mcp-v012-robust` — agent-colab-queue 验证完成

---

## 已知问题

| ID | Issue | 状态 |
|---|---|---|
| W2P-001 | `colab-mcp` `open_colab_browser_connection` 行为 | **resolved (via agent-colab-queue 替代方案)** — 后续不再依赖 colab-mcp |

无新 active issue。

---

## 下周计划 (Tier 排序, P2.11 完成后更新)

| Tier | 任务 | 估时 |
|---|---|---|
| **1** | **多 sequence / 多 log 扩展** — 1 log × 10 anchors + 3 log × 各 5 anchors。 验证 L1/L3/Pi3-LiDAR metric 的 variance | 2-3 天 |
| **1** | **P2.12 depth-binned metrics** — 验证 Pi3 系统性低估是否 binning artifact, 分 5-10m/10-20m/20-40m/>40m 看 abs_rel | 半天 |
| 1 | **寻找 parallax-heavy frame** — 系统扫 frame, 找近物 + cam 重叠区, 给 L3 真正有机会的场景 | 1 天 |
| 2 | Phase 3 OmniStitch baseline (Track D) — 三方对比 L1 / OmniStitch / L3 | 2 天 |
| 2 | Argus / Percep360 diffusion polish — 填 ERP 上下黑边 + 接缝 | 2 天 |
| 2 | D8 paper angle 决定 — 看 Phase 3 数据 | 关键决策点 |
| 3 | 3DGS / proper raycast L3 ERP (Phase 4 候选) — 让 L3 视觉真正超 L1 | 1-2 周 |
| 4 | Pantheon360 集成 (Phase 4) + Waymo Track B 启动 | Phase 4 |

---

## Update log

| Date (UTC) | Update |
|---|---|
| 2026-05-21 | **Wave 1 新-A 柱面 baseline (L2) 完成**: `code/waymo2panorama/projection/cylinder.py` + `scripts/phase3/run_cylindrical_baseline.py` + `eval_cylindrical_cycle.py`。 4-anchor sweep (0/60/90/150) on Pi3 cache (无 AV2 local data, fall back 到 504×504 letterboxed)。 **Cylinder union coverage 58.55% vs Sphere 33.65% (+24.9 pp; per-cam 1.74× alpha)**, seam gradient -0.98 (4/4 anchors)。 Cycle-PSNR 本协议对 projection surface 不敏感, L1/L2 数字 ≈ 0。 视觉 figure `deliverables/images/route_cylinder_vs_sphere.png` + handoff_to_koi_v6.md 路线 10 节填好。 Verdict: ⚠️ 视觉/覆盖率 win, cycle 数字非 win — 跟 plan 风险表 "新-A 跟球面差不多" 预期一致。 paper Section 5 baseline 对照齐了。 |
| 2026-05-20 23:31 | **Phase 3 W1 完成**: 10-anchor P3.1 + 双 batch (P3.1b lidar + cycle) on A100, 总 ~6min wall-clock。 Phase 2 所有 headline 数字 within 1σ。 Pi3 abs_rel 0.202±0.042, ΔPSNR -3.15±0.72 (L3 输 10/10)。 anchor 180 最佳 (KITTI SOTA-ish)。 `notes/phase3_multi_anchor_report.md`。 bug fix `aeaeb0a`: NaN-safe bars_png in cycle eval. |
| 2026-05-20 23:14 | **Phase 3 启动 + P3.3 完成 (CPU)**: depth-binned metrics 证实 Pi3 系统性低估**不是** P2.11 selection-bias 假说, 是真有 depth-dependent 压缩 — bias -12.8% (近场) → -33.8% (远场)。 `notes/phase3_progress_partial.md` + `scripts/phase3/`。 P3.1 multi-anchor Pi3 等 A100 (probe 显示当前是 CPU runtime)。 |
| 2026-05-20 22:51 | **P2.11 Pi3 vs LiDAR 完成**: 99k 匹配点, overall abs_rel 0.215, RMSE 7.70m, δ<1.25 65.3%。 关键发现 Pi3 系统性低估 ~25%, 近场 (<15m) δ<1.25≈0.9 (SOTA 级), 远场 (>20m) 跌到 0.22-0.58。 `notes/pi3_vs_lidar_report.md` + `scripts/phase2/eval_pi3_vs_lidar.py`。 Colab CPU 43.7s。 |
| 2026-05-20 09:01 | **P2.7 cycle-consistency 完成**: L1 mean PSNR 11.78 vs L3 8.65 → -3.13 dB, L3 量化也输给 L1。 写 `notes/l3_evaluation_report.md`, tag `v0.2-l3-mvp`, Phase 2 主线收官。 |
| 2026-05-20 08:45 | 给 Koi 的 week-1 handoff PDF 完成 (含图嵌入)。 完整版 + 精简版双输出。 `deliverables/_render_pdf.py` 自动化渲染脚本。 |
| 2026-05-20 07:35 | L3 `.ply` point cloud 导出脚本 + per-view depth maps。 690K colored 3D 点。 用户本地 Open3D 验证可视化 (`scripts/phase2/view_pointcloud.py`)。 |
| 2026-05-20 07:00-07:20 | L3 ERP 视觉迭代: raw → strict filter → soft blend hybrid → hard mask hybrid。 negative 结论: forward-splat 不优于 L1。 |
| 2026-05-20 06:55 | Phase 2 P2.3-P2.5 实现完成: `sim3_align.py` (Umeyama), `lift_and_project.py` (forward splat), `run_l3_one_frame.py` 跑通。 Sim(3) 残差 0.157m。 |
| 2026-05-20 05:25 | Phase 2 D1 — Pi3X 7-view forward 8.35s 一击命中。 |
| 2026-05-20 04:00-05:00 | Phase 2 D1 (DVGT 路线 v6-v8, 含 HF token 重试): 即使有 dinov3 access, HF safetensors 用 transformers-style keys 与 DVGT 原生 schema 不兼容, load_state_dict 满屏 unexpected keys。 验证 D1 结论: Pi3 胜。 |
| 2026-05-19 22:43 | Phase 2 D1 初版决议 (`v0.2-d1-resolved`): Pi3 by walkover, DVGT 操作性差 (5 次失败)。 后续 user 拿到 HF dinov3 access 后又试了 3 次, 加固决议。 |
| 2026-05-19 21:00-22:00 | agent-colab-queue v0.1.2 final fix (Windows subprocess + git tty 根因), 3-shape stress test 通过, tag `v0.4-acq-mcp-v012-robust`。 |
| 2026-05-18-19 | agent-colab-queue v0.1.0-0.1.1 开发 (Drive-as-queue 框架 + MCP server)。 |
| 2026-05-17 | Phase 1 L1 baseline 完成: sphere projection + multi-band blending + ERP wrap fix。 发现 + 修复 mirror bug (commit `885b5da`)。 跑出 5-10s `.mp4`。 tag `v0.1-l1-mvp`。 |
| 2026-05-16 | Phase 0.5 Spike GO ✅ — AV2 API 验证, 22.49ms 同步, 2×4 mosaic。 plan v2 (Waymo → Track B, Phase 0.5 inserted, D1/D8 deferred, parallel-tracks §14)。 |
| 2026-05-15 | Repo + brainstorm + plan v0/v1。 |
