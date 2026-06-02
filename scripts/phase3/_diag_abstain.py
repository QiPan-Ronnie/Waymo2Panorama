"""Per-pixel ABSTAIN-REASON map for the user's two circled issues:
 (1) gray sedan still has frame parallax (left),  (2) one ground seam didn't connect (right).
For every seam band, classify each pixel: FIRED (green) | abstain-FB-inconsistent (red) |
abstain-coverage (blue) | not-in-band. Overlay on L1 + dump zoom crops so we can SEE whether the
ground seam abstained (and why) or was never put in a band (= a code/geometry bug)."""
from __future__ import annotations
import sys, argparse
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
from waymo2panorama.blending.hard_hdr_of import RING_PAIRS, hard_select
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band
from run_a1_streetview_pipeline import (_make_dis, _circular_center_col,
                                        surround360_view_interp, fit_planes_p3, build_plane_convergence)
from run_a0_plane_dibr_probe import load_lidar_feather

BMW = "02a00399-3857-444e-8db3-a8f58489c394"
ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/a1_streetview_pipeline")
erp_hw = (1024, 2048); H, W = erp_hw
BHW = 80


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prealign", choices=["none", "ground", "plane"], default="none")
    ap.add_argument("--struct", type=float, default=None)
    a = ap.parse_args()
    struct_t = a.struct if a.struct is not None else (8.0 if a.prealign != "none" else 0.0)
    loader = AV2RingLoader(ROOT / BMW)
    ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[0])
    pts, _, _ = load_lidar_feather(ROOT / BMW, ts[0], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = fit_planes_p3(pts); conv = build_plane_convergence(ground, facades, erp_hw)
    if a.prealign == "ground":
        conv_pre = build_plane_convergence(ground, [], erp_hw) if ground else None
    elif a.prealign == "plane":
        conv_pre = conv
    else:
        conv_pre = None
    slabs, ws, content = [], [], []
    for cam in RING_CAMS_7:
        cb = frame.calibrations[cam]
        r1, _a, w1 = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                          convergence_distance_m=None)
        slabs.append(r1); ws.append(w1)
        if conv_pre is not None:
            rp, _b, _c = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw,
                                              convergence_distance_m=conv_pre)
            m = (rp.astype(np.float32).sum(2) > 0)[..., None]
            content.append(np.where(m, rp.astype(np.float32), r1.astype(np.float32)))
        else:
            content.append(r1)
    L1 = hard_select(slabs, ws)
    print(f"[prealign={a.prealign} struct={struct_t}]")
    dis = _make_dis()

    # status: 0 none, 1 fired, 2 FB-abstain, 3 coverage-abstain  (priority: fired wins)
    status = np.zeros((H, W), np.uint8)
    fmag_band = np.zeros((H, W), np.float32)
    for (i, j) in RING_PAIRS:
        overlap = (ws[i] > 1e-6) & (ws[j] > 1e-6)
        if int(overlap.sum()) < 200:
            continue
        cc = _circular_center_col(overlap, W); roll = (W // 2 - cc) if cc is not None else 0
        si = np.roll(content[i], roll, 1); sj = np.roll(content[j], roll, 1)
        wir = np.roll(ws[i], roll, 1); wjr = np.roll(ws[j], roll, 1)
        band, _ = build_voronoi_seam_band(wir.astype(np.float32), wjr.astype(np.float32),
                                          band_half_width=BHW, threshold=1e-6)
        if int(band.sum()) < 100:
            continue
        _novel, reliable, dbg = surround360_view_interp(si, sj, wir, wjr, dis,
                                                        max_disp=60.0, fb_thresh=2.0,
                                                        struct_thresh=struct_t, return_debug=True)
        st = np.zeros((H, W), np.uint8)
        b = band
        st[b & reliable] = 1
        st[b & ~reliable & dbg["covered"] & ~dbg["cons"]] = 2   # FB-inconsistent (parallax/textureless)
        st[b & ~reliable & ~dbg["covered"]] = 3                  # coverage-fail
        # roll back
        st = np.roll(st, -roll, 1)
        fm = np.roll(np.where(b, dbg["fmag"], 0).astype(np.float32), -roll, 1)
        take = st > 0
        status[take] = np.where(status[take] == 1, 1, st[take])   # fired wins
        fmag_band = np.maximum(fmag_band, fm)

    # overlay
    ov = L1.copy()
    col = {1: (0, 220, 0), 2: (230, 0, 0), 3: (0, 80, 255)}  # RGB: green/red/blue
    for k, c in col.items():
        m = status == k
        ov[m] = (0.45 * ov[m] + 0.55 * np.array(c)).astype(np.uint8)
    rw = lambda im, w=1700: cv2.resize(im, (w, round(im.shape[0] * w / im.shape[1])), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT / f"ABSTAIN_{a.prealign}.jpg"), cv2.cvtColor(rw(ov), cv2.COLOR_RGB2BGR),
                [cv2.IMWRITE_JPEG_QUALITY, 92])
    # per-seam fired/abstain breakdown + near(conv<15m) vs far split in band
    nb = status > 0
    near = conv < 15.0
    print("band px:", int(nb.sum()),
          "| fired %.1f%%" % (100 * (status == 1).sum() / max(nb.sum(), 1)),
          "| FB-abstain %.1f%%" % (100 * (status == 2).sum() / max(nb.sum(), 1)),
          "| cov-abstain %.1f%%" % (100 * (status == 3).sum() / max(nb.sum(), 1)))
    for lab, msk in [("NEAR(<15m)", nb & near), ("FAR(>=15m)", nb & ~near)]:
        n = int(msk.sum())
        if n:
            print(f"  {lab}: band {n}  fired {100*(status[msk]==1).mean():.1f}%  "
                  f"FB-abstain {100*(status[msk]==2).mean():.1f}%  cov {100*(status[msk]==3).mean():.1f}%  "
                  f"med|flow| {np.median(fmag_band[msk & (fmag_band>0)]) if (msk&(fmag_band>0)).any() else 0:.1f}px")
    print("[saved] ABSTAIN_overlay.jpg (green=fired, red=FB-abstain, blue=coverage-abstain)")


if __name__ == "__main__":
    raise SystemExit(main())
