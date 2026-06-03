"""codex round-8 LEAD kill-test: BEV GROUND ATLAS -> ERP (escape the ERP-slab-stitching local optimum).

Instead of stitching 7 per-camera ground slabs in ERP (where the single-source cut kinks the road),
build ONE top-down BEV road texture: project all cams onto the LiDAR ground PLANE in ego XY (top-down),
single-source(argmax incidence weight) or agreement-blend per BEV cell -> lane lines are continuous in
GROUND coords -> then render that ONE continuous atlas back into the ERP ground band. Tall LiDAR returns
(>0.5m, cars/poles) are EXCLUDED from the ERP ground mask so the BEV only touches true ground (the object
layer stays the current deliverable). curb (~0.15m off-plane) is the expected residual.

Compare vs the current seamroute deliverable (SR_bmw_final_1024x2048.png) at the wavy seam crops.
KILL: lane/curb still kinks, OR vehicle bleed onto the road, OR road visibly blurrier than current.
Run (CPU): python _bev_ground.py --tag bmw   ->  results/seamroute/BEV_<tag>_{road,curb,graycar}.png
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=a1.BMW_UUID); ap.add_argument("--tag", default="bmw")
    ap.add_argument("--res", type=float, default=0.06, help="BEV cell size (m)")
    ap.add_argument("--rng", type=float, default=32.0, help="BEV half-extent / max ground range (m)")
    ap.add_argument("--blend", action="store_true", help="agreement-gated blend per cell (else argmax-weight single-source)")
    a = ap.parse_args(); t0 = time.time(); erp_hw = (H, W)

    loader = a1.AV2RingLoader(ROOT / a.uuid); ts = loader.anchor_timestamps_ns(); frame = loader.load_synced_frame(ts[0])
    pts, _l, _d = a1.load_lidar_feather(ROOT / a.uuid, ts[0], max_delta_ms=75.0); pts = np.asarray(pts)[:, :3].astype(np.float64)
    ground, _f = a1.fit_planes_p3(pts)
    assert ground is not None, "no ground plane fit"
    n = np.asarray(ground["n"], float); d = float(ground["d"])             # plane: n . X = d
    print(f"[plane] n={n.round(3)} d={d:.2f} resid={ground.get('resid_m',0):.3f}m", flush=True)

    # ---- BEV grid in ego XY (top-down), each cell on the ground plane ----
    R, res = a.rng, a.res
    xs = np.arange(-R, R, res); ys = np.arange(-R, R, res); NB = len(xs)
    GX, GY = np.meshgrid(xs, ys)                                            # (NB,NB)
    GZ = (d - n[0] * GX - n[1] * GY) / n[2]
    bev_pts = np.stack([GX, GY, GZ], -1).reshape(-1, 3)                     # (M,3)
    hom = np.concatenate([bev_pts, np.ones((len(bev_pts), 1))], 1)         # (M,4)

    atlas = np.zeros((NB * NB, 3), np.float32); best_w = np.zeros(NB * NB, np.float32)
    acc = np.zeros((NB * NB, 3), np.float32); acc_w = np.zeros(NB * NB, np.float32)
    for cam in a1.RING_CAMS_7:
        cb = frame.calibrations[cam]; img = frame.images[cam].astype(np.float32); Hi, Wi = img.shape[:2]
        T_cam_ego = np.linalg.inv(np.asarray(cb.T_ego_cam, float))
        cp = (T_cam_ego @ hom.T).T[:, :3]                                  # camera coords
        z = cp[:, 2]; uv = (np.asarray(cb.K, float) @ cp.T).T
        u = uv[:, 0] / np.maximum(uv[:, 2], 1e-6); v = uv[:, 1] / np.maximum(uv[:, 2], 1e-6)
        inb = (z > 0.3) & (u >= 0) & (u < Wi - 1) & (v >= 0) & (v < Hi - 1)
        idx = np.where(inb)[0]
        if len(idx) == 0: continue
        rng = np.linalg.norm(cp[idx], axis=1); w = (1.0 / np.maximum(rng, 1.0)).astype(np.float32)   # nearer = higher res
        uu = u[idx]; vv = v[idx]; u0 = np.floor(uu).astype(int); v0 = np.floor(vv).astype(int)        # manual bilinear (cv2.remap caps maps at 32767 rows)
        fu = (uu - u0)[:, None].astype(np.float32); fv = (vv - v0)[:, None].astype(np.float32)
        col = (img[v0, u0] * (1 - fu) * (1 - fv) + img[v0, u0 + 1] * fu * (1 - fv)
               + img[v0 + 1, u0] * (1 - fu) * fv + img[v0 + 1, u0 + 1] * fu * fv)
        better = w > best_w[idx]; sel = idx[better]
        atlas[sel] = col[better]; best_w[idx[better]] = w[better]
        acc[idx] += col * w[:, None]; acc_w[idx] += w
    bev = (np.where(acc_w[:, None] > 0, acc / np.maximum(acc_w[:, None], 1e-6), 0).reshape(NB, NB, 3)
           if a.blend else atlas.reshape(NB, NB, 3))
    cov = best_w.reshape(NB, NB) > 0
    print(f"[bev] {NB}x{NB} @ {res}m, coverage={100*cov.mean():.1f}% rt={time.time()-t0:.0f}s", flush=True)

    # ---- render BEV atlas -> ERP ground band ----
    rays = a1._erp_rays(erp_hw).reshape(H, W, 3)
    rn = rays @ n
    with np.errstate(divide="ignore", invalid="ignore"):
        lam = d / rn
    pt = lam[..., None] * rays                                             # ego ground point per ERP pixel
    ground_hit = np.isfinite(lam) & (lam > 0.5) & (lam < R) & (np.abs(rn) > 1e-3)
    ix = (pt[..., 0] + R) / res; iy = (pt[..., 1] + R) / res
    inb = ground_hit & (ix >= 0) & (ix < NB - 1) & (iy >= 0) & (iy < NB - 1)
    erp_g = cv2.remap(bev, ix.astype(np.float32), iy.astype(np.float32), cv2.INTER_LINEAR)
    covmap = cv2.remap(cov.astype(np.float32), ix.astype(np.float32), iy.astype(np.float32), cv2.INTER_NEAREST)  # hard coverage (no soft 0..1 ramp)

    # tall LiDAR mask (cars/poles >0.5m above plane) -> exclude from the ground composite
    hh = pts @ n - d; rr = np.linalg.norm(pts, axis=1); tp = pts[(hh > 0.5) & (rr < 45.0)]
    tall = np.zeros((H, W), np.uint8)
    if len(tp):
        tx, ty, tz = tp[:, 0], tp[:, 1], tp[:, 2]
        tth = np.arctan2(ty, tx); tph = np.arctan2(tz, np.hypot(tx, ty))
        tu = ((np.pi - tth) / (2 * np.pi) * W).astype(int) % W; tv = ((np.pi / 2 - tph) / np.pi * H).astype(int).clip(0, H - 1)
        tall[tv, tu] = 1
    tall = cv2.dilate(tall, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))).astype(bool)
    rows = np.arange(H)[:, None] * np.ones((1, W))
    gmask = inb & (covmap > 0.5) & (~tall) & (rows > H * 0.50)
    # erode a few px: drop boundary pixels whose bilinear erp_g sample blended with the black (uncovered)
    # atlas cells -> kills the dark fringe along the BEV coverage edge.
    gmask = cv2.erode(gmask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))).astype(bool)
    print(f"[erp] ground composite mask = {100*gmask.mean():.2f}% of pano (tall-excluded={100*tall.mean():.1f}%)", flush=True)

    cur = cv2.cvtColor(cv2.imread(str(OUT / "SR_bmw_final_1024x2048.png")), cv2.COLOR_BGR2RGB).astype(np.float32)
    m = cv2.GaussianBlur(gmask.astype(np.float32), (0, 0), 2.0)[..., None]
    bevpano = (cur * (1 - m) + erp_g * m).astype(np.uint8)
    cur_u = cur.astype(np.uint8)

    cv2.imwrite(str(OUT / f"BEV_{a.tag}_atlas.jpg"), cv2.cvtColor(np.clip(bev, 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
    cv2.imwrite(str(OUT / f"BEV_{a.tag}_pano.jpg"), cv2.cvtColor(cv2.resize(bevpano, (2048, 1024)), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
    # ADOPTED: BEV-improved deliverable = the new full-res init for DiT360 thin-seam (DB-14)
    cv2.imwrite(str(OUT / f"SR_{a.tag}_bevfinal_1024x2048.png"), cv2.cvtColor(bevpano, cv2.COLOR_RGB2BGR))
    for name, (u0, v0, vh, hw) in SPOTS.items():
        roll = W // 2 - u0; cc = W // 2
        a_ = cv2.cvtColor(np.roll(cur_u, roll, 1)[v0:v0 + vh, cc - hw:cc + hw], cv2.COLOR_RGB2BGR)
        b_ = cv2.cvtColor(np.roll(bevpano, roll, 1)[v0:v0 + vh, cc - hw:cc + hw], cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(OUT / f"BEV_{a.tag}_{name}.png"), np.vstack([lab(a_, "current (seamroute)"), lab(b_, "BEV-ground (NEW)")]))
    print(f"[saved] {OUT}/BEV_{a.tag}_{{atlas.jpg, pano.jpg, {list(SPOTS)}}} rt={time.time()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
