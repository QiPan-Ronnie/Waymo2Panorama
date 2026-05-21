"""
Phase 3 T2 — OmniStitch (ACM MM 2024) baseline adapter for AV2 7-cam input.

Reference: https://github.com/tngh5004/Omnistitch
Paper: OmniStitch: Depth-aware Stitching Framework for Omnidirectional Vision
       with Multiple Cameras, ACM MM 2024.

# What OmniStitch is

OmniStitch is a **pairwise** image-stitching network (two overlapping camera
views in -> one stitched intermediate frame out). The trained checkpoint operates
at GV360's native ~480-wide image scale on a fixed CARLA-simulated 4-camera
layout (LD / RD / LU / RU "down/up"-tilted views with strong overlap, ~120 deg
per cam, vehicle-roof rig).

The repo's `core/pipeline.py::Pipeline.inference(img0, img1)` is the single
production entry. There is no multi-cam "full SRM" wrapper in the public repo
(the README states the SRM module is closed-source). Therefore "running
OmniStitch on AV2 7-cam" requires us to:

  1. pick adjacent ring-cam pairs;
  2. resample each pair into a comparable side-by-side overlap layout;
  3. invoke `Pipeline.inference` per pair;
  4. composite the 7 pairwise outputs into an ERP slab via the same sphere
     projection we use for L1 (we do NOT re-derive a multi-cam blender).

# Critical caveats (see notes/t2_omnistitch_report.md for full discussion)

  * **No public pretrained weights.** Repo expects `./train-log-/Omnistitch/
    trained-models/model.pkl` — never committed, no HF model, no GitHub release.
    Authors have not responded to issue requests as of the recon date.
  * **Training-set mismatch.** GV360 = synthetic CARLA, 4-cam, fixed wide-FOV,
    strong overlap. AV2 = real-world, 7-ring-cam, narrow-FOV pinhole, mostly
    pairwise-disjoint with thin (~5-15 deg) overlap wedges.
  * **Even if weights surface, "no-transfer" is the dominant prior.** Image
    statistics + parallax magnitude + overlap-width are all out of training
    distribution.

This script is written so the OmniStitch path can be wired up later (whenever
weights surface or whenever we self-train on GV360+AV2). At time of writing,
it executes in two modes:

  --mode adapter-only  : run the pair-extraction + dummy passthrough end-to-end
                         to verify the AV2 -> OmniStitch input pipeline is
                         sound. Output: pair_<a>_<b>.png debug grids (no model
                         inference). Used to demonstrate correctness of the
                         adapter independent of model availability.
  --mode inference     : also load OmniStitch model + run pairwise inference,
                         then composite pairwise stitches into an ERP slab.
                         REQUIRES `--omnistitch-dir` to contain a trained
                         `model.pkl`.

# Input / output layout

Input:
  --log-dir         path to an AV2 sensor log dir (contains sensors/cameras/<cam>/*.jpg
                    and calibration/*.feather). Same as Phase 1/2 av2_loader.
  --anchor-idx      anchor frame index (typically 60 per the Phase 3 W1 sweep).

Output (under --output-dir):
  pair_<L>_<R>.png            side-by-side debug grid (always)
  stitch_<L>_<R>.png          per-pair OmniStitch output  (inference mode only)
  omnistitch_erp.png          composited 7-cam ERP        (inference mode only)
  summary.json                pair list + per-pair stats + verdict
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"

# Adjacent pairs around the ring. front_center sits at the seam; we pair it
# both with its left and right neighbours so the front overlap region is
# covered by an actual OmniStitch stitch (not a passthrough).
RING_PAIRS: list[tuple[str, str]] = [
    ("ring_front_left",  "ring_front_center"),
    ("ring_front_center", "ring_front_right"),
    ("ring_front_right", "ring_side_right"),
    ("ring_side_right",  "ring_rear_right"),
    ("ring_rear_right",  "ring_rear_left"),  # crosses behind; large parallax
    ("ring_rear_left",   "ring_side_left"),
    ("ring_side_left",   "ring_front_left"),
]


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def _letterbox(img: np.ndarray, target_hw: tuple[int, int]) -> np.ndarray:
    """Letterbox (preserve aspect) into target size. Returns uint8 RGB."""
    from PIL import Image as PILImage  # noqa: PLC0415
    th, tw = target_hw
    h, w = img.shape[:2]
    s = min(tw / w, th / h)
    nw, nh = int(round(w * s)), int(round(h * s))
    pil = PILImage.fromarray(img).resize((nw, nh), PILImage.LANCZOS)
    out = np.zeros((th, tw, 3), dtype=np.uint8)
    y0 = (th - nh) // 2
    x0 = (tw - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = np.asarray(pil)
    return out


def _save_pair_debug(out_dir: Path, cam_l: str, cam_r: str,
                     img_l: np.ndarray, img_r: np.ndarray) -> None:
    """Side-by-side debug grid."""
    h = max(img_l.shape[0], img_r.shape[0])
    w = img_l.shape[1] + 4 + img_r.shape[1]
    grid = np.full((h, w, 3), 30, dtype=np.uint8)
    grid[:img_l.shape[0], :img_l.shape[1]] = img_l
    grid[:img_r.shape[0], img_l.shape[1] + 4:img_l.shape[1] + 4 + img_r.shape[1]] = img_r
    Image.fromarray(grid).save(out_dir / f"pair_{cam_l}__{cam_r}.png")


def _try_load_omnistitch(omni_dir: Path, device: str):
    """Try to instantiate OmniStitch Pipeline. Returns (ppl, err)."""
    import os
    if not omni_dir.exists():
        return None, f"omnistitch-dir does not exist: {omni_dir}"

    # candidate weight locations
    candidates = [
        omni_dir / "train-log-" / "Omnistitch" / "trained-models" / "model.pkl",
        omni_dir / "train-log-" / "Omnistitch" / "trained-models" / "best-model.pkl",
        omni_dir / "model.pkl",
        omni_dir / "best-model.pkl",
    ]
    model_file = next((p for p in candidates if p.exists()), None)
    if model_file is None:
        return None, (
            "No model.pkl found in any expected OmniStitch location: "
            + ", ".join(str(p) for p in candidates)
            + ". The public OmniStitch repo does NOT ship pretrained weights."
        )

    sys.path.insert(0, str(omni_dir))
    try:
        from core.pipeline import Pipeline  # type: ignore  # noqa: PLC0415
    except Exception as e:
        return None, f"import core.pipeline failed: {e!r}"

    cfg = dict(
        load_pretrain=True,
        model_name="omnistitch",
        model_file=str(model_file),
        pyr_level=4,
        nr_lvl_skipped=1,
    )
    try:
        ppl = Pipeline(cfg)
        ppl.eval()
        return ppl, None
    except Exception as e:
        return None, f"Pipeline init failed: {e!r}\n{traceback.format_exc()}"


def _omnistitch_stitch_pair(ppl, img_l: np.ndarray, img_r: np.ndarray,
                            target_hw: tuple[int, int] = (480, 480)) -> np.ndarray:
    """Run a single OmniStitch inference on a pair of letterboxed views.

    OmniStitch expects equal-shape HxW RGB inputs, normalized to [0, 1].
    Returns uint8 RGB output of same size.
    """
    import math
    import torch  # noqa: PLC0415
    import torch.nn.functional as F  # noqa: PLC0415

    device = next(ppl.model.parameters()).device

    a = _letterbox(img_l, target_hw)
    b = _letterbox(img_r, target_hw)
    t0 = torch.from_numpy(a.transpose(2, 0, 1)).float().to(device).unsqueeze(0) / 255.0
    t1 = torch.from_numpy(b.transpose(2, 0, 1)).float().to(device).unsqueeze(0) / 255.0

    pyr_level = math.ceil(math.log2((target_hw[1] + 32) / 480) + 3)
    pyr_level = max(pyr_level, 3)
    nr_skip = pyr_level - 3
    divisor = 2 ** (pyr_level - 1 + 2)
    h, w = target_hw
    if (h % divisor) or (w % divisor):
        ph = ((h - 1) // divisor + 1) * divisor
        pw = ((w - 1) // divisor + 1) * divisor
        t0 = F.pad(t0, (0, pw - w, 0, ph - h), "constant", 0.5)
        t1 = F.pad(t1, (0, pw - w, 0, ph - h), "constant", 0.5)

    with torch.no_grad():
        out, _ = ppl.inference(t0, t1, pyr_level=pyr_level, nr_lvl_skipped=nr_skip)
    out = out[:, :, :h, :w]
    arr = (out[0].clamp(0, 1) * 255).byte().cpu().numpy().transpose(1, 2, 0)
    return arr


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-dir", required=True,
                    help="AV2 sensor log dir.")
    ap.add_argument("--anchor-idx", type=int, default=60,
                    help="Anchor frame index (default 60, the Phase 3 W1 standard).")
    ap.add_argument("--omnistitch-dir", default=None,
                    help="Local path to the cloned OmniStitch repo (only needed "
                         "in --mode inference).")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--mode", choices=["adapter-only", "inference"],
                    default="adapter-only",
                    help="adapter-only: verify pair extraction + AV2->OmniStitch "
                         "input pipeline without needing model weights. "
                         "inference: also run model + composite ERP.")
    ap.add_argument("--target-h", type=int, default=480)
    ap.add_argument("--target-w", type=int, default=480)
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: PLC0415

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[t2-omnistitch] mode={args.mode}, log_dir={args.log_dir}, anchor_idx={args.anchor_idx}", flush=True)

    # ---- load anchor frame ----
    t_load_start = time.time()
    loader = AV2RingLoader(Path(args.log_dir))
    anchor_ts = loader.anchor_timestamps_ns()[args.anchor_idx]
    sample = loader.load_synced_frame(anchor_ts)
    t_load = time.time() - t_load_start
    print(f"[t2-omnistitch] AV2 anchor frame loaded in {t_load:.2f}s "
          f"(anchor_ts={anchor_ts})", flush=True)
    for c in RING_CAMS_7:
        img = sample.images[c]
        print(f"  cam={c:22s}  shape={img.shape}", flush=True)

    # ---- inference mode: try to load OmniStitch model ----
    ppl = None
    omni_err: Optional[str] = None
    if args.mode == "inference":
        if args.omnistitch_dir is None:
            omni_err = "--omnistitch-dir is required for --mode inference"
            print(f"[t2-omnistitch] ERROR: {omni_err}", flush=True)
        else:
            ppl, omni_err = _try_load_omnistitch(Path(args.omnistitch_dir), "cuda")
            if ppl is None:
                print(f"[t2-omnistitch] OmniStitch model load FAILED: {omni_err}", flush=True)
            else:
                print("[t2-omnistitch] OmniStitch Pipeline loaded.", flush=True)

    # ---- pairwise extraction + (optional) inference ----
    pair_records: list[dict] = []
    stitched_pairs: dict[tuple[str, str], np.ndarray] = {}
    for cam_l, cam_r in RING_PAIRS:
        img_l = sample.images[cam_l]
        img_r = sample.images[cam_r]
        a = _letterbox(img_l, (args.target_h, args.target_w))
        b = _letterbox(img_r, (args.target_h, args.target_w))
        _save_pair_debug(out_dir, cam_l, cam_r, a, b)
        rec = {"cam_l": cam_l, "cam_r": cam_r, "letterbox_hw": [args.target_h, args.target_w]}

        if ppl is not None:
            try:
                t0 = time.time()
                stitched = _omnistitch_stitch_pair(ppl, img_l, img_r,
                                                   target_hw=(args.target_h, args.target_w))
                dt = time.time() - t0
                stitched_pairs[(cam_l, cam_r)] = stitched
                Image.fromarray(stitched).save(out_dir / f"stitch_{cam_l}__{cam_r}.png")
                rec.update({"inference_ms": dt * 1000.0, "stitched_shape": list(stitched.shape)})
                print(f"[t2-omnistitch] stitched {cam_l} <-> {cam_r}  in {dt * 1000:.0f}ms", flush=True)
            except Exception as e:
                rec["inference_error"] = repr(e)
                print(f"[t2-omnistitch] stitch FAILED for {cam_l} <-> {cam_r}: {e!r}", flush=True)
                traceback.print_exc()
        pair_records.append(rec)

    # ---- composite to ERP (only if at least 4/7 pairs succeeded) ----
    erp_path = out_dir / "omnistitch_erp.png"
    erp_succeeded = False
    erp_err: Optional[str] = None
    if len(stitched_pairs) >= 4:
        try:
            # Composite strategy: project each OmniStitch pair output onto ERP
            # as a virtual "middle" camera whose pose is the SLERP/average of
            # (cam_l, cam_r). This is the closest standalone interpretation of
            # what the paper's (closed-source) SRM wrapper does: each
            # OmniStitch pair output is a new "synthesised view" that we then
            # sphere-project onto the ERP and blend in alongside the 7
            # originals. The new view inherits an "average" K (focal length and
            # principal point scaled by the letterbox to match the output
            # image), so its projection is geometrically sane.
            #
            # We boost virtual-cam blend weight by 1.5x so the OmniStitch
            # contribution dominates wherever it has coverage (it was
            # explicitly trained to stitch parallax-shifted overlaps), while
            # the 7 original cams remain in the blend for non-overlap regions.
            from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: PLC0415
            from waymo2panorama.blending.multiband import multiband_blend  # noqa: PLC0415
            from scipy.spatial.transform import Rotation, Slerp  # noqa: PLC0415

            erp_h, erp_w = 1024, 2048
            slabs: list[np.ndarray] = []
            weights: list[np.ndarray] = []
            cal = sample.calibrations

            # 1) 7 original AV2 ring cams (L1 baseline contribution)
            for c in RING_CAMS_7:
                rgb, _alpha, w = render_camera_to_erp(
                    image=sample.images[c],
                    K=cal[c].K,
                    T_ego_cam=cal[c].T_ego_cam,
                    erp_hw=(erp_h, erp_w),
                )
                slabs.append(rgb)
                weights.append(w)

            # 2) 7 OmniStitch virtual middle cams
            virtual_cams_added = 0
            for (cam_l, cam_r), stitched in stitched_pairs.items():
                T_l = cal[cam_l].T_ego_cam
                T_r = cal[cam_r].T_ego_cam

                # SLERP rotation between the two cam orientations
                R_lr = Rotation.from_matrix(np.stack([T_l[:3, :3], T_r[:3, :3]]))
                slerp = Slerp([0.0, 1.0], R_lr)
                R_mid = slerp(0.5).as_matrix()
                t_mid = 0.5 * (T_l[:3, 3] + T_r[:3, 3])
                T_mid = np.eye(4)
                T_mid[:3, :3] = R_mid
                T_mid[:3, 3] = t_mid

                # K for the stitched image: the original cams had K_l, K_r;
                # the stitched output is at letterbox resolution. Approximate
                # K as the average of (K_l, K_r) rescaled to the letterbox size.
                H_src_l = sample.images[cam_l].shape[0]
                W_src_l = sample.images[cam_l].shape[1]
                scale_l = min(args.target_w / W_src_l, args.target_h / H_src_l)
                H_src_r = sample.images[cam_r].shape[0]
                W_src_r = sample.images[cam_r].shape[1]
                scale_r = min(args.target_w / W_src_r, args.target_h / H_src_r)

                # Rebuild K for the letterboxed canvas (with the pad offset)
                def _letterbox_K(K_src: np.ndarray, src_hw: tuple[int, int],
                                 dst_hw: tuple[int, int]) -> np.ndarray:
                    K_dst = K_src.copy()
                    s = min(dst_hw[1] / src_hw[1], dst_hw[0] / src_hw[0])
                    K_dst[0, 0] *= s; K_dst[1, 1] *= s
                    K_dst[0, 2] = K_dst[0, 2] * s + (dst_hw[1] - src_hw[1] * s) / 2
                    K_dst[1, 2] = K_dst[1, 2] * s + (dst_hw[0] - src_hw[0] * s) / 2
                    return K_dst

                K_l_lb = _letterbox_K(cal[cam_l].K, (H_src_l, W_src_l),
                                      (args.target_h, args.target_w))
                K_r_lb = _letterbox_K(cal[cam_r].K, (H_src_r, W_src_r),
                                      (args.target_h, args.target_w))
                K_mid = 0.5 * (K_l_lb + K_r_lb)

                try:
                    rgb_v, _alpha_v, w_v = render_camera_to_erp(
                        image=stitched, K=K_mid, T_ego_cam=T_mid,
                        erp_hw=(erp_h, erp_w),
                    )
                    slabs.append(rgb_v)
                    weights.append(w_v * 1.5)  # boost OmniStitch contribution
                    virtual_cams_added += 1
                except Exception as e:
                    print(f"[t2-omnistitch] virtual-cam render FAILED for "
                          f"{cam_l}<->{cam_r}: {e!r}", flush=True)

            print(f"[t2-omnistitch] composite: 7 original + {virtual_cams_added} "
                  f"OmniStitch virtual middle cams", flush=True)
            erp = multiband_blend(slabs, weights, num_bands=5)
            Image.fromarray(np.clip(erp, 0, 255).astype(np.uint8)).save(erp_path)

            # Also save the pure L1 baseline (first 7 slabs only) for A/B diff
            erp_l1_only = multiband_blend(slabs[:7], weights[:7], num_bands=5)
            Image.fromarray(np.clip(erp_l1_only, 0, 255).astype(np.uint8)).save(
                out_dir / "l1_baseline_erp.png")

            erp_succeeded = True
            print(f"[t2-omnistitch] wrote {erp_path}", flush=True)
        except Exception as e:
            erp_err = repr(e)
            print(f"[t2-omnistitch] ERP composite FAILED: {e!r}", flush=True)
            traceback.print_exc()
    else:
        erp_err = f"only {len(stitched_pairs)}/{len(RING_PAIRS)} pairs succeeded; need >=4 to composite"

    # ---- summary ----
    summary = {
        "mode": args.mode,
        "log_dir": args.log_dir,
        "anchor_idx": args.anchor_idx,
        "anchor_ts_ns": int(anchor_ts),
        "target_hw": [args.target_h, args.target_w],
        "ring_pairs": [list(p) for p in RING_PAIRS],
        "pairs": pair_records,
        "omnistitch_model_loaded": ppl is not None,
        "omnistitch_load_error": omni_err,
        "erp_succeeded": erp_succeeded,
        "erp_path": str(erp_path) if erp_succeeded else None,
        "erp_error": erp_err,
        "notes": [
            "OmniStitch is a pairwise stitcher; multi-cam composite uses sphere ",
            "projection (L1) of the original AV2 frames + per-pair stitched ",
            "overlay only over the overlap wedges. The OmniStitch model only ",
            "operates on each (cam_l, cam_r) pair independently.",
            "If `omnistitch_model_loaded == false`, the most likely cause is ",
            "missing pretrained weights — the public repo does not ship them.",
        ],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[t2-omnistitch] wrote {out_dir / 'summary.json'}", flush=True)

    if args.mode == "inference" and not erp_succeeded:
        # Non-zero exit so the orchestrator (Colab job) marks the run as failed,
        # but the summary.json is still on disk for offline diagnosis.
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
