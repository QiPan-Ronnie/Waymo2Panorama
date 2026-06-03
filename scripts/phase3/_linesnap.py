"""DB-20260603-17 — line-structure-aware SNAP (the wavy lane line, done directly).

WHY the line kinks: DIS flow ABSTAINS on the textureless asphalt AROUND the lane line (FB-inconsistent),
so the line never gets aligned and jumps at the single-source cut. FIX: trust the flow ONLY at
high-gradient STRUCTURE (the lane line / curb edge itself, where flow IS reliable), then PROPAGATE that
displacement smoothly into the surrounding asphalt (normalized convolution) -> warp the losing slab so
its line meets the neighbour's continuously at the cut; the featureless asphalt deforms invisibly.
Confined to the below-horizon GROUND band, single-source (hard-select), composited onto the deliverable.

Run (CPU): python _linesnap.py --tag bmw   -> results/seamroute/LS_<tag>_{road,curb,graycar,pano}.*
KILL: line still kinks, OR a NEW wiggle/smear appears, OR no visible change vs current.
"""
from __future__ import annotations
import sys, time, argparse
from pathlib import Path
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")
import run_a1_streetview_pipeline as a1

ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/seamroute"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048
SPOTS = {"road": (300, 350, 720, 200), "curb": (1085, 540, 730, 210), "graycar": (720, 330, 620, 220)}


def lab(im, t, h=26):
    b = np.zeros((h, im.shape[1], 3), np.uint8); cv2.putText(b, t, (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    return np.vstack([b, im])


def snap_pair(si, sj, overlap, gband, dis, grad_thr, fb_thresh, prop_sigma, max_disp):
    """Warp sj to AGREE with si, displacement driven by si's high-gradient structure, propagated."""
    gi = cv2.cvtColor(np.clip(si, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gj = cv2.cvtColor(np.clip(sj, 0, 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    kov = cv2.dilate(overlap.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(bool)
    gim = np.where(kov, gi, 0).astype(np.uint8); gjm = np.where(kov, gj, 0).astype(np.uint8)
    f_ij = dis.calc(gim, gjm, None); f_ji = dis.calc(gjm, gim, None)
    np.clip(f_ij, -max_disp, max_disp, out=f_ij); np.clip(f_ji, -max_disp, max_disp, out=f_ji)
    cons = a1._fb_consistency(f_ij, f_ji, fb_thresh)
    gx = cv2.Sobel(gi.astype(np.float32), cv2.CV_32F, 1, 0, 3); gy = cv2.Sobel(gi.astype(np.float32), cv2.CV_32F, 0, 1, 3)
    gmag = np.hypot(gx, gy) / 255.0
    anchor = (overlap & cons & gband & (gmag > grad_thr)).astype(np.float32)
    den = cv2.GaussianBlur(anchor, (0, 0), prop_sigma) + 1e-6                       # normalized-convolution propagation
    fx = cv2.GaussianBlur(f_ij[..., 0] * anchor, (0, 0), prop_sigma) / den
    fy = cv2.GaussianBlur(f_ij[..., 1] * anchor, (0, 0), prop_sigma) / den
    xx, yy = np.meshgrid(np.arange(W), np.arange(H))
    mapx = (xx + fx).astype(np.float32); mapy = (yy + fy).astype(np.float32)
    sj_snap = cv2.remap(sj.astype(np.float32), mapx, mapy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return sj_snap, float(anchor.mean()) * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=a1.BMW_UUID); ap.add_argument("--tag", default="bmw")
    ap.add_argument("--grad-thr", type=float, default=0.10); ap.add_argument("--prop-sigma", type=float, default=25.0)
    ap.add_argument("--band-hw", type=int, default=90); ap.add_argument("--max-disp", type=float, default=60.0)
    ap.add_argument("--fb-thresh", type=float, default=2.5)
    ap.add_argument("--base", default="SR_bmw_final_1024x2048.png", help="deliverable to composite onto + compare")
    a = ap.parse_args(); t0 = time.time(); erp_hw = (H, W)

    loader = a1.AV2RingLoader(ROOT / a.uuid); ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[0])
    pts, _l, _d = a1.load_lidar_feather(ROOT / a.uuid, ts[0], max_delta_ms=75.0); pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, _f = a1.fit_planes_p3(pts); n = np.asarray(ground["n"], float); d = float(ground["d"])
    l1, l1w = [], []
    for cam in a1.RING_CAMS_7:
        cb = frame.calibrations[cam]; r1, _a, w1 = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=None)
        l1.append(r1); l1w.append(w1)

    # below-horizon GROUND band mask (road/sidewalk/curb), off tall objects
    hh = pts @ n - d; rr = np.linalg.norm(pts, axis=1); tp = pts[(hh > 0.5) & (rr < 45.0)]
    tall = np.zeros((H, W), np.uint8)
    if len(tp):
        tx, ty, tz = tp[:, 0], tp[:, 1], tp[:, 2]; tth = np.arctan2(ty, tx); tph = np.arctan2(tz, np.hypot(tx, ty))
        tu = ((np.pi - tth) / (2 * np.pi) * W).astype(int) % W; tv = ((np.pi / 2 - tph) / np.pi * H).astype(int).clip(0, H - 1)
        tall[tv, tu] = 1
    tall = cv2.dilate(tall, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))).astype(bool)
    rows = np.arange(H)[:, None] * np.ones((1, W))
    gband_glob = (rows > H * 0.50) & (~tall)

    dis = a1._make_dis(); snapped = [s.copy().astype(np.float32) for s in l1]; fired = 0.0
    for (i, j) in a1.RING_PAIRS:
        ov = (l1w[i] > 1e-6) & (l1w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        cc = a1._circular_center_col(ov, W); roll = (W // 2 - cc) if cc is not None else 0
        si = np.roll(l1[i], roll, 1).astype(np.float32); sj = np.roll(l1[j], roll, 1).astype(np.float32)
        ovr = np.roll(ov, roll, 1); gb = np.roll(gband_glob, roll, 1)
        band, signed = a1.build_voronoi_seam_band(np.roll(l1w[i], roll, 1).astype(np.float32), np.roll(l1w[j], roll, 1).astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        sj_snap, fr = snap_pair(si, sj, ovr, gb & band, dis, a.grad_thr, a.fb_thresh, a.prop_sigma, a.max_disp)
        # feather the snap into slab j only in the ground band
        dd = np.clip(np.abs(signed) / a.band_hw, 0, 1); feather = np.where(band & gb, 0.5 * (1 + np.cos(np.pi * dd)), 0).astype(np.float32)
        sj_new = sj * (1 - feather[..., None]) + sj_snap * feather[..., None]
        snapped[j] = np.roll(sj_new, -roll, 1); fired += fr
    ls = a1.hard_select([np.clip(s, 0, 255).astype(np.uint8) for s in snapped], l1w)

    base = cv2.cvtColor(cv2.imread(str(OUT / a.base)), cv2.COLOR_BGR2RGB)
    gm = cv2.GaussianBlur((gband_glob & (np.stack(l1w, 0).max(0) > 0)).astype(np.float32), (0, 0), 2.0)[..., None]
    out = (base.astype(np.float32) * (1 - gm) + ls.astype(np.float32) * gm).astype(np.uint8)
    print(f"[{a.tag}] linesnap anchor-fired avg={fired/len(a1.RING_PAIRS):.2f}% rt={time.time()-t0:.0f}s", flush=True)

    cv2.imwrite(str(OUT / f"LS_{a.tag}_pano.jpg"), cv2.cvtColor(out, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
    for name, (u0, v0, vh, hw) in SPOTS.items():
        roll = W // 2 - u0; cc = W // 2
        cur = cv2.cvtColor(np.roll(base, roll, 1)[v0:v0 + vh, cc - hw:cc + hw], cv2.COLOR_RGB2BGR)
        new = cv2.cvtColor(np.roll(out, roll, 1)[v0:v0 + vh, cc - hw:cc + hw], cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(OUT / f"LS_{a.tag}_{name}.png"), np.vstack([lab(cur, "current (deliverable)"), lab(new, "LINE-SNAP (DB-17)")]))
    print(f"[saved] {OUT}/LS_{a.tag}_{{pano.jpg, {list(SPOTS)}}}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
