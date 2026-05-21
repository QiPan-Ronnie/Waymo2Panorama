"""
Phase 3 route 10 / 新-A — Cylindrical (L2) baseline stitching.

Re-runs the 7-ring-cam -> ERP-shape stitch using a *cylindrical* projection
instead of the L1 sphere. 5-band Laplacian blending pipeline is reused
verbatim (see `code/waymo2panorama/blending/multiband.py`).

Two input modes (auto-detected by `--input-mode` or path heuristics):

  1) `--input-mode av2`  + `--log-dir <AV2_log>`
        Native loader path (CameraCalibration from intrinsics.feather +
        egovehicle_SE3_sensor.feather). Reads full-resolution images, then
        the projection samples them onto the 1024x2048 canvas.

  2) `--input-mode pi3-cache` + `--pi3-dir <pi3_cache/anchor_NNN>`
        Reads pre-cached 504x504 letterboxed images + corresponding K matrices
        (output of `run_pi3_multi_anchor.py`). Useful when the AV2 log isn't
        available locally but the Pi3 cache is. Result is at lower spatial
        resolution (because the source imagery was already downsampled) but
        the L1-vs-L2 comparison is apples-to-apples since the *same* cache
        feeds the L1 sphere baseline.

Outputs (under `--output-dir`):
    cylindrical_l2.png            — final 1024x2048 ERP (cylindrical L2)
    sphere_l1.png                 — same 7 cams via the sphere projection
    compare_l1_vs_l2.png          — side-by-side L1 | L2 (vertical bar in between)
    summary.json                  — params, runtime, file paths
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"

RING_CAMS_7 = (
    "ring_front_center",
    "ring_front_left",
    "ring_side_left",
    "ring_rear_left",
    "ring_rear_right",
    "ring_side_right",
    "ring_front_right",
)


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


# ------------------------- input loaders -------------------------------------


def _load_from_pi3_cache(pi3_dir: Path, cam: str) -> dict:
    image = np.asarray(Image.open(pi3_dir / f"image_{cam}.png").convert("RGB"))
    K = np.load(pi3_dir / f"av2_K_letterboxed_{cam}.npy")
    T_ego_cam = np.load(pi3_dir / f"av2_T_ego_cam_{cam}.npy")
    return {"image": image, "K": K, "T_ego_cam": T_ego_cam}


def _load_from_av2(loader, frame, cam: str) -> dict:
    img = frame.images[cam]
    calib = frame.calibrations[cam]
    return {"image": img, "K": calib.K, "T_ego_cam": calib.T_ego_cam}


# ------------------------- main ---------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--input-mode", choices=["av2", "pi3-cache", "auto"], default="auto",
                    help="auto: pick pi3-cache if --pi3-dir given else av2.")
    ap.add_argument("--log-dir", type=Path, default=None,
                    help="(av2 mode) AV2 sensor log root.")
    ap.add_argument("--pi3-dir", type=Path, default=None,
                    help="(pi3-cache mode) Pi3 anchor_NNN/ dir with image_*.png + av2_K_*.npy.")
    ap.add_argument("--anchor", type=int, default=60,
                    help="(av2 mode) which anchor index to render. Also used to name the output dir.")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--num-bands", type=int, default=5)
    ap.add_argument("--v-max", type=float, default=1.0,
                    help="Cylinder vertical FOV: h in [-v_max, +v_max] fills the canvas.")
    ap.add_argument("--no-wrap", action="store_true",
                    help="Disable horizontal wrap padding in multiband (debug).")
    ap.add_argument("--ego-masks-dir", type=Path, default=None)
    ap.add_argument("--also-make-compare-png", type=Path, default=None,
                    help="If set, also write the L1-vs-L2 compare PNG to this absolute path "
                         "(in addition to <output-dir>/compare_l1_vs_l2.png).")
    ap.add_argument("--w2p-code", default=None,
                    help="Path to the `code/` dir holding the waymo2panorama package.")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    # Imports happen AFTER sys.path wiring.
    from waymo2panorama.blending.multiband import multiband_blend
    from waymo2panorama.projection.cylinder import render_camera_to_cylinder
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp as render_sphere

    # Resolve input mode
    mode = args.input_mode
    if mode == "auto":
        if args.pi3_dir is not None:
            mode = "pi3-cache"
        elif args.log_dir is not None:
            mode = "av2"
        else:
            ap.error("provide either --pi3-dir or --log-dir (or set --input-mode explicitly)")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    erp_hw = (args.erp_h, args.erp_w)
    print(f"[cyl-baseline] mode={mode}, erp_hw={erp_hw}, v_max={args.v_max}", flush=True)

    # ---- gather per-cam data ----
    per_cam_data: dict[str, dict] = {}
    anchor_idx = args.anchor
    anchor_ts_ns: int | None = None

    if mode == "pi3-cache":
        if args.pi3_dir is None:
            ap.error("--pi3-dir required for pi3-cache mode")
        pi3_dir = Path(args.pi3_dir)
        if not pi3_dir.exists():
            raise FileNotFoundError(pi3_dir)
        # Cache directory name carries anchor info
        try:
            anchor_idx = int(pi3_dir.name.split("_")[1])
        except (IndexError, ValueError):
            pass
        summary_path = pi3_dir / "summary.json"
        if summary_path.exists():
            s = json.loads(summary_path.read_text())
            anchor_ts_ns = s.get("anchor_timestamp_ns")
        cams = list(RING_CAMS_7)
        for cam in cams:
            per_cam_data[cam] = _load_from_pi3_cache(pi3_dir, cam)
    else:
        if args.log_dir is None:
            ap.error("--log-dir required for av2 mode")
        from waymo2panorama.data_io.av2_loader import AV2RingLoader  # noqa: E402
        loader = AV2RingLoader(args.log_dir)
        ts_all = loader.anchor_timestamps_ns()
        if not (0 <= anchor_idx < len(ts_all)):
            raise IndexError(f"--anchor {anchor_idx} out of range [0, {len(ts_all)})")
        anchor_ts_ns = ts_all[anchor_idx]
        frame = loader.load_synced_frame(anchor_ts_ns)
        cams = list(RING_CAMS_7)
        for cam in cams:
            per_cam_data[cam] = _load_from_av2(loader, frame, cam)

    # ---- ego masks (optional, only meaningful in av2 mode) ----
    ego_masks: dict[str, np.ndarray] = {}
    if args.ego_masks_dir is not None:
        import cv2  # local import to keep startup fast
        for cam in cams:
            p = args.ego_masks_dir / f"{cam}.png"
            if p.exists():
                m = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                ego_masks[cam] = (m > 127).astype(np.uint8)

    # ---- project each cam onto BOTH cylinder and sphere ----
    cyl_slabs: list[np.ndarray] = []
    cyl_weights: list[np.ndarray] = []
    sph_slabs: list[np.ndarray] = []
    sph_weights: list[np.ndarray] = []

    t_proj0 = time.time()
    per_cam_log: list[dict] = []
    for cam in cams:
        d = per_cam_data[cam]
        mask = ego_masks.get(cam)

        cyl_rgb, cyl_alpha, cyl_w = render_camera_to_cylinder(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, ego_mask=mask, v_max=args.v_max,
        )
        sph_rgb, sph_alpha, sph_w = render_sphere(
            image=d["image"], K=d["K"], T_ego_cam=d["T_ego_cam"],
            erp_hw=erp_hw, ego_mask=mask,
        )

        cyl_slabs.append(cyl_rgb)
        cyl_weights.append(cyl_w)
        sph_slabs.append(sph_rgb)
        sph_weights.append(sph_w)

        per_cam_log.append({
            "cam": cam,
            "src_hw": list(d["image"].shape[:2]),
            "cyl_alpha_px": int(cyl_alpha.sum()),
            "sph_alpha_px": int(sph_alpha.sum()),
        })
        print(f"[cyl-baseline]   {cam}: cyl_alpha={int(cyl_alpha.sum())} "
              f"sph_alpha={int(sph_alpha.sum())}", flush=True)
    t_proj_s = time.time() - t_proj0

    # ---- blend each into one ERP ----
    t_blend0 = time.time()
    erp_cyl = multiband_blend(cyl_slabs, cyl_weights, num_bands=args.num_bands, wrap=not args.no_wrap)
    erp_sph = multiband_blend(sph_slabs, sph_weights, num_bands=args.num_bands, wrap=not args.no_wrap)
    t_blend_s = time.time() - t_blend0

    # ---- save outputs ----
    Image.fromarray(erp_cyl).save(out_dir / "cylindrical_l2.png")
    Image.fromarray(erp_sph).save(out_dir / "sphere_l1.png")

    # Stacked compare panel: L1 sphere ON TOP, L2 cylinder BELOW (each is 1024x2048),
    # with a labeled separator strip. Vertical stack reads more naturally than
    # side-by-side for ERP-shape images.
    label_h = 36
    sep_h = 4
    sep = np.full((sep_h, args.erp_w, 3), 32, dtype=np.uint8)
    cap_top = np.full((label_h, args.erp_w, 3), 16, dtype=np.uint8)
    cap_mid = np.full((label_h, args.erp_w, 3), 16, dtype=np.uint8)
    panel = np.concatenate([cap_top, erp_sph, sep, cap_mid, erp_cyl], axis=0)
    try:
        from PIL import ImageDraw, ImageFont
        panel_img = Image.fromarray(panel)
        draw = ImageDraw.Draw(panel_img)
        try:
            font = ImageFont.truetype("arial.ttf", 24)
        except OSError:
            font = ImageFont.load_default()
        # L1 caption sits in the top strip
        draw.text((12, 6), "L1: Sphere projection (baseline)", fill=(255, 255, 0), font=font)
        # L2 caption sits in the middle strip (just above the L2 image)
        mid_y = label_h + args.erp_h + sep_h + 6
        draw.text((12, mid_y), "L2: Cylindrical projection (route 10 / 新-A)", fill=(0, 255, 255), font=font)
        panel_arr = np.asarray(panel_img)
    except Exception:
        panel_arr = panel
    Image.fromarray(panel_arr).save(out_dir / "compare_l1_vs_l2.png")

    if args.also_make_compare_png is not None:
        args.also_make_compare_png.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(panel_arr).save(args.also_make_compare_png)
        print(f"[cyl-baseline] wrote compare PNG -> {args.also_make_compare_png}", flush=True)

    summary = {
        "route": "10 / 新-A",
        "mode": mode,
        "anchor_idx": anchor_idx,
        "anchor_timestamp_ns": anchor_ts_ns,
        "erp_hw": list(erp_hw),
        "params": {
            "v_max": args.v_max,
            "num_bands": args.num_bands,
            "wrap": not args.no_wrap,
        },
        "per_cam": per_cam_log,
        "runtime_s": {
            "projection": round(t_proj_s, 2),
            "blend": round(t_blend_s, 2),
        },
        "outputs": {
            "cylindrical_l2": str((out_dir / "cylindrical_l2.png").resolve()),
            "sphere_l1": str((out_dir / "sphere_l1.png").resolve()),
            "compare_l1_vs_l2": str((out_dir / "compare_l1_vs_l2.png").resolve()),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[cyl-baseline] done -> {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
