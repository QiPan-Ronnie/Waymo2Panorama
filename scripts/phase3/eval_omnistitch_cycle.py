"""
Phase 3 T2 — cycle-consistency PSNR for the OmniStitch ERP output.

The L1 cycle-PSNR (12.34 dB) and IPM-hybrid (+0.20 dB ground) numbers in
`notes/phase3_multi_anchor_report.md` were computed by *holding out* a cam
and reconstructing it from the other 6. The OmniStitch baseline produces a
*single* ERP composite (a forward-only product), so the cleanest comparable
metric is **back-projection PSNR**: for each cam, project the ERP back onto
the cam's image plane (azimuth/elevation -> cam ray -> pinhole pixel) and
PSNR against the original cam image. This is a "round-trip-consistency"
metric — does the ERP, when re-rendered from the cam's POV, look like the
cam saw?

We also compute the same back-projection PSNR for the **L1 baseline ERP**
(re-rendered here from the same AV2 anchor frame) so the comparison is
apples-to-apples: same cams, same anchor, same evaluation, only the ERP
differs.

CLI:
  --log-dir              AV2 sensor log (for original cam images + calibration)
  --anchor-idx           anchor frame index (must match the OmniStitch run)
  --omnistitch-erp       path to omnistitch_erp.png produced by run_omnistitch_baseline.py
  --output-dir           where to write metrics + back-projection PNGs

Outputs:
  omnistitch_backproj_<cam>.png  per-cam OmniStitch ERP-back-projected image
  l1_backproj_<cam>.png          per-cam L1 ERP-back-projected image
  cycle_omnistitch.json          {per_cam: [...], mean: {...}, verdict}
  cycle_omnistitch_bars.png      bar chart L1 vs OmniStitch per cam
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


DEFAULT_W2P_CODE_REL = "../../code"


def _wire_imports(w2p_code: Path) -> None:
    if not w2p_code.exists():
        raise FileNotFoundError(f"required path missing: {w2p_code}")
    sys.path.insert(0, str(w2p_code))


def psnr_masked(a: np.ndarray, b: np.ndarray, mask: Optional[np.ndarray] = None) -> float:
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    if mask is None:
        mse = np.mean((a - b) ** 2)
    else:
        if mask.sum() == 0:
            return float("nan")
        mse = ((a - b) ** 2)[mask].mean()
    if mse <= 1e-12:
        return float("inf")
    return 20.0 * np.log10(255.0) - 10.0 * np.log10(mse)


def backproject_erp_to_cam(
    erp_rgb: np.ndarray, K: np.ndarray, T_ego_cam: np.ndarray,
    cam_hw: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """For each cam pixel: cam-ray -> ego-ray (rotation only) -> ERP (theta, phi) -> sample.

    Mirrors the ERP convention from `code/waymo2panorama/projection/sphere_projection.py`:
        theta = pi - (u_erp + 0.5)/W * 2pi
        phi   = pi/2 - (v_erp + 0.5)/H * pi
        d_ego = (cos(phi)*cos(theta), cos(phi)*sin(theta), sin(phi))

    Returns:
        cam_rgb    (H_cam, W_cam, 3) float32 in [0, 255]
        cam_mask   (H_cam, W_cam) bool, True where the ERP had finite sampling support.
    """
    import cv2  # noqa: PLC0415

    H_cam, W_cam = cam_hw
    H_erp, W_erp = erp_rgb.shape[:2]

    # cam pixel grid -> cam-frame ray
    uu, vv = np.meshgrid(np.arange(W_cam), np.arange(H_cam))
    pix = np.stack([uu + 0.5, vv + 0.5, np.ones_like(uu)], axis=-1).astype(np.float64)
    K_inv = np.linalg.inv(K)
    d_cam = pix @ K_inv.T                              # (H_cam, W_cam, 3)
    d_cam = d_cam / np.linalg.norm(d_cam, axis=-1, keepdims=True)

    # rotate to ego frame (rotation only — L1 convention)
    R_ego_cam = T_ego_cam[:3, :3]
    d_ego = d_cam @ R_ego_cam.T                        # (H_cam, W_cam, 3)
    # numerical safety
    d_ego = d_ego / np.maximum(np.linalg.norm(d_ego, axis=-1, keepdims=True), 1e-12)

    # ego -> (theta, phi)
    x, y, z = d_ego[..., 0], d_ego[..., 1], d_ego[..., 2]
    theta = np.arctan2(y, x)                            # in [-pi, pi]
    phi = np.arcsin(np.clip(z, -1.0, 1.0))              # in [-pi/2, pi/2]

    # (theta, phi) -> ERP (u_erp, v_erp). Inverse of:
    #     theta = pi - (u + 0.5)/W * 2pi
    #     phi   = pi/2 - (v + 0.5)/H * pi
    u_erp = (np.pi - theta) / (2.0 * np.pi) * W_erp - 0.5
    v_erp = (np.pi / 2.0 - phi) / np.pi * H_erp - 0.5
    # azimuth wrap
    u_erp = np.mod(u_erp, W_erp)

    map_x = u_erp.astype(np.float32)
    map_y = v_erp.astype(np.float32)
    sampled = cv2.remap(
        erp_rgb.astype(np.float32), map_x, map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_WRAP,  # wrap horizontally
    )
    # We don't have an alpha channel on the input ERP PNG (PIL saves as RGB), so
    # use "non-black" as a coarse mask of "ERP had content here". Acceptable since
    # both L1 and OmniStitch ERPs are fully populated in the cam fields-of-view.
    cam_mask = sampled.sum(axis=-1) > 1.0
    return sampled, cam_mask


def render_l1_erp(
    sample, erp_hw: tuple[int, int] = (1024, 2048),
) -> np.ndarray:
    """Re-render the L1 baseline ERP from the same AV2 anchor (so we have a
    same-anchor, same-render-pipeline reference)."""
    from waymo2panorama.projection.sphere_projection import render_camera_to_erp  # noqa: PLC0415
    from waymo2panorama.blending.multiband import multiband_blend  # noqa: PLC0415
    from waymo2panorama.data_io.av2_loader import RING_CAMS_7  # noqa: PLC0415

    slabs: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    cal = sample.calibrations
    for c in RING_CAMS_7:
        rgb, _alpha, w = render_camera_to_erp(
            image=sample.images[c],
            K=cal[c].K,
            T_ego_cam=cal[c].T_ego_cam,
            erp_hw=erp_hw,
        )
        slabs.append(rgb)
        weights.append(w)
    erp = multiband_blend(slabs, weights, num_bands=5)
    return np.clip(erp, 0, 255).astype(np.uint8)


def _make_bars_png(
    cams: list[str], psnr_l1: list[float], psnr_omni: list[float], out_path: Path,
) -> None:
    cell_h = 30
    name_w = 240
    bar_w = 360
    finite_l1 = [v for v in psnr_l1 if np.isfinite(v)]
    finite_o = [v for v in psnr_omni if np.isfinite(v)]
    max_psnr = max(max(finite_l1, default=0), max(finite_o, default=0), 25.0)
    H = (len(cams) + 2) * cell_h + 40
    W = name_w + bar_w * 2 + 40
    img = Image.new("RGB", (W, H), color=(20, 20, 24))
    from PIL import ImageDraw, ImageFont  # noqa: PLC0415
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 16)
        big = ImageFont.truetype("DejaVuSans.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
        big = font
    draw.text((10, 10), "ERP back-projection PSNR per cam: L1 (white) vs OmniStitch (cyan)",
              fill=(255, 255, 255), font=big)
    for i, cam in enumerate(cams):
        y = 50 + i * cell_h
        draw.text((10, y + 4), cam, fill=(220, 220, 220), font=font)
        for v, color, y_off in [
            (psnr_l1[i], (240, 240, 240), 0),
            (psnr_omni[i], (90, 220, 240), cell_h // 2 + 2),
        ]:
            length = int(bar_w * v / max_psnr) if np.isfinite(v) else 0
            draw.rectangle([(name_w, y + y_off), (name_w + length, y + y_off + cell_h // 2 - 2)],
                           fill=color)
            label = f"{v:.2f}" if np.isfinite(v) else "nan"
            draw.text((name_w + length + 6, y + y_off), label, fill=color, font=font)
    img.save(out_path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--anchor-idx", type=int, default=60)
    ap.add_argument("--omnistitch-erp", required=True,
                    help="Path to omnistitch_erp.png produced by run_omnistitch_baseline.py.")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--erp-h", type=int, default=1024)
    ap.add_argument("--erp-w", type=int, default=2048)
    ap.add_argument("--cam-eval-h", type=int, default=512,
                    help="Downsample cam GT + back-projection to this height before PSNR "
                         "(both methods use the same downsample, so the comparison stays fair).")
    ap.add_argument("--save-backproj", action="store_true", default=True)
    ap.add_argument("--w2p-code", default=None)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    w2p_code = Path(args.w2p_code) if args.w2p_code else (here / DEFAULT_W2P_CODE_REL).resolve()
    _wire_imports(w2p_code)

    from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7  # noqa: PLC0415

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[t2-cycle] loading AV2 anchor {args.anchor_idx} from {args.log_dir}", flush=True)
    loader = AV2RingLoader(Path(args.log_dir))
    anchor_ts = loader.anchor_timestamps_ns()[args.anchor_idx]
    sample = loader.load_synced_frame(anchor_ts)

    # ---- load OmniStitch ERP and re-render L1 ERP from the same anchor ----
    print(f"[t2-cycle] loading OmniStitch ERP {args.omnistitch_erp}", flush=True)
    erp_omni = np.asarray(Image.open(args.omnistitch_erp).convert("RGB"))
    if erp_omni.shape[:2] != (args.erp_h, args.erp_w):
        from PIL import Image as PILImage  # noqa: PLC0415
        erp_omni = np.asarray(
            PILImage.fromarray(erp_omni).resize((args.erp_w, args.erp_h), PILImage.LANCZOS)
        )

    print("[t2-cycle] re-rendering L1 baseline ERP from same anchor", flush=True)
    t0 = time.time()
    erp_l1 = render_l1_erp(sample, erp_hw=(args.erp_h, args.erp_w))
    print(f"[t2-cycle] L1 ERP rendered in {time.time() - t0:.2f}s", flush=True)
    Image.fromarray(erp_l1).save(out_dir / "l1_erp_reference.png")

    # ---- back-project per cam, compute PSNR ----
    rows: list[dict] = []
    psnr_l1_list: list[float] = []
    psnr_omni_list: list[float] = []
    print(f"{'cam':22s}  {'cov_L1':>7s}  {'cov_OMNI':>9s}  {'PSNR_L1':>8s}  {'PSNR_OMNI':>10s}  ΔPSNR", flush=True)
    print("-" * 90, flush=True)

    for cam in RING_CAMS_7:
        cal = sample.calibrations[cam]
        gt = sample.images[cam]

        # Optional downsample so we can run on CPU in reasonable time
        if args.cam_eval_h and args.cam_eval_h < gt.shape[0]:
            from PIL import Image as PILImage  # noqa: PLC0415
            scale = args.cam_eval_h / gt.shape[0]
            new_h = args.cam_eval_h
            new_w = int(round(gt.shape[1] * scale))
            gt_eval = np.asarray(PILImage.fromarray(gt).resize((new_w, new_h), PILImage.LANCZOS))
            K_eval = cal.K.copy()
            K_eval[0, :] *= scale
            K_eval[1, :] *= scale
        else:
            gt_eval = gt
            K_eval = cal.K
            new_h, new_w = gt.shape[:2]

        l1_bp, l1_mask = backproject_erp_to_cam(erp_l1, K_eval, cal.T_ego_cam, (new_h, new_w))
        om_bp, om_mask = backproject_erp_to_cam(erp_omni, K_eval, cal.T_ego_cam, (new_h, new_w))

        l1_bp_u8 = np.clip(l1_bp, 0, 255).astype(np.uint8)
        om_bp_u8 = np.clip(om_bp, 0, 255).astype(np.uint8)
        intersect = l1_mask & om_mask

        psnr_l1 = psnr_masked(gt_eval, l1_bp_u8, mask=intersect)
        psnr_omni = psnr_masked(gt_eval, om_bp_u8, mask=intersect)
        cov_l1 = float(l1_mask.mean())
        cov_omni = float(om_mask.mean())

        rows.append({
            "cam": cam,
            "coverage_L1": cov_l1, "coverage_OMNI": cov_omni,
            "PSNR_L1": psnr_l1, "PSNR_OMNI": psnr_omni,
            "PSNR_delta_OMNI_minus_L1": psnr_omni - psnr_l1,
        })
        psnr_l1_list.append(psnr_l1)
        psnr_omni_list.append(psnr_omni)
        print(f"{cam:22s}  {cov_l1:7.1%}  {cov_omni:9.1%}  {psnr_l1:8.2f}  {psnr_omni:10.2f}  {psnr_omni - psnr_l1:+5.2f}", flush=True)

        if args.save_backproj:
            gap = 4
            panel = np.full((new_h, 3 * new_w + 2 * gap, 3), 32, dtype=np.uint8)
            panel[:, :new_w] = gt_eval
            panel[:, new_w + gap:2 * new_w + gap] = l1_bp_u8
            panel[:, 2 * new_w + 2 * gap:] = om_bp_u8
            Image.fromarray(panel).save(out_dir / f"backproj_{cam}.png")

    mean_l1 = float(np.nanmean(psnr_l1_list))
    mean_omni = float(np.nanmean(psnr_omni_list))
    delta = mean_omni - mean_l1
    print("-" * 90, flush=True)
    print(f"{'MEAN':22s}                           "
          f"{mean_l1:8.2f}  {mean_omni:10.2f}  {delta:+5.2f}", flush=True)

    # ---- bar chart ----
    _make_bars_png(list(RING_CAMS_7), psnr_l1_list, psnr_omni_list,
                   out_dir / "cycle_omnistitch_bars.png")

    # ---- summary JSON ----
    summary = {
        "log_dir": args.log_dir,
        "anchor_idx": args.anchor_idx,
        "anchor_ts_ns": int(anchor_ts),
        "omnistitch_erp": str(args.omnistitch_erp),
        "metric": ("PSNR of per-cam ERP-back-projection vs original cam image. "
                   "L1 ERP is re-rendered from the same anchor frame and same blending "
                   "pipeline as the OmniStitch composite; the only difference between "
                   "the two ERPs is the overlap-wedge content (OmniStitch's pair-stitch "
                   "output)."),
        "eval_resolution_h": args.cam_eval_h,
        "per_cam": rows,
        "mean": {
            "PSNR_L1": mean_l1,
            "PSNR_OMNI": mean_omni,
            "PSNR_delta_OMNI_minus_L1": delta,
        },
        "verdict_hint": (
            "OMNI - L1 > +0.5 dB: OmniStitch helps the overlap wedges. "
            "|delta| <= 0.5 dB: OmniStitch is statistically tied with sphere projection in overlap. "
            "delta < -0.5 dB: OmniStitch hurts (domain shift dominates the trained prior)."
        ),
        "comparison_context": {
            "Phase3_W1_L1_cycle_PSNR_10anchor_mean": 12.34,
            "Phase3_W1_L1_cycle_PSNR_10anchor_std": 1.31,
            "Phase3_T14_IPM_hybrid_ground_delta_3anchor_mean": 0.20,
            "Phase3_T14_IPM_hybrid_full_delta_3anchor_mean": 0.04,
            "NOTE_metric_differs_from_W1": (
                "Phase 3 W1 cycle-PSNR is a hold-one-out reconstruction (cam_i reconstructed "
                "from the other 6). This eval uses ERP-back-projection (all 7 cams used in the "
                "ERP, then re-rendered into each cam). The numbers therefore have different "
                "absolute magnitudes — what's meaningful is the L1-vs-OmniStitch DELTA at the "
                "SAME anchor under the SAME metric, which is what this file reports."
            ),
        },
    }
    (out_dir / "cycle_omnistitch.json").write_text(json.dumps(summary, indent=2))
    print(f"[t2-cycle] wrote {out_dir / 'cycle_omnistitch.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
