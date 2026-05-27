# 2026-05-27 Seam Routing + DiT360 Goal

## Short Goal For Goal Command

Finish Waymo2Panorama seam investigation. Read `agent/plans/2026-05-27-seam-routing-dit360-goal.md` first. Run Stage A no-DL DP seam-routing on AV2 hard_select; if it is NEG/weak MIXED, run Stage B DiT360 feasibility for seam completion/outpainting. Keep GitHub/Drive/local synced, update `agent/progress.md`, update `agent/handoff.md` only if recommendation changes, never delete user files.

## Project Rules

- Local repo: `D:\BaiduSyncdisk\2024 to future\koi chen\experiments\Waymo2Panorama`
- Colab repo: `/content/waymo2panorama`
- Drive root: `/content/drive/MyDrive/koi_waymo2pano_colab`
- GitHub: `QiPan-Ronnie/Waymo2Panorama`; push directly to `main`
- Use Chinese progress updates.
- Update `agent/progress.md` for meaningful experiments.
- Update `agent/handoff.md` only if the top-level recommendation changes.
- Do not create standalone `*_FINDING.md`, `*_SUMMARY.md`, or `*_PIPELINE.md` in `deliverables/`.
- Never delete user files. Only create scoped outputs.
- Do not use old `agent-colab-queue`.
- Do not use pi3-cache 504 letterbox inputs.

## Current Colab Direct

Use raw HTTP `/exec`:

```text
url=https://biography-definition-ultimate-wage.trycloudflare.com
token=196f2057a235fa5da7935feee1bfdacc
runtime=A100 40GB
```

First verify:

```text
echo alive
nvidia-smi
cd /content/waymo2panorama && git status --short && git rev-parse --short HEAD
```

If the tunnel is stale, read Drive `runtime/active_url.json` or ask user for the fresh JSON.

## Stage A: No-DL DP Seam-Routing V2

Goal: improve over L1 `hard_select` by moving the seam path, not by blending, optical flow, local warp, depth, or DL.

Implement:

- Add a seam-routing module under `code/waymo2panorama/blending/`.
- Input is L1 ERP `slabs + weights`.
- For each adjacent camera overlap:
  - find a band around the current hard-select boundary
  - compute cost map from color difference, gradient mismatch, edge/line crossing penalty, and weight reliability
  - solve a minimum-cost seam path with dynamic programming
  - compose one side from cam A and the other side from cam B
- Final output must remain hard-selected: each pixel comes from exactly one camera.
- Add a driver under `scripts/phase3/`.
- Save outputs under `deliverables/seam_routing_v2/`: single images, comparison panels, seam-path visualization, diagnostics JSON.

Validate:

- `02a00399` anchor `0`: BMW case
- `fbee355f` anchor `95`: pedestrian/object seam
- `0bae3b5e` anchor `30`: clean far-field
- Compare `multiband`, `hard_select`, `seam_local_align`, `seam_routing`.
- Visual verdict is primary.
- POS only if seam-routing is visibly better than `hard_select` without new ghost/doubling.
- Otherwise mark MIXED/NEG clearly.
- Commit/push code, artifacts, and `progress.md` update.

## Stage B: DiT360 Seam Completion, Only If Stage A Fails

Goal: answer whether DiT360 can help our seam problem by learning/filling black or cropped transition regions between camera views.

Study:

- Repo: `https://github.com/Insta360-Research-Team/DiT360`
- Clone to Drive: `/content/drive/MyDrive/koi_waymo2pano_colab/external/DiT360`
- Keep local/GitHub pointer or helper scripts in our repo as appropriate.
- Verify from README/code, not assumptions:
  - exact input format
  - whether perspective image to 360 panorama is supported
  - whether panorama inpainting/outpainting/completion from masked input is supported
  - mask convention
  - expected resolution
  - checkpoint path and GPU memory need

Test if feasible:

- Perspective-to-360 path: if supported, test with AV2 camera inputs and judge scene fidelity.
- Seam-completion path:
  - use L1 `hard_select` panorama first; optionally compare L1 `multiband`
  - create seam masks at 20/40/80 px around hard-select seams
  - preserve non-seam regions
  - optionally test alternating preserved camera regions, e.g. 1/3/5/7 vs 2/4/6
  - run DiT360 completion/outpainting if supported
  - save input, mask, output, before/after seam crops, diagnostics under `deliverables/dit360_seam_completion/`

Evaluate:

- Did seam visibility reduce?
- Did non-mask areas stay faithful?
- Were vehicles, lane lines, buildings, or signs hallucinated/removed?
- Pretty but unfaithful means not suitable for Bosch training data, but possible qualitative paper baseline.
- If blocked by checkpoint, OOM, or API mismatch, document exact blocker and next command in `progress.md`.

## Stop Conditions

- Stop after Stage A if seam-routing is clearly POS.
- Continue to Stage B if Stage A is NEG or weak MIXED.
- Always push finished work to GitHub `main`.
- Leave unrelated local changes untouched.
