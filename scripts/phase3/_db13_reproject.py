"""DB-13: use DrivingForward's LEARNED dense depth (AV2-finetuned, LiDAR-supervised depth_net) to build
a dense ERP ego-range map, then REPROJECT THE REAL camera pixels with it (render_camera_to_erp) — NOT
the 3DGS render (which shreds near-ground). Goal: land the grazing near-ground curb that sparse-LiDAR-kNN
couldn't, single-source, on top of the clean CPU deliverable.

Pipeline: depth_net(6-cam) -> per-cam metric depth -> depth2pc -> ego points -> project to ERP + z-buffer
-> dense ERP range map -> render_camera_to_erp(7 real cams, that range) -> de-double the near-ground band.

Run in df env on A100:
  /opt/miniconda/envs/df/bin/python _db13_reproject.py --uuid <UUID> --tag <name>
Outputs results/db13/: DB13_<tag>_{depthmap.jpg (learned vs LiDAR ERP range), curb.png (L1 | LiDAR-kNN
reproject | learned-depth reproject)}. Vision-judge: does the LEARNED dense depth give a smoother/correct
near-ground range -> a cleaner curb reproject than sparse LiDAR?"""
from __future__ import annotations
import sys, time, argparse
from pathlib import Path
import numpy as np
import cv2
import torch

REPO_DF = Path("/content/DrivingForward")
HERE = Path(__file__).resolve().parent
for p in [str(REPO_DF), str(REPO_DF / "external" / "packnet_sfm"), str(REPO_DF / "external"),
          str(HERE.parent.parent / "code"), str(HERE)]:
    sys.path.insert(0, p)
import types
if "dataset" not in sys.modules:
    st = types.ModuleType("dataset"); st.construct_dataset = lambda *a, **k: None; sys.modules["dataset"] = st

import dibr_drivingforward_av2 as dfa
from network import DepthNetwork
from models.gaussian import depth2pc
from waymo2panorama.data_io.av2_loader import AV2RingLoader, RING_CAMS_7
from waymo2panorama.projection.sphere_projection import render_camera_to_erp
from waymo2panorama.blending.hard_hdr_of import hard_select, RING_PAIRS
from waymo2panorama.blending.seam_local_align import build_voronoi_seam_band
from run_a0_plane_dibr_probe import load_lidar_feather
from scipy.spatial import cKDTree

ROOT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/data/argoverse2/val")
OUT = Path("/content/drive/MyDrive/koi_waymo2pano_colab/results/db13"); OUT.mkdir(parents=True, exist_ok=True)
H, W = 1024, 2048
FAR = 1000.0
DH, DW = 352, 640  # DrivingForward input res


def egopts_to_erp_range(xyz):
    """ego points (N,3) -> dense ERP range map (z-buffer nearest) + coverage mask."""
    x, y, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    rng = np.linalg.norm(xyz, axis=1)
    th = np.arctan2(y, x); ph = np.arctan2(z, np.hypot(x, y))
    u = np.round((np.pi - th) / (2 * np.pi) * W - 0.5).astype(int) % W
    v = np.round((np.pi / 2 - ph) / np.pi * H - 0.5).astype(int)
    ok = (v >= 0) & (v < H) & (rng > 0.5) & (rng < 80.0)
    u, v, rng = u[ok], v[ok], rng[ok]
    rmap = np.full((H, W), np.inf, np.float32)
    order = np.argsort(-rng)  # far first so near overwrites
    rmap[v[order], u[order]] = rng[order]
    cov = np.isfinite(rmap)
    return rmap, cov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", default=dfa_default())
    ap.add_argument("--tag", default="bmw")
    ap.add_argument("--anchor", type=int, default=0)
    ap.add_argument("--weights", default="/content/drive/MyDrive/koi_waymo2pano_colab/results/dfwd_av2_finetune_v1")
    ap.add_argument("--band-hw", type=int, default=80)
    ap.add_argument("--knn-max", type=float, default=22.0)
    a = ap.parse_args()
    t0 = time.time(); erp_hw = (H, W); device = "cuda"

    loader = AV2RingLoader(ROOT / a.uuid); ts = loader.anchor_timestamps_ns()[a.anchor]
    frame = loader.load_synced_frame(ts)
    cams = {c: frame.calibrations[c] for c in RING_CAMS_7}

    # L1 (real-pixel rotation-only)
    l1s, l1w = [], []
    for c in RING_CAMS_7:
        cb = cams[c]; r, _x, wt = render_camera_to_erp(frame.images[c], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=None)
        l1s.append(r); l1w.append(wt)
    L1 = hard_select(l1s, l1w)

    # ---- DrivingForward learned depth (6-cam) -> ego points -> ERP range ----
    cfg = dfa.load_cfg(REPO_DF / "configs/nuscenes/main.yaml")
    depth_net = DepthNetwork(cfg).to(device).eval()
    print("[load depth_net]", dfa._load_state(depth_net, Path(a.weights) / "depth_net.pth"), flush=True)
    chosen, az = dfa.pick_6(list(RING_CAMS_7), frame.calibrations)
    inputs = dfa.build_inputs(frame, chosen, DH, DW, device)
    all_xyz = []
    with torch.no_grad():
        feats = depth_net(inputs)
        for c in range(6):
            K0 = inputs[("K", 0)][:, c, ...]
            depth = dfa.to_depth(feats[("cam", c)][("disp", 0)], K0, DH, DW)
            e2c = inputs["extrinsics_inv"][:, c, ...]
            xyz = depth2pc(depth, e2c, K0)              # (1,N,3) ego points (rows)
            all_xyz.append(xyz[0].cpu().numpy())
    all_xyz = np.concatenate(all_xyz, 0)
    learned_range, learned_cov = egopts_to_erp_range(all_xyz)
    print(f"[{a.tag}] learned ERP-range coverage={100*learned_cov.mean():.0f}% pts={len(all_xyz)}", flush=True)

    # ---- LiDAR ERP range (for comparison) ----
    pts, _l, _d = load_lidar_feather(ROOT / a.uuid, ts, max_delta_ms=75.0)
    lidar_range, lidar_cov = egopts_to_erp_range(np.asarray(pts)[:, :3].astype(np.float64))

    # seam band union
    bandU = np.zeros((H, W), bool)
    for (i, j) in RING_PAIRS:
        ov = (l1w[i] > 1e-6) & (l1w[j] > 1e-6)
        if int(ov.sum()) < 200: continue
        b, _s = build_voronoi_seam_band(l1w[i].astype(np.float32), l1w[j].astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
        bandU |= b & ov

    def reproject_single(rangemap, cov, tag):
        """densify range across band, reproject 7 real cams at it, de-double via winner pick where covered."""
        by, bx = np.where(bandU)
        ky, kx = np.where(cov)
        if len(ky) < 50: return L1.copy(), np.zeros((H, W), bool)
        tree = cKDTree(np.stack([ky, kx], 1)); dist, nn = tree.query(np.stack([by, bx], 1), k=1)
        dense = np.full((H, W), FAR, np.float64); good = dist <= a.knn_max
        dense[by[good], bx[good]] = rangemap[ky[nn[good]], kx[nn[good]]]
        reproj, ral = [], []
        for c in RING_CAMS_7:
            cb = cams[c]; s, al, _w = render_camera_to_erp(frame.images[c], cb.K, cb.T_ego_cam, erp_hw=erp_hw, convergence_distance_m=dense)
            reproj.append(s.astype(np.float32)); ral.append(al)
        win = np.stack([w.astype(np.float32) for w in l1w], 0).argmax(0)
        rwin = np.take_along_axis(np.stack(reproj, 0), win[None, ..., None], axis=0)[0]
        out = L1.astype(np.float32).copy(); fired = np.zeros((H, W), np.float32)
        for (i, j) in RING_PAIRS:
            ov = (l1w[i] > 1e-6) & (l1w[j] > 1e-6)
            if int(ov.sum()) < 200: continue
            band, signed = build_voronoi_seam_band(l1w[i].astype(np.float32), l1w[j].astype(np.float32), band_half_width=a.band_hw, threshold=1e-6)
            agree = band & ov & ral[i] & ral[j] & (np.abs(reproj[i] - reproj[j]).mean(2) < 16.0)
            dd = np.clip(np.abs(signed) / a.band_hw, 0, 1); ramp = np.where(band, 0.5 * (1 + np.cos(np.pi * dd)), 0).astype(np.float32)
            al = cv2.GaussianBlur((agree.astype(np.float32) * ramp), (0, 0), 2.0)[..., None]
            out = out * (1 - al) + rwin * al; fired = np.maximum(fired, agree.astype(np.float32) * ramp)
        print(f"[{a.tag}] {tag} reproject fired={100*(fired>0).mean():.2f}%", flush=True)
        return np.clip(out, 0, 255).astype(np.uint8), fired > 0

    lidar_re, _ = reproject_single(lidar_range, lidar_cov, "LiDAR-kNN")
    learned_re, _ = reproject_single(learned_range, learned_cov, "learned-depth")

    # ---- viz: ERP range maps (learned vs LiDAR) ----
    def colr(rmap, cov):
        d = np.where(cov, np.clip(rmap, 2, 40), 40); c8 = ((d - 2) / 38 * 255).astype(np.uint8)
        return cv2.applyColorMap(c8, cv2.COLORMAP_TURBO)
    dm = np.vstack([colr(lidar_range, lidar_cov), colr(learned_range, learned_cov)])
    cv2.imwrite(str(OUT / f"DB13_{a.tag}_depthmap.jpg"), dm, [cv2.IMWRITE_JPEG_QUALITY, 88])

    # ---- curb crop: L1 | LiDAR-kNN reproject | learned-depth reproject ----
    spots = {"bmw": (1085, 540, 730, 220)}.get(a.tag, (720, 320, 610, 220))
    u, v0, v1, hw = spots; roll = W // 2 - u; cc = W // 2

    def crp(im): return cv2.cvtColor(np.roll(im, roll, 1)[v0:v1, cc - hw:cc + hw], cv2.COLOR_RGB2BGR)
    def lab(im, t):
        b = np.zeros((24, im.shape[1], 3), np.uint8); cv2.putText(b, t, (5, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1); return np.vstack([b, im])
    row = np.hstack([lab(crp(L1), "L1"), lab(crp(lidar_re), "LiDAR-kNN reproj"), lab(crp(learned_re), "learned-depth reproj")])
    row = cv2.resize(row, (row.shape[1] * 2, row.shape[0] * 2), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(OUT / f"DB13_{a.tag}_curb.png"), row)
    print(f"[saved] DB13_{a.tag}_depthmap.jpg + curb.png  runtime {time.time()-t0:.0f}s", flush=True)


def dfa_default():
    return "02a00399-3857-444e-8db3-a8f58489c394"


if __name__ == "__main__":
    raise SystemExit(main())
