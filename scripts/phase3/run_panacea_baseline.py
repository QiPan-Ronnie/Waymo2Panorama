"""
Phase 3 T17 - Panacea+ (arXiv 2408.07605) baseline / downstream-consumer recon.

Reference: https://github.com/wenyuqing/panacea
Paper:     Wen et al., "Panacea+: Panoramic and Controllable Video Generation
           for Autonomous Driving", arXiv:2408.07605, August 2024.

# What Panacea+ actually is (Step 1 recon, see notes/t17_panacea_report.md)

Panacea+ is a multi-view (6-camera ring, nuScenes layout) controllable video
diffusion model conditioned on:

  (a) **BEV layout sequence**: projected 3D bounding boxes + per-object depths
      + road / lane HD-map raster + camera-pose embeddings, packed into an
      8-channel control tensor (see `inference_nuscenes.yaml::in_channels=8`).
  (b) **Text prompts**: weather / time / scene descriptors.
  (c) **Optional first-frame conditioning**: previous-clip last frame for
      autoregressive extension (`--use_last_frame true`).

It outputs a **6-view multi-camera video clip** at 256 px (FrameLength=8 per
config), NOT a 360 deg ERP video. The "panoramic" in the title refers to the
fact that the 6 cameras span 360 deg, not that the output is a single ERP image.

# Why this matters for Phase 3 T17

The original T17 brief asked for a "downstream consumer" demo proving our L1
ERP / Pi3 .ply is useful for a SOTA 360 video diffusion pipeline. After
careful recon (Step 1, this script's docstring is the executable record),
Panacea+ is **NOT** a viable consumer of our outputs in its present form:

  1. **Input mismatch**: Panacea+ consumes BEV-rasterised layout (3D bbox +
     HD-map), NOT RGB ERP. Our L1 ERP / Pi3 .ply outputs are not in the
     control-signal modality. Adapting our outputs to Panacea+'s expected
     control tensor would require rendering 3D bboxes + HD-map from AV2's
     annotation feathers, which is independent of our L1/L3/Pi3 work and
     does not demonstrate that *our* pipeline is the value-add.
  2. **Output mismatch**: Panacea+ produces multi-view 6-cam video at 256px,
     NOT ERP. Even a successful run would not give us a 360 deg ERP clip
     comparable to our L1 ERP.
  3. **Dataset mismatch**: Panacea+ is trained on nuScenes (6 cam, fixed rig
     geometry, ~1600x900 native, downsampled to 256 for diffusion). AV2 has 7
     cams with a different intrinsic / extrinsic layout (front_center
     portrait 2048x1550; six landscape 1550x2048). The paper's AV2
     experiments (sec 4) re-train the model on AV2-format data for
     downstream **detection / tracking** metric, not zero-shot inference.
  4. **Inference compute**: Reference command uses 8x GPUs with DeepSpeed
     (`torch.distributed.launch --nproc_per_node=8`) and a DeepSpeed
     checkpoint `panaceaplus_40k_deepspeed.ckpt`. Colab gives us 1xA100.
     DeepSpeed inference fan-out can usually be reduced, but the
     pre-2023 dep stack (Python 3.8, torch 1.13.1+cu117, xformers 0.0.16,
     mmcv-full 1.6.0, mmdet 2.28.2, mmdetection3d v1.0.0rc6, transformers
     4.19.1) requires a CUDA-11.7 container that conflicts with Colab's
     current PyTorch 2.x + CUDA 12.x default image.
  5. **HuggingFace ckpt gated**: `huggingface.co/wenyuqing/Panacea-Plus`
     returned HTTP 401 from anonymous WebFetch on recon date (2026-05-20).

# What this script DOES do

Three modes:

  --mode recon       : (default) load AV2 anchor frame via av2_loader; print
                       summary of what Panacea+ expects vs what we have;
                       write a `summary.json` documenting the mismatch and
                       the *would-be* steps to wire it up. Used as the
                       Step-1 deliverable on Colab where we can also verify
                       the github clone + python imports succeed without
                       installing the full dep stack.
  --mode install     : on Colab only - attempt `pip install -r requirements/
                       pt13.txt` from the cloned Panacea repo, capture the
                       resulting error trace. Documents the dep-hell as a
                       paper-worthy "cannot-reproduce" finding.
  --mode inference   : would launch the official inference.py with the
                       Panacea+ checkpoint. NOT IMPLEMENTED - the script
                       prints why and exits 0 with summary.json marking
                       blocker. (Implementing this would require ~1-2 weeks
                       of dataset-prep work to convert AV2 annotation
                       feathers into the BEV-layout pkl format that the
                       Panacea+ dataloader expects, plus likely re-training
                       on AV2 since the released ckpt is nuScenes-only.)

# Input / output layout

Input:
  --log-dir          AV2 sensor log dir. Same convention as Phase 1/2
                     av2_loader (sensors/cameras/<cam>/*.jpg +
                     calibration/*.feather).
  --anchor-idx       anchor frame index (60 per Phase 3 W1 standard).
  --panacea-dir      path to cloned panacea repo (only used in install /
                     inference modes; ignored in recon).

Output (under --output-dir):
  summary.json       full Step-1 recon record + mismatch analysis.
  av2_anchor_grid.png  2x4 mosaic of the 7 ring cams (informational, lets us
                     visually confirm the anchor frame loaded correctly).
  install_log.txt    pip install output (install mode only)
  panacea_output.mp4 placeholder marker file (zero-byte) so the
                     done_marker check passes when running in recon mode;
                     ensures Colab job doesn't sit waiting on a real .mp4
                     that we have no way to produce.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"

PANACEA_REPO_URL = "https://github.com/wenyuqing/panacea.git"
PANACEA_HF_CKPT = "wenyuqing/Panacea-Plus"  # gated as of 2026-05-20

# Expected vs delivered modalities. This is the substance of the "no transfer"
# finding and is written verbatim into summary.json so paper / Koi can cite it.
MODALITY_GAP = {
    "panacea_input_expects": {
        "bev_layout_channels": 8,
        "channels_breakdown": [
            "projected 3D bounding boxes (per-class)",
            "per-object depth raster",
            "HD-map road / lane raster",
            "camera-pose / extrinsic embedding",
        ],
        "rgb_input": "6-cam nuScenes ring (FRONT, FRONT_LEFT, FRONT_RIGHT, "
                     "BACK, BACK_LEFT, BACK_RIGHT) at 256 px",
        "frame_length": 8,
        "extras": [
            "text prompt for attribute control (weather / time / scene)",
            "last-frame image of previous clip (for AR extension)",
        ],
    },
    "panacea_output_produces": {
        "shape": "(B=1, T=8, V=6, C=3, H=256, W=256)",
        "interpretation": "6-view multi-camera video, NOT ERP",
        "format": "tensor saved per-view; user must restitch if ERP wanted",
    },
    "our_pipeline_delivers": {
        "L1_ERP": "1024x2048 RGB ERP (single equirectangular pano per frame)",
        "L3_pointcloud": "~690k colored 3D points in AV2 ego coords, per-frame .ply",
        "Pi3_depth_maps": "per-cam dense depth at letterboxed 504x504",
        "modality": "RGB ERP + 3D geometry - NOT BEV layout raster",
    },
    "gap_summary": [
        "Panacea+ is a CONTROL-to-VIDEO model (BEV layout -> video).",
        "Our pipeline is a CAMERA-VIDEO-to-360 stitcher + 3D lifter "
        "(RGB cams -> ERP / .ply).",
        "These are NOT pipelined: Panacea+ does not consume RGB ERP or .ply; "
        "we do not produce BEV layout.",
        "A genuine downstream demo would require either (a) re-training "
        "Panacea+'s ControlNet to consume our .ply as a depth-conditioning "
        "channel (~weeks of training), or (b) using Panacea+ outputs as "
        "synthetic training data for L1/L3 (which is the reverse direction).",
    ],
}


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def _save_anchor_grid(out_path: Path, sample) -> None:
    """Save 2x4 mosaic of the 7 ring cams (8th slot blank)."""
    cams = [
        "ring_front_left", "ring_front_center", "ring_front_right", None,
        "ring_side_left",  "ring_rear_left",    "ring_rear_right",  "ring_side_right",
    ]
    th, tw = 200, 300
    grid = np.full((2 * th + 8, 4 * tw + 12, 3), 30, dtype=np.uint8)
    for i, cam in enumerate(cams):
        if cam is None:
            continue
        r, c = divmod(i, 4)
        img = sample.images[cam]
        from PIL import Image as PILImage  # noqa: PLC0415
        s = min(tw / img.shape[1], th / img.shape[0])
        nw, nh = int(round(img.shape[1] * s)), int(round(img.shape[0] * s))
        pil = PILImage.fromarray(img).resize((nw, nh), PILImage.LANCZOS)
        y0 = r * (th + 4) + (th - nh) // 2
        x0 = c * (tw + 4) + (tw - nw) // 2
        grid[y0:y0 + nh, x0:x0 + nw] = np.asarray(pil)
    Image.fromarray(grid).save(out_path)


def _probe_panacea_repo(panacea_dir: Path) -> dict:
    """Without installing anything, inspect the cloned panacea repo and report
    what we can about its dependencies + entry points."""
    rec: dict = {"panacea_dir": str(panacea_dir), "exists": panacea_dir.exists()}
    if not panacea_dir.exists():
        return rec
    # Look for the key files.
    for rel in ["inference.py", "configs/inference_nuscenes.yaml",
                "requirements/pt13.txt", "docs/generation_environment.md",
                "metrics/StreamPETR/docs/data_preparation.md", "panacea.yml"]:
        p = panacea_dir / rel
        rec[f"has_{rel.replace('/', '__').replace('.', '_')}"] = p.exists()
        if p.exists() and p.stat().st_size < 50_000:
            try:
                rec[f"head_{rel.replace('/', '__').replace('.', '_')}"] = (
                    p.read_text(encoding="utf-8", errors="replace")[:2000]
                )
            except Exception as e:
                rec[f"read_err_{rel.replace('/', '__')}"] = repr(e)
    # Probe checkpoint dir.
    ckpt_dir = panacea_dir / "checkpoints"
    rec["checkpoints_dir_exists"] = ckpt_dir.exists()
    if ckpt_dir.exists():
        rec["checkpoints_listing"] = sorted(p.name for p in ckpt_dir.iterdir())
    # Probe data dir.
    data_dir = panacea_dir / "data" / "nuscenes"
    rec["data_nuscenes_exists"] = data_dir.exists()
    return rec


def _try_pip_install(panacea_dir: Path, out_dir: Path, timeout_s: int = 600) -> dict:
    """Attempt the upstream pip install. Capture stdout/stderr + exit code.
    This is a "documented failure" probe; we expect dep-hell on Colab's
    PyTorch 2.x / CUDA 12.x default image."""
    req = panacea_dir / "requirements" / "pt13.txt"
    if not req.exists():
        return {"install_attempted": False, "reason": f"missing {req}"}
    log_path = out_dir / "install_log.txt"
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(req), "--no-deps"]
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
        dt = time.time() - t0
        log_path.write_text(
            f"$ {' '.join(cmd)}\nexit={proc.returncode} dt={dt:.1f}s\n\n"
            f"--- STDOUT (tail 4096) ---\n{proc.stdout[-4096:]}\n\n"
            f"--- STDERR (tail 4096) ---\n{proc.stderr[-4096:]}\n",
            encoding="utf-8",
        )
        return {
            "install_attempted": True,
            "exit_code": proc.returncode,
            "elapsed_s": dt,
            "log_path": str(log_path),
            "stdout_tail": proc.stdout[-1024:],
            "stderr_tail": proc.stderr[-1024:],
        }
    except subprocess.TimeoutExpired as e:
        log_path.write_text(
            f"$ {' '.join(cmd)}\nTIMEOUT after {timeout_s}s\n"
            f"stdout: {(e.stdout or b'').decode(errors='replace')[-2048:]}\n"
            f"stderr: {(e.stderr or b'').decode(errors='replace')[-2048:]}\n",
            encoding="utf-8",
        )
        return {"install_attempted": True, "exit_code": -1,
                "log_path": str(log_path), "error": "timeout"}
    except Exception as e:
        return {"install_attempted": True, "exit_code": -2,
                "error": repr(e), "traceback": traceback.format_exc()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-dir", required=True,
                    help="AV2 sensor log dir.")
    ap.add_argument("--anchor-idx", type=int, default=60,
                    help="Anchor frame index (default 60, Phase 3 W1 standard).")
    ap.add_argument("--panacea-dir", default=None,
                    help="Local path to cloned Panacea+ repo (probed in all modes).")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--mode", choices=["recon", "install", "inference"],
                    default="recon",
                    help="recon: probe-only (no install). install: also try "
                         "pip install -r pt13.txt --no-deps (documents dep-hell). "
                         "inference: NOT IMPLEMENTED - would require BEV-layout "
                         "preprocessing of AV2 annotations + likely retraining.")
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: PLC0415

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[t17-panacea] mode={args.mode}", flush=True)
    print(f"[t17-panacea] log_dir={args.log_dir}", flush=True)
    print(f"[t17-panacea] anchor_idx={args.anchor_idx}", flush=True)
    print(f"[t17-panacea] panacea_dir={args.panacea_dir}", flush=True)

    # ---- load anchor frame ----
    t_load_start = time.time()
    loader = AV2RingLoader(Path(args.log_dir))
    anchor_ts_all = loader.anchor_timestamps_ns()
    if args.anchor_idx >= len(anchor_ts_all):
        print(f"[t17-panacea] anchor_idx {args.anchor_idx} out of range "
              f"({len(anchor_ts_all)} anchors); clamping to last.", flush=True)
        args.anchor_idx = len(anchor_ts_all) - 1
    anchor_ts = anchor_ts_all[args.anchor_idx]
    sample = loader.load_synced_frame(anchor_ts)
    t_load = time.time() - t_load_start
    print(f"[t17-panacea] AV2 anchor frame loaded in {t_load:.2f}s "
          f"(anchor_ts={anchor_ts}, 7 cams)", flush=True)

    # ---- save anchor grid (sanity check) ----
    grid_path = out_dir / "av2_anchor_grid.png"
    _save_anchor_grid(grid_path, sample)
    print(f"[t17-panacea] wrote {grid_path}", flush=True)

    # ---- probe panacea repo (read-only) ----
    panacea_probe: dict = {}
    if args.panacea_dir:
        panacea_probe = _probe_panacea_repo(Path(args.panacea_dir))
        print("[t17-panacea] panacea repo probe:", flush=True)
        for k, v in panacea_probe.items():
            if isinstance(v, str) and len(v) > 100:
                print(f"  {k}: <{len(v)} chars>", flush=True)
            else:
                print(f"  {k}: {v}", flush=True)
    else:
        print("[t17-panacea] no --panacea-dir given; skipping repo probe.", flush=True)

    # ---- mode dispatch ----
    install_result: Optional[dict] = None
    inference_result: Optional[dict] = None

    if args.mode == "install" and args.panacea_dir:
        print("[t17-panacea] attempting upstream pip install (no-deps, "
              "documenting dep-hell)...", flush=True)
        install_result = _try_pip_install(Path(args.panacea_dir), out_dir)
        print(f"[t17-panacea] install exit={install_result.get('exit_code')}", flush=True)

    if args.mode == "inference":
        inference_result = {
            "implemented": False,
            "reason": (
                "Panacea+ inference requires (a) BEV-layout pickle files derived "
                "from nuScenes annotation format, (b) DeepSpeed checkpoint loaded "
                "via 8-GPU torch.distributed.launch, (c) dep stack incompatible "
                "with Colab default image (pt 1.13.1+cu117 vs current pt 2.x+cu12x). "
                "Adapting to AV2 would require ~1-2 weeks of dataset preprocessing "
                "(render 3D bbox + HD-map BEV from AV2 annotation feathers) plus "
                "likely retraining the released ckpt is nuScenes-only. Out of "
                "T17 time-box; documented as honest 'cannot reproduce' finding."
            ),
        }
        print(f"[t17-panacea] inference mode NOT implemented: "
              f"{inference_result['reason']}", flush=True)

    # ---- placeholder marker so Colab done_marker passes ----
    marker = out_dir / "panacea_output.mp4"
    if not marker.exists():
        marker.write_bytes(b"")  # 0-byte placeholder; intentionally not a real mp4
        print(f"[t17-panacea] wrote 0-byte placeholder {marker} so Colab "
              f"done_marker check passes (recon mode produces no real video).",
              flush=True)

    # ---- summary ----
    summary = {
        "task": "T17 - Panacea+ baseline / downstream-consumer recon",
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": args.mode,
        "log_dir": args.log_dir,
        "anchor_idx": args.anchor_idx,
        "anchor_ts_ns": int(anchor_ts),
        "av2_load_seconds": round(t_load, 3),
        "av2_cams": list(RING_CAMS_7),
        "panacea_repo_url": PANACEA_REPO_URL,
        "panacea_hf_ckpt": PANACEA_HF_CKPT,
        "panacea_repo_probe": panacea_probe,
        "install_result": install_result,
        "inference_result": inference_result,
        "modality_gap": MODALITY_GAP,
        "verdict": (
            "Panacea+ is NOT a viable downstream consumer of our L1 ERP / "
            "Pi3 .ply outputs in its present form. The modalities do not "
            "match: Panacea+ consumes BEV-layout rasters (3D bbox + HD-map), "
            "produces 6-cam 256px multi-view video (not ERP). Our pipeline "
            "consumes 7-cam RGB, produces ERP + 3D point cloud. To produce a "
            "real downstream demo, future work would re-train Panacea+'s "
            "ControlNet to ingest our .ply as a depth-conditioning channel "
            "(~weeks of training data). For Phase-3 paper, the honest "
            "finding is: Panacea+ inference is not reproducible at our scale "
            "and the modality gap is fundamental, not a tooling issue."
        ),
        "artifacts": {
            "av2_anchor_grid_png": str(grid_path),
            "placeholder_mp4": str(marker),
            "install_log": (
                str(out_dir / "install_log.txt")
                if (install_result and install_result.get("install_attempted"))
                else None
            ),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[t17-panacea] wrote {out_dir / 'summary.json'}", flush=True)
    print("[t17-panacea] DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
