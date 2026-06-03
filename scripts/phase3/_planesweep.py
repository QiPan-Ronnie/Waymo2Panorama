"""Plane-sweep MVS in the seam band (the last untested reach; codex + depth-over agent both pointed
here). For each adjacent camera pair, sweep depth planes: at each depth render BOTH cams to ERP via
the N1 single-ego-centre reprojection (render_camera_to_erp convergence=d), measure cross-view
PHOTO-CONSISTENCY |slab_i - slab_j|; pick the depth that AGREES per pixel -> that is the true surface
depth -> the agreeing colour is the DE-DOUBLED ego-centre view. Where the two never agree (occlusion)
or agree at all depths (textureless) -> low confidence -> fall back to L1. This is depth-from-real-
cross-view-evidence + faithful reproject-to-true (not selection, not mixing, not sparse-LiDAR, not a
learned prior). Vision-judge: does it de-double the mid-range building / the cars cleanly?"""
from __future__ import annotations
import sys, time
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

BMW = "02a00399-3857-444e-8db3-a8f58489c394"
ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/killtest"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048
DEPTHS = np.geomspace(2.5, 60.0, 24).astype(np.float64)   # 24 planes, 2.5–60 m


def lab(im, t):
    b = np.zeros((24, im.shape[1], 3), np.uint8); cv2.putText(b, t, (5, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    return np.vstack([b, im])


def main():
    t0 = time.time()
    loader = AV2RingLoader(ROOT / BMW); ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[0])
    cams = {cam: frame.calibrations[cam] for cam in RING_CAMS_7}
    l1s, l1w = [], []
    for cam in RING_CAMS_7:
        cb = cams[cam]; r, _a, wt = render_camera_to_erp(frame.images[cam], cb.K, cb.T_ego_cam, erp_hw=(H, W), convergence_distance_m=None)
        l1s.append(r); l1w.append(wt)
    L1 = hard_select(l1s, l1w)
    out = L1.astype(np.float32).copy()
    conf_map = np.zeros((H, W), np.float32)

    # plane-sweep per ring pair, confined to the seam band
    for (i, j) in RING_PAIRS:
        ov = (l1w[i] > 1e-6) & (l1w[j] > 1e-6)
        if int(ov.sum()) < 200:
            continue
        band, signed = build_voronoi_seam_band(l1w[i].astype(np.float32), l1w[j].astype(np.float32), band_half_width=90, threshold=1e-6)
        bm = band & ov
        if int(bm.sum()) < 200:
            continue
        ci, cj = cams[RING_CAMS_7[i]], cams[RING_CAMS_7[j]]
        best_cost = np.full((H, W), 1e9, np.float32); best_col = np.zeros((H, W, 3), np.float32); second_cost = np.full((H, W), 1e9, np.float32)
        for d in DEPTHS:
            si, ai, _ = render_camera_to_erp(frame.images[RING_CAMS_7[i]], ci.K, ci.T_ego_cam, erp_hw=(H, W), convergence_distance_m=float(d))
            sj, aj, _ = render_camera_to_erp(frame.images[RING_CAMS_7[j]], cj.K, cj.T_ego_cam, erp_hw=(H, W), convergence_distance_m=float(d))
            both = ai & aj & bm
            cost = np.where(both, np.abs(si - sj).mean(2), 1e9).astype(np.float32)
            # 3x3 box-smooth the cost (patch consistency, less noise) within band
            cost_s = cv2.blur(cost, (5, 5))
            upd = cost_s < best_cost
            second_cost = np.where(upd, best_cost, np.minimum(second_cost, cost_s))
            best_col = np.where(upd[..., None], 0.5 * (si + sj), best_col)
            best_cost = np.where(upd, cost_s, best_cost)
        # confidence: low best_cost (agree) AND a clear minimum (second_cost - best_cost margin = not flat/textureless)
        agree = best_cost < 32.0                        # LOOSENED (was 18) — fire on more of the band
        distinct = (second_cost - best_cost) > 1.5      # LOOSENED (was 4) — accept shallower dips
        conf = (bm & agree & distinct).astype(np.float32)
        # feather inside band by cos ramp so the edit fades at the band edge
        dd = np.clip(np.abs(signed) / 90.0, 0, 1); ramp = np.where(band, 0.5 * (1 + np.cos(np.pi * dd)), 0).astype(np.float32)
        a = (conf * ramp)
        a = cv2.GaussianBlur(a, (0, 0), 2.0)[..., None]
        out = out * (1 - a) + best_col * a
        conf_map = np.maximum(conf_map, conf * ramp)
        print(f"  seam {i}-{j}: band {int(bm.sum())} conf-used {int((conf>0).sum())} ({100*conf[bm].mean():.0f}% of band)", flush=True)
    out = np.clip(out, 0, 255).astype(np.uint8)
    print(f"[planesweep] total conf-edit {100*(conf_map>0).mean():.2f}% runtime {time.time()-t0:.0f}s", flush=True)

    def rw(im, w=1500): return cv2.resize(im, (w, round(im.shape[0]*w/im.shape[1])), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(OUT / "PSWEEP_full.jpg"), cv2.cvtColor(np.vstack([lab(rw(L1), "L1"), lab(rw(out), "plane-sweep MVS (de-doubled, conf-gated)")]), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
    rows = []
    for u, v0, v1, tag in [(1760, 360, 600, "bmw"), (720, 330, 560, "graycar"), (300, 330, 560, "storefront")]:
        roll = W // 2 - u; cc = W // 2
        def cr(im): return cv2.cvtColor(np.roll(im, roll, 1)[v0:v1, cc-180:cc+180], cv2.COLOR_RGB2BGR)
        rows.append(np.hstack([lab(cr(L1), "L1 "+tag), lab(cr(out), "plane-sweep")]))
    g = np.vstack(rows); g = cv2.resize(g, (g.shape[1]*2, g.shape[0]*2), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(OUT / "PSWEEP_zoom.jpg"), g, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print("[saved] PSWEEP_full.jpg + PSWEEP_zoom.jpg")


if __name__ == "__main__":
    raise SystemExit(main())
