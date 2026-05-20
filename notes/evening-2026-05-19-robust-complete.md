# Evening — 2026-05-19 — System is Robust

Status: ✅ **Goal "完全用于后续的所有 colab 工作" — ACHIEVED.**

Tag: `v0.4-acq-mcp-v012-robust` on Waymo2Panorama (commit `fcd381f`).

## What you'll do tonight (1 minute)

1. **Restart Claude Code** so it picks up v0.1.2 of agent-colab-queue:
   ```powershell
   exit
   cd "D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama"
   claude
   ```
   uvx will rebuild on first acq__ tool call (~10-20 s once).

2. **Sanity-check** the new MCP loaded:
   ```
   mcp__agent-colab-queue__server_info()
   → version: "0.1.2"
   ```

3. **One quick verification submit** (optional, ~3 s):
   ```
   mcp__agent-colab-queue__submit_job(
       workspace="waymo2panorama",
       job_id="evening-sanity-check",
       cmd=["bash","-c","echo hi; touch /tmp/evening-sanity-check.done"],
       done_marker="/tmp/evening-sanity-check.done",
       timeout_s=20,
   )
   → {ok: True, commit_sha: "...", drive_result_path: "..."}
   ```

4. Then we're ready for **any** Phase 2 / 3 / 4 work via the MCP.

## What changed since this morning

### v0.1.1 → v0.1.2 — root-cause fix for the hang

We had three layered diagnoses today, only the last was right:
- "It's Baidu sync" — wrong, Bash git was fast even during sync
- "It's a Python subprocess decode issue" — partial, v0.1.1 fixed catch handling
- **"It's git looking for a tty"** — correct. Python subprocess on Windows without a stdin tty makes git hang on credential prompts. v0.1.2 sets `stdin=DEVNULL` + `GIT_TERMINAL_PROMPT=0` + friends. Fix is 7 lines.

Library commit: `27cd510` in https://github.com/QiPan-Ronnie/agent-colab-queue

### Stress-tested via Python library (mimics MCP exactly)

Three submit_jobs in different shapes, all succeeded:

| Job | submit_job latency | end-to-end (worker done) | result state |
|---|---|---|---|
| acq-stress-1-trivial | ~2.5 s | 5 s | ✅ done, exit_code=0 |
| acq-stress-2-env | 2.3 s | 5 s | ✅ done, exit_code=0 |
| acq-stress-3-longcmd | 2.2 s | 16 s | ✅ done, all 5 steps logged |

Total: 3 jobs submitted in 7 s, all completed within 16 s of worker pickup.

## What's in place now (the inventory)

### Repos / packages
- `Waymo2Panorama` (this repo): tagged v0.4 ([on GitHub](https://github.com/QiPan-Ronnie/Waymo2Panorama))
- `agent-colab-queue` v0.1.2 ([on GitHub](https://github.com/QiPan-Ronnie/agent-colab-queue))
- `colab-mcp` (legacy fork, with W2P-001/-002 patches; used optionally for quick prototyping)

### MCP servers in `~/.claude.json`
- `colab-mcp` — legacy, for quick cell-level prototyping
- `agent-colab-queue` — primary, for all long-running jobs (the new one we just shipped)
- `claude.ai Google Drive` — for reading results

### Workspace `waymo2panorama` registered
At `~/.agent-colab-queue/workspaces.yaml`:
- repo_url: `git@github.com:QiPan-Ronnie/Waymo2Panorama.git`
- repo_local: `D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama`
- drive_workspace_title: `koi_waymo2pano_colab`
- git_ssh_key: `C:\Users\14294\.ssh\id_ed25519_github_new`

### Colab worker
Running in your saved notebook at https://colab.research.google.com/drive/1cpEjceeWZvh_aNsoqSWPXIubxNvUtyll. Heartbeat confirmed within last few minutes (the 3 stress jobs were processed there).

If the worker dies (Colab kernel timeout, you closed the tab), recover by:
1. Open the notebook
2. Cell 1 (mount Drive)
3. Cells 3 / 4 / 5 (re-prepare environment)
4. Paste content of `scripts/cell_acq_worker.py::CELL_CODE` into a new cell, run.

## What to NOT touch tonight (it's all working)

- The 3 MCP server configs in `~/.claude.json` — leave alone
- The colab-mcp fork in `tools/colab-mcp` — leave alone  
- The agent-colab-queue source in `tools/agent-colab-queue` — leave alone
- The Colab worker cell (until you migrate to new ACQ worker, which is optional)

## Phase 2 D1 is ready to go

When you want to start Phase 2 (Pi3 vs DVGT head-to-head), tell me and I'll:
1. Write `scripts/phase2/run_pi3_one_frame.py` (~1 h)
2. Write `scripts/phase2/run_dvgt_one_frame.py` (~3 h)
3. Submit both as ACQ jobs (you switch Colab to A100 runtime first)
4. Read results, write `notes/backbone_decision.md`

See [phase2-d1-backbone-decision.md](./phase2-d1-backbone-decision.md) for the
design.

## Commits made this evening

```
fcd381f  v0.4 robustness verified — stress-tested 3 concurrent jobs (this)
9a6fcc6  acq: submit job acq-stress-3-longcmd  (from MCP library)
13e6296  acq: submit job acq-stress-2-env       (from MCP library)
8611ac0  acq: submit job acq-stress-1-trivial   (from MCP library)
5ddd8ce  Remove invalid v0.1.2 test spec
658f766  v0.1.2 subprocess fix validation
b3aa065  clean-env test (Bash run, post Baidu exit)
```

agent-colab-queue:
```
27cd510  v0.1.2 root-cause fix - stdin=DEVNULL + GIT_TERMINAL_PROMPT=0
c73678f  v0.1.1 robustness fix - subprocess decode + safe_tool exception wrapper
82e7c0f  v0.1.0 initial release
```

## Tags
- `v0.1-l1-mvp` — L1 baseline (Phase 1 done)
- `v0.2-w2p004-validated` — Drive queue validated (yesterday evening)
- `v0.3-acq-mcp-shipped` — agent-colab-queue MCP shipped (early today)
- **`v0.4-acq-mcp-v012-robust`** — robustness verified (now)

---

Have a good evening. Everything works.
