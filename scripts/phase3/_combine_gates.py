"""★ align(flow-verified) ⊕ LiDAR(depth-verified) UNION de-doubling — the cheap "make it visible" run.

The two source-faithful de-doublers each fire only where THEIR evidence verifies the geometry, else L1:
  • view-interp (Surround360 flow + FB-consistency gate)  → fires on TEXTURED co-visible mid-range
  • LiDAR-depth reproject + cross-view RGB gate            → fires on LiDAR-supported + cross-view-agree
This unions them: start from the flow-verified `view` panorama as the base, then add the LiDAR-gated
de-doubling ONLY where view abstained (so no method overwrites another; each pixel is de-doubled by at
most one VERIFIED method; far field + under-determined residual = byte-exact L1). Pure CPU (cv2+numpy+
scipy). Goal: does unioning the two verified fractions produce a VISIBLE improvement over L1, or is the
verifiable fraction just intrinsically small? Vision-judge L1 vs view vs combine + the lossless crops.

Run (CPU): python _combine_gates.py --uuid <UUID> --tag <name>
Outputs to results/combine_gates/: CG_<tag>_{stack.jpg, <spot>_{L1,view,combine}.png (LOSSLESS)}.
The .png crops are native-res, NO resize/NO jpeg → they settle whether the magenta/green near-car tint
is a real pano defect or just a 2x-NEAREST+JPEG figure artifact (hard_select cannot blend colour)."""
from __future__ import annotations
import sys, time, argparse
from pathlib import Path
import cv2
import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8")

import run_a1_streetview_pipeline as a1  # reuses every tested function (view_interp_panorama, etc.)

ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/combine_gates"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048
FAR = 1000.0
# native-res crop windows per anchor: (centre_col_u, row0, row1, half_width_cols)
SPOTS = {
    "bmw": {"bmw": (1760, 330, 640, 240), "graycar": (720, 320, 600, 240), "wall": (1150, 330, 620, 240)},
}
DEFAULT_SPOTS = {"a": (720, 320, 600, 240), "b": (1400, 320, 600, 240), "c": (300, 320, 600, 240)}


def lab(im, t):
    b = np.zeros((30, im.shape[1], 3), np.uint8); cv2.putText(b, t, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return np.vstack([b, im])


def rw(im, w=1500):
    return cv2.resize(np.clip(im, 0, 255).astype(np.uint8), (w, round(im.shape[0] * w / im.shape[1])), interpolation=cv2.INTER_AREA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=a1.BMW_UUID)
    ap.add_argument("--tag", default="bmw")
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--band-hw", type=int, default=80)
    ap.add_argument("--max-disp", type=float, default=60.0)
    ap.add_argument("--fb-thresh", type=float, default=2.0)
    ap.add_argument("--struct-thresh", type=float, default=15.0)
    ap.add_argument("--gate", type=float, default=16.0)       # LiDAR cross-view RGB residual gate (0..255)
    ap.add_argument("--knn-max", type=float, default=22.0)    # max px dist to a LiDAR pt to trust the dense depth
    a = ap.parse_args()
    t0 = time.time()
    erp_hw = (H, W)

    # ---- scene (mirror run_a1 --mode view --prealign ground --obj-route --with-photo) ----
    loader = a1.AV2RingLoader(ROOT / a.uuid); ts = loader.anchor_timestamps_ns()
    frame = loader.load_synced_frame(ts[a.anchor])
    pts, _l, dms = a1.load_lidar_feather(ROOT / a.uuid, ts[a.anchor], max_delta_ms=75.0)
    pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, facades = a1.fit_planes_p3(pts)
    obj_mask = a1.off_plane_object_erp(pts, ground, facades, erp_hw)
    conv = a1.build_plane_convergence(ground, facades, erp_hw)
    conv_pre = a1.build_plane_convergence(ground, [], erp_hw) if ground else None  # GROUND-only pre-align
    print(f"[{a.tag}] lidar={pts.shape[0]} dt={dms:.0f}ms ground={'y' if ground else 'N'} "
          f"facades={len(facades)} obj={obj_mask.mean()*100:.2f}%", flush=True)

    cams = {cam: frame.calibrations[cam] for cam in a1.RING_CAMS_7}
    l1_slabs, l1_w, pl_slabs = [], [], []
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]
        r1, _a1, w1 = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=None)
        l1_slabs.append(r1); l1_w.append(w1)
        if conv_pre is not None:
            rp, _ap, _wp = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=conv_pre)
            pl_slabs.append(rp)
        else:
            pl_slabs.append(r1)
    L1 = a1.hard_select(l1_slabs, l1_w)
    w_base, n_routed = a1.object_coherent_weights(l1_w, obj_mask)
    print(f"[{a.tag}] obj-routed {n_routed} compact near-objects", flush=True)

    # ---- layer 1: flow-verified view-interp (with E1.5 photo base), ground-prealigned content ----
    content = []
    for ps, ls in zip(pl_slabs, l1_slabs):
        m = (ps.astype(np.float32).sum(2) > 0)[..., None]
        content.append(np.where(m, ps.astype(np.float32), ls.astype(np.float32)))
    view_base = a1.blend_seam_confined(l1_slabs, w_base, band_half_width=a.band_hw, lowfreq_cutoff=5)["out"]
    view_res, view_touched, n_pairs = a1.view_interp_panorama(
        content, l1_slabs, l1_w, obj_mask, view_base, band_half_width=a.band_hw,
        max_disp=a.max_disp, fb_thresh=a.fb_thresh, protect_obj=False, struct_thresh=a.struct_thresh)
    print(f"[{a.tag}] view: pairs={n_pairs} flow-fired={100*(view_touched>0).mean():.2f}%", flush=True)

    # ---- layer 2: LiDAR-depth reproject + cross-view gate, composited where view ABSTAINED ----
    bandU = np.zeros((H, W), bool)
    for (i, j) in a1.RING_PAIRS:
        ov = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(ov.sum()) < 200:
            continue
        band, _s = a1.build_voronoi_seam_band(l1_w[i].astype(np.float32), l1_w[j].astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        bandU |= band & ov
    # densify sparse LiDAR range across the band (kNN), FAR (=L1) outside support
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    th = np.arctan2(y, x); ph = np.arctan2(z, np.hypot(x, y))
    ul = (((np.pi - th) / (2 * np.pi) * W - 0.5) % W); vl = ((np.pi / 2 - ph) / np.pi * H - 0.5)
    rng = np.linalg.norm(pts, axis=1)
    ui = np.round(ul).astype(int) % W; vi = np.round(vl).astype(int)
    ok = (vi >= 0) & (vi < H) & (rng > 1.0) & (rng < 80.0)
    ui, vi, rng = ui[ok], vi[ok], rng[ok]
    rangemap = np.full((H, W), np.inf, np.float32)
    for k in range(len(ui)):
        if rng[k] < rangemap[vi[k], ui[k]]:
            rangemap[vi[k], ui[k]] = rng[k]
    known = np.isfinite(rangemap)
    ky, kx = np.where(known)
    tree = cKDTree(np.stack([ky, kx], 1))
    by, bx = np.where(bandU)
    dist, nn = tree.query(np.stack([by, bx], 1), k=1)
    dense_depth = np.full((H, W), FAR, np.float64)
    near = dist <= a.knn_max
    dense_depth[by[near], bx[near]] = rangemap[ky[nn[near]], kx[nn[near]]]

    reproj, ralpha = [], []
    for cam in a1.RING_CAMS_7:
        cb = cams[cam]
        s, al, _w = a1.render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=dense_depth)
        reproj.append(s.astype(np.float32)); ralpha.append(al)

    out = view_res.astype(np.float32).copy()
    lidar_fired = np.zeros((H, W), np.float32)
    for (i, j) in a1.RING_PAIRS:
        ov = (l1_w[i] > 1e-6) & (l1_w[j] > 1e-6)
        if int(ov.sum()) < 200:
            continue
        band, signed = a1.build_voronoi_seam_band(l1_w[i].astype(np.float32), l1_w[j].astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        bm = band & ov & ralpha[i] & ralpha[j]
        resid = np.abs(reproj[i] - reproj[j]).mean(2)
        agree = bm & (resid < a.gate) & (view_touched <= 0.0)   # only where view ABSTAINED → clean union
        dd = np.clip(np.abs(signed) / a.band_hw, 0, 1)
        ramp = np.where(band, 0.5 * (1 + np.cos(np.pi * dd)), 0).astype(np.float32)
        al = cv2.GaussianBlur((agree.astype(np.float32) * ramp), (0, 0), 2.0)[..., None]
        avg = 0.5 * (reproj[i] + reproj[j])
        out = out * (1 - al) + avg * al
        lidar_fired = np.maximum(lidar_fired, agree.astype(np.float32) * ramp)
    combine = np.clip(out, 0, 255).astype(np.uint8)
    union = (view_touched > 0) | (lidar_fired > 0)
    print(f"[{a.tag}] LiDAR-added {100*(lidar_fired>0).mean():.2f}%  →  UNION de-doubled {100*union.mean():.2f}% "
          f"(flow {100*(view_touched>0).mean():.2f} + lidar {100*(lidar_fired>0).mean():.2f}); runtime {time.time()-t0:.0f}s", flush=True)

    # ---- review stack: L1 | view | combine ----
    stack = np.vstack([lab(rw(L1), "L1 hard_select"),
                       lab(rw(view_res), "view (flow-verified)"),
                       lab(rw(combine), "combine = view ⊕ LiDAR-depth (union, verified)")])
    cv2.imwrite(str(OUT / f"CG_{a.tag}_stack.jpg"), cv2.cvtColor(stack, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])

    # ---- LOSSLESS native-res crops (settle the tint; honest zoom, NO nearest-upscale / NO jpeg) ----
    spots = SPOTS.get(a.tag, DEFAULT_SPOTS)
    for name, (u, v0, v1, hw) in spots.items():
        roll = W // 2 - u; cc = W // 2
        for img, lbl in [(L1, "L1"), (view_res, "view"), (combine, "combine")]:
            crop = np.roll(img, roll, 1)[v0:v1, cc - hw:cc + hw]
            cv2.imwrite(str(OUT / f"CG_{a.tag}_{name}_{lbl}.png"), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    print(f"[saved] {OUT}/CG_{a.tag}_stack.jpg + lossless {{spot}}_{{L1,view,combine}}.png", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
