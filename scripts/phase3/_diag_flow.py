"""Diagnose WHY the Surround360 FB-consistency gate rejects ~the whole band.
For each ring seam: render both cams rotation-only, roll seam to centre, DIS flow both ways,
report in-band median |flow|, median FB-error, and FB<thr fractions at 2/4/8/16 px. Also dump the
two slabs + flow-mag + FB-err heatmaps for the worst seam so we can EYEBALL the flow quality."""
from __future__ import annotations
import sys
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "code"))
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")

from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
from waymo2panorama.projection.sphere_projection import render_camera_to_erp
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band
from run_a1_streetview_pipeline import (_make_dis, _fb_consistency, _circular_center_col,
                                        fit_planes_p3, build_plane_convergence)
from run_a0_plane_dibr_probe import load_lidar_feather

BMW = "02a00399-3857-444e-8db3-a8f58489c394"
ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/a1_streetview_pipeline")
erp_hw = (1024, 2048); H, W = erp_hw
BHW = 80


def main():
    loader = AV2RingLoader(ROOT / BMW)
    ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[0])
    pts, _, _ = load_lidar_feather(ROOT / BMW, ts[0], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = fit_planes_p3(pts)
    conv = build_plane_convergence(ground, facades, erp_hw)

    slabs, ws, pslabs = [], [], []
    for cam in RING_CAMS_7:
        cb = frame.calibrations[cam]
        r1, _a, w1 = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                          convergence_distance_m=None)
        rp, _ap, _wp = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                            convergence_distance_m=conv)
        slabs.append(r1); ws.append(w1); pslabs.append(rp)

    dis = _make_dis()
    print(f"{'seam':>8} {'band_px':>8} {'med|fl|':>8} {'medFBerr':>9} "
          f"{'FB<2':>6} {'FB<4':>6} {'FB<8':>6} {'FB<16':>7} {'covOK':>6}")
    worst = None
    for (i, j) in RING_PAIRS:
        overlap = (ws[i] > 1e-6) & (ws[j] > 1e-6)
        if int(overlap.sum()) < 200:
            continue
        cc = _circular_center_col(overlap, W); roll = (W // 2 - cc) if cc is not None else 0
        si = np.roll(slabs[i], roll, 1); sj = np.roll(slabs[j], roll, 1)
        wir = np.roll(ws[i], roll, 1); wjr = np.roll(ws[j], roll, 1)
        band, signed = build_voronoi_seam_band(wir.astype(np.float32), wjr.astype(np.float32),
                                               band_half_width=BHW, threshold=1e-6)
        gi = cv2.cvtColor(np.clip(si, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
        gj = cv2.cvtColor(np.clip(sj, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
        # FIX: mask both grayscales to the OVERLAP before DIS (Surround360 computes flow on the
        # overlapping strip only). Outside the wedge both = 0 → DIS sees black==black → 0 flow,
        # so the huge cross-content flow can't propagate into the band.
        ov = (wir > 1e-6) & (wjr > 1e-6)
        kov = cv2.dilate(ov.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(bool)
        gi = np.where(kov, gi, 0).astype(np.uint8)
        gj = np.where(kov, gj, 0).astype(np.uint8)
        fij = dis.calc(gi, gj, None); fji = dis.calc(gj, gi, None)
        for fl in (fij, fji):
            fl[..., 0] = cv2.GaussianBlur(fl[..., 0], (0, 0), 1.5)
            fl[..., 1] = cv2.GaussianBlur(fl[..., 1], (0, 0), 1.5)
        xx, yy = np.meshgrid(np.arange(W), np.arange(H))
        mx = (xx + fij[..., 0]).astype(np.float32); my = (yy + fij[..., 1]).astype(np.float32)
        bwd = cv2.remap(fji, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        fberr = np.sqrt(((fij + bwd) ** 2).sum(-1))
        fmag = np.sqrt((fij ** 2).sum(-1))
        bm = band & (gi > 0) & (gj > 0)
        n = int(bm.sum())
        if n < 50:
            continue
        f2 = float((fberr[bm] < 2).mean()); f4 = float((fberr[bm] < 4).mean())
        f8 = float((fberr[bm] < 8).mean()); f16 = float((fberr[bm] < 16).mean())
        covok = float(((gi > 0) & (gj > 0))[band].mean())
        print(f"{i}-{j:>6} {n:>8} {np.median(fmag[bm]):>8.2f} {np.median(fberr[bm]):>9.2f} "
              f"{f2:>6.2f} {f4:>6.2f} {f8:>6.2f} {f16:>7.2f} {covok:>6.2f}")
        # also report what cv2 phase-corr / pure |flow| says: how much disparity is there
        if worst is None or np.median(fmag[bm]) > worst[1]:
            worst = ((i, j), float(np.median(fmag[bm])), roll, si, sj, fmag, fberr, band)

    if worst is not None:
        (i, j), _m, roll, si, sj, fmag, fberr, band = worst
        c = W // 2; y0, y1, c0, c1 = 300, 760, c - 230, c + 230
        def hm(a, vmax):
            v = np.clip(a / vmax, 0, 1)[y0:y1, c0:c1]
            return cv2.applyColorMap((v * 255).astype(np.uint8), cv2.COLORMAP_JET)
        sic = cv2.cvtColor(np.clip(si[y0:y1, c0:c1], 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        sjc = cv2.cvtColor(np.clip(sj[y0:y1, c0:c1], 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        bandc = cv2.cvtColor((band[y0:y1, c0:c1].astype(np.uint8) * 255), cv2.COLOR_GRAY2BGR)
        montage = np.hstack([sic, sjc, hm(fmag, 20.0), hm(fberr, 8.0), bandc])
        cv2.imwrite(str(OUT / "FLOWDIAG_worst.jpg"), montage, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print(f"[worst seam {i}-{j}] montage: slab_i | slab_j | |flow|(0-20px JET) | "
              f"FBerr(0-8px JET) | band  -> FLOWDIAG_worst.jpg")


if __name__ == "__main__":
    raise SystemExit(main())
