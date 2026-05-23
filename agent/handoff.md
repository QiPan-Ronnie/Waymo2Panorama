# Waymo2Panorama — Agent Handoff

**Updated**: 2026-05-23 (post 7-route video supplementary + agent-colab-direct refactor plan approved)
**Maintainer**: rotating Claude sessions; user is Qi Pan (panq@usc.edu), advisor Koi Chen

---

## 🆕 Pending architecture refactor (high priority for next agent)

**Approved 2026-05-23**: Replace `agent-colab-queue` (git-as-queue, polluting main with infra commits) with new repo `agent-colab-direct` (direct Colab kernel access via Cloudflare Tunnel + Flask executor + Drive-mediated URL handoff).

**Plan file**: `C:\Users\14294\.claude\plans\snug-shimmying-wave.md` (~600 lines, 6 days implementation, includes 6 optimizations: single-cell setup, auto sync↔async, persistent shell, @checkpointed decorator, CF named tunnel, CLI init).

**Why this matters**: every Colab task today commits + pushes to main (15+ noise commits/day). After paper draft starts, this becomes painful. Refactor solves git pollution, also delivers AutoDL-like UX (agent writes code → runs on Colab kernel directly → sees output, no async queue mental model).

**Migration scope for Waymo2Panorama** (after `agent-colab-direct` v0.1.0 ships):
- Generate `notebooks/runtime.ipynb` via `colab-direct init`
- DELETE: `scripts/cell_acq_worker.py`, `scripts/cell_worker_bootstrap.py`, `scripts/runtime_filter.py`, `code/waymo2panorama/utils/drive_queue.py`
- LEAVE: `jobs/*.json` (18 files) as historical audit archive
- Update this handoff's "Infrastructure" section

**Timing decision (user's call)**: do the 6-day refactor BEFORE paper draft (cleaner main for paper commits), or AFTER paper investiture (preserve cycles for paper work). Plan file has both arguments.

---

## TL;DR

Sub-project of the Koi paper chain. Goal: take **Argoverse 2 ring 7-cam frames** (same timestamp) and stitch them into a **360° ERP panorama** that downstream consumers (Pantheon360 / GEN3C / Cosmos) can use. Target venue: **3DV 2026** (main or D&B), advisor Koi Chen.

**Current state (2026-05-21)**:
- **8 stitching routes done + benchmarked** (L1 sphere, L3 Pi3 forward-splat, IPM ground hybrid, 新-A cylinder, 新-B graph-cut seam, 新-C IPM multi-region, 新-D wide-baseline stereo, 新-E HDR compensation).
- **Final Koi deliverable shipped**: `deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.{md,pdf}` (13 pages, 11 figures, 8 routes + 3 external NEG + 3 downstream demos).
- **新-F VGGT** (4th backbone NEG) — **blocked**: `facebook/VGGT-1B-Commercial` is gated repo, user needs to click "Agree and access" on HF first.
- **T13 self-sup Pi3 finetune** — **deferred pending Koi feedback** (5-6 day GPU train, high cost, gated on paper angle decision).
- **Paper angle**: candidate is **A' Method paper** (3 stack-able positive contributions: 新-C IPM ground +0.20 dB / 新-E HDR -18% color gap / 新-B graph-cut visual win) + 4-5 NEG. Awaiting Koi 拍板 (G3 v6 gate).

---

## Start here (new agent onboarding, ~30 min read)

In this order:

1. **`agent/progress.md`** — single source of truth, every track's "怎么做 / 结果 / Deliverables / Status / Next" block. Top of file is the latest entry. Read top 5-10 blocks for current state.
2. **`deliverables/handoff_to_koi_w2_2026-05-21_v6cpu_done.md`** — what we told the advisor. Has the 8-route summary table (§1), per-route details (§1.1-§1.8), downstream demos (§2), external baselines (§3), pending GPU routes (§4), final ranking table (§5).
3. **`C:\Users\14294\.claude\plans\snug-shimmying-wave.md`** — full plan v6.1 with strategic context (why we pivoted from system integration to stitching-only), 16 routes table, sequencing, decision gates G1-G5, risk register, contracts for each subagent.
4. **`agent/2026-05-15-brainstorm-survey.md`** — original brainstorm survey (CV concepts, dataset comparison, related work). Reference; concepts still apply.

---

## Project chain context (Koi Chen paper line)

```
AV2 / Waymo / nuScenes multi-cam (THIS PROJECT)
        │
        ▼ stitch 7-cam → 360° ERP
ERP panoramic video + 3D cache
        │
        ▼ (downstream, paused per v6.1 pivot)
04-pantheon360  (3D-aware 360° video diffusion, CVPR 2026)
        │
        ▼
360 world simulation / Cosmos-Predict / Argus
```

We control the **first step only**. Downstream (Pantheon360 / GEN3C / Argus / Cosmos) is paused per v6.1 strategic pivot — user has paper deadlines on stitching methodology, downstream is post-publication work.

---

## 8 stitching routes (current snapshot)

| ID | Name | Verdict | Headline metric | Code |
|---|---|---|---|---|
| **L1** | Sphere baseline + 5-band Laplacian | ✅ Strong baseline | cycle-PSNR **12.34 ± 1.31 dB** (10 anchors) | `code/waymo2panorama/projection/sphere.py` |
| **L3** | Pi3 forward-splat | ❌ Structural NEG (paper Section 4) | -3.15 dB vs L1, 10/10 lose | `code/waymo2panorama/pipeline/lift_and_project.py` |
| **IPM** (T14) | Ground plane hybrid | ⚠️ Marginal +0.05 dB | +0.20 dB on ground-only mask | `code/waymo2panorama/projection/ipm_ground.py` |
| **新-A** | Cylindrical L2 | ⚠️ Coverage gain only | +24.9 pp coverage, cycle ~flat | `code/waymo2panorama/projection/cylinder.py` |
| **新-B** | Graph-cut seam | ✅ Visual win | -12.4% seam-band \|grad\| (4/4 anchors) | `code/waymo2panorama/blending/graphcut_seam.py` |
| **新-C** | IPM multi-region (ground+sky+building) | ✅ Method win | **+0.20 dB on ground** (4× T14), building branch deferred | `code/waymo2panorama/projection/ipm_multi_region.py` |
| **新-D** | Wide-baseline stereo (邻 cam) | ⚠️ Partial (5/7 pairs) | 44 inlier 3D pts/pair median, sparse | `code/waymo2panorama/stereo/wide_baseline_stereo.py` |
| **新-E** | HDR cross-cam compensation | ✅ Drop-in preprocess | **-18% lum gap** (4 anchors mean) | `code/waymo2panorama/color/hdr_gain_estimate.py` |

Plus **3 external NEG**: OmniStitch (-6.67 dB), Apple Depth Pro (2.84× worse abs_rel), Temporal Pi3 (no improvement).
Plus **3 downstream demos** (paused): ViPE SLAM, GEN3C, Panacea+ (modality NEG).

---

## Open decisions (G3 v6 gate, awaits user/Koi)

| Decision | Default if no input | Trigger to flip |
|---|---|---|
| **Paper angle**: A' Method / B-with-C / C Negative-only | A' Method (3 positives stackable) | Koi reviews PDF + says "not enough positives, go B" |
| **Run 新-F VGGT** (4th backbone NEG) | Skip (gated repo blocker + low marginal value) | User requests HF access + says "do it" |
| **Run T13 Pi3 self-sup finetune** (5-6 day A100) | Skip (high cost, paper angle A' doesn't need it) | Koi says "need at least 1 backbone-level win" |
| **Target venue**: 3DV 2026 main / D&B / CVPR workshop | 3DV 2026 main (angle A' fits) | Reviewer feedback on draft v0 |

---

## Currently in-flight (Colab worker state)

- **Worker**: last alive 2026-05-23 ~14:00 UTC (after user re-ran cell at 12:56 UTC), polling every 5 s, no active jobs.
- **Likely state at handoff time**: A100 disconnected (user did so after video generation session). Worker dead until cell re-run on next Colab session.
- **3 new-f jobs queued in repo `jobs/`**:
  - `phase3-new-f-vggt-1-install-v1.json` — CRASHED at step 6 (HF gated repo 403). Will re-attempt + crash again on next worker restart (~3 min waste). See `jobs/README.md` for retry protocol.
  - `phase3-new-f-vggt-2-eval-v1.json` — done_marker exists (status: skipped_install_missing), worker skips.
  - `phase3-new-f-vggt-3-tar-cache-v1.json` — done_marker exists (status: skipped_eval_missing), worker skips.

To unblock 新-F: user clicks "Agree and access" at https://huggingface.co/facebook/VGGT-1B-Commercial → worker auto-retries on next git pull.

---

## Video deliverables (added 2026-05-23, paper supplementary)

7 of 8 routes have 5-second mp4 videos at anchor 60 area (start_sec=3.0, 100 frames @ 20 fps, 1024×2048 ERP). 新-D wide-baseline stereo is NOT videoable (outputs sparse 3D points, not dense ERP).

All on Drive at `MyDrive/koi_waymo2pano_colab/outputs/<route>_video/<log_id>/<route>_video.mp4`:

| Route | Drive folder | Size | Wall time | Direct view URL |
|---|---|---|---|---|
| L1 baseline (HDR `--also-baseline`) | `hdr_video/.../baseline_video.mp4` | 17 MB | (parallel with HDR) | `13jNNJCV8FjMGMUbqo03I47ZMTTTJBpro` |
| L3 Pi3 forward-splat | `l3_video/.../l3_video.mp4` | 24 MB | 7 min | `1PZEvwFoCeQUc0oatymgYL7cw0XyF-AcL` |
| T14 IPM ground hybrid | `ipm_hybrid_video/.../ipm_hybrid_video.mp4` | 13 MB | 7.7 min | `1ozuDgzl4g-Anxg1qHJTq8m6liQrSDkn4` |
| 新-A 柱面 (L2) | `cylindrical_video/.../cylindrical_video.mp4` | 26 MB | 5.7 min | `1YvkYTW2dEHrBkH0wKTmxl2s9UoZwIs1z` |
| 新-B Graph-cut seam | `graphcut_video/.../graphcut_video.mp4` | 17 MB | 16 min | `1aA9iw8RTLFTOXFwGYFYAwBFFIvHbwa2s` |
| 新-C IPM multi-region | `ipm_multi_region_video/.../ipm_multi_region_video.mp4` | 13 MB | 12 min | `1O5dAAq6MASxUtFyebuzrPN3fK6FTbLoX` |
| 新-E HDR cross-cam | `hdr_video/.../hdr_video.mp4` | 15 MB | 16 min | `1Ln-BV6zU_FwQ7yzdY2_e9Y0X3V74-cUA` |

**Video driver scripts** (all at `scripts/run_*_video.py`, follow same pattern as `run_l1_baseline.py`):
- `run_l3_video.py` — Pi3 → Sim(3) → forward-splat per frame
- `run_cylindrical_video.py` — sphere → cylinder, multiband blend
- `run_graphcut_video.py` — L1 + apply_graphcut_seams (Boykov-Kolmogorov min-cut)
- `run_hdr_video.py` — L1 + per-cam 6-param gain+bias (Huber LS); `--also-baseline` flag writes parallel L1 mp4
- `run_ipm_hybrid_video.py` — Pi3 + detect_ground_from_pi3 + ipm_project_ground + sphere fallback
- `run_ipm_multi_region_video.py` — Pi3 + ipm_project_multi_region (ground + sky routing; building branch off by default)

**To generate more videos** (different anchor / log / duration): copy a `run_*_video.py` script, edit args (or just pass CLI flags), write a new `jobs/phase3-*-video-*.json` spec. Worker picks up automatically. Pattern documented in `jobs/README.md`.

---

## Infrastructure (must-know)

### agent-colab-queue (user's own MCP)
- Worker runs in Colab cell, pulls `jobs/*.json` from GitHub every 10 s
- Sorts jobs **alphabetically** (NOT by created_at) — name jobs with prefix order in mind
- Result JSON written to Drive `koi_waymo2pano_colab/results/<job_id>.json`
- Done-marker file written to wherever the job spec says (typically a target output JSON)
- Worker is robust to job crashes; keeps polling
- Heartbeat at `koi_waymo2pano_colab/worker/heartbeat.json` (updates every 5 s when alive)
- Workspace info: `mcp__agent-colab-queue__workspace_info(name="waymo2panorama")`
- See `[[agent-colab-queue-framework]]` memory for details

### Drive workspace
- Root: `MyDrive/koi_waymo2pano_colab/` (panq@usc.edu)
- Cached fileIds in `[[drive-folder-ids-koi-waymo2pano]]` memory
- Subdirs: `outputs/phase3/` (all run results), `data/argoverse2/` (4 logs, ~32 GB), `cache/` (tar'd conda envs for fast restore), `worker/heartbeat.json`, `results/<job_id>.json`

### GitHub
- Repo: `git@github.com:QiPan-Ronnie/Waymo2Panorama.git`
- Direct push to main is **authorized** (per memory `[[feedback-direct-push-main-waymo2pano]]`) — no PR review needed for this repo
- Worker pulls from main on every poll

### Code layout
```
code/waymo2panorama/
  data_io/         AV2 ring loader (RING_CAMS_7, calib parsing)
  projection/      sphere / cylinder / ipm_ground / ipm_multi_region
  blending/        multiband (Laplacian pyramid) + graphcut_seam
  stereo/          wide_baseline_stereo
  color/           hdr_gain_estimate
  alignment/       sim3_align (Umeyama)
  pipeline/        lift_and_project (L3 forward-splat) + depth_bayesian_fusion
scripts/
  phase2/          single-anchor evals (eval_pi3_vs_lidar, eval_cycle_consistency)
  phase3/          multi-anchor batch evals + each new route driver + run_pi3_multi_anchor + run_vggt_multi_anchor
  phase4/          (future) T13 finetune scripts will go here
deliverables/
  handoff_to_koi_w2_2026-05-21_v6cpu_done.{md,pdf}    THE Koi handoff
  _make_*.py / _render_pdf*.py                         figure / PDF tooling
  images/route_*.png                                   actual figures used
  learning_plan.md                                     user's own CV roadmap
agent/
  progress.md          single source of truth, append-only log
  handoff.md           this file
notes/
  new_{a,b,c,d,e,f}_*.md  per-route research/design docs
  t13_*.md                T13 finetune design doc
jobs/*.json              Colab queue specs (worker pulls these)
outputs/phase3/*         all run results (mostly mirrored to Drive)
```

---

## Defensive lessons (HARD-WON — read before any Colab job)

These cost us hours/dollars. Don't repeat:

1. **`set -e -o pipefail` + `cmd | head -N` = death**. `head` closes stdin after N lines, sends SIGPIPE upstream, pipefail kills the script. Use `tail -N` instead. Bit us on T11 GEN3C install (exit 141, 5 sec into install).
2. **Conda activate hates `set -u`**. `activate-gcc_linux-64.sh` references unbound `SYS_SYSROOT`. Wrap `conda activate` in `set +u`/`set -u`. Bit us on T11 v1.
3. **HF gated repos need explicit access click**. `facebook/VGGT-1B-Commercial` 403'd on us even with HF token — must click "Agree and access" on the HF model page. Bit us on 新-F.
4. **Worker sorts jobs alphabetically, NOT by created_at**. If you submit `bbb-install` then `aaa-eval`, eval runs first. Name jobs with prefix order in mind.
5. **Tar conda env to Drive after heavy installs**. Saves ~50 min on Colab reconnects. See `[[feedback-colab-tar-env-to-drive]]` for the zstd-tar restore pattern.
6. **No way to remotely shut down Colab runtime**. Worker is a Python process inside Colab; we can stop submitting jobs but A100 stays alive until user manually disconnects. Tell user explicitly when failing.
7. **Pi3 vs VGGT input pipeline difference**: Pi3 takes raw (B, V, 3, H, W) tensor; VGGT takes file paths via `load_and_preprocess_images()`. The driver `run_vggt_multi_anchor.py` saves letterbox images to a tmp dir then feeds paths to VGGT.
8. **`eval_cycle_consistency.py` applies Sim(3) alignment via `pose_<cam>.npy`**. For Pi3 this converts world→ego. For VGGT/Depth Pro we use AV2 truth extrinsics so save `pose_<cam>.npy = T_ego_cam` to make Sim(3) collapse to identity.
9. **Pi3 repo path on fresh Colab session is `/content/Pi3`, NOT `/content/01-pi3-Pi3`**. Use the 3-URL clone fallback: `git clone yyfan2014/Pi3 || yyfz/Pi3 || yyfan2014/Pi3-clean`. Bit us on L3 video v1 + v2 (wasted 3 min total). Pass `--pi3-repo /content/Pi3` explicitly in job spec.
10. **API signature drift between similar modules**: `detect_ground_from_pi3()` does NOT take `conf` kwarg even though `segment_regions_from_pi3()` does. Don't copy-paste kwarg patterns between modules — always `grep -n "^def "` the target module first. Bit us on T14 video v1 (crashed first frame).
11. **Path resolution from `scripts/` vs `scripts/phase3/`**: scripts at `scripts/` level need `here / "../code"` (1 level up) and `here / "../../01-pi3/..."` (2 levels up). Scripts at `scripts/phase3/` need `"../../code"` and `"../../../01-pi3/..."`. Don't blindly copy the path constants between locations. Bit us on L3 video v1.
12. **Python `print()` is block-buffered when piped via `tee`** (default 4-8 KB buffer). Long Pi3 model loads can take 30-60s with NO output visible in `tail -f run.log`. Use `print(..., flush=True)` or `PYTHONUNBUFFERED=1` env var for real-time progress on long-running scripts.
13. **Worker idle ≠ A100 free**. Worker Python process can be alive + polling but A100 runtime is still allocated and billed (~$3-4/h). Always tell user to disconnect Colab runtime when work finishes, even if worker is "just idle".
14. **Drive API metadata cache delay**: `modifiedTime` from `search_files` can be 30-60s stale. If checking worker liveness, do 2-3 reads spaced ~30s apart before declaring worker dead.

---

## Memory references (Claude session memory)

Located in `C:\Users\14294\.claude\projects\D--BaiduSyncdisk-2024-to-future-koi-chen\memory\`:
- `[[agent-colab-queue-framework]]` — robust agent↔Colab framework details
- `[[drive-folder-ids-koi-waymo2pano]]` — cached Drive fileIds (root, results, heartbeat)
- `[[feedback-direct-push-main-waymo2pano]]` — user authorizes direct push to main
- `[[feedback-colab-tar-env-to-drive]]` — zstd-tar pattern for heavy installs
- `[[feedback-prefer-robust-frameworks]]` — engineer fixes over workarounds

---

## Tooling

- Python 3.10/3.12 (Colab compat)
- AV2 API: `pip install av2`
- Pi3: `../../../01-pi3/code/official/Pi3` (local clone) + HF `yyfz233/Pi3X` (open)
- VGGT: `git clone https://github.com/facebookresearch/vggt` + HF `facebook/VGGT-1B-Commercial` (GATED)
- Pytorch: bf16 native on A100, fp32 fallback
- HF auth: token at `C:\Users\14294\.huggingface\token` (local only, NEVER send to Colab/agent)
- GitHub SSH key: `C:\Users\14294\.ssh\id_ed25519_github_new` (local only, NEVER send to Colab/agent)
- Compute: Colab Pro A100 (user's account, panq@usc.edu)
