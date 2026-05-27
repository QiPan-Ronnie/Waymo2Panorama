# Phase 2 D1 — Ready to launch, waiting on A100 runtime

Date: 2026-05-19 evening
Status: all code committed (`d862024`), Colab worker alive on CPU runtime — blocked on user switching to A100.

## What's ready

| Component | Status |
|---|---|
| `scripts/phase2/run_pi3_one_frame.py` | ✅ committed |
| `scripts/phase2/run_dvgt_one_frame.py` | ✅ committed |
| `scripts/phase2/compare_pi3_vs_dvgt.py` | ✅ committed |
| AV2 data on Drive (`02a00399-...`) | ✅ verified (Drive folder fileId `1LWod1aIPnMh_klwyevSrgSzt3JxF2FNI`) |
| acq worker heartbeat | ✅ fresh (2026-05-20T05:02 UTC) |
| acq MCP submit_job → worker pull → result on Drive | ✅ end-to-end verified by `phase2-gpu-probe` (15s round-trip on CPU) |

## What blocks us

GPU probe (`jobs/phase2-gpu-probe.json`) reported:
```
cuda_available: False
device:         cpu
torch:          2.10.0+cpu
```

Pi3X and DVGT both need GPU. Pi3 wants `torch 2.5.1+cu124` ideally, DVGT wants `torch 2.8.0+cu128`. Neither will run on a CPU runtime.

## What the user needs to do (5 minutes)

1. **Open the Colab tab** (the one where the worker is running).
2. **Runtime → Change runtime type → A100 GPU → Save.**
   - This kills the current kernel. The worker stops cleanly (heartbeat goes stale; acq jobs queued during the gap will pick up when the worker re-starts).
3. **Re-run the worker cell** (the one from `scripts/cell_acq_worker.py`). It auto-reinstalls acq + re-clones the repo. ~30 s.
4. **Tell the agent**: "切好了" / "A100 ready" — and I'll immediately submit a fresh GPU probe and then the Pi3 + DVGT jobs.

## What I'll do after the switch (autonomous)

1. Re-submit `phase2-gpu-probe` to confirm A100 visible, note torch version.
2. Submit Pi3 job (`phase2-pi3-one-frame`).
   - If torch is wrong version, the job's bash cmd will `pip install` Pi3's stack first.
   - Expected wall-clock: ~3 minutes (model download + 7-view forward).
3. Submit DVGT job (`phase2-dvgt-one-frame`).
   - Job's bash cmd will (a) git clone DVGT, (b) pip install DVGT deps, (c) hf_hub_download the checkpoint, (d) run script.
   - First run expected ~6-10 minutes (cold install + checkpoint pull).
4. Run comparison script (locally is fine — just reads npy files from Drive).
5. Write `notes/backbone_decision.md` with the 7-metric scorecard + verdict.
6. Tag `v0.2-d1-resolved` and update `plan.md` Phase 2 task list.

## Why two separate jobs (not one)

Pi3 and DVGT have **conflicting PyTorch versions**:
- Pi3 wants `torch 2.5.1+cu124`
- DVGT wants `torch 2.8.0+cu128`

Each job will `pip install` its own torch stack at the start of its bash cmd. Running Pi3 first means we don't tear down what Pi3 needs; DVGT then overlays its own torch (since it's last). If DVGT clobbers Pi3 deps after, that's fine — we never need both in the same kernel.

## If A100 isn't available

Colab Pro sometimes serves T4 instead. T4 (16GB) should fit Pi3X at 504×504×7-view bf16 (~10 GB est). DVGT may OOM — would fall back to V100 or split-batch. **If A100 unavailable, accept T4 and document the GPU model in the comparison report.** No need to delay.

## Quick reference

| Action | Command |
|---|---|
| Verify worker alive | `mcp__claude_ai_Google_Drive__download_file_content(fileId="1SYBCSBWvvTk0npseVlFMUFTtNJV4LpGl")` decode → check `updated_at` |
| Verify GPU | submit a `python -c "import torch; print(torch.cuda.get_device_name(0))"` job |
| Submit Pi3 | `mcp__agent-colab-queue__submit_job(workspace="waymo2panorama", job_id="phase2-pi3-one-frame", cmd=[...], done_marker="/tmp/...")` |
| Pull result | `mcp__claude_ai_Google_Drive__search_files(query="parentId='15gGDXHr7OU_ujHTmHfFK8Tm-7FdBGVT_' and title='phase2-pi3-one-frame.json'")` |
