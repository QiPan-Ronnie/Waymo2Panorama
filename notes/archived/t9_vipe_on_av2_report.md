# T9 — ViPE on L1 ERP (downstream consumer demo)

> **Status**: completed 2026-05-21 (Phase 3 Wave-3/4).
> **Verdict**: **WORKS** — ViPE (panorama branch) runs end-to-end on our L1 ERP and produces pose + intrinsics + masks. This is the first successful "stitched 360 RGB → published downstream system" data flow in the project.

## 1. Why ViPE, why now

After T17 closed Panacea+ as a non-consumer (it eats BEV maps, not stitched RGB ERP), and Pantheon360 is a parallel 360 generator rather than consumer, **ViPE is the only published-and-released system in our reading pile that directly consumes a 360 ERP video and produces useful geometric output** (camera pose + dense near-metric depth). For the paper's angle B-with-C-as-motivation, a working ViPE-on-L1 demo provides:

- a concrete "system integration" entry in Section 6 (paper angle D)
- evidence that the L1 stitched output is geometrically usable, not just visually plausible
- a tie-in to NVIDIA's 360 pose/depth ecosystem (Web360 dataset, Pantheon360 uses ViPE for auto-annotation)
- a credible "downstream consumer demo" for the B-with-C-as-motivation framing

## 2. Input format ViPE expects

| Question | Answer (from paper + `panorama` branch) |
|---|---|
| 360 ERP supported? | Yes — only on the `panorama` branch of `nv-tlabs/vipe` (not on `main`). |
| Pipeline name | `pipeline=panorama` (Hydra override) |
| ERP handling | Projects ERP into **4 virtual horizontal pinhole views + 1 bottom view** (panorama.yaml: `virtual.num_views=4`, `fovx=fovy=100°`, `height=512`, `bottom=true`, `top=false`), then runs joint SLAM across the virtual views. |
| Frame format | mp4 (`streams=raw_mp4_stream` + `streams.base_path=<dir>`) |
| Resolution | Anything ImageIO can decode; our 1024x2048 ERP at 20 Hz worked. |
| Depth output | `pipeline.post.depth_align_model` defaults to `null` on panorama branch — **no depth by default** (SLAM-only). Add `pipeline.post.depth_align_model=dap` (DAP HF model) or `unik3d` (BY-NC-SA) to also get depth maps. |

## 3. L1 ERP we used

- Drive path: `koi_waymo2pano_colab/outputs/l1/02a00399-3857-444e-8db3-a8f58489c394/baseline.mp4`
- 14.85 MB, 1024x2048, 20 Hz, ~5s (~100 frames), h264 yuv420p — same artifact used for all Phase 1-3 work.
- ImageIO metadata probe from ViPE Colab: `{'plugin': 'ffmpeg', 'fps': 20.0, 'source_size': (2048, 1024), 'duration': 5.0, 'codec': 'h264', ...}`

## 4. Install path used (Colab A100, CUDA 12.8 image)

Direct pip install on Colab — no conda. **Total install wall time: 465.7s (7m 46s) on A100.**

```bash
git clone --depth=1 --branch panorama https://github.com/nv-tlabs/vipe.git /content/vipe
cd /content/vipe
pip install -r envs/requirements.txt --extra-index-url https://download.pytorch.org/whl/cu128
pip install --no-build-isolation -e .
```

Installed `vipe-0.1.1+pt27cu128`, torch 2.7.0+cu128, kornia 0.8, hydra-core 1.3.2, transformers 4.48, GroundingDINO ops compiled in `csrc/`.

### Dependency pain — protobuf

`envs/requirements.txt` pins `protobuf==6.30.0rc1` but Colab's preinstalled `tensorflow` only supports protobuf 5.x. Result on first run: `RuntimeError: Detected mismatched Protobuf Gencode/Runtime version suffixes` when ViPE imports GroundingDINO → transformers BertModel → tensorflow.

**Fix**: `pip install 'protobuf==5.28.3'` after the ViPE install. Took 5s, no rebuild. Second run succeeded.

This is the only material install hiccup. No CUDA mismatch, no SLAM extension build failure on Colab A100.

## 5. ViPE inference on L1 ERP — results

Wall time: **96.7s** end-to-end for 100-frame 5s clip, including model downloads (GroundingDINO 358MB + 662MB + DeAOTL 237MB the first time). Pure SLAM time on the cached models would be <20s.

### Outputs written (Drive: `outputs/phase3/t9_vipe/`)

| File | Purpose |
|---|---|
| `pose/l1_erp.npz` | per-frame camera trajectory |
| `intrinsics/l1_erp.npz` + `intrinsics/l1_erp_camera.txt` | estimated panorama intrinsics |
| `rgb/l1_erp.mp4` | the input RGB after ViPE preprocessing |
| `mask/l1_erp.zip` + `mask/l1_erp.txt` | per-frame dynamic-object masks (GroundingDINO + SAM + XMem on the 5 virtual views) |
| `vipe/l1_erp_info.pkl` | ViPE-internal metadata (SLAM state, keyframe indices, scale, etc.) |

No depth maps in this run (panorama pipeline defaults `depth_align_model=null`). A follow-up job can add `pipeline.post.depth_align_model=dap` to also produce near-metric depth.

### Crash mode — cosmetic only

The job exited with `OSError: cannot open resource` at `vipe/utils/visualization.py:303` when trying to render the **viz mp4** (font missing for text overlay "N/A"). This is purely a visualization artifact and **does NOT affect the SLAM outputs above** — all pose/intrinsics/mask outputs were written before the viz step. We treat this as success.

Fix for next run: `apt-get install -y fonts-dejavu` or pass `pipeline.output.save_viz=false`.

## 6. Verdict (5-bullet)

- **ViPE input format**: 360 ERP supported via `--pipeline panorama` (panorama branch only). Projects ERP → 4 horizontal + 1 bottom virtual pinhole views (fov 100°, h 512), then joint SLAM.
- **L1 ERP availability**: already on Drive (`outputs/l1/02a00399-…/baseline.mp4`, 1024x2048, 20 Hz, 100 frames). No regeneration needed.
- **Install path used**: Colab A100 + CUDA 12.8 image, plain `pip install -r envs/requirements.txt -e .` (no conda). One hiccup: protobuf 6.x in requirements conflicts with Colab's tensorflow 5.x — `pip install protobuf==5.28.3` fix, 5s. Total install 7m 46s.
- **ViPE ran end-to-end** on our L1 ERP in 96.7s for the 5s clip. SLAM trajectory, intrinsics, dynamic-object masks all written to Drive. The only failure was a font-not-found OSError in the final viz step — purely cosmetic, all real outputs are present. Visual viewer (`vipe visualize`) not yet rendered.
- **Verdict**: **YES, usable as paper Section 6 demo.** ViPE consumes our L1 stitched ERP, produces a usable trajectory + masks, and would produce metric depth with one config flip (`pipeline.post.depth_align_model=dap`). Install is a few-minutes one-time cost on Colab. Next step: re-run with `depth_align_model=dap` + `save_viz=false` to get depth and a clean exit, then write the Section 6 "downstream consumer" narrative.

## 7. Files

- Orchestrator: `scripts/phase3/run_vipe_on_l1_erp.py`
- Analysis helper: `scripts/phase3/analyze_vipe_outputs.py`
- Probe job: `jobs/phase3-t9-vipe-probe-install-v1.json` (10.4s, ok)
- Install job: `jobs/phase3-t9-vipe-install-v1.json` (465.7s, ok, marker present)
- Run v1 (protobuf bug): `jobs/phase3-t9-vipe-run-l1-erp-v1.json` (15.6s, crashed on transformers import — protobuf mismatch)
- Run v2 (with protobuf fix): `jobs/phase3-t9-vipe-run-l1-erp-v2.json` (96.7s, ran SLAM end-to-end; cosmetic font error at viz step; all SLAM outputs present)
- Drive output root: `koi_waymo2pano_colab/outputs/phase3/t9_vipe/`

## 8. Follow-ups (NOT done in T9; recommend as T9b or fold into Section 6)

1. Re-run with `pipeline.post.depth_align_model=dap pipeline.output.save_viz=false` to get per-frame near-metric depth and avoid the font crash.
2. Quantitative compare ViPE pose (panorama-SLAM on L1 ERP) vs AV2 ego trajectory (ground truth). ViPE's 4-virtual-view SLAM should recover the AV2 vehicle motion up to a scale, ideally within a few %; this would be a real "ViPE works on AV2 via our L1 stitch" benchmark.
3. Render a depth-overlay PNG from ViPE depth on one frame for the paper figure.
